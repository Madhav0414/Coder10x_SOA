"""NEWS2 -- the early-warning score a real ward already runs.

This is the comparator that matters: not a research model, but the score a
nurse is already calculating.  The evaluation in prahari/pipeline.py scores
NEWS2 and the twin on the same charted rounds and sweeps both thresholds,
because comparing a loud system with a quiet one at default settings proves
nothing.
"""

from __future__ import annotations

# NEWS2 escalation thresholds (Royal College of Physicians)
NEWS2_URGENT = 5
NEWS2_EMERGENCY = 7


def news2_score(hr: float, sbp: float, spo2: float, resp_rate: float) -> int:
    """NEWS2 on the four parameters a step-down ward reliably charts.

    Temperature and consciousness level are omitted because our twin does not
    model them; the score is therefore a lower bound, which is the
    conservative direction for a comparator we intend to beat.
    """
    score = 0

    if resp_rate <= 8:
        score += 3
    elif resp_rate <= 11:
        score += 1
    elif resp_rate <= 20:
        score += 0
    elif resp_rate <= 24:
        score += 2
    else:
        score += 3

    if spo2 <= 91:
        score += 3
    elif spo2 <= 93:
        score += 2
    elif spo2 <= 95:
        score += 1

    if sbp <= 90:
        score += 3
    elif sbp <= 100:
        score += 2
    elif sbp <= 110:
        score += 1
    elif sbp >= 220:
        score += 3

    if hr <= 40:
        score += 3
    elif hr <= 50:
        score += 1
    elif hr <= 90:
        score += 0
    elif hr <= 110:
        score += 1
    elif hr <= 130:
        score += 2
    else:
        score += 3

    return score


def news2_from_observation(obs) -> int:
    return news2_score(obs.hr, obs.sbp, obs.spo2, obs.resp_rate)
