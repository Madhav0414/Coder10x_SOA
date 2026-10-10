"""Nohria-Stevenson haemodynamic profiles.

The bedside classification of acute heart failure: congested or not (wet /
dry) by perfusion adequate or not (warm / cold).  Its two axes are two
quantities the twin estimates -- filling pressure and cardiac index -- so the
twin reports in the clinician's own frame.  Guideline therapy per profile is
standard practice and is what the counterfactual engine is allowed to offer.

Thresholds: wet if PCWP > 18 mmHg, cold if cardiac index < 2.2 L/min/m2.
"""


from __future__ import annotations

from dataclasses import dataclass

from .cardiac import CI_COLD_THRESHOLD, PCWP_WET_THRESHOLD, Hemodynamics


@dataclass(frozen=True)
class Profile:
    code: str
    label: str
    warm: bool
    wet: bool
    description: str
    therapy: str
    mortality_rank: int   # 1 = best outcome, 4 = worst

    @property
    def is_critical(self) -> bool:
        return self.code == "C"


PROFILE_A = Profile(
    code="A",
    label="Warm & Dry",
    warm=True,
    wet=False,
    description="Compensated. Adequate perfusion, no congestion.",
    therapy="Optimise oral guideline-directed medical therapy. Plan discharge.",
    mortality_rank=1,
)

PROFILE_B = Profile(
    code="B",
    label="Warm & Wet",
    warm=True,
    wet=True,
    description="Congested but perfusing. The most common presentation.",
    therapy="IV loop diuretic +/- vasodilator. Decongest.",
    mortality_rank=2,
)

PROFILE_L = Profile(
    code="L",
    label="Cold & Dry",
    warm=False,
    wet=False,
    description="Low output without congestion. Often under-filled.",
    therapy="Cautious fluid challenge. Review vasodilators and diuretic dose.",
    mortality_rank=3,
)

PROFILE_C = Profile(
    code="C",
    label="Cold & Wet",
    warm=False,
    wet=True,
    description="Congested AND hypoperfused. The worst quadrant.",
    therapy="Inotrope plus diuretic. Consider critical care escalation.",
    mortality_rank=4,
)

ALL_PROFILES = [PROFILE_A, PROFILE_B, PROFILE_L, PROFILE_C]


def classify(hemo: Hemodynamics) -> Profile:
    """Place a patient in the Stevenson quadrant from their haemodynamics."""
    wet = hemo.pcwp > PCWP_WET_THRESHOLD
    cold = hemo.cardiac_index < CI_COLD_THRESHOLD

    if not cold and not wet:
        return PROFILE_A
    if not cold and wet:
        return PROFILE_B
    if cold and not wet:
        return PROFILE_L
    return PROFILE_C


def margin_to_cold(hemo: Hemodynamics) -> float:
    """How much cardiac index headroom remains before hypoperfusion."""
    return hemo.cardiac_index - CI_COLD_THRESHOLD


def margin_to_wet(hemo: Hemodynamics) -> float:
    """How much filling-pressure headroom remains before congestion."""
    return PCWP_WET_THRESHOLD - hemo.pcwp


def severity_score(hemo: Hemodynamics) -> float:
    """Continuous 0-1 severity, so we can rank patients inside a quadrant.

    Quadrant alone is too coarse for triage: two cold-wet patients are not
    equally urgent.  This blends how far past each threshold they are.
    """
    cold_excess = max(0.0, CI_COLD_THRESHOLD - hemo.cardiac_index) / CI_COLD_THRESHOLD
    wet_excess = max(0.0, hemo.pcwp - PCWP_WET_THRESHOLD) / 20.0
    hypox = max(0.0, 94.0 - hemo.spo2) / 20.0
    hypot = max(0.0, 70.0 - hemo.map) / 30.0
    raw = 0.40 * cold_excess + 0.30 * wet_excess + 0.15 * hypox + 0.15 * hypot
    return float(min(1.0, raw))
