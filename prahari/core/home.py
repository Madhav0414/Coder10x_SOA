"""The twin at home: the ward-fitted heart, run over days, read through a band.

The ward identifies the heart.  At home there is only a wrist band and,
sometimes, a bathroom scale, and what changes over days is fluid: a patient
retaining a few hundred millilitres a day slides from compensated to
congested over one to three weeks, usually without noticing.

Physics -- the ward model at its daily quasi-steady state:

  retained fluid E    a fraction reaches the stressed central volume; the rest
                      is peripheral oedema
  filling pressure    central volume over venous compliance
  lung water          Starling balance, filtration = reabsorption
  cardiac index       the same ventricular-arterial coupling, with baroreflex
                      heart rate and resistance at equilibrium

What a band and a scale see, as change from the discharge state:

  weight              +1 kg per litre retained
  resting heart rate  baroreflex response, plus sympathetic drive from lung water
  HRV                 falls with sympathetic drive
  awake in bed        rises with lung water -- orthopnoea, waking breathless
  sleep               falls as time awake in bed rises
  steps               fall with cardiac index and breathlessness on exertion

Weight, filling pressure and lung water come from the model.  The five band
coefficients below are first-order assumptions with the right direction, in
the same spirit as the drug magnitudes in interventions.py.

The twin then works backwards: a grid posterior over when retention started
and how fast it runs, every channel weighted by its own day-to-day noise and
missing days simply absent.  Forward, it asks when the retained fluid reaches
this particular heart's decompensation point -- and how that moves under a
diuretic increase or a fluid restriction.
"""


from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .cardiac import (
    CI_COLD_THRESHOLD,
    G_HR,
    G_SVR,
    HR_MAX,
    HR_MIN,
    K_FILTRATION,
    K_REABSORPTION,
    MAP_SETPOINT,
    PCWP_WET_THRESHOLD,
    SVR_MAX,
    SVR_MIN,
    CardiacParams,
    solve_hemodynamics,
)

NAN = float("nan")

# Of each litre retained, the share that reaches the central circulation and
# lungs; the rest is oedema in the legs and gut.  Set so that a typical heart
# decompensates after about three litres, the usual weight gain before an
# admission.
CENTRAL_FRACTION = 0.15
# Lung water at Starling equilibrium, mL per mmHg of filling pressure above 18.
LUNG_PER_MMHG = K_FILTRATION / K_REABSORPTION
DISCHARGE_PCWP_MIN = 12.0      # the driest anyone is discharged
DISCHARGE_CI = 2.3             # warm enough to go home
EVENT_SPO2 = 92.0              # lung water enough to desaturate at rest

# Band coefficients (first-order assumptions; see module docstring).
# In failing hearts rising filling pressure drives sympathetic tone up (the
# cardiac sympathetic afferent reflex is enhanced), which the baroreflex alone
# would miss: it sees more output from more volume and slows the heart.
HR_PER_MMHG = 1.2              # bpm of resting heart rate per mmHg of filling pressure
HR_PER_ML_LUNG = 0.012         # bpm per mL of lung water (hypoxic drive)
HRV_PER_BPM = 0.03             # fall in log-HRV per bpm of heart-rate rise
HRV_PER_ML_LUNG = 0.0004
AWAKE_PER_MMHG = 3.0           # minutes awake in bed per mmHg -- orthopnoea
AWAKE_PER_ML_LUNG = 0.06       # and per mL of lung water -- waking breathless
SLEEP_LOSS_OF_AWAKE = 0.8      # hours of sleep lost per hour awake in bed
STEPS_CI_EXPONENT = 1.5
STEPS_LUNG_SCALE = 800.0       # mL of lung water that costs e-fold of activity

# Channel order used throughout.  Each is compared on an additive scale:
# steps and HRV as logs, so a change is a proportion.
CHANNELS = ("weight", "rhr", "hrv", "awake", "sleep", "steps")
NOISE_FLOOR = {"weight": 0.25, "rhr": 1.5, "hrv": 0.08, "awake": 8.0, "sleep": 0.35, "steps": 0.25}

# What a change of plan does to net daily retention (mL/day).  First-order,
# like every drug effect in this project.
HOME_PLANS = {
    "no_change": ("No change", 0.0),
    "restrict": ("Fluid restriction 1.5 L/day", 150.0),
    "diuretic": ("Double the loop diuretic", 300.0),
}

E_GRID = np.arange(-600.0, 7001.0, 50.0)


# --------------------------------------------------------------------------
# Physiology at the daily timescale
# --------------------------------------------------------------------------

def _equilibrium(p: CardiacParams, v_central: float, v_int: float):
    """Baroreflex heart rate and resistance at rest, for a given volume state."""
    hr, svr = p.hr_base, p.svr_base
    for _ in range(60):
        h = solve_hemodynamics(replace(p, v_central=v_central, v_interstitial=v_int, hr=hr, svr=svr))
        err = (MAP_SETPOINT - h.map) / MAP_SETPOINT
        hr_t = float(np.clip(p.hr_base * (1.0 + G_HR * err), HR_MIN, HR_MAX))
        svr_t = float(np.clip(p.svr_base * (1.0 + G_SVR * err), SVR_MIN, SVR_MAX))
        if abs(hr_t - hr) < 0.01 and abs(svr_t - svr) < 0.001:
            break
        hr, svr = 0.5 * (hr + hr_t), 0.5 * (svr + svr_t)
    return solve_hemodynamics(replace(p, v_central=v_central, v_interstitial=v_int, hr=hr, svr=svr)), hr


def _split(p: CardiacParams, total_central: float):
    """Share retained volume between circulation and lung at Starling balance."""
    wet_edge = PCWP_WET_THRESHOLD * p.c_venous
    if total_central <= wet_edge:
        return max(150.0, total_central), 0.0
    vc = (total_central + LUNG_PER_MMHG * PCWP_WET_THRESHOLD) / (1.0 + LUNG_PER_MMHG / p.c_venous)
    return vc, max(0.0, total_central - vc)


def discharge_params(p: CardiacParams) -> CardiacParams:
    """The heart that goes home: as dry as it can be while staying warm.

    Diuresis stops when output starts to fall, so a weak heart is sent home
    fuller -- at a higher filling pressure, with less room before it tips
    over.  That, not a population threshold, is what makes one patient's
    three litres another patient's one and a half.  If even a filling
    pressure of 18 leaves the heart cold, it is taken to have recovered just
    enough contractility to be warm there: nobody is discharged cold.
    """
    base = replace(p, emax=p.emax_base, v_interstitial=0.0, hr=p.hr_base, svr=p.svr_base)

    def warm(q, pcwp):
        return _equilibrium(q, pcwp * q.c_venous, 0.0)[0].cardiac_index >= DISCHARGE_CI

    for pcwp in np.arange(DISCHARGE_PCWP_MIN, PCWP_WET_THRESHOLD + 1e-9, 0.25):
        if warm(base, pcwp):
            return replace(base, v_central=float(pcwp * base.c_venous))
    top = replace(base, v_central=PCWP_WET_THRESHOLD * base.c_venous)
    lo, hi = base.emax, 4.0
    for _ in range(40):                      # bisect for the warm threshold
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if warm(replace(top, emax=mid, emax_base=mid), PCWP_WET_THRESHOLD) else (mid, hi)
    return replace(top, emax=hi, emax_base=hi)


@dataclass(frozen=True)
class HomeCurve:
    """One heart's response to retained fluid, tabulated over E_GRID."""

    E: np.ndarray
    pcwp: np.ndarray
    ci: np.ndarray
    hr: np.ndarray
    spo2: np.ndarray
    lung: np.ndarray
    delta: dict           # channel -> expected change from discharge, per E
    event_E: float        # mL retained at which this heart decompensates

    def at(self, name: str, E):
        return np.interp(E, self.E, getattr(self, name))

    def expect(self, channel: str, E):
        return np.interp(E, self.E, self.delta[channel])


def response_curve(p_dis: CardiacParams) -> HomeCurve:
    v0 = p_dis.v_central
    rows = []
    for E in E_GRID:
        vc, vi = _split(p_dis, v0 + CENTRAL_FRACTION * E)
        h, hr = _equilibrium(p_dis, vc, vi)
        rows.append((h.pcwp, h.cardiac_index, hr, h.spo2, vi))
    pcwp, ci, hr, spo2, lung = (np.array(c) for c in zip(*rows))

    i0 = int(np.argmin(np.abs(E_GRID)))
    d_pcwp = np.maximum(0.0, pcwp - pcwp[i0])
    d_hr = (hr - hr[i0]) + HR_PER_MMHG * d_pcwp + HR_PER_ML_LUNG * lung
    awake = AWAKE_PER_MMHG * d_pcwp + AWAKE_PER_ML_LUNG * lung
    delta = {
        "weight": E_GRID / 1000.0,
        "rhr": d_hr,
        "hrv": -HRV_PER_BPM * d_hr - HRV_PER_ML_LUNG * lung,
        "awake": awake,
        "sleep": -SLEEP_LOSS_OF_AWAKE * awake / 60.0,
        # Activity is capped by symptoms: output above discharge does not make
        # anyone walk further, output below it and lung water make them stop.
        "steps": STEPS_CI_EXPONENT * np.log(np.clip(ci / ci[i0], 0.1, 1.0)) - lung / STEPS_LUNG_SCALE,
    }
    bad = ((ci < CI_COLD_THRESHOLD) & (pcwp > PCWP_WET_THRESHOLD)) | (spo2 < EVENT_SPO2)
    bad &= E_GRID > 0
    event_E = float(E_GRID[np.argmax(bad)]) if bad.any() else float(E_GRID[-1])
    return HomeCurve(E=E_GRID, pcwp=pcwp, ci=ci, hr=hr, spo2=spo2, lung=lung,
                     delta=delta, event_E=event_E)


# --------------------------------------------------------------------------
# Reading the band: assimilation
# --------------------------------------------------------------------------

def channel_values(days: list) -> dict:
    """Wearable records -> per-channel arrays on the model's additive scales."""
    def arr(f):
        return np.array([f(d) for d in days], dtype=float)

    def pos_log(x):
        return np.log(x) if x is not None and x == x and x > 0 else NAN

    return {
        "weight": arr(lambda d: d.weight_kg if d.weight_kg is not None else NAN),
        "rhr": arr(lambda d: d.resting_hr),
        "hrv": arr(lambda d: pos_log(d.hrv_ms)),
        "awake": arr(lambda d: getattr(d, "awake_min", NAN)),
        "sleep": arr(lambda d: d.sleep_hours),
        "steps": arr(lambda d: pos_log(d.steps) if d.steps is not None and d.steps > 0 else NAN),
    }


def _noise(y: np.ndarray, floor: float) -> float:
    """Day-to-day noise from first differences, robust to a slow trend."""
    d = np.diff(y[~np.isnan(y)])
    if d.size < 4:
        return floor * 2.0
    mad = float(np.median(np.abs(d - np.median(d)))) * 1.4826 / np.sqrt(2.0)
    return max(floor, mad)


R_GRID = np.arange(25.0, 601.0, 25.0)      # mL/day of net retention
P_STABLE = 0.6                             # prior: most patients stay stable
TEMPER = 0.5                               # day-to-day noise is correlated; do not over-trust it
FORECAST_DAYS = 7                          # "decompensating within a week"
BASELINE_DAYS = 7                          # nothing is flagged in the first week home
BAND_HIGH, BAND_MODERATE = 0.5, 0.2        # on P(decompensation within a week)

# The way each channel moves as fluid builds.
_DIRECTION = {"weight": 1, "rhr": 1, "hrv": -1, "awake": 1, "sleep": -1, "steps": -1}

_WORDS = {
    "weight": ("Weight", "kg", 1.0, 1),
    "rhr": ("Resting heart rate", "bpm", 1.0, 0),
    "hrv": ("HRV", "%", 100.0, 0),
    "awake": ("Awake in bed", "min a night", 1.0, 0),
    "sleep": ("Sleep", "h a night", 1.0, 1),
    "steps": ("Steps", "%", 100.0, 0),
}


@dataclass
class HomeEstimate:
    day: int                   # last day of data
    p_event: float             # P(decompensation within FORECAST_DAYS)
    p_stable: float            # posterior mass on "not retaining"
    days_to_event: float | None   # posterior median, None if beyond 60 days
    interval: tuple            # 10th-90th percentile of days to event
    fluid_ml: float            # estimated fluid retained now
    rate_ml_day: float         # estimated retention rate
    pcwp: float                # filling pressure implied now
    lung_ml: float
    event_E: float             # this heart's decompensation point, mL
    drivers: list              # plain-language evidence, strongest first
    band: str

    @property
    def flagged(self) -> bool:
        return self.band == "high"


class HomeTwin:
    """The ward twin, sent home."""

    def __init__(self, params: CardiacParams):
        self.discharge = discharge_params(params)
        self.curve = response_curve(self.discharge)

    # ------------------------------------------------------------ inverse
    def _hypotheses(self, T: int):
        t = np.arange(1, T + 1, dtype=float)
        d0 = np.arange(0, T, dtype=float)          # retention runs from day d0 on
        rr, dd = np.meshgrid(R_GRID, d0, indexing="ij")
        rate = np.concatenate([[0.0], rr.ravel()])
        onset = np.concatenate([[0.0], dd.ravel()])
        E = rate[:, None] * np.maximum(0.0, t[None, :] - onset[:, None])
        prior = np.concatenate([[P_STABLE], np.full(rr.size, (1.0 - P_STABLE) / rr.size)])
        return rate, onset, E, np.log(prior)

    def _posterior(self, days: list, channels=CHANNELS):
        T = len(days)
        ys = channel_values(days)
        rate, onset, E, logp = self._hypotheses(T)
        ll = np.zeros(len(rate))
        terms = {}
        for c in channels:
            y = ys[c]
            ok = ~np.isnan(y)
            if ok.sum() < 5:
                continue
            sigma = _noise(y, NOISE_FLOOR[c])
            g = self.curve.expect(c, E)                  # hypotheses x days
            resid = np.where(ok[None, :], y[None, :] - g, 0.0)
            base = resid.sum(axis=1) / ok.sum()          # this patient's own level
            r = np.where(ok[None, :], resid - base[:, None], 0.0)
            terms[c] = -0.5 * (r ** 2).sum(axis=1) / sigma ** 2
            ll += terms[c]
        post = logp + TEMPER * ll
        post = np.exp(post - post.max())
        post /= post.sum()
        return rate, E, post, terms, ys

    def _days_ahead(self, E_now, rate):
        with np.errstate(divide="ignore", invalid="ignore"):
            ahead = np.where(rate > 0, (self.curve.event_E - E_now) / rate, np.inf)
        return np.maximum(ahead, 0.0)

    @staticmethod
    def _quantile(values, weights, q):
        order = np.argsort(values)
        cdf = np.cumsum(weights[order])
        return float(values[order][min(len(order) - 1, int(np.searchsorted(cdf, q)))])

    def estimate(self, days: list, channels=CHANNELS) -> HomeEstimate:
        if len(days) <= BASELINE_DAYS:
            return HomeEstimate(day=days[-1].day if days else 0, p_event=0.0, p_stable=1.0,
                                days_to_event=None, interval=(np.inf, np.inf), fluid_ml=0.0,
                                rate_ml_day=0.0, pcwp=float(self.curve.at("pcwp", 0.0)),
                                lung_ml=0.0, event_E=self.curve.event_E,
                                drivers=["Learning this patient's baseline (first week home)."],
                                band="low")
        rate, E, post, terms, ys = self._posterior(days, channels)
        ahead = self._days_ahead(E[:, -1], rate)
        p_event = float(post[ahead <= FORECAST_DAYS].sum())
        med = self._quantile(ahead, post, 0.5)
        fluid = float(post @ E[:, -1])
        band = "high" if p_event >= BAND_HIGH else ("moderate" if p_event >= BAND_MODERATE else "low")
        return HomeEstimate(
            day=days[-1].day,
            p_event=p_event,
            p_stable=float(post[0]),
            days_to_event=None if not np.isfinite(med) or med > 60 else med,
            interval=(self._quantile(ahead, post, 0.1), self._quantile(ahead, post, 0.9)),
            fluid_ml=fluid,
            rate_ml_day=float(post @ rate),
            pcwp=float(self.curve.at("pcwp", fluid)),
            lung_ml=float(self.curve.at("lung", fluid)),
            event_E=self.curve.event_E,
            drivers=self._drivers(post, terms, ys),
            band=band,
        )

    def _drivers(self, post, terms, ys) -> list:
        """Which channels carried the evidence for retention, in plain words."""
        if post[0] > 0.9:
            return ["Nothing on the band or scale suggests fluid is building."]
        w = post[1:] / max(1e-12, post[1:].sum())
        weight = {c: float(w @ (t[1:] - t[0])) for c, t in terms.items()}
        out = []
        for c in sorted(weight, key=weight.get, reverse=True):
            if weight[c] <= 0.5:
                continue
            y = ys[c]
            first, last = y[:BASELINE_DAYS], y[-3:]
            if np.isnan(first).all() or np.isnan(last).all():
                continue
            name, unit, k, dp = _WORDS[c]
            change = float(np.nanmean(last) - np.nanmean(first))
            if change * _DIRECTION[c] <= 0:          # moved the other way: not evidence
                continue
            if c in ("steps", "hrv"):
                change = float(np.expm1(change))
            out.append(f"{name} {change * k:+.{dp}f} {unit} against the first week")
        return out or ["Several small changes together, none decisive alone."]

    # ------------------------------------------------------------ forward
    def plans(self, days: list, horizon: int = 21, channels=CHANNELS) -> dict:
        """The same forecast under each home plan: the ward's wait-or-act, in days.

        A plan lowers net daily retention from today on; fluid already
        retained stays until the lower rate carries it back down.
        """
        rate, E, post, _, _ = self._posterior(days, channels)
        E_now = E[:, -1]
        t = np.arange(0, horizon + 1, dtype=float)
        out = {"past": (post @ E).tolist()}         # fluid the twin believes was retained, by day
        for key, (label, cut) in HOME_PLANS.items():
            r = rate - cut
            path = np.maximum(E_now[:, None] + r[:, None] * t[None, :], -300.0)
            ahead = self._days_ahead(E_now, r)
            out[key] = {
                "label": label,
                "cut_ml_day": cut,
                "p_event_14d": float(post[ahead <= 14].sum()),
                "days_to_event": self._quantile(ahead, post, 0.5),
                "fluid_path": (post @ path).tolist(),
                "pcwp_path": self.curve.at("pcwp", post @ path).tolist(),
            }
        return out

    def history(self, days: list, channels=CHANNELS) -> list:
        """P(decompensation within a week), as the twin would have said each day."""
        return [(k, self.estimate(days[:k], channels).p_event)
                for k in range(BASELINE_DAYS + 1, len(days) + 1)]


# --------------------------------------------------------------------------
# A patient sent home (simulation, for the evidence and the demo)
# --------------------------------------------------------------------------

def real_deviations(days: list) -> dict:
    """A real wearer's day-to-day variation, centred: the noise a signal must beat."""
    ys = channel_values(days)
    return {c: ys[c] - np.nanmean(ys[c]) for c in ("rhr", "awake", "sleep", "steps")
            if not np.isnan(ys[c]).all()}


def overlay_home(curve: HomeCurve, days: list, onset: float, rate: float,
                 rng: np.random.Generator):
    """A real wearer's record with this heart's response to retention laid over it.

    The real band channels keep their real noise and gaps; the change the
    twin's physiology predicts is added on top.  The scale and HRV, which the
    public data lacks, are simulated around the same fluid path.
    Returns (days, fluid retained per day).
    """
    n = len(days)
    t = np.arange(1, n + 1, dtype=float)
    E = rate * np.maximum(0.0, t - onset)
    x = {c: curve.expect(c, E) for c in CHANNELS}
    w0, hrv0 = float(rng.uniform(55, 80)), float(rng.uniform(22, 40))
    wander = np.cumsum(rng.normal(0, 0.12, n)) + rng.normal(0, 0.35, n)
    hn = rng.normal(0, 0.15, n)
    for i in range(1, n):
        hn[i] += 0.4 * hn[i - 1]
    out = []
    for i, d in enumerate(days):
        steps = int(d.steps * np.exp(x["steps"][i])) if d.steps is not None and d.steps > 0 else d.steps
        awake = d.awake_min
        out.append(replace(
            d,
            resting_hr=d.resting_hr + x["rhr"][i],
            sleep_hours=d.sleep_hours + x["sleep"][i],
            awake_min=max(0.0, awake + x["awake"][i]) if awake == awake else awake,
            steps=steps,
            weight_kg=float(w0 + x["weight"][i] + wander[i]),
            hrv_ms=float(hrv0 * np.exp(x["hrv"][i] + hn[i])),
        ))
    return out, E


def simulate_home(curve: HomeCurve, n_days: int, onset: float, rate: float,
                  rng: np.random.Generator, noise: dict | None = None,
                  base: dict | None = None, scale: bool = True):
    """Days at home for a heart retaining `rate` mL/day from `onset`.

    `noise` takes a real wearer's centred variation (from real_deviations),
    including their missing days.  HRV and weight are always simulated: the
    public band data has no HRV and almost no scale readings.
    Returns (days, true fluid retained per day).
    """
    from .continuity import WearableDay

    base = {"weight": 68.0, "rhr": 64.0, "hrv": 30.0, "awake": 35.0, "sleep": 6.8,
            "steps": 5000.0, **(base or {})}
    t = np.arange(1, n_days + 1, dtype=float)
    E = rate * np.maximum(0.0, t - onset)
    dev = {}
    for c, sd, ar in (("rhr", 2.2, 0.5), ("awake", 18.0, 0.2), ("sleep", 0.8, 0.1), ("steps", 0.45, 0.3)):
        if noise and c in noise:
            dev[c] = np.resize(noise[c], n_days)
        else:
            e = rng.normal(0, sd, n_days)
            for i in range(1, n_days):
                e[i] += ar * e[i - 1]
            dev[c] = e
    if not noise:                                     # the band is not always worn
        off = rng.random(n_days) < 0.08
        for c in dev:
            dev[c] = np.where(off, NAN, dev[c])
    hrv_noise = rng.normal(0, 0.15, n_days)
    for i in range(1, n_days):
        hrv_noise[i] += 0.4 * hrv_noise[i - 1]
    wander = np.cumsum(rng.normal(0, 0.12, n_days)) + rng.normal(0, 0.35, n_days)

    x = {c: curve.expect(c, E) for c in CHANNELS}
    out = []
    for i in range(n_days):
        steps = base["steps"] * np.exp(x["steps"][i] + dev["steps"][i])
        awake = dev["awake"][i]
        out.append(WearableDay(
            day=i + 1,
            resting_hr=float(base["rhr"] + x["rhr"][i] + dev["rhr"][i]),
            hrv_ms=float(base["hrv"] * np.exp(x["hrv"][i] + hrv_noise[i])),
            steps=int(steps) if steps == steps else -1,
            sleep_hours=float(base["sleep"] + x["sleep"][i] + dev["sleep"][i]),
            night_hr_rise=NAN,
            weight_kg=float(base["weight"] + x["weight"][i] + wander[i]) if scale else NAN,
            awake_min=float(max(0.0, base["awake"] + x["awake"][i] + awake)) if awake == awake else NAN,
        ))
    return out, E
