"""Shift handover briefs.

Assembled from twin state -- profile, key haemodynamics, trend, projected
crossing, most useful agent, confidence -- so every statement in a brief
traces to a computed quantity.  No language model is involved.
"""


from __future__ import annotations

from .triage import WardEntry


def _trend_phrase(entry: WardEntry) -> str:
    prog = entry.twin.progression_per_h
    if prog > 0.012:
        return "deteriorating"
    if prog > 0.003:
        return "drifting down slowly"
    if prog < -0.003:
        return "improving"
    return "steady"


def patient_brief(entry: WardEntry) -> str:
    """One paragraph a colleague can act on without re-reading the chart."""
    twin, ehr = entry.twin, entry.ehr
    h = twin.hemodynamics
    p = twin.profile

    lines = []
    lines.append(
        f"{getattr(ehr, 'bed', twin.patient_id)} - {getattr(ehr, 'name', 'Patient')}, "
        f"{getattr(ehr, 'age', '?')}{str(getattr(ehr, 'sex', '?'))[0].upper()}, "
        f"{getattr(ehr, 'admission_dx', 'heart failure')}, day "
        f"{getattr(ehr, 'day_of_stay', '?')}."
    )
    lines.append(
        f"Currently {p.label} (profile {p.code}): cardiac index {h.cardiac_index:.2f}, "
        f"filling pressure {h.pcwp:.0f} mmHg, BP {h.sbp:.0f}/{h.dbp:.0f}, "
        f"saturation {h.spo2:.0f}%. Trend is {_trend_phrase(entry)}."
    )

    if p.code == "C":
        lines.append(
            "Already in the cold-wet quadrant, which carries the worst outcomes "
            "of the four - this is not a projection, it is the current state."
        )
    elif entry.deadline_h is not None:
        lines.append(
            f"Left alone, the twin projects a cold-wet crossing in about "
            f"{entry.deadline_h:.1f} hours."
        )
    else:
        lines.append("No projected decompensation within the next 12 hours.")

    if p.code == "C" and entry.best is not None:
        lines.append(
            f"Strongest single agent on the twin: {entry.best.agent_name}. Guideline for "
            f"this profile is combination therapy with escalation."
        )
    elif entry.best is not None and entry.best.benefit >= 0.05:
        bought = entry.best.hours_bought
        if bought is not None and bought >= 90:
            gain = "prevents the crossing within the window"
        elif bought is not None and bought >= 0.25:
            gain = f"buys about {bought:.1f} hours"
        else:
            gain = "reduces projected severity"
        lines.append(f"Most useful single action: {entry.best.agent_name} - {gain}.")

    if twin.confidence_band == "low":
        lines.append(
            "TWIN CONFIDENCE LOW - observations are sparse or poorly explained by the "
            "model. Treat this brief as a prompt to examine the patient, not as a finding."
        )

    return " ".join(lines)


def ward_handover(entries: list) -> str:
    """The full shift brief, ordered by who needs attention first."""
    header = [
        "PRAHARI SHIFT HANDOVER",
        "=" * 64,
        "Generated from twin state. Every figure below is computed, not inferred",
        "by a language model. Verify at the bedside before acting.",
        "",
    ]
    body = []
    for i, e in enumerate(entries, start=1):
        flag = "[ALERT] " if e.alert else ""
        body.append(f"{i}. {flag}{patient_brief(e)}")
        body.append("")

    alerts = sum(1 for e in entries if e.alert)
    footer = [
        "-" * 64,
        f"{alerts} of {len(entries)} patients flagged for action this shift.",
    ]
    return "\n".join(header + body + footer)
