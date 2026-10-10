"""Cardio-metabolic coupling for diabetic heart-failure patients.

About two in five Indian heart-failure inpatients are also diabetic.  A
high-glycaemic meal drives a post-prandial glucose excursion with sympathetic
activation (heart rate up, which a failing ventricle tolerates poorly), and
its sodium is retained as water over the next hours.  This module turns the
ward timetable into drive terms the cardiac ODE understands.

Glycaemic loads use common Indian hospital-diet portions (rice, roti,
millet).  Excursion height scales with HbA1c.
"""


from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Typical glycaemic loads for common Indian hospital-diet portions.
MEAL_LIBRARY = {
    "rice_heavy": {"label": "Rice, dal, sabzi", "carb_g": 95.0, "sodium_mg": 900.0, "gi": 0.85},
    "roti_moderate": {"label": "Roti, dal, vegetable", "carb_g": 62.0, "sodium_mg": 700.0, "gi": 0.68},
    "millet_low": {"label": "Millet khichdi, low salt", "carb_g": 48.0, "sodium_mg": 320.0, "gi": 0.52},
    "light_snack": {"label": "Tea and biscuit", "carb_g": 22.0, "sodium_mg": 180.0, "gi": 0.75},
}


@dataclass
class ScheduledEvent:
    """One entry on the care timeline."""

    t: float            # hours from now (negative = already happened)
    kind: str           # "meal" | "medication" | "observation"
    label: str
    detail: str = ""
    meal_key: str | None = None
    done: bool = False

    @property
    def is_future(self) -> bool:
        return self.t > 0


def glucose_excursion(t_since_meal: float, carb_g: float, gi: float, hba1c: float | None) -> float:
    """Post-prandial glucose rise above baseline, in mg/dL.

    A gamma-shaped curve: rises over roughly an hour, clears over three to
    four.  Poor glycaemic control (higher HbA1c) means a higher and longer
    peak, which is why the same meal destabilises one patient and not another.
    """
    if t_since_meal <= 0:
        return 0.0
    impairment = 1.0 if hba1c is None else float(np.clip(0.6 + 0.13 * (hba1c - 5.5), 0.6, 2.1))
    peak = 0.55 * carb_g * gi * impairment
    shape = (t_since_meal / 1.0) * np.exp(1.0 - t_since_meal / 1.0)
    return float(max(0.0, peak * shape))


def metabolic_drive(events: list, hba1c: float | None, diabetic: bool) -> dict:
    """Extra drive terms contributed by the feeding schedule.

    Returns multipliers and rates the cardiac ODE already understands, so the
    metabolic layer plugs into the twin without the twin knowing about food.
    """
    if not diabetic:
        return {}

    def at(t: float) -> dict:
        hr_mult, sodium_load = 1.0, 0.0
        for ev in events:
            if ev.kind != "meal" or ev.meal_key is None:
                continue
            meal = MEAL_LIBRARY[ev.meal_key]
            dt = t - ev.t
            if dt <= 0:
                continue
            glucose = glucose_excursion(dt, meal["carb_g"], meal["gi"], hba1c)
            # Sympathetic response scales with the excursion, capped.
            hr_mult *= 1.0 + float(np.clip(glucose / 900.0, 0.0, 0.10))
            # Sodium is retained as water over the following six hours.
            if dt < 6.0:
                sodium_load += meal["sodium_mg"] * 0.030 * np.exp(-dt / 4.0)
        return {"chronotropy": hr_mult, "fluid_rate": sodium_load}

    return {"metabolic_at": at}


def default_timetable(day_hour: float = 20.0, diabetic: bool = False) -> list:
    """The ward timetable a step-down unit actually runs on."""
    events = [
        ScheduledEvent(t=-3.0, kind="meal", label="Dinner", detail="Rice, dal, sabzi",
                       meal_key="rice_heavy", done=True),
        ScheduledEvent(t=-1.0, kind="medication", label="Ramipril 5mg", detail="Evening dose", done=True),
        ScheduledEvent(t=1.5, kind="medication", label="Furosemide 40mg", detail="Night dose due"),
        ScheduledEvent(t=4.0, kind="observation", label="Vitals round", detail="4-hourly obs"),
        ScheduledEvent(t=8.0, kind="observation", label="Vitals round", detail="4-hourly obs"),
        ScheduledEvent(t=11.0, kind="meal", label="Breakfast", detail="Roti, dal, vegetable",
                       meal_key="roti_moderate"),
        ScheduledEvent(t=11.5, kind="medication", label="Carvedilol 3.125mg", detail="Morning dose"),
        ScheduledEvent(t=12.0, kind="observation", label="Consultant round", detail="Daily review"),
    ]
    if diabetic:
        events.insert(
            4,
            ScheduledEvent(t=6.0, kind="observation", label="Capillary glucose",
                           detail="Pre-breakfast check"),
        )
    return sorted(events, key=lambda e: e.t)


def meal_swap_benefit(hba1c: float | None, from_key: str = "rice_heavy",
                      to_key: str = "millet_low") -> dict:
    """What swapping the evening meal would do to the glucose excursion.

    A cheap, non-pharmacological intervention that a ward can actually make,
    and one a dietitian will recognise immediately.
    """
    a, b = MEAL_LIBRARY[from_key], MEAL_LIBRARY[to_key]
    ts = np.linspace(0.05, 5.0, 80)
    peak_a = max(glucose_excursion(t, a["carb_g"], a["gi"], hba1c) for t in ts)
    peak_b = max(glucose_excursion(t, b["carb_g"], b["gi"], hba1c) for t in ts)
    return {
        "from_label": a["label"],
        "to_label": b["label"],
        "peak_from": peak_a,
        "peak_to": peak_b,
        "delta": peak_a - peak_b,
        "sodium_saved_mg": a["sodium_mg"] - b["sodium_mg"],
    }
