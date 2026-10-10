"""Consumer-band data: real Fitbit records, read and written as Apple Health.

Fetch the data first with `python scripts/fetch_data.py`.

Source: Furberg R, Brinton J, Keating M, Ortiz A. Crowd-sourced Fitbit
datasets 03.12.2016-05.12.2016. Zenodo, 10.5281/zenodo.53894. CC BY 4.0.

Each user becomes a list of WearableDay records:
  resting heart rate   5th percentile of that day's per-second heart rate
  steps                daily total (days with zero steps = band not worn)
  sleep                minutes asleep / 60
  awake in bed         minutes in bed not asleep (Fitbit's restless + awake)
  weight               last logged weight that day, if any
HRV is not in this dataset and is left missing.

The same records can be written to, and read back from, an Apple Health
`export.xml` -- the file an iPhone produces from Health > Export -- so the
twin's home stream arrives the way a patient's phone would deliver it.
"""

from __future__ import annotations

import datetime as dt
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.continuity import WearableDay

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "fitbit"
NAN = float("nan")

HK_RESTING_HR = "HKQuantityTypeIdentifierRestingHeartRate"
HK_STEPS = "HKQuantityTypeIdentifierStepCount"
HK_SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
HK_WEIGHT = "HKQuantityTypeIdentifierBodyMass"
HK_HRV = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
HK_ASLEEP = "HKCategoryValueSleepAnalysisAsleepUnspecified"
HK_AWAKE = "HKCategoryValueSleepAnalysisAwake"
HK_TIME = "%Y-%m-%d %H:%M:%S %z"


SUMMARY = "daily_summary.json"      # written next to the CSVs on first load
SUMMARY_VERSION = 1


def _resting_hr(root: Path) -> dict:
    """5th percentile of each user's per-second heart rate, per day.

    The file has 2.5 million rows; it is read in chunks so loading stays well
    inside a small server's memory.
    """
    parts: dict = {}
    for chunk in pd.read_csv(root / "heartrate_seconds_merged.csv", usecols=["Id", "Time", "Value"],
                             dtype={"Id": "int64", "Value": "int16"}, chunksize=400_000):
        day = chunk["Time"].str.partition(" ")[0]
        for key, v in chunk.groupby([chunk["Id"], day])["Value"]:
            parts.setdefault(key, []).append(v.to_numpy())
    return {(int(uid), dt.datetime.strptime(d, "%m/%d/%Y").date()): float(np.quantile(np.concatenate(v), 0.05))
            for (uid, d), v in parts.items()}


def _summary_fresh(root: Path) -> bool:
    s = root / SUMMARY
    if not s.exists():
        return False
    newest = max(p.stat().st_mtime for p in root.glob("*.csv"))
    return s.stat().st_mtime >= newest


def _read_summary(root: Path) -> dict | None:
    data = json.loads((root / SUMMARY).read_text(encoding="utf-8"))
    if data.get("version") != SUMMARY_VERSION:
        return None
    return {int(uid): {"start": dt.date.fromisoformat(u["start"]), "days": [WearableDay(**d) for d in u["days"]]}
            for uid, u in data["users"].items()}


def _write_summary(root: Path, users: dict) -> None:
    data = {"version": SUMMARY_VERSION,
            "users": {str(uid): {"start": u["start"].isoformat(), "days": [asdict(d) for d in u["days"]]}
                      for uid, u in users.items()}}
    try:
        (root / SUMMARY).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass                                   # read-only data folder: just recompute next time


@lru_cache(maxsize=1)
def load_fitbit_users(root: str | None = None, min_days: int = 16) -> dict:
    """Users with at least `min_days` days of heart rate, steps and sleep."""
    root = Path(root) if root else DEFAULT_ROOT
    if not (root / "dailyActivity_merged.csv").exists():
        raise FileNotFoundError(f"Fitbit data not found in {root}; run scripts/fetch_data.py")
    if _summary_fresh(root):
        users = _read_summary(root)
        if users is not None:
            return users

    rest = _resting_hr(root)

    act = pd.read_csv(root / "dailyActivity_merged.csv")
    act["date"] = pd.to_datetime(act.ActivityDate, format="%m/%d/%Y").dt.date
    steps = act.set_index(["Id", "date"]).TotalSteps

    slp = pd.read_csv(root / "sleepDay_merged.csv")
    slp["date"] = pd.to_datetime(slp.SleepDay, format="%m/%d/%Y %I:%M:%S %p").dt.date
    sleep = slp.groupby(["Id", "date"]).TotalMinutesAsleep.sum() / 60.0
    awake = (slp.groupby(["Id", "date"]).TotalTimeInBed.sum()
             - slp.groupby(["Id", "date"]).TotalMinutesAsleep.sum()).astype(float)

    wt = pd.read_csv(root / "weightLogInfo_merged.csv")
    wt["date"] = pd.to_datetime(wt.Date, format="%m/%d/%Y %I:%M:%S %p").dt.date
    weight = wt.groupby(["Id", "date"]).WeightKg.last()

    users = {}
    for uid in sorted({u for u, _ in rest}):
        dates = sorted(d for (u, d) in rest if u == uid)
        if len(dates) < min_days or uid not in set(sleep.index.get_level_values(0)):
            continue
        start = dates[0]
        records = []
        for d in pd.date_range(start, max(dates)).date:
            s = steps.get((uid, d), NAN)
            records.append(WearableDay(
                day=(d - start).days + 1,
                resting_hr=float(rest.get((uid, d), NAN)),
                hrv_ms=NAN,
                steps=int(s) if s == s and s > 0 else -1,
                sleep_hours=float(sleep.get((uid, d), NAN)),
                night_hr_rise=NAN,
                weight_kg=float(weight.get((uid, d), NAN)),
                awake_min=float(awake.get((uid, d), NAN)),
            ))
        users[int(uid)] = {"start": start, "days": records}
    _write_summary(root, users)
    return users


# --------------------------------------------------------------------------
# Apple Health export.xml
# --------------------------------------------------------------------------

def write_apple_health(days: list, path: Path, start: dt.date, source: str = "Prahari") -> Path:
    """Write daily records as an Apple Health export."""
    tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
    root = ET.Element("HealthData", locale="en_IN")
    ET.SubElement(root, "ExportDate", value=dt.datetime.now(tz).strftime(HK_TIME))

    def rec(kind, unit, value, t0, t1):
        a = {"type": kind, "sourceName": source, "startDate": t0.strftime(HK_TIME),
             "endDate": t1.strftime(HK_TIME), "value": value}
        if unit:
            a["unit"] = unit
        ET.SubElement(root, "Record", a)

    for d in days:
        day = dt.datetime.combine(start + dt.timedelta(days=d.day - 1), dt.time(), tz)
        if d.resting_hr == d.resting_hr:
            rec(HK_RESTING_HR, "count/min", f"{d.resting_hr:.0f}", day + dt.timedelta(hours=6),
                day + dt.timedelta(hours=6))
        if d.steps >= 0:
            rec(HK_STEPS, "count", str(d.steps), day + dt.timedelta(hours=8), day + dt.timedelta(hours=22))
        if d.sleep_hours == d.sleep_hours:
            wake = day + dt.timedelta(hours=6, minutes=30)
            asleep = wake - dt.timedelta(hours=d.sleep_hours)
            rec(HK_SLEEP, None, HK_ASLEEP, asleep, wake)
            if d.awake_min == d.awake_min and d.awake_min > 0:
                # Awake in bed after waking, so the record ends on the same
                # day as the night it belongs to (nights are filed by end date).
                rec(HK_SLEEP, None, HK_AWAKE, wake, wake + dt.timedelta(minutes=d.awake_min))
        if d.hrv_ms == d.hrv_ms:
            rec(HK_HRV, "ms", f"{d.hrv_ms:.1f}", day + dt.timedelta(hours=5), day + dt.timedelta(hours=5))
        if d.weight_kg is not None and d.weight_kg == d.weight_kg:
            rec(HK_WEIGHT, "kg", f"{d.weight_kg:.1f}", day + dt.timedelta(hours=7),
                day + dt.timedelta(hours=7))

    path = Path(path)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)
    return path


def read_apple_health(path: Path) -> tuple:
    """Read an Apple Health export into daily records.

    Steps are summed per day, resting heart rate and HRV averaged, sleep and
    awake-in-bed time summed per night (assigned to the day it ends), body
    mass taken as the day's last reading.  Other record types are ignored.
    Returns (start_date, days).
    """
    per_day = {}
    for _, el in ET.iterparse(str(path), events=("end",)):
        if el.tag != "Record":
            continue
        kind = el.get("type")
        if kind not in (HK_RESTING_HR, HK_STEPS, HK_SLEEP, HK_WEIGHT, HK_HRV):
            el.clear()
            continue
        t0 = dt.datetime.strptime(el.get("startDate"), HK_TIME)
        t1 = dt.datetime.strptime(el.get("endDate"), HK_TIME)
        slot = per_day.setdefault(t1.date(), {"hr": [], "hrv": [], "steps": 0, "seen_steps": False,
                                              "sleep": 0.0, "awake": 0.0, "seen_awake": False,
                                              "weight": NAN})
        if kind == HK_RESTING_HR:
            slot["hr"].append(float(el.get("value")))
        elif kind == HK_STEPS:
            slot["steps"] += int(float(el.get("value")))
            slot["seen_steps"] = True
        elif kind == HK_SLEEP and "Asleep" in (el.get("value") or ""):
            slot["sleep"] += (t1 - t0).total_seconds() / 3600.0
        elif kind == HK_SLEEP and el.get("value") == HK_AWAKE:
            slot["awake"] += (t1 - t0).total_seconds() / 60.0
            slot["seen_awake"] = True
        elif kind == HK_HRV:
            slot["hrv"].append(float(el.get("value")))
        elif kind == HK_WEIGHT:
            slot["weight"] = float(el.get("value"))
        el.clear()

    if not per_day:
        return None, []
    start = min(per_day)
    days = []
    for d in pd.date_range(start, max(per_day)).date:
        s = per_day.get(d)
        days.append(WearableDay(
            day=(d - start).days + 1,
            resting_hr=float(np.mean(s["hr"])) if s and s["hr"] else NAN,
            hrv_ms=float(np.mean(s["hrv"])) if s and s["hrv"] else NAN,
            steps=s["steps"] if s and s["seen_steps"] else -1,
            sleep_hours=s["sleep"] if s and s["sleep"] > 0 else NAN,
            night_hr_rise=NAN,
            weight_kg=s["weight"] if s else NAN,
            awake_min=s["awake"] if s and s["seen_awake"] else NAN,
        ))
    return start, days
