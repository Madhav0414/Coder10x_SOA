"""Lumped-parameter cardiovascular model: the twin core.

Two timescales.  The fast one is algebraic: ventricular-arterial coupling
gives stroke volume, output and pressures from the current parameters.  The
slow one is an ODE over hours: circulating volume, pulmonary congestion,
baroreflex control of heart rate and resistance, and disease progression.
A ward patient decompensates over hours, so the slow loop is what we simulate.

Units: volume mL, pressure mmHg, elastance mmHg/mL, SVR mmHg*min/L, heart
rate /min, cardiac output L/min, time hours.

The decompensation spiral is not scripted.  Falling contractility lowers
output and pressure; the baroreflex raises heart rate and resistance; both
raise effective arterial elastance, which lowers stroke volume further.
Narrowing pulse pressure with a rising heart rate falls out of the equations.
"""


from __future__ import annotations

import threading
from dataclasses import dataclass, replace, asdict
from typing import Callable, Sequence

import numpy as np
from scipy.integrate import solve_ivp

# --------------------------------------------------------------------------
# Physiological constants
# --------------------------------------------------------------------------

MAP_SETPOINT = 85.0      # mmHg, baroreflex target
V0_LV = 20.0             # mL, unstressed left-ventricular volume
BSA_DEFAULT = 1.7        # m^2, typical Indian adult body surface area

# Nohria-Stevenson thresholds (clinical standard)
PCWP_WET_THRESHOLD = 18.0    # mmHg  -- above this the patient is "wet"
CI_COLD_THRESHOLD = 2.2      # L/min/m^2 -- below this the patient is "cold"

# Pulmonary filtration
P_FILT_THRESHOLD = 18.0      # mmHg, Starling threshold for lung water
K_FILTRATION = 6.0           # mL/h per mmHg above threshold
K_REABSORPTION = 0.04        # /h

# Baroreflex gains and time constants (hours)
G_HR = 0.9
G_SVR = 0.55
TAU_HR = 0.12
TAU_SVR = 0.30
TAU_EMAX = 2.0

HR_MIN, HR_MAX = 40.0, 165.0
SVR_MIN, SVR_MAX = 6.0, 45.0
EMAX_MIN, EMAX_MAX = 0.15, 4.0


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CardiacParams:
    """Patient-specific physiology.  These are the identity of the twin.

    Every one of these is a knob a cardiac drug can move, which is what makes
    counterfactual simulation possible rather than merely correlational.
    """

    emax: float = 2.1         # contractility (ESPVR slope). HFrEF: 0.4-1.2
    svr: float = 18.0         # systemic vascular resistance
    v_central: float = 400.0  # effective central blood volume above unstressed
    hr: float = 72.0          # heart rate
    c_arterial: float = 1.6   # arterial compliance, mL/mmHg (pulse pressure)
    c_venous: float = 33.0    # venous compliance, mL/mmHg (volume -> PCWP)
    c_diastolic: float = 12.0  # LV diastolic stiffness coeff, mL/mmHg
    v_interstitial: float = 0.0   # pulmonary/peripheral congestion, mL
    bsa: float = BSA_DEFAULT

    # Patient-specific baselines the baroreflex regulates *around*.  A failing
    # heart does not revert to a healthy setpoint, so these travel with the
    # twin rather than being global constants.
    emax_base: float = 2.1
    svr_base: float = 18.0
    hr_base: float = 72.0
    edv_max: float = 210.0    # dilation ceiling -- the flat top of Frank-Starling

    def as_vector(self) -> np.ndarray:
        return np.array(
            [self.emax, self.svr, self.v_central, self.hr, self.v_interstitial],
            dtype=float,
        )

    @staticmethod
    def from_vector(vec: Sequence[float], template: "CardiacParams") -> "CardiacParams":
        emax, svr, v_central, hr, v_int = vec
        return replace(
            template,
            emax=float(emax),
            svr=float(svr),
            v_central=float(v_central),
            hr=float(hr),
            v_interstitial=float(v_int),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Hemodynamics:
    """What the bedside monitor would show, plus what it would not."""

    stroke_volume: float     # mL
    cardiac_output: float    # L/min
    cardiac_index: float     # L/min/m^2
    map: float               # mmHg
    sbp: float               # mmHg
    dbp: float               # mmHg
    pulse_pressure: float    # mmHg
    pcwp: float              # mmHg -- filling pressure (the "wet" axis)
    edv: float               # mL
    ef: float                # ejection fraction, 0-1
    spo2: float              # %, degraded by pulmonary congestion
    resp_rate: float         # breaths/min
    # Congestion is not visible in a blood-pressure cuff.  These are the signs
    # a ward round actually records, and without them filling pressure is not
    # identifiable -- two patients with opposite volume states can produce the
    # same vitals.  Charting them is what keeps the twin from telling a doctor
    # to give fluids to a drowning patient.
    jvp_cm: float            # jugular venous pressure, cm H2O
    edema_grade: float       # 0-3 peripheral oedema
    weight_excess_kg: float  # kg above the patient's dry weight
    # Perfusion is equally invisible to a cuff: the "cold" axis is judged at
    # the bedside from peripheral signs.  Capillary refill lengthens as
    # cardiac index falls.
    crt_s: float             # capillary refill time, seconds

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Fast subsystem: ventricular-arterial coupling
# --------------------------------------------------------------------------

def solve_hemodynamics(p: CardiacParams) -> Hemodynamics:
    """Algebraic steady state for the current parameter set.

    Uses the classical ventricular-arterial coupling result

        SV = Ees * (EDV - V0) / (Ees + Ea)

    where Ea, the effective arterial elastance, is itself a function of heart
    rate and vascular resistance.  Because Ea rises with HR, a baroreflex
    tachycardia increases afterload -- which is precisely why a failing
    ventricle gets worse when it speeds up.
    """
    pcwp = p.v_central / p.c_venous

    # Diastolic pressure-volume relation, saturating.  A ventricle can only
    # dilate so far; past that, extra filling pressure buys almost no extra
    # volume.  That flattening IS the top of the Frank-Starling curve, and it
    # is why a congested patient stops being able to compensate.
    edv = V0_LV + p.edv_max * (1.0 - np.exp(-p.c_diastolic * pcwp / p.edv_max))

    # Effective arterial elastance.  SVR in mmHg*min/L, HR in 1/min -> mmHg/mL
    ea = max(p.hr * p.svr / 1000.0, 0.05)

    # Congestion penalty.  A ventricle held at high filling pressure works
    # against raised wall stress and impaired subendocardial perfusion, and
    # functional mitral regurgitation worsens as the annulus dilates.  Its
    # effective contractility is therefore lower than its intrinsic
    # contractility.  This term is why decongesting a wet patient RAISES
    # cardiac output instead of lowering it -- without it, the model would
    # recommend against diuretics, which is clinically backwards.
    # The coefficient is set so that severe congestion (filling pressure in the
    # low 30s) actually reduces forward output rather than merely failing to
    # improve it -- which is the observed picture in decompensated failure.
    congestion_penalty = 1.0 / (1.0 + 0.055 * max(0.0, pcwp - PCWP_WET_THRESHOLD))
    emax_eff = p.emax * congestion_penalty

    sv = emax_eff * max(edv - V0_LV, 0.0) / (emax_eff + ea)
    sv = float(np.clip(sv, 1.0, 200.0))

    co = sv * p.hr / 1000.0
    ci = co / max(p.bsa, 0.8)
    mean_ap = co * p.svr

    pulse_pressure = sv / max(p.c_arterial, 0.2)
    sbp = mean_ap + (2.0 / 3.0) * pulse_pressure
    dbp = mean_ap - (1.0 / 3.0) * pulse_pressure

    ef = sv / max(edv, 1.0)

    # Pulmonary congestion degrades gas exchange and drives tachypnoea.
    congestion = p.v_interstitial
    spo2 = float(np.clip(98.0 - 0.022 * congestion, 70.0, 100.0))
    resp_rate = float(
        np.clip(14.0 + 0.020 * congestion + 0.10 * max(0.0, 70.0 - mean_ap), 8.0, 48.0)
    )

    # Right-sided filling tracks left-sided filling closely enough for a
    # bedside estimate; JVP is the classic clinical read-out of it.
    jvp = float(np.clip(3.0 + 0.62 * (pcwp - 8.0), 2.0, 26.0))
    edema = float(np.clip(congestion / 320.0, 0.0, 3.0))
    # 1 litre of retained fluid is very nearly 1 kg on the ward scale.
    weight_excess = float(
        max(0.0, (p.v_central - 430.0) + congestion) / 1000.0
    )
    crt = float(np.clip(1.8 + 1.5 * max(0.0, 2.8 - ci), 1.0, 8.0))

    return Hemodynamics(
        stroke_volume=sv,
        cardiac_output=co,
        cardiac_index=ci,
        map=float(mean_ap),
        sbp=float(sbp),
        dbp=float(dbp),
        pulse_pressure=float(pulse_pressure),
        pcwp=float(pcwp),
        edv=float(edv),
        ef=float(ef),
        spo2=spo2,
        resp_rate=resp_rate,
        jvp_cm=jvp,
        edema_grade=edema,
        weight_excess_kg=weight_excess,
        crt_s=crt,
    )


# --------------------------------------------------------------------------
# Slow subsystem: the ODE
# --------------------------------------------------------------------------

DriveFn = Callable[[float], dict]
_LSODA = threading.Lock()


def _zero_drive(_t: float) -> dict:
    return {}


def _derivatives(t: float, y: np.ndarray, p: CardiacParams, drive: DriveFn) -> np.ndarray:
    """Right-hand side of the slow dynamics.

    `drive` returns the current pharmacological effect at time t, expressed as
    multiplicative or additive modifiers.  Keeping drugs in the drive term
    rather than in the state is what lets us re-run the same patient under a
    different treatment and get a genuinely different trajectory.
    """
    state = CardiacParams.from_vector(y, p)
    hemo = solve_hemodynamics(state)
    d = drive(t)

    # --- pulmonary congestion (Starling filtration) ---------------------
    filtration = K_FILTRATION * max(0.0, hemo.pcwp - P_FILT_THRESHOLD)
    # Plasma refill: once filling pressure drops, tissue water returns to the
    # circulation.  This is why decongestion can continue without emptying the
    # vascular compartment, and why diuresing a congested patient is safe in a
    # way that diuresing a dry one is not.
    gradient = max(0.0, (P_FILT_THRESHOLD - hemo.pcwp) / P_FILT_THRESHOLD)
    reabsorption = K_REABSORPTION * state.v_interstitial * (1.0 + 4.0 * gradient)
    d_v_int = filtration - reabsorption

    # --- circulating volume ---------------------------------------------
    # Maintenance intake (oral + IV) runs whether or not anyone orders fluids.
    # When renal perfusion falls, output drops below it and the patient starts
    # retaining -- that imbalance is the cardiorenal engine of decompensation.
    intake = d.get("intake_base", 55.0) + d.get("fluid_rate", 0.0)
    diuresis = d.get("diuresis_rate", 0.0)         # mL/h, drug-driven
    # Baseline renal output falls when perfusion falls -- cardiorenal coupling.
    baseline_uo = 55.0 * float(np.clip(hemo.map / MAP_SETPOINT, 0.25, 1.4))
    # Venodilators redistribute volume into the venous capacitance bed rather
    # than removing it -- same fall in filling pressure, much faster onset.
    preload_shift = d.get("preload_shift", 0.0)
    d_v_central = (
        intake - baseline_uo - diuresis - preload_shift - filtration + reabsorption
    )

    # --- baroreflex + drug chronotropy ----------------------------------
    err = (MAP_SETPOINT - hemo.map) / MAP_SETPOINT
    hr_target = d.get("hr_base", p.hr_base) * (1.0 + G_HR * err) * d.get("chronotropy", 1.0)
    hr_target = float(np.clip(hr_target, HR_MIN, HR_MAX))
    d_hr = (hr_target - state.hr) / TAU_HR

    # --- baroreflex + drug vasoactivity ---------------------------------
    svr_target = d.get("svr_base", p.svr_base) * (1.0 + G_SVR * err) * d.get("vasoactivity", 1.0)
    svr_target = float(np.clip(svr_target, SVR_MIN, SVR_MAX))
    d_svr = (svr_target - state.svr) / TAU_SVR

    # --- contractility: disease trajectory + drug inotropy --------------
    emax_target = d.get("emax_base", p.emax_base) * d.get("inotropy", 1.0)
    emax_target = float(np.clip(emax_target, EMAX_MIN, EMAX_MAX))
    d_emax = (emax_target - state.emax) / TAU_EMAX

    return np.array([d_emax, d_svr, d_v_central, d_hr, d_v_int], dtype=float)


def simulate(
    params: CardiacParams,
    hours: float,
    drive: DriveFn | None = None,
    dt: float = 0.25,
):
    """Roll the twin forward in time.

    Returns a trajectory, not a label.  This is the property that separates a
    digital twin from a risk score: you can ask it what the next twelve hours
    look like, and then ask again under a different decision.
    """
    drive = drive or _zero_drive
    t_eval = np.arange(0.0, hours + 1e-9, dt)

    # LSODA keeps global state and refuses a second problem while one is
    # running; the API answers requests on a thread pool, so take turns.
    with _LSODA:
        sol = solve_ivp(
            _derivatives,
            (0.0, hours),
            params.as_vector(),
            args=(params, drive),
            t_eval=t_eval,
            method="LSODA",
            rtol=1e-5,
            atol=1e-7,
            max_step=0.5,
        )

    lo = np.array([EMAX_MIN, SVR_MIN, 150.0, HR_MIN, 0.0])
    hi = np.array([EMAX_MAX, SVR_MAX, 3000.0, HR_MAX, 6000.0])

    states, hemos = [], []
    for col in sol.y.T:
        st = CardiacParams.from_vector(np.clip(col, lo, hi), params)
        states.append(st)
        hemos.append(solve_hemodynamics(st))

    return sol.t, states, hemos
