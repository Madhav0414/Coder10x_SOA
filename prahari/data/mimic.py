"""Real heart-failure ICU stays from the MIMIC-IV Clinical Database Demo.

Fetch the data first with `python scripts/fetch_data.py`.

Each stay is turned into the same shape the simulated ward produces -- an
EHR-like record plus a chart of Observations -- so the twin is fitted by the
same code.  Three decisions keep the comparison honest:

  * The chart is sampled like a ward round: at every `cadence_h` hours the
    reading is the latest value charted in the preceding hour.
  * A channel the patient's therapy controls is marked missing rather than fed
    in: SpO2 on supplemental oxygen, respiratory rate on a ventilator.
  * CVP, cardiac output/index and pulmonary-artery pressures are never given
    to the twin.  They are kept as held-out measurements to score against.

Bedside signs are mapped onto the twin's scales: oedema charted as pitting
depth (2/4/6/8 mm, anasarca) -> grade 1-3; capillary refill charted as
"<3 s" / ">3 s" -> 2.0 s / 4.0 s.  JVP is not charted in MIMIC and weight has
no known dry weight, so both are left missing.

Data: MIMIC-IV Clinical Database Demo v2.2, PhysioNet, ODbL 1.0.
Johnson A, Bulgarelli L, Pollard T, Horng S, Celi LA, Mark R.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.twin import Observation

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "mimic-iv-demo"

NAN = float("nan")

ITEM = {
    "hr": 220045, "nibp_sys": 220179, "nibp_dia": 220180, "abp_sys": 220050, "abp_dia": 220051,
    "spo2": 220277, "rr": 220210,
    "edema": 224862, "crt_r": 223951, "crt_l": 224308,
    "height": 226730, "weight_adm": 226512,
    "o2_device": 226732, "vent_mode": 223849, "vent_mode_h": 229314,
    # held out -- never passed to the twin
    "cvp": 220074, "pa_dia": 220060, "pcwp": 223771, "pcwp_pa": 226855,
    "co_thermo": 220088, "co_cont": 224842, "co_nicom": 228369, "ci_nicom": 228368,
}

EDEMA_GRADE = {"2 mm": 1.0, "4 mm": 2.0, "6 mm": 3.0, "8 mm": 3.0, "anasarca": 3.0}
VENTILATED = {"endotracheal tube", "tracheostomy tube", "t-piece", "trach mask"}

# inputevents label -> twin formulary key.  Pressors other than noradrenaline
# are mapped onto it (the twin has one vasopressor); loop diuretics onto
# furosemide.  Doses are not scaled -- each administration is one standard dose.
DRUGS = {
    "furosemide (lasix)": "furosemide", "furosemide (lasix) 250/50": "furosemide",
    "bumetanide (bumex)": "furosemide",
    "norepinephrine": "noradrenaline", "vasopressin": "noradrenaline",
    "epinephrine": "noradrenaline", "dopamine": "noradrenaline",
    "phenylephrine": "noradrenaline",
    "dobutamine": "dobutamine", "milrinone": "milrinone",
    "nitroglycerin": "nitroglycerin", "metoprolol": "metoprolol",
}


@dataclass
class RealStay:
    stay_id: int
    subject_id: int
    age: int
    sex: str
    bsa: float
    careunit: str
    los_h: float
    labs: dict
    series: dict          # channel -> (t_hours array, value array); t=0 at ICU admission
    held_out: dict        # measurement -> (t_hours, value)
    o2: tuple             # (t_hours, device strings)
    vent_t: np.ndarray    # times a ventilator mode was charted
    drugs: list = field(default_factory=list)   # (t_hours, formulary key, is_new_start)

    # ------------------------------------------------------------ chart
    def _latest(self, name: str, t: float, window: float) -> float:
        ts, vs = self.series.get(name, (np.empty(0), np.empty(0)))
        m = (ts <= t) & (ts > t - window)
        return float(vs[m][-1]) if m.any() else NAN

    def _on_oxygen(self, t: float) -> tuple:
        ts, dev = self.o2
        m = (ts <= t) & (ts > t - 12.0)
        device = dev[m][-1].lower() if m.any() else "none"
        vented = device in VENTILATED or bool(((self.vent_t <= t) & (self.vent_t > t - 6.0)).any())
        return device != "none", vented

    def observation(self, t: float) -> Observation | None:
        """What a nurse doing a round at time t would have written down."""
        hr = self._latest("hr", t, 1.0)
        sbp = self._latest("nibp_sys", t, 1.0)
        dbp = self._latest("nibp_dia", t, 1.0)
        if np.isnan(sbp) or np.isnan(dbp):
            sbp, dbp = self._latest("abp_sys", t, 1.0), self._latest("abp_dia", t, 1.0)
        if np.isnan(hr) or np.isnan(sbp) or np.isnan(dbp) or sbp <= dbp:
            return None
        oxygen, vented = self._on_oxygen(t)
        refill = [v for v in (self._latest("crt_r", t, 12.0), self._latest("crt_l", t, 12.0))
                  if np.isfinite(v)]
        crt = max(refill) if refill else NAN
        return Observation(
            t=t, hr=hr, sbp=sbp, dbp=dbp,
            spo2=NAN if oxygen else self._latest("spo2", t, 1.0),
            resp_rate=NAN if vented else self._latest("rr", t, 1.0),
            jvp_cm=NAN,
            edema_grade=self._latest("edema", t, 12.0),
            weight_excess_kg=NAN,
            crt_s=crt,
        )

    def chart(self, cadence_h: float = 4.0, until: float | None = None) -> list:
        end = self.los_h if until is None else min(until, self.los_h)
        out = []
        t = cadence_h
        while t <= end + 1e-9:
            o = self.observation(t)
            if o is not None:
                out.append(o)
            t += cadence_h
        return out

    def held(self, name: str, t: float, window: float = 1.0) -> float:
        ts, vs = self.held_out.get(name, (np.empty(0), np.empty(0)))
        m = np.abs(ts - t) <= window
        if not m.any():
            return NAN
        return float(vs[m][np.argmin(np.abs(ts[m] - t))])

    def drugs_started(self, t0: float, t1: float) -> list:
        """Formulary agents newly started in (t0, t1], with offsets from t0."""
        return [(key, t - t0) for t, key, new in self.drugs if new and t0 < t <= t1]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _hf_admissions(root: Path) -> set:
    dx = pd.read_csv(root / "hosp/diagnoses_icd.csv.gz", dtype={"icd_code": str})
    is_hf = ((dx.icd_version == 9) & dx.icd_code.str.startswith("428")) | (
        (dx.icd_version == 10) & dx.icd_code.str.startswith("I50"))
    return set(dx.loc[is_hf, "hadm_id"])


def _edema(v) -> float:
    return EDEMA_GRADE.get(str(v).strip().lower(), NAN)


def _crt(v) -> float:
    s = str(v).lower()
    return 2.0 if "normal" in s and "abnormal" not in s else 4.0 if "abnormal" in s else NAN


@lru_cache(maxsize=2)
def load_hf_stays(root: str | None = None) -> tuple:
    """All heart-failure ICU stays in the demo, as RealStay records."""
    root = Path(root) if root else DEFAULT_ROOT
    if not (root / "icu/chartevents.csv.gz").exists():
        raise FileNotFoundError(f"MIMIC-IV demo not found in {root}; run scripts/fetch_data.py")

    stays = pd.read_csv(root / "icu/icustays.csv.gz", parse_dates=["intime", "outtime"])
    stays = stays[stays.hadm_id.isin(_hf_admissions(root))]
    patients = pd.read_csv(root / "hosp/patients.csv.gz").set_index("subject_id")

    ce = pd.read_csv(root / "icu/chartevents.csv.gz",
                     usecols=["stay_id", "itemid", "charttime", "value", "valuenum"],
                     parse_dates=["charttime"])
    ce = ce[ce.stay_id.isin(set(stays.stay_id)) & ce.itemid.isin(set(ITEM.values()))]
    name_of = {v: k for k, v in ITEM.items()}
    ce["name"] = ce.itemid.map(name_of)

    items = pd.read_csv(root / "icu/d_items.csv.gz", usecols=["itemid", "label"])
    ie = pd.read_csv(root / "icu/inputevents.csv.gz",
                     usecols=["stay_id", "itemid", "starttime", "endtime", "amount", "amountuom",
                              "ordercategoryname"],
                     parse_dates=["starttime", "endtime"])
    ie = ie[ie.stay_id.isin(set(stays.stay_id))].merge(items, on="itemid")

    labs = pd.read_csv(root / "hosp/labevents.csv.gz",
                       usecols=["hadm_id", "itemid", "charttime", "valuenum"])
    lab_names = {50963: "ntprobnp", 50912: "creatinine", 50983: "sodium",
                 50971: "potassium", 51222: "hemoglobin"}
    labs = labs[labs.itemid.isin(set(lab_names)) & labs.hadm_id.isin(set(stays.hadm_id))]

    out = []
    for s in stays.itertuples():
        hours = lambda ts: (ts - s.intime).dt.total_seconds().to_numpy() / 3600.0
        c = ce[ce.stay_id == s.stay_id].sort_values("charttime")

        series, held = {}, {}
        for name, g in c.groupby("name"):
            t = hours(g.charttime)
            if name == "edema":
                v = g.value.map(_edema).to_numpy(dtype=float)
            elif name in ("crt_r", "crt_l"):
                v = g.value.map(_crt).to_numpy(dtype=float)
            elif name in ("o2_device", "vent_mode", "vent_mode_h"):
                continue
            else:
                v = g.valuenum.to_numpy(dtype=float)
            ok = np.isfinite(v)
            target = held if name in ("cvp", "pa_dia", "pcwp", "pcwp_pa", "co_thermo",
                                      "co_cont", "co_nicom", "ci_nicom") else series
            target[name] = (t[ok], v[ok])

        dev = c[c.name == "o2_device"]
        vent = c[c.name.isin(["vent_mode", "vent_mode_h"])]

        height = series.get("height", (None, np.array([])))[1]
        weight = series.get("weight_adm", (None, np.array([])))[1]
        if height.size and weight.size and 120 < height[0] < 220 and 30 < weight[0] < 250:
            bsa = float(np.sqrt(height[0] * weight[0] / 3600.0))      # Mosteller
        else:
            bsa = 1.9

        drugs = []
        d = ie[ie.stay_id == s.stay_id].sort_values("starttime")
        for label, g in d.groupby(d.label.str.lower()):
            key = DRUGS.get(label)
            if key is None:
                continue
            last_end = None
            for row in g.itertuples():
                t = (row.starttime - s.intime).total_seconds() / 3600.0
                # a new start = first segment, or a restart after >1 h off
                new = last_end is None or (row.starttime - last_end).total_seconds() > 3600
                drugs.append((t, key, bool(new)))
                last_end = row.endtime
        bolus = d[d.ordercategoryname.str.contains("Fluids", case=False, na=False)
                  & (d.amount >= 250) & ((d.endtime - d.starttime).dt.total_seconds() <= 7200)]
        for row in bolus.itertuples():
            drugs.append(((row.starttime - s.intime).total_seconds() / 3600.0, "fluid_bolus", True))
        drugs.sort()

        lab = labs[labs.hadm_id == s.hadm_id]
        lab_first = {lab_names[i]: float(g.sort_values("charttime").valuenum.dropna().iloc[0])
                     for i, g in lab.groupby("itemid") if g.valuenum.notna().any()}

        p = patients.loc[s.subject_id]
        out.append(RealStay(
            stay_id=int(s.stay_id), subject_id=int(s.subject_id),
            age=int(p.anchor_age), sex="female" if p.gender == "F" else "male",
            bsa=bsa, careunit=str(s.first_careunit), los_h=float(s.los) * 24.0,
            labs=lab_first, series=series, held_out=held,
            o2=(hours(dev.charttime), dev.value.astype(str).to_numpy()),
            vent_t=hours(vent.charttime), drugs=drugs,
        ))
    return tuple(out)
