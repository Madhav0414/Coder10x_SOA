"""Tests for Prahari.

Two kinds of test here, and the second kind matters more.

Mechanical tests check that code runs.  Physiological tests check that the
model is not quietly wrong -- that a diuretic decongests, that tachycardia
raises afterload, that congestion is recoverable from ward observations.  A
cardiac twin that runs cleanly while recommending fluids for a drowning
patient has passed the first kind and failed the second.
"""

from __future__ import annotations

import numpy as np
import pytest

from prahari.core.cardiac import PCWP_WET_THRESHOLD, CardiacParams, solve_hemodynamics
from prahari.core.continuity import readmission_risk, simulate_home_trajectory
from prahari.core.handover import ward_handover
from prahari.core.interventions import FORMULARY, Plan, build_drive
from prahari.core.metabolic import glucose_excursion, meal_swap_benefit
from prahari.core.stevenson import classify, severity_score
from prahari.core.triage import evaluate_patient
from prahari.core.twin import PatientTwin
from prahari.data.ward import build_ward
from prahari.models.baselines import news2_score
from prahari.models.estimator import fit_twin, shared_inverse


# --------------------------------------------------------------------------
# Physiology
# --------------------------------------------------------------------------

def test_healthy_heart_produces_textbook_values():
    h = solve_hemodynamics(CardiacParams())
    assert 55 <= h.stroke_volume <= 90, h.stroke_volume
    assert 4.0 <= h.cardiac_output <= 6.5, h.cardiac_output
    assert 100 <= h.sbp <= 135, h.sbp
    assert 60 <= h.dbp <= 85, h.dbp
    assert 6 <= h.pcwp <= 15, h.pcwp
    assert 0.45 <= h.ef <= 0.70, h.ef


def test_failing_heart_has_low_ejection_fraction():
    hf = CardiacParams(emax=0.5, emax_base=0.5, v_central=780, c_diastolic=14, edv_max=300)
    h = solve_hemodynamics(hf)
    assert h.ef < 0.30
    assert h.pcwp > PCWP_WET_THRESHOLD


def test_tachycardia_raises_afterload_and_cuts_stroke_volume():
    """Faster rates raise effective arterial elastance and shorten filling.

    This is why a failing ventricle gets worse when it speeds up, and it must
    emerge from the equations rather than being asserted anywhere.
    """
    slow = CardiacParams(emax=0.7, emax_base=0.7, hr=70, v_central=700)
    fast = CardiacParams(emax=0.7, emax_base=0.7, hr=130, v_central=700)
    assert solve_hemodynamics(fast).stroke_volume < solve_hemodynamics(slow).stroke_volume


def test_congestion_penalises_effective_contractility():
    """Within the congested range, MORE congestion must not mean more output.

    Both states below sit above the 18 mmHg congestion threshold, which is the
    regime where the claim holds: on the flat top of the Frank-Starling curve,
    extra filling pressure buys nothing and costs contractility through
    functional mitral regurgitation and pericardial constraint.  Get this
    backwards and the twin recommends against diuretics.
    """
    severe = CardiacParams(emax=0.7, emax_base=0.7, v_central=1120, c_diastolic=14, edv_max=300)
    moderate = CardiacParams(emax=0.7, emax_base=0.7, v_central=740, c_diastolic=14, edv_max=300)

    h_sev, h_mod = solve_hemodynamics(severe), solve_hemodynamics(moderate)
    assert h_sev.pcwp > PCWP_WET_THRESHOLD and h_mod.pcwp > PCWP_WET_THRESHOLD
    assert h_sev.pcwp > h_mod.pcwp
    assert h_mod.cardiac_index > h_sev.cardiac_index, (
        f"more congestion produced more output: {h_mod.cardiac_index:.2f} "
        f"vs {h_sev.cardiac_index:.2f}"
    )


def test_frank_starling_saturates():
    """Past a point, extra filling pressure buys almost no extra volume."""
    p1 = CardiacParams(v_central=700, c_diastolic=14, edv_max=300)
    p2 = CardiacParams(v_central=1100, c_diastolic=14, edv_max=300)
    gain = solve_hemodynamics(p2).edv - solve_hemodynamics(p1).edv
    span = solve_hemodynamics(p2).pcwp - solve_hemodynamics(p1).pcwp
    assert gain / span < 8.0, "diastolic relation is not saturating"


# --------------------------------------------------------------------------
# Stevenson quadrants
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ci,pcwp,expected",
    [(3.0, 12.0, "A"), (3.0, 26.0, "B"), (1.8, 12.0, "L"), (1.8, 26.0, "C")],
)
def test_quadrant_boundaries(ci, pcwp, expected):
    fake = type("H", (), {"cardiac_index": ci, "pcwp": pcwp, "spo2": 97.0, "map": 85.0})()
    assert classify(fake).code == expected


def test_cold_wet_is_the_worst_ranked_profile():
    fake_c = type("H", (), {"cardiac_index": 1.5, "pcwp": 30.0, "spo2": 88.0, "map": 68.0})()
    fake_a = type("H", (), {"cardiac_index": 3.2, "pcwp": 10.0, "spo2": 98.0, "map": 90.0})()
    assert classify(fake_c).mortality_rank > classify(fake_a).mortality_rank
    assert severity_score(fake_c) > severity_score(fake_a)


# --------------------------------------------------------------------------
# Interventions -- direction of effect
# --------------------------------------------------------------------------

def _congested_twin():
    p = CardiacParams(emax=0.70, emax_base=0.70, v_central=790, svr=20, svr_base=19,
                      hr=94, hr_base=88, c_diastolic=14, edv_max=300, v_interstitial=280)
    return PatientTwin("T", p, progression_per_h=0.02, intake_base=95.0)


def test_diuretic_decongests():
    tw = _congested_twin()
    nothing = tw.project(hours=8)
    diuresed = tw.project(hours=8, plan=Plan.of("furosemide"))
    assert diuresed.endpoint().pcwp < nothing.endpoint().pcwp


def test_diuretic_improves_oxygenation():
    tw = _congested_twin()
    nothing = tw.project(hours=10)
    diuresed = tw.project(hours=10, plan=Plan.of("furosemide"))
    assert diuresed.endpoint().spo2 > nothing.endpoint().spo2


def test_inotrope_raises_cardiac_index():
    tw = _congested_twin()
    nothing = tw.project(hours=6)
    dobu = tw.project(hours=6, plan=Plan.of("dobutamine"))
    assert dobu.endpoint().cardiac_index > nothing.endpoint().cardiac_index


def test_fluids_raise_filling_pressure():
    tw = _congested_twin()
    nothing = tw.project(hours=4)
    fluids = tw.project(hours=4, plan=Plan.of("fluid_bolus"))
    assert fluids.endpoint().pcwp > nothing.endpoint().pcwp


def test_treating_beats_doing_nothing_on_severity():
    tw = _congested_twin()
    nothing = tw.project(hours=12).worst_severity()
    best = min(tr.worst_severity() for tr in tw.counterfactuals(hours=12))
    assert best < nothing


def test_treating_before_the_deadline_beats_treating_after_it():
    """The wait-versus-act claim, stated so that it is actually true.

    A naive version of this test ("sooner is always better") FAILS against the
    model, and the reason is worth keeping: a single dose of furosemide washes
    out over a few hours, so a dose at hour zero can leave a patient uncovered
    at hour twelve while a later dose still has effect.  That is real
    pharmacology, not a bug, and it is an argument for repeat dosing.

    What does hold, and what the cockpit actually claims, is narrower: treating
    BEFORE the projected crossing changes the crossing; treating after it does
    not.
    """
    tw = _congested_twin()
    untreated = tw.project(hours=16).time_to_critical()
    assert untreated is not None and untreated < 10.0

    early = tw.project(hours=16, plan=Plan.of("furosemide", at=2.0)).time_to_critical()
    too_late = tw.project(hours=16, plan=Plan.of("furosemide", at=12.0)).time_to_critical()

    assert too_late is not None
    assert early is None or early > too_late, (
        f"early dosing did not move the deadline: early={early}, late={too_late}"
    )
    # Treating after the horse has bolted is no better than not treating.
    assert abs(too_late - untreated) < 1.0


def test_pharmacodynamics_ramp_and_wash_out():
    agent = FORMULARY["furosemide"]
    assert agent.level(t=0.0, t_start=0.0) == 0.0
    assert agent.level(t=1.0, t_start=0.0) > 0.5
    assert agent.level(t=20.0, t_start=0.0) < 0.05


def test_drive_composes_multiplicatively():
    drive = build_drive(Plan.of("dobutamine"), {"emax_base": 1.0})
    assert drive(3.0)["inotropy"] > 1.0
    assert build_drive(Plan(), {})(3.0)["inotropy"] == 1.0


# --------------------------------------------------------------------------
# Twin properties -- the four that define a twin
# --------------------------------------------------------------------------

def test_twin_simulates_a_trajectory_not_a_label():
    tr = _congested_twin().project(hours=12)
    assert len(tr.t) > 20
    assert tr.series("cardiac_index").ndim == 1


def test_twin_counterfactuals_differ_from_each_other():
    tw = _congested_twin()
    finals = {round(tr.endpoint().pcwp, 2) for tr in tw.counterfactuals(hours=10)}
    assert len(finals) > 1, "all interventions produced the same future"


def test_persistence_stops_a_transient_dip_counting_as_decompensation():
    tw = _congested_twin()
    tr = tw.project(hours=12, plan=Plan.of("furosemide"))
    strict = tr.time_to_critical(persist_h=3.0)
    loose = tr.time_to_critical(persist_h=0.25)
    if loose is not None and strict is not None:
        assert strict >= loose


# --------------------------------------------------------------------------
# State recovery from degraded ward observations
# --------------------------------------------------------------------------

def test_twin_recovers_congestion_from_ward_cadence_observations():
    """The identifiability test that caught a real bug.

    Filling pressure is invisible to a blood-pressure cuff.  Without the
    congestion signs a ward actually charts, this recovery fails and the twin
    classifies drowning patients as dry.
    """
    inv = shared_inverse()
    ward = build_ward(n_beds=8, seed=7)
    errors, matches = [], []
    for wp in ward:
        tw = fit_twin(wp.ehr.patient_id, wp.observations,
                      template=CardiacParams(bsa=wp.ehr.bsa, c_diastolic=14, edv_max=295),
                      inverse=inv)
        truth = solve_hemodynamics(wp.true_params)
        errors.append(abs(tw.hemodynamics.pcwp - truth.pcwp))
        matches.append((truth.pcwp > PCWP_WET_THRESHOLD)
                       == (tw.hemodynamics.pcwp > PCWP_WET_THRESHOLD))
    assert np.mean(errors) < 6.0, f"PCWP MAE {np.mean(errors):.1f} mmHg"
    assert np.mean(matches) >= 0.75, f"wet/dry agreement {np.mean(matches):.2f}"


def test_confidence_falls_when_observations_are_sparse():
    inv = shared_inverse()
    ward = build_ward(n_beds=2, seed=3)
    wp = ward[0]
    rich = fit_twin("rich", wp.observations, inverse=inv)
    poor = fit_twin("poor", wp.observations[:1], inverse=inv)
    assert poor.confidence <= rich.confidence


def test_twin_with_no_observations_reports_floor_confidence():
    tw = PatientTwin("empty", CardiacParams())
    assert tw.confidence <= 0.3


# --------------------------------------------------------------------------
# Triage and alarm suppression
# --------------------------------------------------------------------------

def test_compensated_patient_does_not_raise_an_alert():
    calm = CardiacParams(emax=1.4, emax_base=1.4, v_central=430, hr=70, hr_base=70)
    tw = PatientTwin("calm", calm, fit_n_obs=6, fit_residual=1.0)
    entry = evaluate_patient(tw, type("E", (), {"bed": "Bed X"})())
    assert not entry.alert


def test_cold_wet_patient_always_alerts():
    sick = CardiacParams(emax=0.42, emax_base=0.42, v_central=880, hr=112, hr_base=95,
                         c_diastolic=14, edv_max=300, v_interstitial=700)
    tw = PatientTwin("sick", sick, fit_n_obs=6, fit_residual=1.0, progression_per_h=0.02)
    entry = evaluate_patient(tw, type("E", (), {"bed": "Bed Y"})())
    assert entry.twin.profile.code == "C"
    assert entry.alert


def test_ranking_puts_alerts_first():
    from prahari.pipeline import build_ward_state
    state = build_ward_state(n_beds=8, seed=7)
    flags = [e.alert for e in state.entries]
    assert flags == sorted(flags, reverse=True), "alerts are not sorted to the top"


def test_imminent_crossing_alerts_even_when_no_ward_drug_helps(monkeypatch):
    """If the twin projects cold-wet within hours and nothing on the formulary
    changes it, the action is escalation -- that alert must never be
    suppressed.  An earlier version suppressed it.  The formulary is emptied
    here so the no-drug branch is the one actually exercised."""
    import prahari.core.triage as triage

    monkeypatch.setattr(triage, "guideline_options", lambda code: [])
    sick = CardiacParams(emax=0.55, emax_base=0.55, v_central=820, hr=104, hr_base=92,
                         c_diastolic=14, edv_max=300, v_interstitial=420)
    tw = PatientTwin("slide", sick, fit_n_obs=6, fit_residual=1.0,
                     progression_per_h=0.04, intake_base=110.0)
    entry = triage.evaluate_patient(tw, type("E", (), {"bed": "Bed Z"})())

    assert entry.best is None
    assert entry.deadline_h is not None and entry.deadline_h <= triage.ESCALATE_WITHIN_H
    assert entry.alert
    assert "escalate" in entry.reason.lower()


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------

def test_news2_scores_a_well_patient_at_zero():
    assert news2_score(hr=72, sbp=124, spo2=98, resp_rate=16) == 0


def test_news2_escalates_a_sick_patient():
    assert news2_score(hr=132, sbp=86, spo2=89, resp_rate=28) >= 7


# --------------------------------------------------------------------------
# Metabolic and continuity layers
# --------------------------------------------------------------------------

def test_glucose_excursion_peaks_then_clears():
    curve = [glucose_excursion(t, 95, 0.85, 8.0) for t in np.linspace(0.1, 6, 40)]
    assert max(curve) > 20
    assert curve[-1] < max(curve) * 0.4


def test_poor_glycaemic_control_raises_the_peak():
    good = glucose_excursion(1.0, 95, 0.85, 5.6)
    poor = glucose_excursion(1.0, 95, 0.85, 10.5)
    assert poor > good


def test_millet_swap_lowers_the_excursion():
    sw = meal_swap_benefit(8.5)
    assert sw["delta"] > 0
    assert sw["sodium_saved_mg"] > 0


def test_decompensating_home_course_scores_higher_risk():
    tw = _congested_twin()
    stable = readmission_risk(tw, simulate_home_trajectory(tw, days=30, decompensating=False))
    sliding = readmission_risk(tw, simulate_home_trajectory(tw, days=30, decompensating=True))
    assert sliding.risk > stable.risk


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------

def test_full_pipeline_and_handover():
    from prahari.pipeline import build_ward_state
    state = build_ward_state(n_beds=8, seed=7)
    assert len(state.entries) == 8
    brief = ward_handover(state.entries)
    assert "PRAHARI SHIFT HANDOVER" in brief
    assert len(brief) > 400


def test_ward_generates_both_required_data_streams():
    """The twin fuses a static EHR stream with a dynamic
    time-series stream. Assert both are actually present."""
    ward = build_ward(n_beds=4, seed=2)
    for wp in ward:
        assert wp.ehr.bnp > 0 and wp.ehr.creatinine > 0      # static: labs
        assert wp.ehr.admission_dx and wp.ehr.home_meds      # static: dx + meds
        assert wp.ehr.cyp2d6 in {"poor", "intermediate", "normal", "ultrarapid"}
        assert len(wp.observations) >= 2                      # dynamic: time series
        assert all(o.t >= 0 for o in wp.observations)


# --------------------------------------------------------------------------
# Regression tests for bugs found during validation
# --------------------------------------------------------------------------

def test_charting_cadence_does_not_change_patient_identity():
    """Cadence must change only how often a patient is charted, never who
    they are.  An earlier version drew observation noise from the patient
    stream, so switching cadence silently swapped every patient after bed 1
    and the ICU-vs-ward comparison was not like for like."""
    ward4 = build_ward(n_beds=8, seed=11, history_h=16.0, cadence_h=4.0)
    ward1 = build_ward(n_beds=8, seed=11, history_h=16.0, cadence_h=1.0)
    for a, b in zip(ward4, ward1):
        assert a.ehr == b.ehr
        assert a.true_params == b.true_params
        assert len(b.observations) > len(a.observations)


def test_assimilation_beats_trusting_the_last_reading():
    """Fitting the whole chart jointly must recover cardiac index better than
    inverting only the most recent (noisy) observation."""
    from prahari.models.estimator import fit_single
    from prahari.pipeline import truth_at

    inv = shared_inverse()
    snap_err, joint_err = [], []
    for seed in (3, 5):
        for wp in build_ward(n_beds=8, seed=seed, history_h=20.0):
            tpl = CardiacParams(bsa=wp.ehr.bsa, c_diastolic=14, edv_max=295)
            truth = truth_at(wp, wp.observations[-1].t)
            snap, _ = fit_single(wp.observations[-1], tpl, inverse=inv)
            joint = fit_twin(wp.ehr.patient_id, wp.observations, template=tpl, inverse=inv)
            snap_err.append(abs(solve_hemodynamics(snap).cardiac_index - truth.cardiac_index))
            joint_err.append(abs(joint.hemodynamics.cardiac_index - truth.cardiac_index))
    assert np.mean(joint_err) < np.mean(snap_err)


def test_capillary_refill_lengthens_as_output_falls():
    warm = CardiacParams(emax=1.3, emax_base=1.3, v_central=520)
    cold = CardiacParams(emax=0.45, emax_base=0.45, v_central=760, c_diastolic=14, edv_max=300)
    assert solve_hemodynamics(cold).cardiac_index < solve_hemodynamics(warm).cardiac_index
    assert solve_hemodynamics(cold).crt_s > solve_hemodynamics(warm).crt_s


def test_counterfactual_directions_match_the_real_patient():
    """Give each drug to the twin and to the ground-truth patient from the same
    moment; the twin must get the direction of the effect right."""
    from prahari.core.interventions import FORMULARY
    from prahari.pipeline import _truth_continuation, fit_ward_at

    patients = build_ward(n_beds=8, seed=4, history_h=16.0)
    hits = n = 0
    for wp, twin, _e, _v in fit_ward_at(patients, clock=12.0):
        truth = _truth_continuation(wp, 12.0)
        tw0, tr0 = twin.project(hours=6), truth.project(hours=6)
        for key in FORMULARY:
            d_tw = twin.project(hours=6, plan=Plan.of(key)).endpoint().pcwp - tw0.endpoint().pcwp
            d_tr = truth.project(hours=6, plan=Plan.of(key)).endpoint().pcwp - tr0.endpoint().pcwp
            if abs(d_tr) > 0.5:
                n += 1
                hits += int(np.sign(d_tw) == np.sign(d_tr))
    assert n > 10
    assert hits / n >= 0.9


def test_poor_cyp2d6_metaboliser_gets_a_stronger_beta_blocker_effect():
    """The EHR's genetic marker changes simulated drug response."""
    p = CardiacParams(emax=1.2, emax_base=1.2, hr=96, hr_base=96, v_central=520)
    normal = PatientTwin("n", p, pgx_beta_blocker=1.0)
    poor = PatientTwin("p", p, pgx_beta_blocker=1.8)
    hr_normal = normal.project(hours=6, plan=Plan.of("metoprolol")).states[-1].hr
    hr_poor = poor.project(hours=6, plan=Plan.of("metoprolol")).states[-1].hr
    assert hr_poor < hr_normal


def test_forecast_starts_where_the_chart_left_off():
    """A fitted twin must be an equilibrium of its own model.  Baselines taken
    from averages made every forecast open with a spurious hour-long jump
    (heart rate leapt ~35 bpm in the first hour)."""
    from prahari.pipeline import fit_ward_at

    patients = build_ward(n_beds=8, seed=7, history_h=16.0)
    for wp, twin, _entry, visible in fit_ward_at(patients, clock=16.0):
        tr = twin.project(hours=1.0)
        assert abs(tr.states[-1].hr - tr.states[0].hr) < 4.0, wp.ehr.bed
        assert abs(tr.hemo[-1].map - tr.hemo[0].map) < 4.0, wp.ehr.bed


def test_advancing_a_twin_matches_simulating_straight_through():
    """Propagating between observations must not change the physics."""
    tw = _congested_twin()
    direct = tw.project(hours=8).hemo[-1]
    chained = tw.advanced(4.0).project(hours=4).hemo[-1]
    assert abs(direct.cardiac_index - chained.cardiac_index) < 1e-3
    assert abs(direct.pcwp - chained.pcwp) < 1e-2
    assert tw.advanced(4.0).confidence <= tw.confidence


def test_simulations_can_run_on_parallel_server_threads():
    """The API answers requests on a thread pool, and LSODA cannot run two
    problems at once: a ward refit and a what-if arriving together used to
    crash one of them with a 500."""
    import threading
    from prahari.core.cardiac import CardiacParams, simulate

    errors = []

    def work():
        try:
            for _ in range(4):
                simulate(CardiacParams(emax=0.7, emax_base=0.7), hours=6.0)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[0]
