"""Ward triage: who to see next, and when to interrupt someone.

Each patient's twin is run forward under watchful waiting and under the
guideline options for where they are and where they are heading.  From that:

  deadline   hours until the patient enters and stays in cold-wet
  best       the single agent that most reduces projected severity
  urgency    blend of how sick they are now, how soon they cross, and how
             much the best agent helps -- used only for ordering the ward

An alert is raised when the twin is trusted and the patient is already
cold-wet, will cross within ESCALATE_WITHIN_H hours (drug or no drug -- if
nothing on the ward formulary helps, the action is escalation), or is
heading for cold-wet and a ward agent changes that.

This layer is designed to sit behind an early-warning score such as NEWS2,
not to replace it.  See docs/results.md: at matched alarm burden NEWS2
detects deterioration at least as well; what the twin adds is the profile,
the options and the cost of waiting.
"""


from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .interventions import Plan, guideline_options
from .stevenson import severity_score
from .twin import PatientTwin, Trajectory

# A treatment has to move the needle by this much to be worth waking someone.
MEANINGFUL_SEVERITY_DELTA = 0.05
# If the twin does not trust itself this far, we say so rather than alerting.
CONFIDENCE_ALERT_FLOOR = 0.45
# A projected cold-wet crossing this close always alerts, drug or no drug.
ESCALATE_WITHIN_H = 6.0


@dataclass
class BestAction:
    agent_key: str
    agent_name: str
    severity_without: float
    severity_with: float
    critical_without: float | None
    critical_with: float | None
    trajectory: Trajectory

    @property
    def benefit(self) -> float:
        return self.severity_without - self.severity_with

    @property
    def hours_bought(self) -> float | None:
        if self.critical_without is None:
            return None
        if self.critical_with is None:
            return 99.0     # the action prevents the crossing entirely
        return self.critical_with - self.critical_without


@dataclass
class WardEntry:
    twin: PatientTwin
    ehr: object
    do_nothing: Trajectory
    best: BestAction | None
    urgency: float
    alert: bool
    reason: str
    deadline_h: float | None

    @property
    def bed(self) -> str:
        return getattr(self.ehr, "bed", self.twin.patient_id)

    @property
    def profile_code(self) -> str:
        return self.twin.profile.code


def evaluate_patient(twin: PatientTwin, ehr, horizon_h: float = 12.0) -> WardEntry:
    """Simulate this patient's realistic decision set and score the urgency."""
    nothing = twin.project(hours=horizon_h)
    sev_nothing = nothing.worst_severity()
    crit_nothing = nothing.time_to_critical()

    # Options: guideline therapy for where the patient is now, plus where
    # they are heading.  A warm-wet patient sliding toward cold-wet needs the
    # cold-wet menu (an inotrope) considered, not only diuretics.
    options = {a.key: a for a in guideline_options(twin.profile.code)}
    if crit_nothing is not None:
        options.update({a.key: a for a in guideline_options("C")})

    best: BestAction | None = None
    for agent in options.values():
        tr = twin.project(hours=horizon_h, plan=Plan.of(agent.key))
        cand = BestAction(
            agent_key=agent.key,
            agent_name=agent.name,
            severity_without=sev_nothing,
            severity_with=tr.worst_severity(),
            critical_without=crit_nothing,
            critical_with=tr.time_to_critical(),
            trajectory=tr,
        )
        if best is None or cand.benefit > best.benefit:
            best = cand

    # --- urgency ---------------------------------------------------------
    # Three things make a patient urgent: they are sick now, they are heading
    # somewhere worse soon, and there is something we can do about it.
    now_term = severity_score(twin.hemodynamics)
    if crit_nothing is None:
        soon_term = 0.0
        deadline = None
    else:
        deadline = crit_nothing
        soon_term = float(np.clip(1.0 - crit_nothing / horizon_h, 0.0, 1.0))
    act_term = best.benefit if best else 0.0

    urgency = float(
        np.clip(0.40 * now_term + 0.40 * soon_term + 0.20 * min(act_term * 4.0, 1.0), 0.0, 1.0)
    )

    # --- should this interrupt anyone? ----------------------------------
    actionable = best is not None and best.benefit >= MEANINGFUL_SEVERITY_DELTA
    heading_critical = crit_nothing is not None and crit_nothing <= horizon_h
    trusted = twin.confidence >= CONFIDENCE_ALERT_FLOOR

    # An imminent crossing always interrupts: if no ward drug changes it,
    # the action is escalation, and suppressing that would be the one alarm
    # we must never suppress.
    imminent = crit_nothing is not None and crit_nothing <= ESCALATE_WITHIN_H
    already_critical = twin.profile.code == "C"

    alert = bool(
        trusted
        and (
            already_critical
            or imminent
            or (actionable and (heading_critical or now_term > 0.30))
        )
    )

    if not trusted:
        reason = "Twin confidence low - review in person, do not rely on the model."
    elif already_critical:
        reason = (
            f"ALREADY cold-wet - the worst quadrant. "
            f"{best.agent_name if best else 'Escalation'} needed now, not at the round."
        )
    elif imminent and not actionable:
        reason = (
            f"Projected cold-wet in {crit_nothing:.1f} h and no ward drug changes it - "
            f"escalate now (cardiology / higher care)."
        )
    elif alert and heading_critical:
        reason = (
            f"Projected to reach cold-wet in {crit_nothing:.1f} h. "
            f"{best.agent_name} changes that."
        )
    elif alert:
        reason = f"Unwell now and responsive to {best.agent_name}."
    elif heading_critical and not actionable:
        reason = (
            f"Heading for cold-wet in {crit_nothing:.1f} h but no ward-level agent "
            f"changes it - escalate rather than treat."
        )
    elif twin.profile.code == "A":
        reason = "Compensated. No action needed."
    else:
        reason = "Stable on current therapy."

    return WardEntry(
        twin=twin, ehr=ehr, do_nothing=nothing, best=best,
        urgency=urgency, alert=alert, reason=reason, deadline_h=deadline,
    )


def rank_ward(entries: list) -> list:
    """Order the ward so the top of the list is who to see next."""
    return sorted(
        entries,
        key=lambda e: (
            not e.alert,
            e.deadline_h if e.deadline_h is not None else 1e6,
            -e.urgency,
        ),
    )
