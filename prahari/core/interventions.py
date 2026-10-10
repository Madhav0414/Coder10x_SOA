"""Drugs as parameter levers, with pharmacodynamics.

Each agent moves named parameters of the cardiovascular model -- a diuretic
removes volume, an inotrope raises contractility, a vasopressor raises
resistance -- through a drive term the ODE integrates.  Onset, plateau and
washout are modelled per agent, so "give it now" and "give it in four hours"
produce genuinely different trajectories.

Directions follow standard pharmacology; magnitudes are first-order
approximations for a proof of concept.  CYP2D6 phenotype scales the
beta-blocker effect per patient.
"""


from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Intervention:
    key: str
    name: str
    short: str
    onset_h: float           # time constant to reach effect
    duration_h: float        # plateau duration before washout
    washout_h: float         # decay time constant after duration
    effects: dict            # drive-term modifiers at full effect
    rationale: str
    quadrants: tuple         # Stevenson profiles where this is guideline therapy

    def level(self, t: float, t_start: float) -> float:
        """Fraction of full effect at time t, given administration at t_start."""
        if t < t_start:
            return 0.0
        dt = t - t_start
        rise = 1.0 - math.exp(-dt / max(self.onset_h, 1e-3))
        overshoot = max(0.0, dt - self.duration_h)
        decay = math.exp(-overshoot / max(self.washout_h, 1e-3))
        return rise * decay


# --------------------------------------------------------------------------
# The formulary
# --------------------------------------------------------------------------

FUROSEMIDE = Intervention(
    key="furosemide",
    name="IV Furosemide 40mg",
    short="Diuretic",
    onset_h=0.35, duration_h=3.5, washout_h=1.8,
    effects={"diuresis_rate": 115.0},
    rationale="Loop diuretic. Removes volume, drops filling pressure, decongests the lung.",
    quadrants=("B", "C"),
)

NITROGLYCERIN = Intervention(
    key="nitroglycerin",
    name="IV Nitroglycerin",
    short="Vasodilator",
    onset_h=0.12, duration_h=6.0, washout_h=0.4,
    effects={"vasoactivity": 0.78, "preload_shift": 90.0},
    rationale="Venodilator and afterload reducer. Offloads the ventricle fast.",
    quadrants=("B",),
)

DOBUTAMINE = Intervention(
    key="dobutamine",
    name="Dobutamine infusion",
    short="Inotrope",
    onset_h=0.15, duration_h=8.0, washout_h=0.3,
    effects={"inotropy": 1.55, "vasoactivity": 0.92},
    rationale="Beta-1 inotrope. Raises contractility and cardiac output.",
    quadrants=("C", "L"),
)

MILRINONE = Intervention(
    key="milrinone",
    name="Milrinone infusion",
    short="Inodilator",
    onset_h=0.4, duration_h=8.0, washout_h=1.5,
    effects={"inotropy": 1.42, "vasoactivity": 0.74},
    rationale="Inodilator. Raises output while reducing afterload.",
    quadrants=("C",),
)

NORADRENALINE = Intervention(
    key="noradrenaline",
    name="Noradrenaline infusion",
    short="Vasopressor",
    onset_h=0.08, duration_h=8.0, washout_h=0.2,
    effects={"vasoactivity": 1.45, "inotropy": 1.08},
    rationale="Alpha-1 vasopressor. Restores perfusion pressure when MAP fails.",
    quadrants=("C",),
)

FLUID_BOLUS = Intervention(
    key="fluid_bolus",
    name="IV fluid bolus 250mL",
    short="Fluids",
    onset_h=0.15, duration_h=0.8, washout_h=0.3,
    effects={"fluid_rate": 320.0},
    rationale="Volume expansion. Raises preload in the under-filled patient.",
    quadrants=("L",),
)

METOPROLOL = Intervention(
    key="metoprolol",
    name="Metoprolol",
    short="Beta-blocker",
    onset_h=0.8, duration_h=10.0, washout_h=3.0,
    effects={"chronotropy": 0.82, "inotropy": 0.90},
    rationale="Rate control. Lengthens filling time, but lowers contractility.",
    quadrants=("A",),
)

FORMULARY = {
    i.key: i
    for i in (
        FUROSEMIDE, NITROGLYCERIN, DOBUTAMINE, MILRINONE,
        NORADRENALINE, FLUID_BOLUS, METOPROLOL,
    )
}

@dataclass(frozen=True)
class Plan:
    """A set of interventions with administration times, in simulation hours."""

    orders: tuple = field(default_factory=tuple)   # ((key, t_start), ...)

    @staticmethod
    def of(*keys: str, at: float = 0.0) -> "Plan":
        return Plan(orders=tuple((k, at) for k in keys))

    @property
    def is_wait(self) -> bool:
        return len(self.orders) == 0

    def label(self) -> str:
        if self.is_wait:
            return "Watchful waiting"
        parts = []
        for key, t in self.orders:
            name = FORMULARY[key].short
            parts.append(name if t <= 0.01 else f"{name} @ +{t:.0f}h")
        return " + ".join(parts)


# CYP2D6 phenotype -> relative metoprolol effect.  Poor metabolisers reach
# several-fold higher plasma levels; ultrarapid metabolisers clear it before
# it does much.  Factors are deliberately conservative relative to the
# published exposure ratios.
CYP2D6_BETA_BLOCKER = {"poor": 1.8, "intermediate": 1.3, "normal": 1.0, "ultrarapid": 0.6}


def build_drive(plan: Plan, baseline, agent_scale: dict | None = None):
    """Turn a treatment plan into the drive function the ODE integrates.

    `baseline` is a dict or a callable of time (the callable form carries
    disease progression).  `agent_scale` scales an agent's effect size per
    patient -- used for pharmacogenomics.  Multiplicative effects compose,
    additive rates sum.
    """
    mult_keys = ("inotropy", "vasoactivity", "chronotropy")
    add_keys = ("diuresis_rate", "fluid_rate", "preload_shift")
    baseline_fn = baseline if callable(baseline) else (lambda _t: baseline)
    agent_scale = agent_scale or {}

    def drive(t: float) -> dict:
        out = dict(baseline_fn(t))
        for key in mult_keys:
            out[key] = 1.0
        for key in add_keys:
            out[key] = 0.0

        for key, t_start in plan.orders:
            agent = FORMULARY[key]
            lvl = agent.level(t, t_start) * agent_scale.get(key, 1.0)
            if lvl <= 1e-6:
                continue
            for name, value in agent.effects.items():
                if name in mult_keys:
                    out[name] *= 1.0 + (value - 1.0) * lvl
                else:
                    out[name] += value * lvl
        return out

    return drive


def guideline_options(profile_code: str) -> list:
    """Interventions that are guideline therapy for a given Stevenson quadrant."""
    return [i for i in FORMULARY.values() if profile_code in i.quadrants]
