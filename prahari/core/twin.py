"""PatientTwin: state, forward simulation, counterfactuals, confidence.

A twin here means four things, each a method or property below:

  state         physiological parameters fitted to one patient (params)
  forward       a trajectory under a given plan (project)
  intervention  the same patient under a different plan (counterfactuals,
                wait_versus_act)
  sync          refitted at each observation, and carried forward between
                them (advanced), with confidence decaying as it extrapolates
"""


from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from .cardiac import (
    CI_COLD_THRESHOLD,
    PCWP_WET_THRESHOLD,
    CardiacParams,
    Hemodynamics,
    simulate,
    solve_hemodynamics,
)
from .interventions import FORMULARY, Plan, build_drive, guideline_options
from .stevenson import Profile, classify, severity_score

# How far past its fitted region the twin is willing to speak with confidence.
CONFIDENCE_FLOOR = 0.25


@dataclass
class Trajectory:
    """A single simulated future under one decision."""

    plan: Plan
    t: np.ndarray
    states: list
    hemo: list

    @property
    def label(self) -> str:
        return self.plan.label()

    def series(self, field_name: str) -> np.ndarray:
        return np.array([getattr(h, field_name) for h in self.hemo], dtype=float)

    def time_to_critical(self, persist_h: float = 1.0) -> float | None:
        """Hours until the patient enters and STAYS IN the cold-wet quadrant.

        Cold-wet carries the worst outcomes of the four profiles, so this is
        the deadline the ward actually cares about.  Persistence matters: a
        brief dip across the threshold during decongestion is not a
        decompensation event, and counting it would make the twin recommend
        against treatments that work.
        """
        critical = np.array(
            [
                (h.cardiac_index < CI_COLD_THRESHOLD) and (h.pcwp > PCWP_WET_THRESHOLD)
                for h in self.hemo
            ]
        )
        if not critical.any():
            return None

        dt = float(self.t[1] - self.t[0]) if len(self.t) > 1 else 0.25
        need = max(1, int(round(persist_h / dt)))

        run = 0
        for i, flag in enumerate(critical):
            run = run + 1 if flag else 0
            if run >= need:
                return float(self.t[i - need + 1])
        return None

    def worst_severity(self) -> float:
        return float(max(severity_score(h) for h in self.hemo))

    def endpoint(self) -> Hemodynamics:
        return self.hemo[-1]


@dataclass
class Observation:
    """What the ward actually recorded, at the cadence it actually recorded it."""

    t: float              # hours since admission
    hr: float
    sbp: float
    dbp: float
    spo2: float
    resp_rate: float
    # Congestion signs from the ward round.  Coarse, subjective, recorded once
    # a shift -- and still the only way to see filling pressure at the bedside.
    jvp_cm: float = 8.0
    edema_grade: float = 0.0
    weight_excess_kg: float = 0.0
    crt_s: float = 2.0

    @property
    def map(self) -> float:
        return self.dbp + (self.sbp - self.dbp) / 3.0

    @property
    def pulse_pressure(self) -> float:
        return self.sbp - self.dbp


@dataclass
class PatientTwin:
    """A runnable copy of one patient."""

    patient_id: str
    params: CardiacParams
    observations: list = field(default_factory=list)
    fit_residual: float = 0.0
    fit_n_obs: int = 0
    # Ongoing insult: fractional loss of contractility per hour.  A patient
    # with active ischaemia keeps getting worse whatever we do about volume,
    # and that is exactly the patient a ward misses overnight.
    progression_per_h: float = 0.0
    # Extra maintenance intake (mL/h) above the 55 mL/h default -- the fluid a
    # congested inpatient is still receiving while nobody is watching.
    intake_base: float = 55.0
    # Pharmacogenomic scaling of beta-blocker effect (CYP2D6 phenotype).
    pgx_beta_blocker: float = 1.0
    # Hours the twin has been propagated forward since its last observation.
    stale_h: float = 0.0

    # ---------------------------------------------------------------- state
    @property
    def hemodynamics(self) -> Hemodynamics:
        return solve_hemodynamics(self.params)

    @property
    def profile(self) -> Profile:
        return classify(self.hemodynamics)

    @property
    def severity(self) -> float:
        return severity_score(self.hemodynamics)

    # ------------------------------------------------------- forward simulate
    def _baseline_drive(self):
        emax0 = self.params.emax_base
        decay = self.progression_per_h
        svr0, hr0, intake = self.params.svr_base, self.params.hr_base, self.intake_base

        def baseline(t: float) -> dict:
            return {
                "emax_base": max(0.18, emax0 * (1.0 - decay * t)),
                "svr_base": svr0,
                "hr_base": hr0,
                "intake_base": intake,
            }

        return baseline

    def project(self, hours: float = 12.0, plan: Plan | None = None, dt: float = 0.25) -> Trajectory:
        """Roll this patient forward under a decision (default: do nothing)."""
        plan = plan or Plan()
        drive = build_drive(plan, self._baseline_drive(),
                            agent_scale={"metoprolol": self.pgx_beta_blocker})
        t, states, hemo = simulate(self.params, hours, drive=drive, dt=dt)
        return Trajectory(plan=plan, t=t, states=states, hemo=hemo)

    def advanced(self, hours: float) -> "PatientTwin":
        """The same twin carried forward `hours` with no intervention.

        Between charted observations the twin keeps simulating; what-ifs must
        branch from the patient as they are now, not as they were at the last
        reading.  Progression continues at the same absolute rate.
        """
        if hours <= 1e-6:
            return self
        state = self.project(hours=hours).states[-1]
        remaining = max(1e-3, 1.0 - self.progression_per_h * hours)
        return PatientTwin(
            patient_id=self.patient_id,
            params=replace(state, emax_base=max(0.18, self.params.emax_base * remaining)),
            observations=self.observations,
            fit_residual=self.fit_residual,
            fit_n_obs=self.fit_n_obs,
            progression_per_h=self.progression_per_h / remaining,
            intake_base=self.intake_base,
            pgx_beta_blocker=self.pgx_beta_blocker,
            stale_h=self.stale_h + hours,
        )

    # --------------------------------------------------------- counterfactual
    def counterfactuals(self, hours: float = 12.0, extra: Sequence[Plan] = ()) -> list:
        """Simulate the realistic decision set for this patient right now.

        The option list is drawn from guideline therapy for the patient's
        current Stevenson quadrant, so we are not offering a cardiologist a
        menu of physiologically silly choices.
        """
        plans = [Plan()]  # watchful waiting is always on the menu
        for agent in guideline_options(self.profile.code):
            plans.append(Plan.of(agent.key))
        plans.extend(extra)

        seen, unique = set(), []
        for p in plans:
            sig = tuple(sorted(p.orders))
            if sig not in seen:
                seen.add(sig)
                unique.append(p)

        return [self.project(hours=hours, plan=p) for p in unique]

    def wait_versus_act(self, agent_key: str, delay_h: float = 6.0, hours: float = 14.0) -> dict:
        """The question a doctor leaving a shift actually asks.

        Compares giving a drug now against giving the same drug after a delay.
        This is what converts a risk score into a decision with a deadline.
        """
        now = self.project(hours=hours, plan=Plan.of(agent_key, at=0.0))
        later = self.project(hours=hours, plan=Plan.of(agent_key, at=delay_h))
        nothing = self.project(hours=hours)

        return {
            "agent": FORMULARY[agent_key].name,
            "delay_h": delay_h,
            "act_now": now,
            "act_later": later,
            "do_nothing": nothing,
            "critical_if_nothing": nothing.time_to_critical(),
            "critical_if_delayed": later.time_to_critical(),
            "critical_if_now": now.time_to_critical(),
            "severity_now": now.worst_severity(),
            "severity_later": later.worst_severity(),
            "severity_nothing": nothing.worst_severity(),
        }

    # ------------------------------------------------------------- confidence
    @property
    def confidence(self) -> float:
        """How much the twin trusts itself for this patient.

        This exists because of the Epic sepsis failure: a model that cannot
        say "I do not know" gets trusted uniformly and then betrays that trust
        uniformly.  Confidence falls when observations are few, stale, or
        poorly explained by the fitted parameters.
        """
        if self.fit_n_obs == 0:
            return CONFIDENCE_FLOOR

        obs_term = min(1.0, self.fit_n_obs / 8.0)
        resid_term = float(np.exp(-self.fit_residual / 9.0))

        # Every hour since the last reading is an hour of pure extrapolation.
        stale_term = float(np.exp(-self.stale_h / 12.0))

        return float(np.clip(0.55 * resid_term + 0.30 * obs_term + 0.15 * stale_term,
                             CONFIDENCE_FLOOR, 0.98))

    @property
    def confidence_band(self) -> str:
        c = self.confidence
        if c >= 0.75:
            return "high"
        if c >= 0.5:
            return "moderate"
        return "low"

    # ---------------------------------------------------------- explanation
    def explain(self) -> list:
        """Why this patient looks the way they do, in clinical language.

        Falls out of the physics for free: because the state variables are
        named physiological quantities rather than anonymous features, the
        explanation is just a reading of the state.
        """
        h = self.hemodynamics
        notes = []

        if h.cardiac_index < CI_COLD_THRESHOLD:
            notes.append(
                f"Cardiac index {h.cardiac_index:.2f} L/min/m2 is below the 2.2 "
                f"hypoperfusion threshold - contractility is {self.params.emax:.2f} "
                f"against a baseline of {self.params.emax_base:.2f}."
            )
        if h.pcwp > PCWP_WET_THRESHOLD:
            notes.append(
                f"Filling pressure {h.pcwp:.1f} mmHg exceeds 18 - the patient is "
                f"congested, with {self.params.v_interstitial:.0f} mL of accumulated lung water."
            )
        if h.pulse_pressure < 30 and self.params.hr > self.params.hr_base + 8:
            notes.append(
                f"Narrow pulse pressure ({h.pulse_pressure:.0f} mmHg) with heart rate "
                f"{self.params.hr:.0f} above baseline {self.params.hr_base:.0f} - this is "
                f"compensation, and it is the pattern that precedes decompensation."
            )
        if h.spo2 < 94:
            notes.append(f"Saturation {h.spo2:.0f}% reflects pulmonary congestion.")
        if h.map < 70:
            notes.append(f"Mean arterial pressure {h.map:.0f} mmHg - perfusion pressure is failing.")
        if not notes:
            notes.append("Haemodynamics are within compensated range on all four axes.")
        return notes
