"""Real-data plumbing: missing channels, MIMIC-IV loading, Apple Health I/O.

Tests needing the downloaded datasets skip if `scripts/fetch_data.py` has
not been run.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from prahari.core.cardiac import CardiacParams
from prahari.core.continuity import WearableDay, inject_decompensation, readmission_risk
from prahari.core.twin import PatientTwin
from prahari.data.ward import build_ward
from prahari.models.estimator import fit_twin, shared_inverse

DATA = Path(__file__).resolve().parents[1] / "data"
HAS_MIMIC = (DATA / "mimic-iv-demo" / "icu" / "chartevents.csv.gz").exists()
HAS_FITBIT = (DATA / "fitbit" / "dailyActivity_merged.csv").exists()


# --------------------------------------------------------------------------
# Missing channels
# --------------------------------------------------------------------------

def test_twin_fits_a_chart_with_gaps():
    """Oxygen masks SpO2, ventilators set the breathing rate, JVP and weight go
    uncharted -- the fit must use what is there and ignore what is not."""
    wp = build_ward(n_beds=3, seed=5, history_h=16.0)[2]
    gappy = []
    for o in wp.observations:
        o = type(o)(**{**o.__dict__})
        o.spo2 = o.resp_rate = o.jvp_cm = o.weight_excess_kg = float("nan")
        gappy.append(o)
    tpl = CardiacParams(bsa=wp.ehr.bsa, c_diastolic=14, edv_max=295)
    tw = fit_twin("gappy", gappy, template=tpl, inverse=shared_inverse())
    h = tw.hemodynamics
    assert all(math.isfinite(v) for v in (h.cardiac_index, h.pcwp, h.map, tw.confidence))


def test_missing_readings_contribute_exactly_nothing():
    """A missing reading is no information -- not a reading of zero."""
    from prahari.models.estimator import _observed, _residuals
    o = build_ward(n_beds=1, seed=2, history_h=4.0)[0].observations[-1]
    o = type(o)(**{**o.__dict__})
    o.spo2 = o.jvp_cm = float("nan")
    r = _residuals(np.array([1.0, 18.0, 500.0, o.hr, 100.0]), CardiacParams(), _observed(o))
    assert r[3] == 0.0 and r[5] == 0.0          # spo2 and jvp slots
    assert np.isfinite(r).all()
    assert np.count_nonzero(r) >= 5             # the charted channels still count


# --------------------------------------------------------------------------
# MIMIC-IV demo
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_MIMIC, reason="MIMIC-IV demo not downloaded")
def test_mimic_heart_failure_stays_load():
    from prahari.data.mimic import load_hf_stays
    stays = load_hf_stays()
    assert len(stays) == 32
    rounds = [o for s in stays for o in s.chart(4.0)]
    assert len(rounds) > 500
    assert all(o.sbp > o.dbp for o in rounds)


@pytest.mark.skipif(not HAS_MIMIC, reason="MIMIC-IV demo not downloaded")
def test_held_out_measurements_never_reach_the_twin():
    """CVP is the answer key for the congestion axis; JVP must stay missing."""
    from prahari.data.mimic import load_hf_stays
    for s in load_hf_stays():
        for o in s.chart(4.0):
            assert math.isnan(o.jvp_cm)
            assert math.isnan(o.weight_excess_kg)


@pytest.mark.skipif(not HAS_MIMIC, reason="MIMIC-IV demo not downloaded")
def test_therapy_controlled_channels_are_masked():
    """SpO2 on oxygen and breathing rate on a ventilator are set by the machine."""
    from prahari.data.mimic import load_hf_stays
    for s in load_hf_stays():
        for o in s.chart(4.0):
            oxygen, vented = s._on_oxygen(o.t)
            if oxygen:
                assert math.isnan(o.spo2)
            if vented:
                assert math.isnan(o.resp_rate)


# --------------------------------------------------------------------------
# Apple Health and the home detector
# --------------------------------------------------------------------------

def _days(n=24, seed=1, weight=True):
    rng = np.random.default_rng(seed)
    return [WearableDay(day=i + 1, resting_hr=float(62 + rng.normal(0, 2)), hrv_ms=float("nan"),
                        steps=int(7000 + rng.normal(0, 1800)), sleep_hours=float(7 + rng.normal(0, 0.5)),
                        night_hr_rise=float("nan"),
                        weight_kg=float(70 + rng.normal(0, 0.3)) if weight else float("nan"))
            for i in range(n)]


def test_apple_health_export_round_trips(tmp_path):
    import datetime as dt
    from prahari.data.wearables import read_apple_health, write_apple_health
    days = _days()
    path = write_apple_health(days, tmp_path / "export.xml", dt.date(2026, 10, 1))
    start, back = read_apple_health(path)
    assert start == dt.date(2026, 10, 1)
    assert len(back) == len(days)
    for a, b in zip(days, back):
        assert abs(a.resting_hr - b.resting_hr) <= 0.5
        assert a.steps == b.steps
        assert abs(a.sleep_hours - b.sleep_hours) <= 1 / 3600   # export resolution: 1 s
        assert abs(a.weight_kg - b.weight_kg) <= 0.05


def test_apple_health_carries_awake_time_and_hrv(tmp_path):
    import datetime as dt
    from dataclasses import replace
    from prahari.data.wearables import read_apple_health, write_apple_health
    days = [replace(d, awake_min=20.0 + d.day, hrv_ms=30.0 + d.day, sleep_hours=8.5)
            for d in _days()]
    _, back = read_apple_health(write_apple_health(days, tmp_path / "e.xml", dt.date(2026, 10, 1)))
    assert len(back) == len(days)
    for a, b in zip(days, back):
        assert abs(a.awake_min - b.awake_min) < 0.1      # filed under the right night
        assert abs(a.hrv_ms - b.hrv_ms) < 0.1


def test_detector_waits_for_a_baseline_week():
    ref = PatientTwin("r", CardiacParams(emax=1.0, emax_base=1.0))
    assert readmission_risk(ref, _days(5)).band == "low"


def test_injected_decompensation_is_detected_and_raw_noise_is_not():
    ref = PatientTwin("r", CardiacParams(emax=1.0, emax_base=1.0))
    days = _days(30)
    assert readmission_risk(ref, days).band == "low"
    sick = inject_decompensation(days, onset_day=12)
    assert readmission_risk(ref, sick).risk > readmission_risk(ref, days).risk + 0.3


@pytest.mark.skipif(not HAS_FITBIT, reason="Fitbit data not downloaded")
def test_fitbit_users_load_with_real_resting_heart_rates():
    from prahari.data.wearables import load_fitbit_users
    users = load_fitbit_users()
    assert len(users) >= 10
    hrs = [d.resting_hr for u in users.values() for d in u["days"] if d.resting_hr == d.resting_hr]
    assert 40 < np.median(hrs) < 90
