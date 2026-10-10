"""After discharge: the same twin, followed on a consumer band.

The twin is calibrated in the ward, where the chart identifies its
physiology.  At home only a cheap wrist band is available -- resting heart
rate, HRV, steps, sleep -- plus a bathroom scale.  Those cannot identify the
physiology, but they can show drift away from the discharge state, which is
what readmission risk tracks here.

Context: in the Trivandrum Heart Failure Registry about one in three patients
was readmitted within a year and 30.8% died, with the steepest risk in the
first three months.  The home streams here are simulated.
"""


from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .twin import PatientTwin


@dataclass
class WearableDay:
    """One day of consumer-band data after discharge."""

    day: int
    resting_hr: float       # bpm, overnight minimum
    hrv_ms: float           # rMSSD proxy
    steps: int
    sleep_hours: float
    night_hr_rise: float    # bpm above the discharge baseline
    weight_kg: float | None = None
    awake_min: float = float("nan")   # time in bed not asleep: restless or awake


@dataclass
class ReadmissionRisk:
    day: int
    risk: float             # 0-1
    drivers: list
    band: str


def simulate_home_trajectory(
    twin: PatientTwin,
    days: int = 30,
    decompensating: bool = False,
    seed: int = 5,
) -> list:
    """Generate the wearable stream a discharged patient would produce.

    A stable patient recovers slowly: resting heart rate falls, activity and
    sleep improve.  A decompensating patient does the opposite, and does it
    *before* they feel unwell enough to come back -- which is the entire
    window this is trying to open.
    """
    rng = np.random.default_rng(seed)
    base_hr = twin.params.hr_base
    out = []

    for d in range(1, days + 1):
        if decompensating:
            # Congestion builds silently: heart rate creeps up, activity falls.
            drift = (d / days) ** 1.7
            rhr = base_hr + 16.0 * drift
            hrv = 28.0 - 11.0 * drift
            steps = int(max(300, 3800 - 2600 * drift))
            sleep = 7.0 - 2.4 * drift
            night_rise = 3.0 + 11.0 * drift
            weight = 68.0 + 4.5 * drift
        else:
            recov = (d / days) ** 0.7
            rhr = base_hr - 8.0 * recov
            hrv = 28.0 + 9.0 * recov
            steps = int(2600 + 3400 * recov)
            sleep = 6.6 + 0.9 * recov
            night_rise = max(0.0, 3.0 - 2.4 * recov)
            weight = 68.0 - 0.6 * recov

        out.append(
            WearableDay(
                day=d,
                resting_hr=float(rhr + rng.normal(0, 1.8)),
                hrv_ms=float(max(4.0, hrv + rng.normal(0, 2.2))),
                steps=int(max(0, steps + rng.normal(0, 380))),
                sleep_hours=float(np.clip(sleep + rng.normal(0, 0.45), 2.5, 10.0)),
                night_hr_rise=float(max(0.0, night_rise + rng.normal(0, 1.1))),
                weight_kg=float(weight + rng.normal(0, 0.25)),
            )
        )
    return out


BASELINE_DAYS = 7     # the first week home defines "normal" for this patient
RECENT_DAYS = 3       # compare against a short recent average, not one day


def _mean(values) -> float:
    v = [x for x in values if x is not None and x == x]
    return float(np.mean(v)) if v else float("nan")


def _steps(d) -> float:
    return float(d.steps) if d.steps is not None and d.steps >= 0 else float("nan")


def readmission_risk(twin: PatientTwin, history: list) -> ReadmissionRisk:
    """Score drift away from this patient's own first week at home.

    The recent average (last 3 days) is compared with the baseline average
    (first 7 days).  Single-day comparisons false-alarm constantly on real
    wear data -- step counts alone swing by half from one day to the next --
    so both sides are averaged.  A channel with no data contributes nothing.

    Four named drivers, each something the patient could be asked about on
    a phone call: resting heart rate, weight, activity, sleep.
    """
    if len(history) <= BASELINE_DAYS:
        return ReadmissionRisk(day=history[-1].day if history else 0, risk=0.0,
                               drivers=["Still learning this patient's baseline (first week home)."],
                               band="low")

    base = history[:BASELINE_DAYS]
    recent = history[-RECENT_DAYS:]
    drivers = []

    hr_rise = _mean(d.resting_hr for d in recent) - _mean(d.resting_hr for d in base)
    hr_term = float(np.clip(hr_rise / 12.0, 0.0, 1.0)) if hr_rise == hr_rise else 0.0
    if hr_rise == hr_rise and hr_rise > 4:
        drivers.append(f"Resting heart rate {hr_rise:.0f} bpm above the first week home")

    gain = _mean(d.weight_kg for d in recent) - _mean(d.weight_kg for d in base)
    wt_term = float(np.clip(gain / 3.0, 0.0, 1.0)) if gain == gain else 0.0
    if gain == gain and gain > 1.5:
        drivers.append(f"Weight up {gain:.1f} kg - likely fluid, not tissue")

    b_steps, r_steps = _mean(_steps(d) for d in base), _mean(_steps(d) for d in recent)
    act_drop = (b_steps - r_steps) / b_steps if b_steps == b_steps and r_steps == r_steps and b_steps > 0 else float("nan")
    act_term = float(np.clip(act_drop / 0.5, 0.0, 1.0)) if act_drop == act_drop else 0.0
    if act_drop == act_drop and act_drop > 0.25:
        drivers.append(f"Daily steps down {act_drop * 100:.0f}%")

    sleep_loss = _mean(d.sleep_hours for d in base) - _mean(d.sleep_hours for d in recent)
    sleep_term = float(np.clip(sleep_loss / 2.0, 0.0, 1.0)) if sleep_loss == sleep_loss else 0.0
    if sleep_loss == sleep_loss and sleep_loss > 1.0:
        drivers.append(f"Sleeping {sleep_loss:.1f} h less - breathlessness lying flat is often "
                       f"the first symptom patients ignore")

    risk = float(np.clip(0.34 * hr_term + 0.30 * wt_term + 0.22 * act_term + 0.14 * sleep_term, 0.0, 1.0))
    # A frail ventricle has less reserve to absorb the same drift.
    risk = float(np.clip(risk * (1.0 + 0.30 * max(0.0, 1.0 - twin.params.emax)), 0.0, 1.0))

    band = "high" if risk >= 0.55 else ("moderate" if risk >= 0.30 else "low")
    if not drivers:
        drivers.append("No meaningful drift from the first week home.")
    return ReadmissionRisk(day=history[-1].day, risk=risk, drivers=drivers, band=band)


def inject_decompensation(history: list, onset_day: int, span_days: float = 14.0,
                          hr_rise: float = 12.0, step_drop: float = 0.45,
                          sleep_drop: float = 1.5, weight_gain: float = 3.0) -> list:
    """Overlay a simulated heart-failure decompensation on real wear data.

    From `onset_day` the drift builds over `span_days`: resting heart rate up,
    activity and sleep down, weight up (only where weight exists).  The real
    day-to-day noise underneath is untouched, which is the point: it tests
    whether the signal is detectable through real-world variability.
    """
    out = []
    for d in history:
        f = float(np.clip((d.day - onset_day) / span_days, 0.0, 1.0)) ** 1.5
        out.append(WearableDay(
            day=d.day,
            resting_hr=d.resting_hr + hr_rise * f if d.resting_hr == d.resting_hr else d.resting_hr,
            hrv_ms=d.hrv_ms,
            steps=int(d.steps * (1.0 - step_drop * f)) if d.steps >= 0 else d.steps,
            sleep_hours=d.sleep_hours - sleep_drop * f if d.sleep_hours == d.sleep_hours else d.sleep_hours,
            night_hr_rise=d.night_hr_rise,
            weight_kg=(d.weight_kg + weight_gain * f) if d.weight_kg is not None and d.weight_kg == d.weight_kg else d.weight_kg,
            awake_min=d.awake_min,
        ))
    return out
