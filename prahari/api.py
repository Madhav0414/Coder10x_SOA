"""HTTP API for the central station.

The browser owns the clock.  Between charted observations it interpolates each
twin's forecast locally, so the display stays live without a round trip; it
only asks the server again when the clock crosses a charting boundary and a
new observation arrives.  Twin fits are cached on (seed, cadence, reveal,
horizon) for the same reason.

All times in responses are absolute hours from admission.
"""

from __future__ import annotations

import json
import zlib
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .core.home import FORECAST_DAYS, HomeTwin, overlay_home, simulate_home
from .core.handover import patient_brief, ward_handover
from .core.interventions import CYP2D6_BETA_BLOCKER, FORMULARY, guideline_options
from .core.metabolic import default_timetable, meal_swap_benefit
from .core.stevenson import classify
from .core.triage import rank_ward
from .core.twin import PatientTwin
from .data.ward import build_ward
from .models.baselines import news2_from_observation
from .models.estimator import shared_inverse
from .pipeline import fit_ward_at, truth_at

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
RESULTS = ROOT / "docs" / "results.json"

HISTORY_H = 30.0
DEFAULTS = dict(seed=7, cadence=4.0, horizon=12.0)

@asynccontextmanager
async def _lifespan(_app):
    # Train (or load) the inverse operator and fit the opening ward before the
    # first request, so the page never opens onto a spinner.
    shared_inverse()
    _ward(DEFAULTS["seed"], DEFAULTS["cadence"], 4, DEFAULTS["horizon"])
    try:
        from .data.wearables import load_fitbit_users
        load_fitbit_users()
    except FileNotFoundError:
        pass
    yield


app = FastAPI(title="Prahari", docs_url="/api/docs", redoc_url=None, lifespan=_lifespan)


# --------------------------------------------------------------------------
# Model state
# --------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _raw_ward(seed: int, cadence: float):
    return build_ward(n_beds=8, seed=seed, history_h=HISTORY_H, cadence_h=cadence)


@lru_cache(maxsize=48)
def _ward(seed: int, cadence: float, reveal: int, horizon: float):
    """Twins fitted on what was charted by this round, carried forward to it,
    ranked.  Each row: (patient, twin_now, entry, visible, round_clock)."""
    clock = reveal * cadence
    rows = [(wp, tw, entry, visible, clock)
            for wp, tw, entry, visible in fit_ward_at(_raw_ward(seed, cadence), clock, horizon)]
    order = {e.bed: i for i, e in enumerate(rank_ward([r[2] for r in rows]))}
    rows.sort(key=lambda r: order[r[2].bed])
    return rows


def _get(seed, cadence, reveal, horizon, bed: int | None = None):
    rows = _ward(int(seed), float(cadence), int(reveal), float(horizon))
    if bed is None:
        return rows
    for r in rows:
        if int(r[0].ehr.bed.split()[-1]) == bed:
            return r
    raise HTTPException(404, f"no bed {bed}")


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _series(tr, t0: float, step: int = 1) -> dict:
    idx = range(0, len(tr.t), step)
    return {
        "t": [_r(t0 + tr.t[i], 3) for i in idx],
        "hr": [_r(tr.states[i].hr, 1) for i in idx],
        "sbp": [_r(tr.hemo[i].sbp, 1) for i in idx],
        "dbp": [_r(tr.hemo[i].dbp, 1) for i in idx],
        "map": [_r(tr.hemo[i].map, 1) for i in idx],
        "spo2": [_r(tr.hemo[i].spo2, 1) for i in idx],
        "rr": [_r(tr.hemo[i].resp_rate, 1) for i in idx],
        "ci": [_r(tr.hemo[i].cardiac_index, 3) for i in idx],
        "pcwp": [_r(tr.hemo[i].pcwp, 2) for i in idx],
    }


def _abs(rel, t0):
    return None if rel is None else _r(t0 + rel, 3)


def _obs(o) -> dict:
    return {
        "t": _r(o.t, 3), "hr": o.hr, "sbp": o.sbp, "dbp": o.dbp, "spo2": o.spo2,
        "rr": o.resp_rate, "jvp": o.jvp_cm, "edema": o.edema_grade,
        "weight": o.weight_excess_kg, "news2": news2_from_observation(o),
    }


def _bed(wp, tw, entry, visible, start) -> dict:
    e = wp.ehr
    t0 = start
    best = entry.best
    return {
        "bed": int(e.bed.split()[-1]),
        "id": e.patient_id,
        "name": e.name,
        "age": e.age,
        "sex": e.sex[0].upper(),
        "dx": e.admission_dx,
        "etiology": e.etiology,
        "ef": e.lvef_pct,
        "nyha": e.nyha,
        "day": e.day_of_stay,
        "diabetic": e.diabetes,
        "quadrant": {"code": tw.profile.code, "label": tw.profile.label},
        "alert": entry.alert,
        "urgency": _r(entry.urgency, 3),
        "reason": entry.reason,
        "deadline": _abs(entry.deadline_h, t0),
        "best": None if best is None else {
            "key": best.agent_key,
            "name": best.agent_name,
            "short": FORMULARY[best.agent_key].short,
            "deadline": _abs(best.critical_with, t0),
            "benefit": _r(best.benefit, 3),
        },
        "confidence": _r(tw.confidence, 3),
        "confidenceBand": tw.confidence_band,
        "synced": _r(visible[-1].t, 3),
        "obs": [_obs(o) for o in visible],
        "forecast": _series(entry.do_nothing, t0),
    }


def _ensemble(tw: PatientTwin, hours: float, t0: float, n: int = 8) -> dict:
    """Forecast band from perturbing the fitted state.

    Spread widens as confidence falls, so a twin that is unsure of a patient
    shows it on the chart rather than in a footnote.
    """
    rng = np.random.default_rng(zlib.crc32(tw.patient_id.encode()))
    spread = 0.03 + 0.14 * (1.0 - tw.confidence)
    runs = []
    for _ in range(n):
        k = 1.0 + rng.normal(0, spread)
        p = replace(
            tw.params,
            emax=tw.params.emax * k,
            emax_base=tw.params.emax_base * k,
            v_central=tw.params.v_central * (1.0 + rng.normal(0, spread * 0.6)),
        )
        twin = PatientTwin(
            tw.patient_id, p,
            progression_per_h=tw.progression_per_h * max(0.0, 1.0 + rng.normal(0, 0.35)),
            intake_base=tw.intake_base,
        )
        runs.append(twin.project(hours=hours))

    out = {"t": [_r(t0 + t, 3) for t in runs[0].t]}
    for key, fn in (
        ("hr", lambda tr: [s.hr for s in tr.states]),
        ("sbp", lambda tr: [h.sbp for h in tr.hemo]),
        ("spo2", lambda tr: [h.spo2 for h in tr.hemo]),
        ("rr", lambda tr: [h.resp_rate for h in tr.hemo]),
        ("ci", lambda tr: [h.cardiac_index for h in tr.hemo]),
        ("pcwp", lambda tr: [h.pcwp for h in tr.hemo]),
    ):
        arr = np.array([fn(tr) for tr in runs])
        out[key] = {
            "lo": [_r(v, 2) for v in np.percentile(arr, 10, axis=0)],
            "hi": [_r(v, 2) for v in np.percentile(arr, 90, axis=0)],
        }
    return out


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

def _params(seed: int, cadence: float, reveal: int, horizon: float):
    if cadence not in (1.0, 4.0):
        raise HTTPException(400, "cadence must be 1 or 4")
    max_reveal = int(HISTORY_H // cadence)
    return seed, cadence, max(0, min(reveal, max_reveal)), horizon


@app.get("/api/ward")
def ward(seed: int = 7, cadence: float = 4.0, reveal: int = 4, horizon: float = 12.0):
    seed, cadence, reveal, horizon = _params(seed, cadence, reveal, horizon)
    rows = _get(seed, cadence, reveal, horizon)
    return {
        "reveal": reveal,
        "clock": reveal * cadence,
        "cadence": cadence,
        "horizon": horizon,
        "historyH": HISTORY_H,
        "beds": [_bed(*r) for r in rows],
    }


@app.get("/api/patient/{bed}")
def patient(bed: int, seed: int = 7, cadence: float = 4.0, reveal: int = 4, horizon: float = 12.0):
    seed, cadence, reveal, horizon = _params(seed, cadence, reveal, horizon)
    wp, tw, entry, visible, start = _get(seed, cadence, reveal, horizon, bed)
    e = wp.ehr
    t0 = start
    h = tw.hemodynamics

    options = []
    for tr in tw.counterfactuals(hours=horizon):
        tc = tr.time_to_critical()
        options.append({
            "label": tr.label,
            "deadline": _abs(tc, t0),
            "severity": _r(tr.worst_severity(), 3),
            "ci": _r(tr.endpoint().cardiac_index, 2),
            "pcwp": _r(tr.endpoint().pcwp, 1),
        })

    metabolic = None
    if e.diabetes:
        sw = meal_swap_benefit(e.hba1c)
        metabolic = {
            "hba1c": _r(e.hba1c, 1),
            "from": sw["from_label"], "to": sw["to_label"],
            "peakFrom": _r(sw["peak_from"], 0), "peakTo": _r(sw["peak_to"], 0),
            "delta": _r(sw["delta"], 0), "sodiumSaved": _r(sw["sodium_saved_mg"], 0),
        }

    pgx = CYP2D6_BETA_BLOCKER.get(e.cyp2d6, 1.0)

    return {
        **_bed(wp, tw, entry, visible, start),
        "record": {
            "bsa": _r(e.bsa, 2),
            "priorAdmissions": e.prior_admissions_12m,
            "ckd": e.ckd, "hypertension": e.hypertension,
            "labs": [
                ["BNP", e.bnp, "pg/mL", e.bnp > 400],
                ["Creatinine", _r(e.creatinine, 2), "mg/dL", e.creatinine > 1.3],
                ["Sodium", e.sodium, "mmol/L", e.sodium < 135],
                ["Potassium", _r(e.potassium, 1), "mmol/L", not 3.5 <= e.potassium <= 5.0],
                ["Haemoglobin", _r(e.hemoglobin, 1), "g/dL", e.hemoglobin < 12],
                ["HbA1c", _r(e.hba1c, 1) if e.hba1c else None, "%", bool(e.hba1c and e.hba1c > 7)],
            ],
            "meds": list(e.home_meds),
            "cyp2d6": e.cyp2d6,
            "pgxFactor": pgx,
        },
        "twin": {
            "ci": _r(h.cardiac_index, 2),
            "pcwp": _r(h.pcwp, 1),
            "sv": _r(h.stroke_volume, 0),
            "ef": _r(h.ef * 100, 0),
            "emax": _r(tw.params.emax, 2),
            "emaxBase": _r(tw.params.emax_base, 2),
            "svr": _r(tw.params.svr, 1),
            "volume": _r(tw.params.v_central, 0),
            "lungWater": _r(tw.params.v_interstitial, 0),
            "progression": _r(tw.progression_per_h * 100, 2),
            "residual": _r(tw.fit_residual, 2),
            "nObs": tw.fit_n_obs,
            "staleH": _r(tw.stale_h, 2),
        },
        "profile": {
            "code": tw.profile.code, "label": tw.profile.label,
            "description": tw.profile.description, "therapy": tw.profile.therapy,
        },
        "explain": tw.explain(),
        "options": options,
        "agents": [{"key": a.key, "name": a.name, "short": a.short, "rationale": a.rationale}
                   for a in guideline_options(tw.profile.code)],
        "bestPath": None if entry.best is None else _series(entry.best.trajectory, t0),
        "band": _ensemble(tw, horizon, t0),
        "timeline": [
            {"t": _r(t0 + ev.t, 2), "kind": ev.kind, "label": ev.label,
             "detail": ev.detail, "done": ev.done}
            for ev in default_timetable(diabetic=e.diabetes)
        ],
        "metabolic": metabolic,
        "brief": patient_brief(entry),
    }


@app.get("/api/whatif/{bed}")
def whatif(bed: int, agent: str, delay: float = 4.0, now: float | None = None, seed: int = 7,
           cadence: float = 4.0, reveal: int = 4, horizon: float = 12.0):
    """Fork the twin at `now`: give the agent now, after `delay`, or not at all."""
    seed, cadence, reveal, horizon = _params(seed, cadence, reveal, horizon)
    if agent not in FORMULARY:
        raise HTTPException(400, f"unknown agent {agent}")
    wp, tw, entry, visible, start = _get(seed, cadence, reveal, horizon, bed)
    now = start if now is None else max(start, float(now))
    w = tw.advanced(now - start).wait_versus_act(agent, delay_h=float(delay), hours=horizon + 2)
    out = {"agent": FORMULARY[agent].name, "delay": delay, "now": _r(now, 3),
           "given": _r(now + delay, 3)}
    for key in ("do_nothing", "act_later", "act_now"):
        tr = w[key]
        out[key] = {
            "t": [_r(now + t, 3) for t in tr.t],
            "ci": [_r(h.cardiac_index, 3) for h in tr.hemo],
            "pcwp": [_r(h.pcwp, 2) for h in tr.hemo],
            "deadline": _abs(tr.time_to_critical(), now),
            "severity": _r(tr.worst_severity(), 3),
        }
    return out


HOME_ONSET_DAY = 12
HOME_RATE = 250.0      # mL/day retained in the demonstration course
REAL_CHANNELS = ("rhr", "steps", "sleep", "awake")


def _num(x, n=2):
    """JSON-safe number: NaN and missing become null."""
    return None if x is None or x != x else round(float(x), n)


def _nums(xs, n=2):
    return [_num(x, n) for x in xs]


@lru_cache(maxsize=64)
def _home(seed, cadence, reveal, horizon, bed, course):
    _, tw, _, _, _ = _get(seed, cadence, reveal, horizon, bed)
    ht = HomeTwin(tw.params)
    rate = HOME_RATE if course == "decompensating" else 0.0
    rng = np.random.default_rng([seed, bed, 31])
    try:
        from .data.wearables import load_fitbit_users
        users = load_fitbit_users()
        uid = sorted(users)[(bed - 1) % len(users)]
        days, E = overlay_home(ht.curve, users[uid]["days"], HOME_ONSET_DAY, rate, rng)
        source = {
            "kind": "real",
            "label": f"Band: real records of volunteer {sorted(users).index(uid) + 1} of {len(users)} "
                     "(Fitbit, Zenodo 10.5281/zenodo.53894, CC BY 4.0). Scale and HRV: simulated.",
            "channels": {c: ("real" if c in REAL_CHANNELS else "simulated") for c in
                         ("weight", "rhr", "hrv", "awake", "sleep", "steps")},
        }
    except FileNotFoundError:
        days, E = simulate_home(ht.curve, 30, HOME_ONSET_DAY, rate, rng)
        source = {"kind": "simulated",
                  "label": "Simulated band and scale (run scripts/fetch_data.py for real band records).",
                  "channels": {c: "simulated" for c in ("weight", "rhr", "hrv", "awake", "sleep", "steps")}}
    source["onset"] = HOME_ONSET_DAY if rate else None
    source["rate"] = rate
    return ht, days, E, source


@lru_cache(maxsize=64)
def _home_history(seed, cadence, reveal, horizon, bed, course):
    ht, full, _, _ = _home(seed, cadence, reveal, horizon, bed, course)
    return tuple(ht.history(full))


@app.get("/api/discharge/{bed}")
def discharge(bed: int, course: str = "recorded", upto: int | None = None, seed: int = 7,
              cadence: float = 4.0, reveal: int = 4, horizon: float = 12.0):
    """The twin at home: the ward-fitted heart, read through a band and a scale.

    With the Fitbit data fetched, each bed is paired with one real volunteer's
    band records.  `recorded` shows them as they are; `decompensating` lays
    this heart's own response to 250 mL/day of retention over them from day
    HOME_ONSET_DAY.  Scale and HRV are simulated around the same fluid path.

    `upto` is "today": the twin sees days 1..upto only.  Left out, it opens on
    the last day of a recorded course, or on the day the twin first called a
    decompensating one.
    """
    seed, cadence, reveal, horizon = _params(seed, cadence, reveal, horizon)
    course = "decompensating" if course == "decompensating" else "recorded"
    ht, full, E, source = _home(seed, cadence, reveal, horizon, bed, course)
    history = _home_history(seed, cadence, reveal, horizon, bed, course)
    first_call = next((k for k, p in history if p >= 0.5), None)
    if upto is None:
        upto = first_call if course == "decompensating" and first_call else len(full)
    upto = int(np.clip(upto, 8, len(full)))
    days = full[:upto]
    est = ht.estimate(days)
    plans = ht.plans(days, horizon=21)
    hit = np.nonzero(E >= ht.curve.event_E)[0]
    d = ht.discharge
    return {
        "source": source,
        "heart": {
            "pcwp": _r(d.v_central / d.c_venous, 1),
            "limitL": _r(ht.curve.event_E / 1000.0, 2),
            "contractility": _r(d.emax, 2),
        },
        "estimate": {
            "pEvent": _r(est.p_event, 3), "band": est.band, "horizonDays": FORECAST_DAYS,
            "pBuilding": _r(1.0 - est.p_stable, 3),
            "daysToEvent": _r(est.days_to_event, 1),
            "interval": [_num(x, 1) if np.isfinite(x) else None for x in est.interval],
            "fluidL": _r(est.fluid_ml / 1000.0, 2), "rate": _r(est.rate_ml_day, 0),
            "pcwp": _r(est.pcwp, 1), "lung": _r(est.lung_ml, 0), "drivers": est.drivers,
        },
        "today": upto,
        "totalDays": len(full),
        "firstCall": first_call,
        "history": [{"day": k, "p": _r(p, 3)} for k, p in history],
        "pastL": _nums(np.array(plans.pop("past")) / 1000.0),
        "trueL": _nums(E / 1000.0) if source["onset"] else None,
        "trueEventDay": int(hit[0]) + 1 if hit.size else None,
        "plans": {k: {"label": v["label"], "cut": v["cut_ml_day"], "p14": _r(v["p_event_14d"], 3),
                      "daysToEvent": _num(v["days_to_event"], 1) if np.isfinite(v["days_to_event"]) else None,
                      "fluidL": _nums(np.array(v["fluid_path"]) / 1000.0),
                      "pcwp": _nums(v["pcwp_path"], 1)}
                  for k, v in plans.items()},
        "days": [
            {"day": x.day, "rhr": _num(x.resting_hr, 1), "hrv": _num(x.hrv_ms, 1),
             "steps": x.steps if x.steps is not None and x.steps >= 0 else None,
             "sleep": _num(x.sleep_hours, 2), "awake": _num(x.awake_min, 0),
             "weight": _num(x.weight_kg, 2)}
            for x in full
        ],
    }


@app.get("/api/evidence")
def evidence(seed: int = 7, cadence: float = 4.0, reveal: int = 4, horizon: float = 12.0):
    seed, cadence, reveal, horizon = _params(seed, cadence, reveal, horizon)
    rows = []
    for wp, tw, entry, visible, start in _get(seed, cadence, reveal, horizon):
        truth = truth_at(wp, start)
        fit = tw.hemodynamics
        rows.append({
            "bed": int(wp.ehr.bed.split()[-1]),
            "trueQ": classify(truth).code, "twinQ": tw.profile.code,
            "trueCI": _r(truth.cardiac_index, 2), "twinCI": _r(fit.cardiac_index, 2),
            "truePCWP": _r(truth.pcwp, 1), "twinPCWP": _r(fit.pcwp, 1),
            "confidence": _r(tw.confidence, 2),
            "staleH": _r(tw.stale_h, 1),
        })
    summary = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else None
    return {"rows": rows, "summary": summary}


@app.get("/api/handover", response_class=PlainTextResponse)
def handover(seed: int = 7, cadence: float = 4.0, reveal: int = 4, horizon: float = 12.0):
    seed, cadence, reveal, horizon = _params(seed, cadence, reveal, horizon)
    return ward_handover([r[2] for r in _get(seed, cadence, reveal, horizon)])


@app.get("/api/health")
def health():
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")

