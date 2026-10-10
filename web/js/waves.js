/* Waveform synthesis and sweep rendering.
 *
 * Traces are generated from each twin's current state, not recorded:
 *   heart rate      -> beat interval (with a little beat-to-beat variability)
 *   systolic/dia    -> arterial envelope, on a FIXED 30-160 mmHg scale so
 *                      hypotension visibly sits low, as on a real monitor
 *   cardiac index   -> pleth amplitude, so a patient going cold loses pulse
 *                      volume on screen before the numbers say so
 *   resp rate       -> respiratory trace
 *
 * Rendering is sweep-mode: the trace is drawn left to right and an erase bar
 * runs ahead of the pen, the way bedside monitors draw.  Channel colours come
 * from the page theme through Waves.palette().
 */
(function () {
  "use strict";

  const g = (x, m, s) => { const d = (x - m) / s; return Math.exp(-0.5 * d * d); };
  const sig = (x) => 1 / (1 + Math.exp(-x));

  // ---------------------------------------------------------------- ECG ---
  function ecgBeat(x, rr) {
    const qt = 0.40 * Math.sqrt(rr);        // QT shortens with rate (Bazett)
    return 0.11 * g(x, -0.165, 0.024)       // P
         - 0.09 * g(x, -0.028, 0.009)       // Q
         + 1.00 * g(x, 0, 0.0105)           // R
         - 0.23 * g(x, 0.027, 0.011)        // S
         + 0.29 * g(x, qt * 0.62, 0.052);   // T
  }

  // --------------------------------------------- pressure-shaped templates ---
  const TPL_N = 240;
  const tplCache = new Map();

  function pulse(kind, u) {
    if (kind === "art") {
      return g(u, 0.11, 0.045)                       // systolic upstroke/peak
        + 0.48 * g(u, 0.225, 0.07)                   // late systolic shoulder
        + 0.17 * g(u, 0.365, 0.03)                   // dicrotic wave
        + 0.46 * sig((u - 0.19) / 0.035) * Math.exp(-(u - 0.19) / 0.95);
    }
    return g(u, 0.2, 0.085)                          // pleth: damped, smoother
      + 0.3 * g(u, 0.42, 0.075)
      + 0.36 * sig((u - 0.25) / 0.06) * Math.exp(-(u - 0.25) / 0.85);
  }

  function template(kind, rr) {
    const key = kind + Math.round(rr * 100);
    let t = tplCache.get(key);
    if (t) return t;
    t = new Float32Array(TPL_N);
    for (let i = 0; i < TPL_N; i++) {
      const u = (i / TPL_N) * rr;
      t[i] = pulse(kind, u) + pulse(kind, u + rr) + pulse(kind, u + 2 * rr);
    }
    let lo = Infinity, hi = -Infinity;
    for (const v of t) { if (v < lo) lo = v; if (v > hi) hi = v; }
    for (let i = 0; i < TPL_N; i++) t[i] = (t[i] - lo) / (hi - lo || 1);
    tplCache.set(key, t);
    return t;
  }

  // ------------------------------------------------------------- patient ---
  class VirtualPatient {
    constructor(seed) {
      this.hr = 75; this.sys = 120; this.dia = 75; this.perf = 1; this.rpm = 16;
      this.rs = [];           // recent R-peak times (s)
      this.nextR = null;
      this.onBeat = null;
      this.rng = mulberry32(seed || 1);
      this.respT0 = 0;
    }
    set(v) {
      if (v.hr) this.hr = v.hr;
      if (v.sys) this.sys = v.sys;
      if (v.dia) this.dia = v.dia;
      if (v.perf != null) this.perf = Math.max(0.12, Math.min(1.1, v.perf));
      if (v.rpm) this.rpm = v.rpm;
    }
    get rr() { return 60 / Math.max(30, Math.min(200, this.hr)); }

    schedule(t) {
      if (this.nextR === null) {
        // Backfill ~12 s of beats so a freshly opened trace is already full.
        let r = t - this.rng() * this.rr;
        const back = [];
        while (r > t - 12) { back.unshift(r); r -= this.rr * (1 + (this.rng() - 0.5) * 0.05); }
        this.rs = back;
        this.nextR = back[back.length - 1] + this.rr;
      }
      while (this.nextR <= t) {
        this.rs.push(this.nextR);
        if (this.rs.length > 60) this.rs.shift();
        if (this.onBeat) this.onBeat();
        this.nextR += this.rr * (1 + (this.rng() - 0.5) * 0.05);
      }
    }
    lastR(t, delay = 0) {
      for (let i = this.rs.length - 1; i >= 0; i--) {
        if (this.rs[i] + delay <= t) return this.rs[i];
      }
      return t - this.rr;
    }
    ecg(t) {
      const R = this.lastR(t);
      return ecgBeat(t - R, this.rr) + ecgBeat(t - R - this.rr, this.rr);
    }
    phase(kind, t, delay) {
      const rr = this.rr;
      const u = t - (this.lastR(t, delay) + delay);
      const tpl = template(kind, rr);
      return tpl[Math.min(TPL_N - 1, Math.max(0, Math.floor((u / rr) * TPL_N)))];
    }
    art(t) { return this.dia + (this.sys - this.dia) * this.phase("art", t, 0.14); }
    pleth(t) { return 0.08 + this.perf * this.phase("pleth", t, 0.26); }
    resp(t) {
      const period = 60 / Math.max(4, this.rpm);
      const p = ((t - this.respT0) % period) / period;
      // inspiration is quicker than expiration
      const w = p < 0.4 ? (p / 0.4) * 0.5 : 0.5 + ((p - 0.4) / 0.6) * 0.5;
      return 0.5 - 0.5 * Math.cos(2 * Math.PI * w);
    }
  }

  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // --------------------------------------------------------------- sweep ---
  const SCALES = {
    ecg: [-0.42, 1.16],
    art: [30, 160],
    pleth: [0, 1.25],
    resp: [-0.1, 1.1],
  };

  const PALETTE = { ecg: "#0e9f6e", art: "#e03e45", pleth: "#2378e1", resp: "#c98a00" };

  class Sweep {
    constructor(canvas, patient, channel, pxPerSec) {
      this.c = canvas;
      this.ctx = canvas.getContext("2d");
      this.p = patient;
      this.ch = channel;
      this.pxPerSec = pxPerSec || 110;
      this.x = 0;
      this.lastT = null;
      this.lastY = null;
      this.acc = 0;
      this.resize();
    }
    resize() {
      const r = this.c.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      this.w = Math.max(10, r.width);
      this.h = Math.max(10, r.height);
      this.c.width = Math.round(this.w * dpr);
      this.c.height = Math.round(this.h * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.ctx.clearRect(0, 0, this.w, this.h);
      this.x = 0;
      this.lastY = null;
      this.lastT = null;
      if (this.c.isConnected && this.c.offsetParent !== null) this.prime(performance.now() / 1000);
    }
    get color() { return PALETTE[this.ch]; }
    y(v) {
      const [lo, hi] = SCALES[this.ch];
      const pad = this.ch === "ecg" ? 4 : 5;
      return pad + (1 - (v - lo) / (hi - lo)) * (this.h - 2 * pad);
    }
    sample(t) {
      switch (this.ch) {
        case "ecg": return this.p.ecg(t);
        case "art": return this.p.art(t);
        case "pleth": return this.p.pleth(t);
        default: return this.p.resp(t);
      }
    }
    prime(t) {
      // Draw one full screen of history so the trace never opens empty.
      const span = this.w / this.pxPerSec;
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.w, this.h);
      ctx.lineWidth = this.ch === "ecg" ? 1.6 : 1.5;
      ctx.lineJoin = "round";
      ctx.strokeStyle = this.color;
      ctx.beginPath();
      const n = Math.floor(this.w * 2);
      for (let i = 0; i <= n; i++) {
        const x = i / 2;
        const y = this.y(this.sample(t - span + x / this.pxPerSec));
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        this.lastY = y;
      }
      ctx.stroke();
      this.x = this.w;
      this.lastT = t;
      this.acc = 0;
    }
    draw(t) {
      if (this.lastT === null) { this.prime(t); return; }
      if (t - this.lastT > 0.5) this.lastT = t - 0.5;   // tab was hidden: skip ahead
      const dt = t - this.lastT;
      this.lastT = t;
      this.acc += dt * this.pxPerSec;
      const steps = Math.floor(this.acc * 2);     // two samples per pixel
      if (steps < 1) return;
      const dx = steps / 2;
      this.acc -= dx;

      const ctx = this.ctx;
      const gap = 14;
      ctx.lineWidth = this.ch === "ecg" ? 1.6 : 1.5;
      ctx.lineJoin = "round";
      ctx.strokeStyle = this.color;

      const t0 = t - dx / this.pxPerSec;
      ctx.beginPath();
      let x = this.x;
      if (this.lastY !== null) ctx.moveTo(x, this.lastY);
      for (let i = 1; i <= steps; i++) {
        const ts = t0 + (i / 2) / this.pxPerSec;
        const nx = x + 0.5;
        const yy = this.y(this.sample(ts));
        if (nx > this.w) {
          ctx.stroke();
          x = 0;
          ctx.beginPath();
          ctx.moveTo(0, yy);
          this.lastY = yy;
          continue;
        }
        if (this.lastY === null) ctx.moveTo(nx, yy); else ctx.lineTo(nx, yy);
        x = nx;
        this.lastY = yy;
      }
      ctx.stroke();
      this.x = x;

      // erase bar ahead of the pen
      const ex = this.x + 1;
      if (ex + gap <= this.w) ctx.clearRect(ex, 0, gap, this.h);
      else {
        ctx.clearRect(ex, 0, this.w - ex, this.h);
        ctx.clearRect(0, 0, gap - (this.w - ex), this.h);
      }
    }
  }

  // -------------------------------------------------------------- engine ---
  const sweeps = new Set();
  const patients = new Set();
  let running = false;

  function frame() {
    const t = performance.now() / 1000;
    for (const p of patients) p.schedule(t);
    for (const s of sweeps) {
      if (!s.c.isConnected) { sweeps.delete(s); continue; }
      if (s.c.offsetParent === null) { s.lastT = null; continue; }
      s.draw(t);
    }
    requestAnimationFrame(frame);
  }

  const ro = new ResizeObserver((entries) => {
    for (const e of entries) {
      for (const s of sweeps) if (s.c === e.target) s.resize();
    }
  });

  window.Waves = {
    patient(seed) {
      const p = new VirtualPatient(seed);
      patients.add(p);
      return p;
    },
    release(p) { patients.delete(p); },
    // Size and draw every visible trace now, without waiting for a frame --
    // call after inserting canvases so nothing ever paints empty.
    refresh() {
      const t = performance.now() / 1000;
      for (const p of patients) p.schedule(t);
      for (const s of sweeps) {
        if (!s.c.isConnected || s.c.offsetParent === null) continue;
        s.resize();
        s.prime(t);
      }
    },
    // Recolour every trace, e.g. when the ward switches to night mode.
    palette(p) {
      Object.assign(PALETTE, p);
      this.refresh();
    },
    attach(canvas, patient, channel, pxPerSec) {
      const s = new Sweep(canvas, patient, channel, pxPerSec);
      sweeps.add(s);
      ro.observe(canvas);
      if (!running) { running = true; requestAnimationFrame(frame); }
      return s;
    },
  };
})();
