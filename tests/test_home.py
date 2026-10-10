"""The twin at home: the ward-fitted heart, read through a band and a scale."""

import numpy as np

from prahari.core.cardiac import CardiacParams
from prahari.core.home import BASELINE_DAYS, HomeTwin, simulate_home

STRONG = CardiacParams(emax=1.3, emax_base=1.3)
WEAK = CardiacParams(emax=0.55, emax_base=0.55)


def test_weak_heart_goes_home_fuller_and_tips_over_sooner():
    strong, weak = HomeTwin(STRONG), HomeTwin(WEAK)
    pcwp = lambda ht: ht.discharge.v_central / ht.discharge.c_venous
    assert pcwp(weak) > pcwp(strong)
    assert weak.curve.event_E < strong.curve.event_E


def test_retained_fluid_raises_filling_pressure_weight_and_breathlessness():
    c = HomeTwin(STRONG).curve
    E = np.array([0.0, 1000.0, 2000.0, 3000.0])
    assert np.all(np.diff(c.at("pcwp", E)) > 0)
    assert np.allclose(c.expect("weight", E), E / 1000.0)
    assert c.expect("awake", 3000.0) > c.expect("awake", 0.0)
    assert c.expect("steps", 3000.0) < 0.0          # activity falls
    assert c.expect("hrv", 3000.0) < 0.0            # HRV falls


def _course(rate, seed=3, scale=True):
    ht = HomeTwin(WEAK)
    days, E = simulate_home(ht.curve, 30, onset=10, rate=rate,
                            rng=np.random.default_rng(seed), scale=scale)
    return ht, days, E


def test_calls_a_decompensation_before_it_happens():
    ht, days, E = _course(250.0)
    event = int(np.argmax(E >= ht.curve.event_E)) + 1
    first = next(k for k, p in ht.history(days) if p >= 0.5)
    assert 10 < first < event


def test_stays_quiet_on_a_stable_patient():
    ht, days, _ = _course(0.0)
    assert max(p for _, p in ht.history(days)) < 0.5


def test_first_week_is_only_a_baseline():
    ht, days, _ = _course(400.0)
    assert ht.estimate(days[:BASELINE_DAYS]).p_event == 0.0


def test_a_bigger_diuretic_buys_more_time():
    ht, days, _ = _course(250.0)
    plans = ht.plans(days[:18])
    assert (plans["diuretic"]["days_to_event"] > plans["restrict"]["days_to_event"]
            > plans["no_change"]["days_to_event"])


def test_band_alone_still_sees_it_without_a_scale():
    ht, days, E = _course(300.0, scale=False)
    event = int(np.argmax(E >= ht.curve.event_E)) + 1
    est = ht.estimate(days[:event - 1])
    assert est.p_event > 0.5
    assert not any(d.startswith("Weight") for d in est.drivers)
