/* Small SVG chart kit, each chart drawn for one job:
 *   trend     -- charted observations, then the twin's forecast with a band
 *   quadrant  -- the Nohria-Stevenson map with the projected path
 *   lines     -- a few trajectories on a shared axis (wait vs act, curves)
 *   spark     -- one wearable signal over thirty days at home
 *   ring      -- one value against its clinical threshold
 *   donut     -- how the ward splits across the four profiles
 * Colours are CSS custom properties, so every chart follows the day/night theme
 * without being redrawn.
 */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const ADMIT_HOUR = 6;   // t = 0 is 06:00 on day 1 of the stay
  const PAINT = new Set(["fill", "stroke"]);

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) {
      const v = attrs[k];
      // var(--x) is not valid in a presentation attribute; it is in a style.
      if (PAINT.has(k) && String(v).startsWith("var(")) n.style.setProperty(k, v);
      else n.setAttribute(k, v);
    }
    if (parent) parent.appendChild(n);
    return n;
  }

  function clock(h) {
    const abs = ADMIT_HOUR + h;
    const day = Math.floor(abs / 24) + 1;
    const hh = Math.floor(abs % 24);
    const mm = Math.floor((abs * 60) % 60);
    return { day, hhmm: String(hh).padStart(2, "0") + ":" + String(mm).padStart(2, "0") };
  }

  function dur(h) {
    if (h == null) return "";
    const m = Math.max(0, Math.round(h * 60));
    return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
  }

  function frame(host, w, h, pad) {
    host.innerHTML = "";
    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, class: "chart", role: "img" }, host);
    return { svg, x0: pad[3], x1: w - pad[1], y0: pad[0], y1: h - pad[2] };
  }

  function path(points) {
    let d = "";
    let pen = false;
    for (const [x, y] of points) {
      if (x == null || y == null || !isFinite(y)) { pen = false; continue; }
      d += (pen ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      pen = true;
    }
    return d;
  }

  const widthOf = (host, fallback) => Math.max(200, Math.round(host.clientWidth || fallback));

  function niceTicks(lo, hi, n) {
    const span = hi - lo;
    const raw = span / n;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(3));
    return out;
  }

  // ------------------------------------------------------------ trend ---
  function trend(host, cfg) {
    const W = widthOf(host, 420), H = cfg.height || 150;
    // Widen the given range to whatever the data actually reach.
    const vals = [
      ...(cfg.past || []).map((p) => p.v),
      ...(cfg.forecast ? cfg.forecast.v : []),
      ...(cfg.band ? [...cfg.band.lo, ...cfg.band.hi] : []),
    ].filter((v) => v != null && isFinite(v));
    if (vals.length) {
      const pad = (Math.max(...vals) - Math.min(...vals)) * 0.08 + 1e-6;
      cfg = { ...cfg, lo: Math.min(cfg.lo, Math.min(...vals) - pad), hi: Math.max(cfg.hi, Math.max(...vals) + pad) };
    }
    const f = frame(host, W, H, [10, 34, 20, 4]);
    const { svg } = f;
    const sx = (t) => f.x0 + ((t - cfg.t0) / (cfg.t1 - cfg.t0)) * (f.x1 - f.x0);
    const sy = (v) => f.y1 - ((v - cfg.lo) / (cfg.hi - cfg.lo)) * (f.y1 - f.y0);

    for (const v of niceTicks(cfg.lo, cfg.hi, 4)) {
      el("line", { x1: f.x0, x2: f.x1, y1: sy(v), y2: sy(v), class: "grid" }, svg);
      el("text", { x: f.x1 + 6, y: sy(v) + 3 }, svg).textContent = v;
    }
    for (let t = Math.ceil(cfg.t0 / 4) * 4; t <= cfg.t1; t += 4) {
      el("text", { x: sx(t), y: H - 4, "text-anchor": "middle" }, svg).textContent = clock(t).hhmm;
    }
    for (const th of cfg.thresholds || []) {
      el("line", { x1: f.x0, x2: f.x1, y1: sy(th.v), y2: sy(th.v), stroke: th.color || "var(--ink-3)",
                   "stroke-dasharray": "3 3", "stroke-width": 1.2 }, svg);
    }
    if (cfg.band) {
      const up = cfg.band.t.map((t, i) => [sx(t), sy(cfg.band.hi[i])]);
      const dn = cfg.band.t.map((t, i) => [sx(t), sy(cfg.band.lo[i])]).reverse();
      el("path", { d: path(up) + path(dn).replace(/^M/, "L") + "Z",
                   fill: cfg.color, "fill-opacity": 0.12, stroke: "none" }, svg);
    }
    if (cfg.forecast) {
      el("path", { d: path(cfg.forecast.t.map((t, i) => [sx(t), sy(cfg.forecast.v[i])])),
                   fill: "none", stroke: cfg.color, "stroke-width": 1.8,
                   "stroke-dasharray": "5 4" }, svg);
    }
    if (cfg.past && cfg.past.length) {
      el("path", { d: path(cfg.past.map((p) => [sx(p.t), sy(p.v)])),
                   fill: "none", stroke: cfg.color, "stroke-width": 1.6, opacity: 0.5 }, svg);
      for (const p of cfg.past) {
        el("circle", { cx: sx(p.t), cy: sy(p.v), r: 3.4, fill: "var(--card)", stroke: cfg.color,
                       "stroke-width": 2 }, svg);
      }
    }
    if (cfg.now != null) {
      el("line", { x1: sx(cfg.now), x2: sx(cfg.now), y1: f.y0 - 2, y2: f.y1,
                   stroke: "var(--ink-3)", "stroke-width": 1, "stroke-dasharray": "2 2" }, svg);
      el("text", { x: sx(cfg.now) + 4, y: f.y0 + 6, class: "strong" }, svg).textContent = "now";
    }
  }

  // --------------------------------------------------------- quadrant ---
  const PCWP_WET = 18, CI_COLD = 2.2;

  function quadrant(host, cfg) {
    const W = widthOf(host, 460), H = Math.round(Math.min(400, W * 0.7));
    const f = frame(host, W, H, [8, 10, 36, 44]);
    const { svg } = f;
    const X = [4, 40], Y = [0.8, 4.6];
    const sx = (v) => f.x0 + ((v - X[0]) / (X[1] - X[0])) * (f.x1 - f.x0);
    const sy = (v) => f.y1 - ((Math.max(Y[0], Math.min(Y[1], v)) - Y[0]) / (Y[1] - Y[0])) * (f.y1 - f.y0);

    const zones = [
      [X[0], PCWP_WET, CI_COLD, Y[1], "var(--qA)", "A · Warm & dry"],
      [PCWP_WET, X[1], CI_COLD, Y[1], "var(--qB)", "B · Warm & wet"],
      [X[0], PCWP_WET, Y[0], CI_COLD, "var(--qL)", "L · Cold & dry"],
      [PCWP_WET, X[1], Y[0], CI_COLD, "var(--qC)", "C · Cold & wet"],
    ];
    for (const [a, b, c, d, col, lab] of zones) {
      el("rect", { x: sx(a), y: sy(d), width: sx(b) - sx(a), height: sy(c) - sy(d),
                   fill: col, "fill-opacity": 0.08 }, svg);
      const lx = a < PCWP_WET ? sx(a) + 10 : sx(b) - 10;
      const ly = d > CI_COLD ? sy(d) + 18 : sy(c) - 10;
      el("text", { x: lx, y: ly, fill: col, class: "zone",
                   "text-anchor": a < PCWP_WET ? "start" : "end" }, svg).textContent = lab;
    }
    el("line", { x1: sx(PCWP_WET), x2: sx(PCWP_WET), y1: f.y0, y2: f.y1, stroke: "var(--line-2)" }, svg);
    el("line", { x1: f.x0, x2: f.x1, y1: sy(CI_COLD), y2: sy(CI_COLD), stroke: "var(--line-2)" }, svg);

    for (const v of [10, 18, 26, 34]) {
      el("text", { x: sx(v), y: f.y1 + 15, "text-anchor": "middle" }, svg).textContent = v;
    }
    for (const v of [1.0, 1.5, 2.2, 3.0, 4.0]) {
      el("text", { x: f.x0 - 7, y: sy(v) + 3, "text-anchor": "end" }, svg).textContent = v.toFixed(1);
    }
    el("text", { x: (f.x0 + f.x1) / 2, y: H - 3, "text-anchor": "middle", class: "axis-label" }, svg)
      .textContent = "Filling pressure, mmHg  →  congestion";
    const yl = el("text", { x: 12, y: (f.y0 + f.y1) / 2, "text-anchor": "middle", class: "axis-label",
                            transform: `rotate(-90 12 ${(f.y0 + f.y1) / 2})` }, svg);
    yl.textContent = "Cardiac index, L/min/m²  →  perfusion";

    const defs = el("defs", {}, svg);
    for (const [id, col] of [["arR", "var(--crit)"], ["arG", "var(--ok)"]]) {
      const m = el("marker", { id, viewBox: "0 0 10 10", refX: 7, refY: 5, markerWidth: 7,
                               markerHeight: 7, orient: "auto-start-reverse" }, defs);
      el("path", { d: "M0 0L10 5L0 10z", fill: col }, m);
    }

    function draw(p, col, dash, marker, side) {
      const pts = p.pcwp.map((v, i) => [sx(v), sy(p.ci[i])]);
      el("path", { d: path(pts), fill: "none", stroke: col, "stroke-width": 2.6,
                   "stroke-dasharray": dash || "none", "marker-end": `url(#${marker})`,
                   "stroke-linecap": "round" }, svg);
      if (p.t) {
        const t0 = p.t[0];
        for (let i = 0; i < p.t.length; i++) {
          const rel = p.t[i] - t0;
          if (rel > 0.01 && Math.abs(rel / 4 - Math.round(rel / 4)) < 1e-3) {
            el("circle", { cx: pts[i][0], cy: pts[i][1], r: 3, fill: col }, svg);
            el("text", { x: pts[i][0] + (side < 0 ? -7 : 7), y: pts[i][1] + (side < 0 ? -7 : 14),
                         fill: col, class: "strong",
                         "text-anchor": side < 0 ? "end" : "start" }, svg).textContent = "+" + rel + "h";
          }
        }
      }
    }
    if (cfg.best) draw(cfg.best, "var(--ok)", "6 5", "arG", -1);
    if (cfg.path) draw(cfg.path, "var(--crit)", null, "arR", 1);
    if (cfg.now) {
      el("circle", { cx: sx(cfg.now.pcwp), cy: sy(cfg.now.ci), r: 11, fill: "var(--twin)",
                     "fill-opacity": 0.16 }, svg);
      el("circle", { cx: sx(cfg.now.pcwp), cy: sy(cfg.now.ci), r: 6, fill: "var(--card)",
                     stroke: "var(--twin)", "stroke-width": 2.6 }, svg);
      el("text", { x: sx(cfg.now.pcwp) + 14, y: sy(cfg.now.ci) + 4, fill: "var(--twin)",
                   class: "strong" }, svg).textContent = "now";
    }
  }

  // ------------------------------------------------------------ lines ---
  function lines(host, cfg) {
    const W = widthOf(host, 480), H = cfg.height || 220;
    const f = frame(host, W, H, [12, 38, 22, 4]);
    const { svg } = f;
    const sx = (t) => f.x0 + ((t - cfg.t0) / (cfg.t1 - cfg.t0)) * (f.x1 - f.x0);
    const sy = (v) => f.y1 - ((Math.max(cfg.lo, Math.min(cfg.hi, v)) - cfg.lo) / (cfg.hi - cfg.lo)) * (f.y1 - f.y0);
    const xfmt = cfg.xfmt || ((t) => clock(t).hhmm);
    const xstep = cfg.xstep || 2;

    for (const v of niceTicks(cfg.lo, cfg.hi, 4)) {
      el("line", { x1: f.x0, x2: f.x1, y1: sy(v), y2: sy(v), class: "grid" }, svg);
      el("text", { x: f.x1 + 6, y: sy(v) + 3 }, svg).textContent = cfg.yfmt ? cfg.yfmt(v) : v;
    }
    for (let t = Math.ceil(cfg.t0 / xstep - 1e-9) * xstep; t <= cfg.t1 + 1e-9; t += xstep) {
      el("text", { x: sx(t), y: H - 5, "text-anchor": "middle" }, svg).textContent = xfmt(t);
    }
    if (cfg.threshold) {
      el("line", { x1: f.x0, x2: f.x1, y1: sy(cfg.threshold.v), y2: sy(cfg.threshold.v),
                   stroke: "var(--crit)", "stroke-dasharray": "3 3", "stroke-width": 1.2 }, svg);
      el("text", { x: f.x0 + 4, y: sy(cfg.threshold.v) + 13, fill: "var(--crit)", class: "strong" }, svg)
        .textContent = cfg.threshold.label;
    }
    // Stagger labels so markers a few days apart do not print over each other.
    [...(cfg.markers || [])].sort((a, b) => a.t - b.t).forEach((m, i) => {
      el("line", { x1: sx(m.t), x2: sx(m.t), y1: f.y0, y2: f.y1, stroke: m.color,
                   "stroke-dasharray": "2 3", "stroke-width": 1.2 }, svg);
      el("text", { x: sx(m.t) + 4, y: f.y0 + 9 + (i % 2) * 13, fill: m.color, class: "strong" }, svg)
        .textContent = m.label;
    });
    for (const s of cfg.series) {
      // A missing value is a gap in the line, never a zero.
      el("path", { d: path(s.t.map((t, i) => [sx(t), s.v[i] == null ? null : sy(s.v[i])])), fill: "none",
                   stroke: s.color, "stroke-width": s.width || 2.4,
                   "stroke-dasharray": s.dash || "none", "stroke-linecap": "round",
                   "stroke-linejoin": "round" }, svg);
    }
  }

  // ------------------------------------------------------------ spark ---
  function spark(host, cfg) {
    const W = widthOf(host, 260), H = cfg.height || 86;
    const f = frame(host, W, H, [8, 30, 16, 2]);
    const { svg } = f;
    const vals = cfg.v.filter((v) => v != null && isFinite(v));
    if (!vals.length) return;
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.05 || 1;
    lo -= pad; hi += pad;
    const t0 = cfg.t[0], t1 = cfg.t[cfg.t.length - 1];
    const sx = (t) => f.x0 + ((t - t0) / (t1 - t0 || 1)) * (f.x1 - f.x0);
    const sy = (v) => f.y1 - ((v - lo) / (hi - lo)) * (f.y1 - f.y0);
    const fmt = cfg.fmt || ((v) => Math.round(v));

    for (const v of [lo + pad, hi - pad]) {
      el("line", { x1: f.x0, x2: f.x1, y1: sy(v), y2: sy(v), class: "grid" }, svg);
      el("text", { x: f.x1 + 5, y: sy(v) + 3 }, svg).textContent = fmt(v);
    }
    for (const d of [t0, 10, 20, t1]) {
      if (d < t0 || d > t1) continue;
      el("text", { x: sx(d), y: H - 3, "text-anchor": d === t0 ? "start" : d === t1 ? "end" : "middle" }, svg)
        .textContent = "day " + d;
    }
    if (cfg.marker != null) {
      el("line", { x1: sx(cfg.marker), x2: sx(cfg.marker), y1: f.y0, y2: f.y1, stroke: "var(--warn)",
                   "stroke-dasharray": "2 3", "stroke-width": 1.2 }, svg);
    }
    if (cfg.today != null && cfg.today < t1) {
      el("rect", { x: sx(cfg.today), y: f.y0, width: Math.max(0, f.x1 - sx(cfg.today)), height: f.y1 - f.y0,
                   fill: "var(--card-2)", "fill-opacity": 0.75 }, svg);
      el("line", { x1: sx(cfg.today), x2: sx(cfg.today), y1: f.y0, y2: f.y1, stroke: "var(--ink-3)",
                   "stroke-width": 1 }, svg);
    }
    const pts = cfg.t.map((t, i) => [sx(t), cfg.v[i] == null ? null : sy(cfg.v[i])]);
    el("path", { d: path(pts), fill: "none", stroke: cfg.color, "stroke-width": 2,
                 "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    // A reading with no neighbour draws no line, so mark it.
    pts.forEach(([x, y], i) => {
      if (y == null) return;
      const lone = (i === 0 || pts[i - 1][1] == null) && (i === pts.length - 1 || pts[i + 1][1] == null);
      if (lone) el("circle", { cx: x, cy: y, r: 2.6, fill: cfg.color }, svg);
    });
  }

  // ------------------------------------------------------------- ring ---
  // A 270-degree gauge.  `bad(v)` picks the warning colour; the tick marks
  // the clinical threshold so the eye reads distance-to-threshold, not a number.
  function ring(host, cfg) {
    const S = cfg.size || 132, r = S / 2 - 11, c = S / 2;
    host.innerHTML = "";
    const svg = el("svg", { viewBox: `0 0 ${S} ${S}`, class: "ring", role: "img",
                            width: S, height: S }, host);
    const a0 = 135, sweep = 270;
    const pt = (deg) => [c + r * Math.cos((deg * Math.PI) / 180), c + r * Math.sin((deg * Math.PI) / 180)];
    const arc = (from, to) => {
      const [x0, y0] = pt(from), [x1, y1] = pt(to);
      return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
    };
    const frac = (v) => Math.max(0, Math.min(1, (v - cfg.lo) / (cfg.hi - cfg.lo)));
    el("path", { d: arc(a0, a0 + sweep), fill: "none", stroke: "var(--track)", "stroke-width": 10,
                 "stroke-linecap": "round" }, svg);
    const v = cfg.value;
    if (v != null && isFinite(v)) {
      const col = cfg.color || (cfg.bad && cfg.bad(v) ? cfg.badColor || "var(--crit)" : cfg.goodColor || "var(--ok)");
      const to = a0 + Math.max(2, sweep * frac(v));
      el("path", { d: arc(a0, to), fill: "none", stroke: col, "stroke-width": 10,
                   "stroke-linecap": "round" }, svg);
    }
    if (cfg.threshold != null) {
      const deg = a0 + sweep * frac(cfg.threshold);
      const [xi, yi] = [c + (r - 9) * Math.cos((deg * Math.PI) / 180), c + (r - 9) * Math.sin((deg * Math.PI) / 180)];
      const [xo, yo] = [c + (r + 9) * Math.cos((deg * Math.PI) / 180), c + (r + 9) * Math.sin((deg * Math.PI) / 180)];
      el("line", { x1: xi, y1: yi, x2: xo, y2: yo, stroke: "var(--ink)", "stroke-width": 2.2,
                   "stroke-linecap": "round" }, svg);
    }
    el("text", { x: c, y: c + 4, "text-anchor": "middle", class: "ring-v" }, svg)
      .textContent = v == null || !isFinite(v) ? "--" : cfg.fmt ? cfg.fmt(v) : v;
    if (cfg.unit) el("text", { x: c, y: c + 22, "text-anchor": "middle", class: "ring-u" }, svg).textContent = cfg.unit;
  }

  // ------------------------------------------------------------ donut ---
  function donut(host, cfg) {
    const S = cfg.size || 150, c = S / 2, r = S / 2 - 12, w = 18;
    host.innerHTML = "";
    const svg = el("svg", { viewBox: `0 0 ${S} ${S}`, class: "donut", role: "img", width: S, height: S }, host);
    const total = cfg.segments.reduce((n, s) => n + s.v, 0) || 1;
    el("circle", { cx: c, cy: c, r, fill: "none", stroke: "var(--track)", "stroke-width": w }, svg);
    const C = 2 * Math.PI * r;
    const gap = cfg.segments.filter((s) => s.v).length > 1 ? 3 : 0;
    let off = 0;
    for (const s of cfg.segments) {
      if (!s.v) continue;
      const len = (s.v / total) * C;
      el("circle", { cx: c, cy: c, r, fill: "none", stroke: s.color, "stroke-width": w,
                     "stroke-dasharray": `${Math.max(0, len - gap)} ${C}`, "stroke-dashoffset": -off,
                     transform: `rotate(-90 ${c} ${c})` }, svg);
      off += len;
    }
    el("text", { x: c, y: c + 5, "text-anchor": "middle", class: "ring-v" }, svg).textContent = cfg.big;
    el("text", { x: c, y: c + 23, "text-anchor": "middle", class: "ring-u" }, svg).textContent = cfg.small;
  }

  window.Charts = { trend, quadrant, lines, spark, ring, donut, clock, dur, niceTicks };
})();
