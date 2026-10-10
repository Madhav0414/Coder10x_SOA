"""Experiment 6: the twin at home, against the rules patients are taught now.

Four detectors on the same data:

  weight, 3 days   the self-care rule: call if weight rises 2 kg in 3 days
  weight, dry      call if weight is 2 kg above the first week home
  drift score      the previous home detector (continuity.readmission_risk):
                   recent average against the first week, hand-set weights
  home twin        core.home: the ward-fitted heart; flags when
                   P(decompensation within a week) >= 0.5

Simulated cohort.  Every bed of the simulated wards goes home twice: once
stable, once retaining fluid from a random day at a random rate.  Truth is
the patient's TRUE heart; the detector only gets the heart the ward FITTED,
so a bad ward fit shows up here as a bad home forecast.  Day-to-day noise and
missing days are real -- each patient borrows a real Fitbit volunteer's
centred variation in resting heart rate, steps, sleep and awake-in-bed time.
HRV and weight are simulated.

Real records.  The volunteers themselves, unmodified: healthy people, so any
flag is a false alarm.  Then the same records with the generic decompensation
overlay from continuity.inject_decompensation, which predates the home twin
and does not follow its physiology.

The alarm threshold (0.5) and every home-twin setting were fixed before these
experiments were run.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .core.continuity import inject_decompensation, readmission_risk
from .core.home import HOME_PLANS, HomeTwin, real_deviations, simulate_home
from .data.ward import build_ward
from .pipeline import fit_ward_at

FOLLOW_UP = 30          # days at home
FIRST_DAY = 8           # every detector gets a baseline week
ONSET = (5, 14)         # retention starts on a day in this range
RATE = (150.0, 400.0)   # mL/day retained -- 2 to 5 kg in a fortnight
WEIGHT_RULE_KG = 2.0
SWEEP = (0.3, 0.5, 0.7, 0.9)   # alternative alarm thresholds, reported, not chosen
NAN = float("nan")


# --------------------------------------------------------------------------
# Detectors: each returns, per day k (data up to and including day k), a flag
# --------------------------------------------------------------------------

def _weights(days):
    return np.array([d.weight_kg if d.weight_kg is not None else NAN for d in days], dtype=float)


def flags_weight_3d(days, twin=None, patient=None):
    w = _weights(days)
    out = {}
    for k in range(FIRST_DAY, len(days) + 1):
        now, before = w[k - 1], w[k - 4] if k >= 4 else NAN
        out[k] = bool(now == now and before == before and now - before >= WEIGHT_RULE_KG)
    return out


def flags_weight_dry(days, twin=None, patient=None):
    w = _weights(days)
    base = np.nanmean(w[:7]) if not np.isnan(w[:7]).all() else NAN
    return {k: bool(w[k - 1] == w[k - 1] and base == base and w[k - 1] - base >= WEIGHT_RULE_KG)
            for k in range(FIRST_DAY, len(days) + 1)}


def flags_drift(days, twin=None, patient=None):
    return {k: readmission_risk(patient, days[:k]).band == "high"
            for k in range(FIRST_DAY, len(days) + 1)}


def flags_twin(days, twin=None, patient=None):
    return {k: p >= 0.5 for k, p in twin.history(days)}


DETECTORS = {
    "weight_3d": ("Weight rule: +2 kg in 3 days", flags_weight_3d, True),
    "weight_dry": ("Weight rule: +2 kg over first week", flags_weight_dry, True),
    "drift": ("Drift score (previous)", flags_drift, False),
    "twin": ("Home twin", flags_twin, False),
}


def _without_scale(days):
    return [replace(d, weight_kg=NAN) for d in days]


# --------------------------------------------------------------------------
# Simulated cohort
# --------------------------------------------------------------------------

def _noise_sources(rng):
    """Real wearers' centred variation, and their own levels, if the data is here."""
    try:
        from .data.wearables import load_fitbit_users
        users = load_fitbit_users()
    except FileNotFoundError:
        return None
    out = []
    for u in users.values():
        days = u["days"]
        dev = real_deviations(days)
        lvl = lambda f: float(np.nanmean([f(d) for d in days]))
        base = {"rhr": lvl(lambda d: d.resting_hr),
                "steps": lvl(lambda d: d.steps if d.steps > 0 else NAN),
                "sleep": lvl(lambda d: d.sleep_hours),
                "awake": lvl(lambda d: d.awake_min)}
        base = {k: v for k, v in base.items() if v == v}
        out.append((dev, base))
    return out


def home_cohort(seeds=tuple(range(1, 13)), rng_seed: int = 17) -> list:
    rng = np.random.default_rng(rng_seed)
    sources = _noise_sources(rng)
    cases, j = [], 0
    for s in seeds:
        for wp, tw, _, _ in fit_ward_at(build_ward(seed=s), 16.0):
            truth = HomeTwin(wp.truth_trajectory.states[0])
            twin = HomeTwin(tw.params)
            for course in ("stable", "retaining"):
                onset = int(rng.integers(ONSET[0], ONSET[1] + 1))
                rate = float(rng.uniform(*RATE)) if course == "retaining" else 0.0
                noise, base = sources[j % len(sources)] if sources else (None, {})
                j += 1
                base = {**base, "weight": float(rng.uniform(52, 82)), "hrv": float(rng.uniform(18, 45))}
                days, E = simulate_home(truth.curve, FOLLOW_UP, onset, rate, rng, noise=noise, base=base)
                hit = np.nonzero(E >= truth.curve.event_E)[0]
                cases.append({
                    "seed": s, "bed": wp.ehr.bed, "course": course, "onset": onset, "rate": rate,
                    "event": int(hit[0]) + 1 if hit.size else None,
                    "days": days, "E": E, "truth": truth, "twin": twin, "patient": tw,
                })
    return cases


def _twin_sweep(cases, scale: bool) -> list:
    """The home twin at other alarm thresholds -- the whole trade-off."""
    hist = []
    for c in cases:
        days = c["days"] if scale else _without_scale(c["days"])
        hist.append(dict(c["twin"].history(days)))
    rows = []
    for th in SWEEP:
        fa = n = det = elig = 0
        leads = []
        for c, h in zip(cases, hist):
            if c["course"] == "stable":
                fa += sum(p >= th for p in h.values())
                n += len(h)
            elif c["event"] is not None and c["event"] > FIRST_DAY:
                elig += 1
                early = [k for k, p in sorted(h.items()) if p >= th and c["onset"] < k < c["event"]]
                if early:
                    det += 1
                    leads.append(c["event"] - early[0])
        rows.append({"threshold": th, "false_alarm_rate": fa / n if n else None,
                     "sensitivity": det / elig if elig else None,
                     "median_lead_days": float(np.median(leads)) if leads else None})
    return rows


def _score(cases, scale: bool) -> dict:
    out = {}
    for key, (label, fn, needs_scale) in DETECTORS.items():
        if needs_scale and not scale:
            continue
        stable_days = stable_flags = stable_ever = n_stable = 0
        detected, leads, eligible, late, censored = 0, [], 0, 0, 0
        for c in cases:
            days = c["days"] if scale else _without_scale(c["days"])
            f = fn(days, twin=c["twin"], patient=c["patient"])
            if c["course"] == "stable":
                n_stable += 1
                stable_days += len(f)
                stable_flags += sum(f.values())
                stable_ever += any(f.values())
                continue
            if c["event"] is None or c["event"] <= FIRST_DAY:
                censored += 1
                continue
            eligible += 1
            early = [k for k, v in f.items() if v and c["onset"] < k < c["event"]]
            if early:
                detected += 1
                leads.append(c["event"] - early[0])
            elif any(v for k, v in f.items() if k >= c["event"]):
                late += 1
        out[key] = {
            "label": label,
            "false_alarm_rate": stable_flags / stable_days if stable_days else None,
            "stable_ever_flagged": stable_ever,
            "stable": n_stable,
            "eligible": eligible,
            "detected": detected,
            "sensitivity": detected / eligible if eligible else None,
            "median_lead_days": float(np.median(leads)) if leads else None,
            "caught_only_after_event": late,
            "censored": censored,
        }
    out["twin_sweep"] = _twin_sweep(cases, scale)
    return out


def _forecast_and_plans(cases) -> dict:
    """At the twin's first alarm: was the forecast right, and were the plans?"""
    errors, inside, agree, n_plan = [], 0, {k: 0 for k in HOME_PLANS}, 0
    gained_true, gained_pred = [], []
    for c in cases:
        if c["course"] != "retaining" or c["event"] is None or c["event"] <= FIRST_DAY:
            continue
        f = flags_twin(c["days"], twin=c["twin"])
        first = next((k for k, v in sorted(f.items()) if v and c["onset"] < k < c["event"]), None)
        if first is None:
            continue
        seen = c["days"][:first]
        est = c["twin"].estimate(seen)
        actual = c["event"] - first
        if est.days_to_event is not None:
            errors.append(abs(est.days_to_event - actual))
        lo, hi = est.interval
        inside += lo <= actual <= hi
        plans = c["twin"].plans(seen)
        n_plan += 1
        E_now, event_E = c["E"][first - 1], c["truth"].curve.event_E
        for key, (_, cut) in HOME_PLANS.items():
            r = c["rate"] - cut
            true_days = (event_E - E_now) / r if r > 0 else np.inf
            prevented_true = true_days > 14
            prevented_pred = plans[key]["p_event_14d"] < 0.5
            agree[key] += prevented_true == prevented_pred
            if key == "restrict" and np.isfinite(true_days) and np.isfinite(plans[key]["days_to_event"]):
                gained_true.append(true_days - actual)
                gained_pred.append(plans[key]["days_to_event"] - plans["no_change"]["days_to_event"])
    return {
        "alarms": n_plan,
        "days_to_event_mae": float(np.mean(errors)) if errors else None,
        "interval_coverage": inside / n_plan if n_plan else None,
        "plan_agreement": {k: agree[k] / n_plan if n_plan else None for k in HOME_PLANS},
        "restriction_days_gained_true": float(np.median(gained_true)) if gained_true else None,
        "restriction_days_gained_twin": float(np.median(gained_pred)) if gained_pred else None,
    }


def event_fluid_error(cases) -> dict:
    """How far the fitted heart's decompensation point is from the true one."""
    seen, err = set(), []
    for c in cases:
        k = (c["seed"], c["bed"])
        if k in seen:
            continue
        seen.add(k)
        err.append(c["twin"].curve.event_E - c["truth"].curve.event_E)
    err = np.array(err)
    return {"patients": len(err), "median_abs_ml": float(np.median(np.abs(err))),
            "bias_ml": float(np.mean(err)),
            "true_range_ml": [float(min(c["truth"].curve.event_E for c in cases)),
                              float(max(c["truth"].curve.event_E for c in cases))]}


# --------------------------------------------------------------------------
# Real volunteers
# --------------------------------------------------------------------------

def _real_scale(days, rng):
    w0 = float(rng.uniform(55, 80))
    wander = np.cumsum(rng.normal(0, 0.12, len(days))) + rng.normal(0, 0.35, len(days))
    return [replace(d, weight_kg=float(w0 + wander[i]),
                    hrv_ms=float(30.0 * np.exp(rng.normal(0, 0.15))))
            for i, d in enumerate(days)]


def real_volunteers(onset_day: int = 12, window: int = 14, seed: int = 5) -> dict:
    """False alarms on real records; the generic overlay, detected or not."""
    from .data.wearables import load_fitbit_users

    import tempfile
    from pathlib import Path

    from .data.wearables import read_apple_health, write_apple_health

    users = load_fitbit_users()
    rng = np.random.default_rng(seed)
    hearts = [(HomeTwin(tw.params), tw) for _, tw, _, _ in fit_ward_at(build_ward(seed=7), 16.0)]
    # The whole home stream -- band, scale, HRV -- goes through the file a
    # phone exports, and the twin reads what comes back.
    tmp = Path(tempfile.mkdtemp(prefix="prahari_health_"))
    records, intact = [], 0
    for uid, u in users.items():
        written = _real_scale(u["days"], rng)
        _, back = read_apple_health(write_apple_health(written, tmp / f"{uid}.xml", u["start"]))
        intact += len(back) == len(written)
        records.append(back)
    out = {"users": len(users), "hearts": len(hearts), "onset_day": onset_day,
           "apple_health_roundtrip": f"{intact}/{len(users)}"}
    for setting in ("band_only", "band_and_scale"):
        res = {}
        for key, (label, fn, needs_scale) in DETECTORS.items():
            if needs_scale and setting == "band_only":
                continue
            fa = fa_days = 0
            det = elig = 0
            lags = []
            for rec in records:
                base_days = _without_scale(rec) if setting == "band_only" else rec
                for twin, patient in hearts:
                    f = fn(base_days, twin=twin, patient=patient)
                    fa += sum(f.values())
                    fa_days += len(f)
                    if len(base_days) < onset_day + window:
                        continue
                    elig += 1
                    sick = inject_decompensation(base_days, onset_day)
                    fs = fn(sick, twin=twin, patient=patient)
                    hit = [k for k, v in sorted(fs.items()) if v and onset_day < k <= onset_day + window]
                    if hit:
                        det += 1
                        lags.append(hit[0] - onset_day)
            res[key] = {"label": label, "false_alarm_rate": fa / fa_days if fa_days else None,
                        "user_days": fa_days, "injected": elig, "detected": det,
                        "median_days_to_detect": float(np.median(lags)) if lags else None}
        out[setting] = res
    return out


def home_experiment(seeds=tuple(range(1, 13))) -> dict:
    cases = home_cohort(seeds)
    out = {
        "cohort": {"patients": len(cases) // 2, "stable": sum(c["course"] == "stable" for c in cases),
                   "retaining": sum(c["course"] == "retaining" for c in cases),
                   "follow_up_days": FOLLOW_UP, "real_noise": _noise_sources(None) is not None},
        "event_fluid": event_fluid_error(cases),
        "simulated": {"band_and_scale": _score(cases, True), "band_only": _score(cases, False)},
        "at_alarm": _forecast_and_plans(cases),
    }
    try:
        out["real"] = real_volunteers()
    except FileNotFoundError:
        pass
    return out
