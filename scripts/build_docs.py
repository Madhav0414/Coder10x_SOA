"""Build the slide deck and the architecture diagram from the experiment results.

    python scripts/run_experiments.py     # produces docs/results.json
    python scripts/build_docs.py          # produces the PDFs below

Outputs
    docs/presentation.pdf     the deck, every figure filled from results.json
    docs/architecture.pdf     the architecture diagram
    docs/img/architecture.png

Team details come from docs/src/team.json if present:
    {"team": "...", "college": "...", "repo": "github.com/..."}

Rendering uses a local Chromium browser (Edge or Chrome) in headless mode.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DOCS = ROOT / "docs"
SRC = DOCS / "src"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "chromium", "chromium-browser", "msedge",
]


def browser() -> str | None:
    for b in BROWSERS:
        if Path(b).exists() or shutil.which(b):
            return b
    return None


def pct(x) -> str:
    return "—" if x is None else f"{100 * x:.0f}%"


def bed6_wait_or_act() -> dict:
    """Hours cold-wet in the next 14 h for the Bed 6 demo, from the same code
    path the app's what-if endpoint uses (seed 7, 22:00, furosemide, 4 h delay)."""
    from prahari.core.interventions import Plan
    from prahari.data.ward import build_ward
    from prahari.pipeline import fit_ward_at

    patients = build_ward(n_beds=8, seed=7, history_h=30.0, cadence_h=4.0)
    for wp, twin, _e, _v in fit_ward_at(patients, clock=16.0, horizon_h=12.0):
        if wp.ehr.bed == "Bed 6":
            out = {}
            for key, plan in (("nothing", Plan()), ("later", Plan.of("furosemide", at=4.0)),
                              ("now", Plan.of("furosemide", at=0.0))):
                tr = twin.project(hours=14.0, plan=plan)
                hours = sum(0.25 for h in tr.hemo if h.cardiac_index < 2.2 and h.pcwp > 18)
                # Round half up, as the browser does, so the deck matches the app.
                out[key] = f"{math.floor(hours * 10 + 0.5) / 10:.1f}"
            return out
    raise RuntimeError("Bed 6 not found")


def curve_svg(curves: dict) -> str:
    """Sensitivity against alarm burden for both systems, as inline SVG."""
    W, H, L, R, T, B = 520, 360, 58, 18, 18, 52
    xs = [p["alarms_per_patient_day"] for s in ("news2", "prahari") for p in curves[s]]
    x0, x1 = min(xs) - 0.1, max(xs) + 0.1
    y0, y1 = 0.2, 1.0
    sx = lambda v: L + (v - x0) / (x1 - x0) * (W - L - R)
    sy = lambda v: T + (1 - (v - y0) / (y1 - y0)) * (H - T - B)
    g = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
         f'style="font-family:IBM Plex Mono,monospace">']
    for v in (0.2, 0.4, 0.6, 0.8, 1.0):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" stroke="#1b2632"/>')
        g.append(f'<text x="{L - 8}" y="{sy(v) + 4:.1f}" fill="#5f6f7e" font-size="12" text-anchor="end">{int(v * 100)}%</text>')
    step = 0.5
    v = round(x0 / step) * step
    while v <= x1:
        if v >= x0:
            g.append(f'<text x="{sx(v):.1f}" y="{H - B + 20}" fill="#5f6f7e" font-size="12" text-anchor="middle">{v:.1f}</text>')
        v += step
    g.append(f'<text x="{(L + W - R) / 2}" y="{H - 10}" fill="#9fb0bf" font-size="12.5" text-anchor="middle">alarms per patient-day</text>')
    g.append(f'<text x="16" y="{(T + H - B) / 2}" fill="#9fb0bf" font-size="12.5" text-anchor="middle" '
             f'transform="rotate(-90 16 {(T + H - B) / 2})">deteriorations caught before onset</text>')
    for sysname, col in (("news2", "#9fb0bf"), ("prahari", "#2be374")):
        pts = sorted((p["alarms_per_patient_day"], p["sensitivity"]) for p in curves[sysname])
        d = "M" + " L".join(f"{sx(a):.1f} {sy(b):.1f}" for a, b in pts)
        g.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2.6"/>')
        for a, b in pts:
            g.append(f'<circle cx="{sx(a):.1f}" cy="{sy(b):.1f}" r="3.6" fill="{col}"/>')
    g.append(f'<text x="{W - R}" y="{sy(0.33):.1f}" fill="#9fb0bf" font-size="13" text-anchor="end">NEWS2 thresholds</text>')
    g.append(f'<text x="{W - R}" y="{sy(0.26):.1f}" fill="#2be374" font-size="13" text-anchor="end">Prahari horizons</text>')
    g.append("</svg>")
    return "".join(g)


def main() -> None:
    res = json.loads((DOCS / "results.json").read_text(encoding="utf-8"))
    team = {"team": "[TEAM NAME]", "college": "[COLLEGE / INCUBATOR]", "repo": "[PUBLIC GITHUB LINK]"}
    if (SRC / "team.json").exists():
        team.update(json.loads((SRC / "team.json").read_text(encoding="utf-8")))

    sr, cf, oc = res["state_recovery"], res["counterfactual"], res["operating_curves"]
    names = {"A": "A — warm, dry", "B": "B — warm, wet", "L": "L — cold, dry", "C": "C — cold, wet"}
    rows = []
    for q in "ABLC":
        d = cf["by_true_quadrant"][q]
        if d["n"]:
            rows.append(f'<tr><td>{names[q]}</td><td class="r">{pct(d["furo_helps"] / d["n"])}</td>'
                        f'<td class="r">{pct(d["furo_harms"] / d["n"])}</td>'
                        f'<td class="r">{pct(d["twin_right"] / d["n"])}</td></tr>')
    bed6 = bed6_wait_or_act()

    fill = {
        "TEAM": team["team"], "COLLEGE": team["college"], "REPO": team["repo"],
        "WARDS": str(res["wards"]),
        "N_STATES": str(sr["ward"]["n"]),
        "ICU_Q": pct(sr["icu"]["quadrant_acc"]), "ICU_WET": pct(sr["icu"]["wet_acc"]),
        "ICU_COLD": pct(sr["icu"]["cold_acc"]), "ICU_CI": f'{sr["icu"]["ci_mae"]:.2f}',
        "ICU_PCWP": f'{sr["icu"]["pcwp_mae"]:.1f} mmHg',
        "W_Q": pct(sr["ward"]["quadrant_acc"]), "W_WET": pct(sr["ward"]["wet_acc"]),
        "W_COLD": pct(sr["ward"]["cold_acc"]), "W_CI": f'{sr["ward"]["ci_mae"]:.2f}',
        "W_PCWP": f'{sr["ward"]["pcwp_mae"]:.1f} mmHg',
        "DIR": pct(cf["direction_accuracy"]), "DIR_N": f'{cf["direction_n"]:,}',
        "CORR": f'{cf["effect_ci_corr"]:.2f}',
        "REGRET_X": f'{cf["regret_news2_reflex"] / max(cf["regret_twin"], 1e-3):.0f}',
        "HARM_ROWS": "\n    ".join(rows),
        "EVENTS": str(oc["events"]),
        "CURVE_SVG": curve_svg(oc),
        "BED6_NOTHING": bed6["nothing"], "BED6_LATER": bed6["later"], "BED6_NOW": bed6["now"],
    }
    rm, home = res.get("real_mimic"), res.get("home")
    if rm is None or home is None or "real" not in home:
        raise SystemExit("results.json lacks real-data or home results; run scripts/run_experiments.py")
    ho = rm["held_out"]
    sb, so = home["simulated"]["band_and_scale"], home["simulated"]["band_only"]
    caught = lambda x: f'{x["detected"]} of {x["eligible"]}'
    lead = lambda x: "—" if x["median_lead_days"] is None else f'{x["median_lead_days"]:.1f}'
    fill.update({
        "RM_STAYS": str(rm["cohort"]["stays"]),
        "RM_CVP_STAYS": str(ho["cvp"]["stays"]), "RM_CVP_R": f'{ho["cvp"]["r"]:.2f}',
        "RM_CI_STAYS": str(ho["ci"]["stays"]), "RM_CI_R": f'{ho["ci"]["r"]:.2f}',
        "RM_PA_STAYS": str(ho["pa_dia"]["stays"]), "RM_PA_R": f'{ho["pa_dia"]["r"]:.2f}',
        "RM_FC_TWIN": f'{rm["forecast"]["map"]["twin"]:.1f}',
        "RM_FC_PERS": f'{rm["forecast"]["map"]["persistence"]:.1f}',
        "H_PATIENTS": str(home["cohort"]["patients"]),
        "H_W3": caught(sb["weight_3d"]), "H_W3_LEAD": lead(sb["weight_3d"]),
        "H_WD": caught(sb["weight_dry"]), "H_WD_LEAD": lead(sb["weight_dry"]),
        "H_TW": caught(sb["twin"]), "H_TW_LEAD": lead(sb["twin"]),
        "H_TB": caught(so["twin"]), "H_TB_LEAD": lead(so["twin"]),
        "H_RFA_S": f'{100 * home["real"]["band_and_scale"]["twin"]["false_alarm_rate"]:.1f}%',
        "H_RFA_B": f'{100 * home["real"]["band_only"]["twin"]["false_alarm_rate"]:.1f}%',
        "H_RANGE": "{:.1f}–{:.1f}".format(*(x / 1000 for x in home["event_fluid"]["true_range_ml"])),
        "H_MAE": f'{home["at_alarm"]["days_to_event_mae"]:.1f}',
        "H_COV": f'{100 * home["at_alarm"]["interval_coverage"]:.0f}%',
    })
    html = (SRC / "deck.html").read_text(encoding="utf-8")
    for k, v in fill.items():
        html = html.replace("{{" + k + "}}", v)
    missing = [line for line in html.splitlines() if "{{" in line]
    if missing:
        raise SystemExit(f"unfilled placeholders: {missing[:3]}")
    built = SRC / "deck.built.html"
    built.write_text(html, encoding="utf-8")

    b = browser()
    if not b:
        print(f"No Chromium browser found; open {built} and print to PDF manually.")
        return
    common = [b, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=4000"]
    jobs = [
        (built, DOCS / "presentation.pdf"),
        (SRC / "architecture.html", DOCS / "architecture.pdf"),
    ]
    for src, out in jobs:
        subprocess.run(common + [f"--print-to-pdf={out}", src.as_uri()], capture_output=True, timeout=120)
        print(f"  {out.relative_to(ROOT)}  {out.stat().st_size // 1024} KB")
    (DOCS / "img").mkdir(exist_ok=True)
    subprocess.run([b, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1123,794",
                    "--virtual-time-budget=3000", f"--screenshot={DOCS / 'img' / 'architecture.png'}",
                    (SRC / "architecture.html").as_uri()], capture_output=True, timeout=120)
    print("  docs/img/architecture.png")
    print(f"\nBed 6 demo — hours cold-wet of next 14: nothing {bed6['nothing']}, "
          f"in 4 h {bed6['later']}, now {bed6['now']}")


if __name__ == "__main__":
    main()
