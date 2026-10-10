"""The simulated cardiac step-down ward.

Produces two streams:

  static   an EHR per patient: demographics, diagnosis, LVEF, NYHA, labs,
           home medication, CYP2D6 phenotype
  dynamic  the ward chart: vitals and bedside signs at ward cadence, with
           measurement noise, rounding and missed rounds

Ground truth is simulated with the same physics the twin uses, then degraded
before the twin sees it.  The twin never receives true parameters; they are
kept only for scoring.  Patient identity and physiology depend only on the
seed, and observation noise has its own random stream, so changing the
charting cadence changes how a patient is charted, never who they are.

The patient mix is shaped on the Trivandrum Heart Failure Registry: mean age
around 61, about a third women, rheumatic disease present alongside ischaemic
and hypertensive causes, and a large diabetic overlap.
"""


from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

from ..core.cardiac import CardiacParams
from ..core.twin import Observation

# Ward reality: vitals every four hours, not every second.
WARD_CADENCE_H = 4.0
ICU_CADENCE_H = 0.25


@dataclass
class EHR:
    """The static stream."""

    patient_id: str
    bed: str
    name: str
    age: int
    sex: str
    bsa: float
    admission_dx: str
    etiology: str
    lvef_pct: int
    nyha: int
    prior_admissions_12m: int
    diabetes: bool
    ckd: bool
    hypertension: bool
    # Labs
    bnp: int              # pg/mL
    creatinine: float     # mg/dL
    sodium: int           # mmol/L
    potassium: float      # mmol/L
    hemoglobin: float     # g/dL
    hba1c: float | None   # % -- only for the diabetic patients
    # Pharmacogenomics: CYP2D6 phenotype drives metoprolol clearance
    cyp2d6: str
    home_meds: tuple
    day_of_stay: int

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def summary(self) -> str:
        bits = [f"{self.age}{self.sex[0].upper()}", self.admission_dx, f"EF {self.lvef_pct}%"]
        if self.diabetes:
            bits.append("T2DM")
        if self.ckd:
            bits.append("CKD")
        return " | ".join(bits)


@dataclass
class WardPatient:
    """Ground truth plus what the ward wrote down."""

    ehr: EHR
    true_params: CardiacParams
    progression_per_h: float
    intake_base: float
    observations: list = field(default_factory=list)
    # Held back from the twin; used only to score how well it did.
    truth_trajectory: object = None


# --------------------------------------------------------------------------
# Patient archetypes
# --------------------------------------------------------------------------

_NAMES = [
    "R. Krishnan", "S. Devi", "M. Iqbal", "P. Natarajan",
    "A. Fernandes", "L. Bai", "V. Reddy", "T. Joseph",
]

_ARCHETYPES = [
    # (label, etiology, emax, v_central, hr, ef, progression/h, intake, congestion)
    ("stable_compensated",  "Ischaemic",   1.15, 520.0, 74.0, 42, 0.000,  55.0,   0.0),
    ("quiet_congestion",    "Hypertensive", 0.95, 690.0, 82.0, 35, 0.004,  85.0, 140.0),
    ("silent_slide",        "Ischaemic",   0.68, 760.0, 94.0, 24, 0.030,  95.0, 260.0),
    ("post_mi_recovering",  "Ischaemic",   0.88, 600.0, 86.0, 30, -0.006, 60.0,  90.0),
    ("rheumatic_valve",     "Rheumatic",   0.80, 720.0, 90.0, 28, 0.012,  80.0, 220.0),
    ("cardiorenal",         "Ischaemic",   0.72, 800.0, 96.0, 25, 0.018, 110.0, 340.0),
    ("dry_low_output",      "Dilated CM",  0.62, 380.0, 98.0, 22, 0.008,  45.0,   0.0),
    ("near_discharge",      "Hypertensive", 1.30, 470.0, 72.0, 46, -0.004, 55.0,  0.0),
]

_DX = {
    "Ischaemic": "Acute decompensated HF, ischaemic",
    "Hypertensive": "Acute decompensated HF, hypertensive",
    "Rheumatic": "Decompensated rheumatic mitral disease",
    "Dilated CM": "Dilated cardiomyopathy, decompensated",
}


def _make_ehr(rng: np.random.Generator, idx: int, arch, patient_id: str) -> EHR:
    label, etiology, _emax, _vc, _hr, ef, _prog, _intake, _cong = arch

    age = int(np.clip(rng.normal(61, 12), 28, 88))
    sex = "female" if rng.random() < 0.31 else "male"
    bsa = float(np.clip(rng.normal(1.62 if sex == "female" else 1.74, 0.11), 1.3, 2.1))

    diabetes = rng.random() < 0.38          # heavy overlap in Indian HF cohorts
    ckd = rng.random() < 0.26 or label == "cardiorenal"
    htn = etiology == "Hypertensive" or rng.random() < 0.45

    # Sicker hearts leak more BNP; congestion raises it further.
    bnp = int(np.clip(rng.normal(2600 - 38 * ef, 700), 120, 9000))
    creat = float(np.clip(rng.normal(1.9 if ckd else 1.05, 0.35), 0.5, 5.0))

    meds = ["Ramipril", "Furosemide"]
    if ef < 40:
        meds.append("Carvedilol")
    if ef < 35:
        meds.append("Spironolactone")
    if diabetes:
        meds.append("Metformin")

    return EHR(
        patient_id=patient_id,
        bed=f"Bed {idx + 1}",
        name=_NAMES[idx % len(_NAMES)],
        age=age,
        sex=sex,
        bsa=bsa,
        admission_dx=_DX[etiology],
        etiology=etiology,
        lvef_pct=int(np.clip(ef + rng.normal(0, 2), 10, 60)),
        nyha=int(np.clip(4 - ef // 15, 2, 4)),
        prior_admissions_12m=int(rng.integers(0, 4)),
        diabetes=diabetes,
        ckd=ckd,
        hypertension=htn,
        bnp=bnp,
        creatinine=creat,
        sodium=int(np.clip(rng.normal(136, 4), 122, 145)),
        potassium=float(np.clip(rng.normal(4.2, 0.5), 2.8, 6.2)),
        hemoglobin=float(np.clip(rng.normal(11.4, 1.6), 6.5, 16.0)),
        hba1c=float(np.clip(rng.normal(8.1, 1.3), 5.4, 13.0)) if diabetes else None,
        cyp2d6=str(rng.choice(["poor", "intermediate", "normal", "ultrarapid"],
                              p=[0.07, 0.28, 0.58, 0.07])),
        home_meds=tuple(meds),
        day_of_stay=int(rng.integers(1, 6)),
    )


def _true_params(rng: np.random.Generator, arch, ehr: EHR) -> CardiacParams:
    _label, _et, emax, v_central, hr, _ef, _prog, _intake, cong = arch
    jitter = lambda x, s: float(x * (1.0 + rng.normal(0, s)))

    emax_j = jitter(emax, 0.07)
    return CardiacParams(
        emax=emax_j,
        emax_base=emax_j,
        svr=jitter(19.5, 0.08),
        svr_base=jitter(19.0, 0.06),
        v_central=jitter(v_central, 0.05),
        hr=jitter(hr, 0.05),
        hr_base=jitter(hr - 4, 0.05),
        c_arterial=jitter(1.5, 0.10),
        c_venous=jitter(33.0, 0.08),
        c_diastolic=jitter(14.0, 0.08),
        v_interstitial=max(0.0, jitter(cong, 0.15)) if cong > 0 else 0.0,
        bsa=ehr.bsa,
        edv_max=jitter(295.0, 0.07),
    )


# --------------------------------------------------------------------------
# Observation generation and degradation
# --------------------------------------------------------------------------

def degrade_to_observations(
    t: np.ndarray,
    hemo: list,
    cadence_h: float = WARD_CADENCE_H,
    rng: np.random.Generator | None = None,
    missing_rate: float = 0.10,
    noise: bool = True,
) -> list:
    """Turn a dense ground-truth trajectory into what a ward chart would hold.

    This is the Ward Degrader.  It is the heart of the headline experiment:
    take physiology sampled continuously, throw away 94% of it, add the
    measurement error of a busy nurse with a cuff, drop a tenth of the
    readings entirely -- and then ask whether the twin can still see the
    patient sliding.
    """
    rng = rng or np.random.default_rng(0)
    dt = float(t[1] - t[0]) if len(t) > 1 else 0.25
    step = max(1, int(round(cadence_h / dt)))

    obs = []
    for i in range(0, len(t), step):
        if rng.random() < missing_rate:
            continue   # nobody charted this round
        h = hemo[i]
        n = (lambda s: float(rng.normal(0, s))) if noise else (lambda s: 0.0)
        obs.append(
            Observation(
                t=float(t[i]),
                # Manual cuff readings are rounded and imprecise.
                hr=float(np.clip(round(h_hr(h) + n(2.5)), 30, 200)),
                sbp=float(np.clip(round((h.sbp + n(5.0)) / 2) * 2, 50, 260)),
                dbp=float(np.clip(round((h.dbp + n(4.0)) / 2) * 2, 25, 160)),
                spo2=float(np.clip(round(h.spo2 + n(1.2)), 50, 100)),
                resp_rate=float(np.clip(round(h.resp_rate + n(1.8)), 6, 60)),
                # Congestion signs are eyeballed, so they are coarse: JVP to
                # the nearest centimetre, oedema to the nearest half grade,
                # weight to the nearest 100 g.
                jvp_cm=float(np.clip(round(h.jvp_cm + n(1.5)), 2, 30)),
                edema_grade=float(np.clip(round((h.edema_grade + n(0.35)) * 2) / 2, 0, 3)),
                weight_excess_kg=float(
                    np.clip(round((h.weight_excess_kg + n(0.4)) * 10) / 10, -2, 25)
                ),
                # Capillary refill has poor inter-observer agreement.
                crt_s=float(np.clip(round((h.crt_s + n(0.6)) * 2) / 2, 1.0, 8.0)),
            )
        )
    return obs


def h_hr(hemo) -> float:
    """Heart rate implied by the haemodynamics (CO = SV * HR)."""
    return hemo.cardiac_output * 1000.0 / max(hemo.stroke_volume, 1e-6)


def build_ward(
    n_beds: int = 8,
    history_h: float = 16.0,
    seed: int = 7,
    cadence_h: float = WARD_CADENCE_H,
) -> list:
    """Generate a full ward: EHR, true physiology, and the observed chart."""
    from ..core.twin import PatientTwin  # local import avoids a cycle

    rng = np.random.default_rng(seed)
    patients = []

    for idx in range(n_beds):
        arch = _ARCHETYPES[idx % len(_ARCHETYPES)]
        pid = f"P{idx + 1:03d}"
        ehr = _make_ehr(rng, idx, arch, pid)
        params = _true_params(rng, arch, ehr)
        prog, intake = arch[6], arch[7]

        # Run the patient forward to "now" to build their charted history.
        truth_twin = PatientTwin(pid, params, progression_per_h=prog, intake_base=intake)
        hist = truth_twin.project(hours=history_h, dt=0.25)

        # Observation noise gets its own stream.  Sharing the patient stream
        # would make the number of readings (i.e. the cadence) change every
        # later patient's identity, and the cadence comparison would no longer
        # be the same patients charted differently.
        obs_rng = np.random.default_rng([seed, idx, 7919])
        obs = degrade_to_observations(hist.t, hist.hemo, cadence_h=cadence_h, rng=obs_rng)

        patients.append(
            WardPatient(
                ehr=ehr,
                true_params=hist.states[-1],
                progression_per_h=prog,
                intake_base=intake,
                observations=obs,
                truth_trajectory=hist,
            )
        )

    return patients
