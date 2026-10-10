"""End-to-end assembly: ward chart -> fitted twins -> ranked ward.

The API, the tests and the experiments all go through `fit_ward_at`, so the
numbers in the README come from the same code path the demo runs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .core.cardiac import CardiacParams
from .core.interventions import CYP2D6_BETA_BLOCKER
from .core.stevenson import classify
from .core.triage import WardEntry, evaluate_patient, rank_ward
from .data.ward import ICU_CADENCE_H, WARD_CADENCE_H, build_ward
from .models.baselines import NEWS2_URGENT, news2_from_observation
from .models.estimator import fit_twin, shared_inverse


@dataclass
class WardState:
    patients: list      # WardPatient -- holds ground truth, never used for fitting
    twins: list         # PatientTwin, same order as patients
    entries: list       # WardEntry, ranked
    cadence_h: float

    @property
    def alerts(self) -> list:
        return [e for e in self.entries if e.alert]

    def entry_for(self, bed: str) -> WardEntry | None:
        return next((e for e in self.entries if e.bed == bed), None)


def fit_ward_at(patients: list, clock: float, horizon_h: float = 12.0) -> list:
    """Fit every twin on what was charted up to `clock`.

    Returns (patient, twin, entry, visible_observations) per bed, unranked.
    Twins start from a population-average heart, not from the true state.
    """
    inverse = shared_inverse()
    rows = []
    for wp in patients:
        visible = [o for o in wp.observations if o.t <= clock + 1e-9] or wp.observations[:1]
        template = CardiacParams(bsa=wp.ehr.bsa, c_diastolic=14.0, edv_max=295.0)
        twin = fit_twin(wp.ehr.patient_id, visible, template=template, inverse=inverse)
        twin.pgx_beta_blocker = CYP2D6_BETA_BLOCKER.get(wp.ehr.cyp2d6, 1.0)
        # A missed reading leaves the twin behind the clock: carry it forward
        # so every bed is judged as of this round.  (No-op when charted now.)
        twin = twin.advanced(max(0.0, clock - visible[-1].t))
        rows.append((wp, twin, evaluate_patient(twin, wp.ehr, horizon_h=horizon_h), visible))
    return rows


def build_ward_state(
    n_beds: int = 8,
    seed: int = 7,
    cadence_h: float = WARD_CADENCE_H,
    history_h: float = 16.0,
    horizon_h: float = 12.0,
) -> WardState:
    patients = build_ward(n_beds=n_beds, seed=seed, history_h=history_h, cadence_h=cadence_h)
    rows = fit_ward_at(patients, clock=history_h, horizon_h=horizon_h)
    return WardState(
        patients=patients,
        twins=[r[1] for r in rows],
        entries=rank_ward([r[2] for r in rows]),
        cadence_h=cadence_h,
    )


def truth_at(wp, t: float):
    """Ground-truth haemodynamics at time t (for scoring only)."""
    tr = wp.truth_trajectory
    return tr.hemo[int(np.argmin(np.abs(tr.t - t)))]


# --------------------------------------------------------------------------
# Experiment 1 -- state recovery at ICU versus ward cadence
# --------------------------------------------------------------------------

def ward_degradation_experiment(
    n_beds: int = 8,
    seeds: tuple = tuple(range(1, 9)),
    history_h: float = 16.0,
    horizon_h: float = 12.0,
) -> dict:
    """Same patients, same twin; only the charting interval changes.

    Patient identity and physiology depend on the seed alone, so each arm
    sees the identical cohort.  The twin is scored against ground truth at
    the time of its last observation.
    """
    results = {"icu": [], "ward": []}
    for seed in seeds:
        for tag, cadence in (("icu", ICU_CADENCE_H * 4), ("ward", WARD_CADENCE_H)):
            patients = build_ward(n_beds=n_beds, seed=seed, history_h=history_h, cadence_h=cadence)
            for wp, twin, _entry, visible in fit_ward_at(patients, history_h, horizon_h):
                truth = truth_at(wp, visible[-1].t)
                fit = twin.hemodynamics
                results[tag].append({
                    "ci_err": abs(fit.cardiac_index - truth.cardiac_index),
                    "pcwp_err": abs(fit.pcwp - truth.pcwp),
                    "quadrant_match": classify(truth).code == twin.profile.code,
                    "wet_match": (truth.pcwp > 18) == (fit.pcwp > 18),
                    "cold_match": (truth.cardiac_index < 2.2) == (fit.cardiac_index < 2.2),
                    "confidence": twin.confidence,
                    "n_obs": len(visible),
                })

    summary = {}
    for tag, rows in results.items():
        summary[tag] = {
            "n": len(rows),
            "ci_mae": float(np.mean([r["ci_err"] for r in rows])),
            "pcwp_mae": float(np.mean([r["pcwp_err"] for r in rows])),
            "quadrant_acc": float(np.mean([r["quadrant_match"] for r in rows])),
            "wet_acc": float(np.mean([r["wet_match"] for r in rows])),
            "cold_acc": float(np.mean([r["cold_match"] for r in rows])),
            "confidence": float(np.mean([r["confidence"] for r in rows])),
            "obs_per_patient": float(np.mean([r["n_obs"] for r in rows])),
        }
    return summary


# --------------------------------------------------------------------------
# Experiment 2 -- prospective, round-by-round evaluation
# --------------------------------------------------------------------------

def _onset(wp, persist_steps: int = 4):
    """Time the patient truly enters cold-wet and stays for >= 1 h, or None."""
    fl = [h.cardiac_index < 2.2 and h.pcwp > 18 for h in wp.truth_trajectory.hemo]
    for i in range(len(fl) - persist_steps):
        if all(fl[i:i + persist_steps]):
            return float(wp.truth_trajectory.t[i])
    return None


def prospective_evaluation(
    n_beds: int = 8,
    seeds: tuple = tuple(range(1, 9)),
    cadence_h: float = WARD_CADENCE_H,
    history_h: float = 28.0,
    horizon_h: float = 12.0,
) -> dict:
    """Replay each ward round by round, as it would have run live.

    At every charting round both systems see only what has been charted so
    far.  A bed scores as a NEWS2 alarm if its reading that round is >= 5,
    and as a Prahari alert if its twin -- refitted on everything so far --
    raises one.  Beds with no reading that round are not scored by either.

    Reported for each system:
      sensitivity  deteriorations alarmed on at or before true cold-wet onset
      lead         median hours of warning on those
      alarms/day   alarms per 24 patient-hours of monitoring
      PPV          share of alarms on a patient who is cold-wet within the
                   next `horizon_h` hours (or already is)
    Patients already cold-wet when monitoring starts are excluded from the
    sensitivity denominator; they are a detection problem, not a forecast one.
    """
    stats = {s: {"alarms": 0, "true": 0, "caught": 0, "lead": []} for s in ("news2", "prahari")}
    scored = events = 0
    ci_err, pcwp_err = [], []

    n_rounds = int(history_h // cadence_h)
    for seed in seeds:
        patients = build_ward(n_beds=n_beds, seed=seed, history_h=history_h, cadence_h=cadence_h)
        onset = {wp.ehr.patient_id: _onset(wp) for wp in patients}
        first = {"news2": {}, "prahari": {}}

        for k in range(1, n_rounds + 1):
            clock = k * cadence_h
            for wp, twin, entry, visible in fit_ward_at(patients, clock, horizon_h):
                pid = wp.ehr.patient_id
                charted = [o for o in wp.observations if abs(o.t - clock) < 1e-6]
                if not charted:
                    continue
                scored += 1
                truth = truth_at(wp, clock)
                ci_err.append(abs(twin.hemodynamics.cardiac_index - truth.cardiac_index))
                pcwp_err.append(abs(twin.hemodynamics.pcwp - truth.pcwp))

                t_on = onset[pid]
                soon = t_on is not None and t_on <= clock + horizon_h
                fired = {
                    "news2": news2_from_observation(charted[0]) >= NEWS2_URGENT,
                    "prahari": entry.alert,
                }
                for system, on in fired.items():
                    if on:
                        stats[system]["alarms"] += 1
                        stats[system]["true"] += int(soon)
                        first[system].setdefault(pid, clock)

        for pid, t_on in onset.items():
            if t_on is None or t_on < cadence_h:
                continue
            events += 1
            for system in stats:
                t_alarm = first[system].get(pid)
                if t_alarm is not None and t_alarm <= t_on + 1e-9:
                    stats[system]["caught"] += 1
                    stats[system]["lead"].append(t_on - t_alarm)

    patient_days = scored * cadence_h / 24.0
    out = {
        "rounds_scored": scored,
        "deteriorations": events,
        "state_ci_mae": float(np.mean(ci_err)),
        "state_pcwp_mae": float(np.mean(pcwp_err)),
    }
    for system, s in stats.items():
        out[system] = {
            "alarms": s["alarms"],
            "alarms_per_patient_day": s["alarms"] / patient_days if patient_days else 0.0,
            "ppv": s["true"] / s["alarms"] if s["alarms"] else None,
            "sensitivity": s["caught"] / events if events else None,
            "caught": s["caught"],
            "median_lead_h": float(np.median(s["lead"])) if s["lead"] else None,
        }
    return out


# --------------------------------------------------------------------------
# Experiment 3 -- operating curves at matched alarm burden
# --------------------------------------------------------------------------

def operating_curves(
    n_beds: int = 8,
    seeds: tuple = tuple(range(1, 13)),
    cadence_h: float = WARD_CADENCE_H,
    history_h: float = 28.0,
    soon_h: float = 12.0,
) -> dict:
    """Sweep each system's threshold and trace sensitivity against alarm rate.

    Comparing a quiet system with a loud one at their default settings says
    nothing; either can be made louder or quieter.  The fair question is which
    catches more deteriorations for the same number of interruptions.

      NEWS2    alarm when score >= k, k = 3..9
      Prahari  alarm when the twin projects cold-wet within T hours
               (or the patient is cold-wet now), T = 0..24
    """
    records = []   # (seed, pid, clock, news2, twin_deadline_h)
    onsets = {}
    for seed in seeds:
        patients = build_ward(n_beds=n_beds, seed=seed, history_h=history_h, cadence_h=cadence_h)
        for wp in patients:
            onsets[(seed, wp.ehr.patient_id)] = _onset(wp)
        for k in range(1, int(history_h // cadence_h) + 1):
            clock = k * cadence_h
            for wp, twin, _entry, _visible in fit_ward_at(patients, clock, horizon_h=soon_h):
                charted = [o for o in wp.observations if abs(o.t - clock) < 1e-6]
                if not charted:
                    continue
                if twin.profile.code == "C":
                    deadline = 0.0
                else:
                    deadline = twin.project(hours=24.0).time_to_critical()
                records.append((seed, wp.ehr.patient_id, clock,
                                news2_from_observation(charted[0]), deadline))

    patient_days = len(records) * cadence_h / 24.0
    events = {key: t for key, t in onsets.items() if t is not None and t >= cadence_h}

    def score(fires) -> dict:
        first, alarms, true = {}, 0, 0
        for rec, on in zip(records, fires):
            if not on:
                continue
            seed, pid, clock = rec[0], rec[1], rec[2]
            alarms += 1
            t_on = onsets[(seed, pid)]
            true += int(t_on is not None and t_on <= clock + soon_h)
            first.setdefault((seed, pid), clock)
        caught, leads = 0, []
        for key, t_on in events.items():
            t_alarm = first.get(key)
            if t_alarm is not None and t_alarm <= t_on + 1e-9:
                caught += 1
                leads.append(t_on - t_alarm)
        return {
            "alarms_per_patient_day": alarms / patient_days,
            "sensitivity": caught / len(events) if events else 0.0,
            "ppv": true / alarms if alarms else None,
            "median_lead_h": float(np.median(leads)) if leads else None,
        }

    news2 = [{"threshold": k, **score([r[3] >= k for r in records])} for k in range(3, 10)]
    twin = [{"threshold_h": T, **score([r[4] is not None and r[4] <= T for r in records])}
            for T in (0, 2, 4, 6, 8, 10, 12, 16, 20, 24)]
    return {"events": len(events), "patient_days": patient_days, "news2": news2, "prahari": twin}


# --------------------------------------------------------------------------
# Experiment 4 -- are the counterfactuals right?
# --------------------------------------------------------------------------

def _truth_continuation(wp, clock: float):
    """The real patient from `clock` onward, as a runnable twin (scoring only)."""
    from dataclasses import replace as _replace
    from .core.twin import PatientTwin as _PT

    tr = wp.truth_trajectory
    i = int(np.argmin(np.abs(tr.t - clock)))
    state = tr.states[i]
    p = wp.progression_per_h
    remaining = max(1e-3, 1.0 - p * clock)
    e0 = state.emax_base * remaining
    twin = _PT(
        "truth",
        _replace(state, emax_base=max(0.18, e0)),
        progression_per_h=p / remaining,
        intake_base=wp.intake_base,
    )
    twin.pgx_beta_blocker = CYP2D6_BETA_BLOCKER.get(wp.ehr.cyp2d6, 1.0)
    return twin


def counterfactual_validation(
    n_beds: int = 8,
    seeds: tuple = tuple(range(1, 13)),
    cadence_h: float = WARD_CADENCE_H,
    history_h: float = 28.0,
    rounds: tuple = (2, 4, 6),
    effect_h: float = 6.0,
    horizon_h: float = 12.0,
) -> dict:
    """Give every drug to the twin and to the real patient; compare outcomes.

    Prediction accuracy says nothing about whether a twin's what-if answers
    are right.  Here the twin (fitted only from the chart) and the ground-truth
    patient are each run under every agent in the formulary from the same
    moment, and we score:

      direction   does the twin get the sign of the effect right?
      magnitude   error in the predicted change in CI and PCWP at effect_h
      decision    regret of the twin's chosen agent, measured on the REAL
                  patient, against the best agent in hindsight -- compared
                  with a quadrant-blind ward reflex (IV furosemide for all)
    """
    from .core.interventions import FORMULARY, Plan

    agents = list(FORMULARY)
    step = int(round(effect_h / 0.25))
    dir_hits = dir_n = 0
    d_ci_err, d_pcwp_err, pred_ci, true_ci = [], [], [], []
    regret_twin, regret_reflex, regret_wait, regret_news2 = [], [], [], []
    top1 = 0
    harm = {"n": 0, "reflex_harmful": 0, "twin_harmful": 0}
    by_q = {q: {"n": 0, "furo_helps": 0, "furo_harms": 0, "twin_right": 0} for q in "ABLC"}

    for seed in seeds:
        patients = build_ward(n_beds=n_beds, seed=seed, history_h=history_h, cadence_h=cadence_h)
        for k in rounds:
            clock = k * cadence_h
            for wp, twin, _entry, _visible in fit_ward_at(patients, clock, horizon_h):
                charted = [o for o in wp.observations if abs(o.t - clock) < 1e-6]
                if not charted:
                    continue
                truth = _truth_continuation(wp, clock)
                true_q = classify(truth_at(wp, clock)).code

                tw_base = twin.project(hours=horizon_h)
                tr_base = truth.project(hours=horizon_h)
                tw_sev, tr_sev = {}, {}
                tw_sev["wait"], tr_sev["wait"] = tw_base.worst_severity(), tr_base.worst_severity()

                for a in agents:
                    tw_run = twin.project(hours=horizon_h, plan=Plan.of(a))
                    tr_run = truth.project(hours=horizon_h, plan=Plan.of(a))
                    tw_sev[a], tr_sev[a] = tw_run.worst_severity(), tr_run.worst_severity()

                    for field, errs in (("cardiac_index", d_ci_err), ("pcwp", d_pcwp_err)):
                        d_tw = getattr(tw_run.hemo[step], field) - getattr(tw_base.hemo[step], field)
                        d_tr = getattr(tr_run.hemo[step], field) - getattr(tr_base.hemo[step], field)
                        errs.append(abs(d_tw - d_tr))
                        if field == "cardiac_index":
                            pred_ci.append(d_tw)
                            true_ci.append(d_tr)
                        if abs(d_tr) > (0.05 if field == "cardiac_index" else 0.5):
                            dir_n += 1
                            dir_hits += int(np.sign(d_tw) == np.sign(d_tr))

                best_true = min(tr_sev, key=tr_sev.get)
                pick = min(tw_sev, key=tw_sev.get)
                top1 += int(pick == best_true)
                regret_twin.append(tr_sev[pick] - tr_sev[best_true])
                regret_reflex.append(tr_sev["furosemide"] - tr_sev[best_true])
                regret_wait.append(tr_sev["wait"] - tr_sev[best_true])
                # What a busy ward actually does: diurese when the score fires.
                fired = news2_from_observation(charted[0]) >= NEWS2_URGENT
                regret_news2.append(tr_sev["furosemide" if fired else "wait"] - tr_sev[best_true])

                q = by_q[true_q]
                q["n"] += 1
                q["furo_helps"] += int(tr_sev["furosemide"] < tr_sev["wait"] - 0.02)
                q["furo_harms"] += int(tr_sev["furosemide"] > tr_sev["wait"] + 0.02)
                q["twin_right"] += int(twin.profile.code == true_q)

                # Where the reflex diuretic does harm compared with waiting.
                if tr_sev["furosemide"] > tr_sev["wait"] + 0.02:
                    harm["n"] += 1
                    harm["reflex_harmful"] += 1
                    harm["twin_harmful"] += int(tr_sev[pick] > tr_sev["wait"] + 0.02)

    n = len(regret_twin)
    return {
        "decisions": n,
        "direction_accuracy": dir_hits / dir_n if dir_n else None,
        "direction_n": dir_n,
        "effect_ci_mae": float(np.mean(d_ci_err)),
        "effect_pcwp_mae": float(np.mean(d_pcwp_err)),
        "effect_ci_corr": float(np.corrcoef(pred_ci, true_ci)[0, 1]),
        "top1_agreement": top1 / n,
        "regret_twin": float(np.mean(regret_twin)),
        "regret_reflex": float(np.mean(regret_reflex)),
        "regret_wait": float(np.mean(regret_wait)),
        "regret_news2_reflex": float(np.mean(regret_news2)),
        "by_true_quadrant": by_q,
        "reflex_harm_cases": harm["n"],
        "twin_harm_in_those": harm["twin_harmful"],
    }
