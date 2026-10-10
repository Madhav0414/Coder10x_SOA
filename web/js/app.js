/* Prahari central station -- views, data and the ward clock.
 *
 * The browser owns the clock.  Each bed's twin arrives with a forecast from
 * its last charted observation; between charting rounds the display reads the
 * virtual patient off that forecast, so nothing needs the server until the
 * next observation lands.
 */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const SPEED = { 1: 1 / 60, 2: 5 / 60, 3: 20 / 60 };   // simulated hours per real second
  const ACTION = {
    A: "No acute action",
    B: "Decongest",
    L: "Hold diuretic · cautious fluid",
    C: "Inotrope + diuretic · escalate",
  };
  const PROFILE = { A: "Warm & dry", B: "Warm & wet", L: "Cold & dry", C: "Cold & wet" };

  // Hand-drawn 24px line icons.
  const ICONS = {
    pulse: '<path d="M2.5 12.5h4.2l2.3-6 4.4 11.5 2.7-7.2 1.6 1.7h3.8"/>',
    grid: '<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/>',
    rows: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.8" cy="6.5" r="1.1"/><circle cx="4.8" cy="12" r="1.1"/><circle cx="4.8" cy="17.5" r="1.1"/>',
    chart: '<path d="M3.5 20.5h17"/><rect x="5" y="11" width="3.4" height="6.5" rx="1"/><rect x="10.3" y="5" width="3.4" height="12.5" rx="1"/><rect x="15.6" y="8.5" width="3.4" height="9" rx="1"/>',
    clipboard: '<rect x="5" y="4.5" width="14" height="16.5" rx="2.5"/><path d="M9 4.8V4c0-.8.6-1.5 1.4-1.5h3.2c.8 0 1.4.7 1.4 1.5v.8M8.8 10.5h6.4M8.8 14.5h4.2"/>',
    moon: '<path d="M19.5 14.6A7.8 7.8 0 0 1 9.4 4.5a7.8 7.8 0 1 0 10.1 10.1z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.8v1.9M12 19.3v1.9M5.5 5.5l1.3 1.3M17.2 17.2l1.3 1.3M2.8 12h1.9M19.3 12h1.9M5.5 18.5l1.3-1.3M17.2 6.8l1.3-1.3"/>',
    heart: '<path d="M12 19.8s-7.6-4.5-7.6-10.1A4.2 4.2 0 0 1 12 7.2a4.2 4.2 0 0 1 7.6 2.5c0 5.6-7.6 10.1-7.6 10.1z"/>',
    drop: '<path d="M12 3.3s5.9 6.1 5.9 10.5a5.9 5.9 0 0 1-11.8 0C6.1 9.4 12 3.3 12 3.3z"/><path d="M9.3 14.2a2.8 2.8 0 0 0 2.4 2.6"/>',
    o2: '<circle cx="9.5" cy="14" r="5.6"/><circle cx="18" cy="6.4" r="2.7"/>',
    lungs: '<path d="M12 3.5v7.2M12 8.4c-.8 1.4-2.3 2.2-2.3 2.2M12 8.4c.8 1.4 2.3 2.2 2.3 2.2"/><path d="M9.3 8C7.8 6.9 6.3 7.7 5.5 9.6 4.6 12 4 15.3 4.3 17.6c.2 1.4 1.4 2 2.9 1.5 1.9-.7 2.7-2.1 2.7-4.2V9.3"/><path d="M14.7 8c1.5-1.1 3-.3 3.8 1.6.9 2.4 1.5 5.7 1.2 8-.2 1.4-1.4 2-2.9 1.5-1.9-.7-2.7-2.1-2.7-4.2V9.3"/>',
    search: '<circle cx="11" cy="11" r="6.3"/><path d="M19.8 19.8l-4.3-4.3"/>',
    back: '<path d="M14.5 5.5L8 12l6.5 6.5"/>',
    next: '<path d="M9.5 5.5L16 12l-6.5 6.5"/>',
    play: '<path d="M8 5.2v13.6c0 .7.8 1.2 1.4.8l10.3-6.8a1 1 0 0 0 0-1.6L9.4 4.4C8.8 4 8 4.5 8 5.2z" fill="currentColor"/>',
    pause: '<rect x="6.5" y="5" width="3.6" height="14" rx="1.2" fill="currentColor"/><rect x="13.9" y="5" width="3.6" height="14" rx="1.2" fill="currentColor"/>',
    print: '<path d="M7 9V3.5h10V9"/><rect x="3.5" y="9" width="17" height="7.8" rx="2"/><path d="M7 14h10v6.5H7z"/>',
    alert: '<path d="M10.3 4.3a2 2 0 0 1 3.4 0l7.4 13a2 2 0 0 1-1.7 3H4.6a2 2 0 0 1-1.7-3z"/><path d="M12 9.5v4.2M12 17v.1"/>',
    bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.6 2H4.4z"/><path d="M10 21a2.1 2.1 0 0 0 4 0"/>',
    hourglass: '<path d="M6.5 3.5h11M6.5 20.5h11M7.8 3.5c0 4.6 4.2 5.4 4.2 8.5s-4.2 3.9-4.2 8.5M16.2 3.5c0 4.6-4.2 5.4-4.2 8.5s4.2 3.9 4.2 8.5"/>',
    twin: '<circle cx="9" cy="12" r="5.6"/><circle cx="15" cy="12" r="5.6"/>',
    pill: '<rect x="2.8" y="8.3" width="18.4" height="7.4" rx="3.7" transform="rotate(-40 12 12)"/><path d="M9.6 9.1l4.8 5.8"/>',
    flask: '<path d="M9.3 3.5h5.4M10.4 3.5v5.6L5.1 18.3a1.5 1.5 0 0 0 1.3 2.2h11.2a1.5 1.5 0 0 0 1.3-2.2L13.6 9.1V3.5M7.3 14.8h9.4"/>',
    dna: '<path d="M7.5 3.5c0 5.3 9 5 9 8.5s-9 3.2-9 8.5M16.5 3.5c0 5.3-9 5-9 8.5s9 3.2 9 8.5M9 7h6M9 17h6"/>',
    doc: '<path d="M6.5 3.5h7l4 4v13h-11z"/><path d="M13.5 3.5v4h4M9 12.5h6M9 16h6"/>',
    home: '<path d="M4 10.8l8-6.3 8 6.3v9.7H4z"/><path d="M10 20.5v-5.5h4v5.5"/>',
    steps: '<ellipse cx="8.4" cy="8.2" rx="2.6" ry="4.1"/><ellipse cx="15.6" cy="13.6" rx="2.6" ry="4.1"/><path d="M7.2 15.3h2.6M14.4 20.6H17"/>',
    scale: '<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="M8.3 10a5.3 5.3 0 0 1 7.4 0L13 12.5"/>',
    watch: '<rect x="7" y="6.5" width="10" height="11" rx="3"/><path d="M9.2 6.5l.6-3h4.4l.6 3M9.2 17.5l.6 3h4.4l.6-3"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    meal: '<path d="M7 3.5v17M4.6 3.5v5a2.4 2.4 0 0 0 4.8 0v-5M17.2 20.5V3.5c-2.2 1-3.6 3.6-3.6 7.2h3.6"/>',
  };
  const icon = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

  const params = new URLSearchParams(location.search);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } },
  };
  const S = {
    seed: +params.get("seed") || 7,
    cadence: 4,
    horizon: 12,
    clock: +params.get("t") || 16,
    maxClock: 30,
    live: false,
    speed: 2,
    reveal: null,
    ward: null,
    pending: null,
    view: "wall",
    bed: null,
    detail: null,
    tab: params.get("tab") || "forecast",
    agent: null,
    delay: 4,
    course: params.get("course") === "decompensating" ? "decompensating" : "recorded",
    homeDay: null,
    layout: params.get("layout") === "list" ? "list" : "grid",
    filter: "all",
    sort: "priority",
    q: "",
    theme: params.get("theme") || store.get("prahari.theme") || "day",
  };

  const tiles = new Map();     // bed number -> { el, row, refs, rrefs, vp, bed }
  let lastUi = 0;

  // ------------------------------------------------------------ utilities
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad2 = (n) => String(n).padStart(2, "0");
  const fmt = (v, d = 0) => (v == null || !isFinite(v) ? "--" : (+v).toFixed(d));
  const revealFor = (clock) => Math.floor(clock / S.cadence + 1e-9);
  const hhmm = (t) => Charts.clock(t).hhmm;
  const initials = (name) => name.replace(/\./g, " ").split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const hue = (bed) => (bed * 53 + 200) % 360;
  const avatar = (b, cls = "") => `<span class="avatar ${cls}" style="--av-h:${hue(b.bed)}">${initials(b.name)}</span>`;
  const n2cls = (n) => (n >= 7 ? "crit" : n >= 5 ? "warn" : "");
  const qpill = (q, cls = "") => `<span class="pill q ${cls}" data-q="${q.code}">${q.code} · ${PROFILE[q.code]}</span>`;

  function query(extra) {
    return new URLSearchParams({
      seed: S.seed, cadence: S.cadence, reveal: S.reveal, horizon: S.horizon, ...(extra || {}),
    }).toString();
  }
  async function api(path, extra, text) {
    const r = await fetch(`/api/${path}?${query(extra)}`);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return text ? r.text() : r.json();
  }

  function interp(series, key, clock) {
    const t = series.t;
    if (clock <= t[0]) return series[key][0];
    for (let i = 0; i < t.length - 1; i++) {
      if (clock < t[i + 1]) {
        const w = (clock - t[i]) / (t[i + 1] - t[i]);
        return series[key][i] + (series[key][i + 1] - series[key][i]) * w;
      }
    }
    return series[key][t.length - 1];
  }
  function stateAt(bed, clock) {
    const f = bed.forecast;
    const o = {};
    for (const k of ["hr", "sbp", "dbp", "map", "spo2", "rr", "ci", "pcwp"]) o[k] = interp(f, k, clock);
    return o;
  }
  const lastObs = (bed) => bed.obs[bed.obs.length - 1];

  // NEWS2 scoring bands, so a number turns amber at 2 points and red at 3.
  function flag(k, v) {
    if (v == null || !isFinite(v)) return "";
    v = Math.round(v);
    switch (k) {
      case "hr": return v <= 40 || v >= 131 ? "crit" : v >= 111 ? "warn" : "";
      case "sbp": return v <= 90 || v >= 220 ? "crit" : v <= 100 ? "warn" : "";
      case "spo2": return v <= 91 ? "crit" : v <= 93 ? "warn" : "";
      case "rr": return v <= 8 || v >= 25 ? "crit" : v >= 21 ? "warn" : "";
    }
    return "";
  }

  function tileState(b) {
    if (b.alert) return "alert";
    if (b.deadline != null || lastObs(b).news2 >= 5) return "watch";
    return "ok";
  }

  function countdown(b) {
    if (b.quadrant.code === "C") return { cls: "crit", html: "Cold-wet <b>now</b>", short: "now" };
    if (b.deadline == null) return { cls: "", html: `No crossing ≤ ${S.horizon} h`, short: "" };
    const rem = b.deadline - S.clock;
    if (rem <= 0) return { cls: "crit", html: "Cold-wet <b>due</b>", short: "due" };
    return { cls: b.alert ? "crit" : "warn", html: `Cold-wet in <b>${Charts.dur(rem)}</b>`, short: `in ${Charts.dur(rem)}` };
  }

  function planLine(b) {
    const act = ACTION[b.quadrant.code];
    if (b.quadrant.code === "C") {
      return b.best ? `<b>Escalate</b> · strongest single agent: <b>${esc(b.best.name)}</b>` : "<b>Escalate</b>";
    }
    if (b.best && b.best.benefit >= 0.05) {
      const after = b.best.deadline == null ? `no crossing ≤ ${S.horizon} h` : `crossing ${hhmm(b.best.deadline)}`;
      return `<b>${esc(b.best.short)}</b> now → ${after}`;
    }
    if (b.quadrant.code === "L") return `<b>${act}</b>`;
    return esc(b.reason);
  }

  function beat(node) {
    if (!node) return;
    node.classList.add("beat");
    setTimeout(() => node.classList.remove("beat"), 200);
  }

  // ---------------------------------------------------------------- data
  // One ward request at a time.  Refits queue on the server, so asking for
  // every round as the clock passes it means each reply is already stale when
  // it lands -- and a screen that discards stale replies never updates.  The
  // reply on its way is always newer than what is shown: use it, then ask for
  // the round the clock has reached.
  let inflight = null;

  async function sync(force) {
    const rv = revealFor(S.clock);
    if (!force && rv === S.reveal && S.ward) return;
    if (inflight && !force) return;
    S.reveal = rv;
    const req = api("ward");
    S.pending = inflight = req;
    let data;
    try { data = await req; } catch (e) { console.error(e); return; } finally { if (inflight === req) inflight = null; }
    if (S.pending !== req) return;      // superseded by a feed change or a step
    S.ward = data;
    S.maxClock = data.historyH;
    renderWall();
    renderKpis();
    renderSide();
    if (S.view === "patient" && S.bed != null) loadPatient(S.bed, true);
    if (S.view === "evidence") renderEvidence();
    if (S.view === "handover") renderHandover();
  }

  // ---------------------------------------------------------------- ward
  const WAVES3 = [["ecg", "II"], ["art", "ART"], ["pleth", "Pleth"]];
  const waveHTML = (list) => list.map(([ch, lead]) =>
    `<div class="wave" data-ch="${ch}"><span class="lead">${lead}</span><canvas data-ch="${ch}"></canvas></div>`).join("");

  function cardHTML(b) {
    return `
      <div class="pc-top">
        ${avatar(b)}
        <div class="who"><div class="name">${esc(b.name)}</div>
          <div class="meta">${b.age} ${b.sex} · ${esc(b.etiology)} · EF ${b.ef}% · NYHA ${b.nyha}</div></div>
        <div class="bedtag">Bed<b>${pad2(b.bed)}</b></div>
      </div>
      <div class="pc-tags"><span data-ref="n2"></span><span data-ref="q"></span><span class="cd" data-ref="cd"></span></div>
      <div class="strip">${waveHTML(WAVES3)}</div>
      <div class="vits">
        <div class="vit" data-k="hr" data-ref="v-hr"><div class="vl"><span data-ref="heart">${icon("heart")}</span>HR</div><div class="vv"><b data-ref="hr">--</b><small>bpm</small></div></div>
        <div class="vit" data-k="bp" data-ref="v-bp"><div class="vl">${icon("drop")}BP <small data-ref="map"></small></div><div class="vv"><b data-ref="bp">--</b></div></div>
        <div class="vit" data-k="spo2" data-ref="v-spo2"><div class="vl">${icon("o2")}SpO<sub>2</sub></div><div class="vv"><b data-ref="spo2">--</b><small>%</small></div></div>
        <div class="vit" data-k="rr" data-ref="v-rr"><div class="vl">${icon("lungs")}RR</div><div class="vv"><b data-ref="rr">--</b><small>/min</small></div></div>
      </div>
      <div class="pc-plan" data-ref="plan"></div>
      <div class="pc-foot">
        <div class="conf" data-ref="conf" title="How well the twin's fit explains this patient's chart"><span>Twin</span><span class="bar"><i data-ref="confbar"></i></span><b data-ref="confv"></b></div>
        <span class="sync" data-ref="sync"></span>
      </div>`;
  }

  function rowHTML(b) {
    return `
      <td><span class="bedtag"><b>${pad2(b.bed)}</b></span></td>
      <td><div class="who-cell">${avatar(b, "sm")}<div><b>${esc(b.name)}</b><span>${b.age} ${b.sex} · ${esc(b.etiology)} · EF ${b.ef}%</span></div></div></td>
      <td><span data-ref="n2"></span></td>
      <td><span data-ref="q"></span></td>
      <td class="v-cell" data-ref="hr"></td><td class="v-cell" data-ref="bp"></td>
      <td class="v-cell" data-ref="spo2"></td><td class="v-cell" data-ref="rr"></td>
      <td><span class="cd" data-ref="cd"></span></td>
      <td class="plan-cell" data-ref="plan"></td>
      <td><div class="conf" data-ref="conf"><span class="bar"><i data-ref="confbar"></i></span><b data-ref="confv"></b></div></td>`;
  }

  const refsOf = (root) => {
    const r = {};
    root.querySelectorAll("[data-ref]").forEach((n) => (r[n.dataset.ref] = n));
    return r;
  };

  function makeTile(b) {
    const el = document.createElement("article");
    el.className = "pcard";
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.innerHTML = cardHTML(b);
    const row = document.createElement("tr");
    row.innerHTML = rowHTML(b);
    const refs = refsOf(el), rrefs = refsOf(row);
    const vp = Waves.patient(b.bed * 7919 + S.seed);
    vp.onBeat = () => {
      beat(refs.heart);
      if (S.view === "patient" && S.bed === b.bed) beat($("#view-patient .vc[data-k=hr] .ic"));
    };
    el.querySelectorAll("canvas").forEach((cv) => Waves.attach(cv, vp, cv.dataset.ch, 80));
    el.addEventListener("click", () => openPatient(b.bed));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") openPatient(b.bed); });
    row.addEventListener("click", () => openPatient(b.bed));
    return { el, row, refs, rrefs, vp, bed: b };
  }

  function orderedBeds() {
    const beds = [...S.ward.beds];
    if (S.sort === "bed") beds.sort((a, b) => a.bed - b.bed);
    return beds;
  }

  function paintStatic(r, b, compact) {
    const q = b.quadrant;
    const n2 = lastObs(b).news2;
    r.n2.className = `pill ${n2cls(n2)}`;
    r.n2.textContent = compact ? n2 : `NEWS2 ${n2}`;
    r.q.className = "pill q";
    r.q.dataset.q = q.code;
    r.q.textContent = `${q.code} · ${PROFILE[q.code]}`;
    r.plan.innerHTML = planLine(b);
    r.conf.dataset.band = b.confidenceBand;
    r.confbar.style.width = `${Math.round(b.confidence * 100)}%`;
    r.confv.textContent = `${Math.round(b.confidence * 100)}%`;
  }

  function renderWall() {
    const grid = $("#grid"), body = $("#roster-body");
    const seen = new Set();
    for (const b of orderedBeds()) {
      let t = tiles.get(b.bed);
      if (!t || t.bed.name !== b.name) {
        if (t) { Waves.release(t.vp); t.el.remove(); t.row.remove(); }
        t = makeTile(b);
        tiles.set(b.bed, t);
      }
      t.bed = b;
      seen.add(b.bed);
      grid.appendChild(t.el);     // re-appending reorders without rebuilding canvases
      body.appendChild(t.row);
      t.el.dataset.state = t.row.dataset.state = tileState(b);
      paintStatic(t.refs, b, false);
      paintStatic(t.rrefs, b, true);
      t.el.setAttribute("aria-label", `Bed ${b.bed}, ${b.name}, ${b.quadrant.label}`);
    }
    for (const [bed, t] of tiles) {
      if (!seen.has(bed)) { Waves.release(t.vp); t.el.remove(); t.row.remove(); tiles.delete(bed); }
    }
    applyFilter();
    updateUi(true);
  }

  function matches(b) {
    if (S.filter !== "all" && tileState(b) !== S.filter) return false;
    const words = S.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const hay = `${b.name} bed ${b.bed} ${pad2(b.bed)} ${b.etiology} ${b.dx} ${PROFILE[b.quadrant.code]} ${b.quadrant.code}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  }

  function applyFilter() {
    if (!S.ward) return;
    const counts = { all: 0, alert: 0, watch: 0, ok: 0 };
    let shown = 0;
    for (const t of tiles.values()) {
      counts.all++;
      counts[tileState(t.bed)]++;
      const ok = matches(t.bed);
      t.el.hidden = t.row.hidden = !ok;
      if (ok) shown++;
    }
    $$("#filter [data-n]").forEach((n) => (n.textContent = counts[n.dataset.n]));
    $("#grid").hidden = S.layout !== "grid" || !shown;
    $("#roster").hidden = S.layout !== "list" || !shown;
    $("#board-empty").hidden = shown > 0;
    Waves.refresh();              // newly shown traces must paint at once
  }

  function renderKpis() {
    const beds = S.ward.beds;
    const act = beds.filter((b) => b.alert);
    const fired = beds.filter((b) => lastObs(b).news2 >= 5);
    const split = { A: 0, B: 0, L: 0, C: 0 };
    fired.forEach((b) => split[b.quadrant.code]++);
    const conf = beds.reduce((n, b) => n + b.confidence, 0) / beds.length;
    const synced = Math.max(...beds.map((b) => b.synced));
    const next = (S.reveal + 1) * S.cadence;
    $("#kpis").innerHTML = `
      <section class="card kpi ${act.length ? "crit" : ""}">
        <div class="k"><span class="ic">${icon("alert")}</span>Needs action now</div>
        <div class="v">${act.length}<small>of ${beds.length} beds</small></div>
        <div class="s">${act.length ? act.map((b) => `<span class="pill sm crit">Bed ${pad2(b.bed)}</span>`).join("")
          : "Nothing crosses into cold-wet within 6 h"}</div>
      </section>
      <section class="card kpi warn">
        <div class="k"><span class="ic">${icon("bell")}</span>NEWS2 ≥ 5</div>
        <div class="v">${fired.length}<small>beds triggered</small></div>
        <div class="split">${["A", "B", "L", "C"].filter((q) => split[q]).map((q) =>
          `<i style="flex:${split[q]};background:var(--q${q})" title="${split[q]} × ${q} · ${PROFILE[q]}"></i>`).join("")}</div>
        <div class="s">${fired.length ? "The twin splits them: " + Object.entries(split).filter(([, n]) => n)
          .map(([q, n]) => `<b>${n}</b> ${q}`).join(" · ") : "No early-warning trigger"}</div>
      </section>
      <section class="card kpi" id="kpi-cross"></section>
      <section class="card kpi twin">
        <div class="k"><span class="ic">${icon("twin")}</span>Last charting round</div>
        <div class="v">${hhmm(synced)}<small>next ${hhmm(next)}</small></div>
        <div class="s">${S.cadence === 1 ? "Hourly" : "4-hourly"} chart · mean twin confidence <b>${Math.round(conf * 100)}%</b></div>
      </section>`;
    paintCrossKpi();
  }

  function paintCrossKpi() {
    const el = $("#kpi-cross");
    if (!el || !S.ward) return;
    const beds = S.ward.beds;
    const wet = beds.filter((b) => b.quadrant.code === "C");
    const up = beds.filter((b) => b.quadrant.code !== "C" && b.deadline != null)
      .sort((a, b) => a.deadline - b.deadline)[0];
    let v, s;
    if (up) {
      const rem = up.deadline - S.clock;
      v = rem <= 0 ? "Due" : Charts.dur(rem);
      s = `Bed ${pad2(up.bed)} · ${esc(up.name)} · now ${up.quadrant.code}`;
    } else {
      v = "None";
      s = `No bed crosses within ${S.horizon} h`;
    }
    if (wet.length) s += ` · already cold-wet: bed ${wet.map((b) => pad2(b.bed)).join(", ")}`;
    const html = `<div class="k"><span class="ic">${icon("hourglass")}</span>Next cold-wet crossing</div>
      <div class="v">${v}</div><div class="s">${s}</div>`;
    const cls = `card kpi ${up && up.alert ? "crit" : up ? "warn" : ""}`;
    if (el.innerHTML !== html) el.innerHTML = html;
    if (el.className !== cls) el.className = cls;
  }

  function renderSide() {
    const beds = S.ward.beds;
    const rows = beds.filter((b) => b.alert || lastObs(b).news2 >= 5);
    const fired = beds.filter((b) => lastObs(b).news2 >= 5);
    const cnt = $("#queue-count");
    cnt.textContent = rows.length;
    cnt.classList.toggle("zero", !rows.length);
    $("#queue").innerHTML = rows.length ? rows.map((b) => {
      const n2 = lastObs(b).news2;
      return `<button class="qrow ${b.alert ? "crit" : "warn"}" data-bed="${b.bed}">
        ${avatar(b, "sm")}
        <span>
          <span class="qh"><b>Bed ${pad2(b.bed)}</b>· ${esc(b.name)}<span class="qcd" data-cd="${b.bed}"></span></span>
          <span class="qflow"><span class="pill sm ${n2cls(n2)}">NEWS2 ${n2}</span>→${qpill(b.quadrant, "sm")}</span>
          <span class="qact">${esc(ACTION[b.quadrant.code])}</span>
        </span></button>`;
    }).join("") : `<div class="quiet">No bed needs action.</div>`;

    const split = { A: 0, B: 0, L: 0, C: 0 };
    fired.forEach((b) => split[b.quadrant.code]++);
    const parts = Object.entries(split).filter(([, n]) => n).map(([q, n]) => `<b>${n}</b> ${q}`).join(" · ");
    $("#tally").innerHTML = `NEWS2 ≥ 5 on <b>${fired.length}</b> of ${beds.length} beds${fired.length ? ` — the twin splits them ${parts}` : ""}. The score says who is sick; the twin says what kind.`;

    const counts = { A: 0, B: 0, L: 0, C: 0 }, which = { A: [], B: [], L: [], C: [] };
    [...beds].sort((a, b) => a.bed - b.bed).forEach((b) => { counts[b.quadrant.code]++; which[b.quadrant.code].push(pad2(b.bed)); });
    $("#mix").innerHTML = `<div id="mix-donut"></div><div class="mix-legend">${["A", "B", "L", "C"].map((q) => `
      <div class="row"><i style="background:var(--q${q})"></i><span>${q} · ${PROFILE[q]}</span><b>${counts[q]}</b>
      ${which[q].length ? `<em>Bed ${which[q].join(", ")}</em>` : ""}</div>`).join("")}</div>`;
    Charts.donut($("#mix-donut"), {
      segments: ["A", "B", "L", "C"].map((q) => ({ v: counts[q], color: `var(--q${q})` })),
      big: beds.length, small: "beds", size: 128,
    });
    updateUi(true);
  }

  // --------------------------------------------------------- live update
  function updateUi(force) {
    const now = performance.now();
    if (!force && now - lastUi < 200) return;
    lastUi = now;

    const c = Charts.clock(S.clock);
    $("#clock-day").textContent = `Day ${c.day}`;
    $("#clock-time").textContent = c.hhmm;
    $("#clock").classList.toggle("running", S.live);

    if (!S.ward) return;
    for (const t of tiles.values()) {
      const b = t.bed;
      const s = stateAt(b, S.clock);
      t.vp.set({ hr: s.hr, sys: s.sbp, dia: s.dbp, perf: s.ci / 3.1, rpm: s.rr });
      const f = { hr: flag("hr", s.hr), bp: flag("sbp", s.sbp), spo2: flag("spo2", s.spo2), rr: flag("rr", s.rr) };
      const r = t.refs, w = t.rrefs;
      r.hr.textContent = w.hr.textContent = fmt(s.hr);
      r.bp.textContent = `${fmt(s.sbp)}/${fmt(s.dbp)}`;
      r.map.textContent = `MAP ${fmt(s.map)}`;
      w.bp.textContent = `${fmt(s.sbp)}/${fmt(s.dbp)}`;
      r.spo2.textContent = w.spo2.textContent = fmt(s.spo2);
      r.rr.textContent = w.rr.textContent = fmt(s.rr);
      for (const k of ["hr", "bp", "spo2", "rr"]) {
        r[`v-${k}`].className = `vit ${f[k]}`;
        w[k].className = `v-cell ${f[k]}`;
      }
      const cd = countdown(b);
      r.cd.className = w.cd.className = `cd ${cd.cls}`;
      r.cd.innerHTML = w.cd.innerHTML = cd.html;
      r.sync.textContent = `Charted ${hhmm(b.synced)} · +${Charts.dur(S.clock - b.synced)}`;
    }
    $$("#queue [data-cd]").forEach((n) => {
      const t = tiles.get(+n.dataset.cd);
      if (t) n.textContent = countdown(t.bed).short;
    });
    paintCrossKpi();
    if (S.view === "patient" && S.detail) updatePatientLive();
  }

  let lastFrame = performance.now();
  function loop(now) {
    const dt = Math.min(0.25, (now - lastFrame) / 1000);
    lastFrame = now;
    if (S.live) {
      S.clock = Math.min(S.maxClock, S.clock + dt * SPEED[S.speed]);
      if (S.clock >= S.maxClock) setLive(false);
    }
    // Live or stepped, catch up with the clock once nothing is in flight.
    if (revealFor(S.clock) !== S.reveal) sync();
    updateUi(false);
    requestAnimationFrame(loop);
  }

  function setLive(on) {
    if (on && S.clock >= S.maxClock - 1e-6) { S.clock = 16; sync(true); }
    S.live = on;
    const btn = $("#btn-live");
    btn.classList.toggle("live", on);
    btn.innerHTML = on ? `${icon("pause")}Live` : `${icon("play")}Go live`;
    btn.setAttribute("aria-pressed", on);
    $("#clock").classList.toggle("running", on);
  }

  function step(dir) {
    const next = (revealFor(S.clock) + dir) * S.cadence;
    S.clock = Math.max(S.cadence, Math.min(S.maxClock, next));
    sync();
    updateUi(true);
  }

  // ------------------------------------------------------------ routing
  function setHeader(view, d) {
    const brand = `<span class="brand">Prahari</span><span class="deva" lang="hi">प्रहरी</span>`;
    const ward = `<a href="#">Ward 4B</a>`;
    const sep = `<span class="sep">/</span>`;
    const H = {
      wall: [[brand, "<span>Cardiac step-down · heart failure · 8 beds</span>"], "Ward 4B"],
      patient: [[brand, ward, `<span>Bed ${d ? pad2(d.bed) : ""}</span>`], "Patient details"],
      evidence: [[brand, ward, "<span>Evidence</span>"], "How we know — and where it fails"],
      handover: [[brand, ward, "<span>Handover</span>"], "Shift handover"],
    }[view];
    $("#crumbs").innerHTML = H[0].join(sep);
    $("#page-title").textContent = H[1];
    $$(".rail .nav[data-nav]").forEach((n) => n.classList.toggle("on", n.dataset.nav === (view === "patient" ? "wall" : view)));
  }

  function show(view) {
    const changed = S.view !== view;
    S.view = view;
    for (const v of ["wall", "patient", "evidence", "handover"]) $(`#view-${v}`).hidden = v !== view;
    setHeader(view, S.detail);
    if (changed) window.scrollTo(0, 0);
    if (view === "wall") Waves.refresh();
  }
  function route() {
    const h = location.hash.replace(/^#/, "");
    const m = h.match(/^bed=(\d+)/);
    if (m) return openPatient(+m[1], true);
    S.bed = null;
    S.detail = null;
    if (h === "evidence") { show("evidence"); return renderEvidence(); }
    if (h === "handover") { show("handover"); return renderHandover(); }
    show("wall");
  }
  function goWall() {
    if (location.hash && location.hash !== "#") location.hash = "";
    else route();
  }

  // -------------------------------------------------------------- patient
  async function openPatient(bed, fromRoute) {
    if (!fromRoute && location.hash !== `#bed=${bed}`) { location.hash = `bed=${bed}`; return; }
    const same = S.bed === bed && S.view === "patient";
    S.bed = bed;
    if (!same) {
      S.detail = null;
      show("patient");
      setHeader("patient", { bed });
      $("#view-patient").innerHTML = `<div class="loading">Opening bed ${pad2(bed)}…</div>`;
    }
    await loadPatient(bed, same);
  }

  async function loadPatient(bed, keepTab) {
    let d;
    try { d = await api(`patient/${bed}`); } catch (e) { console.error(e); return; }
    if (S.bed !== bed) return;
    S.detail = d;
    if (!keepTab) S.tab = params.get("tab") || "forecast";
    if (!keepTab) S.homeDay = null;
    // Open on the twin's own best choice; the doctor can try the others.
    const best = d.best && d.agents.find((a) => a.key === d.best.key);
    if (!keepTab || !d.agents.find((a) => a.key === S.agent)) {
      S.agent = best ? best.key : d.agents[0] ? d.agents[0].key : null;
    }
    renderPatient();
  }

  const TABS = [["forecast", "Forecast"], ["trends", "Trends"], ["record", "Record"], ["discharge", "After discharge"]];
  const vcard = (k, ic, label) =>
    `<div class="vc" data-k="${k}"><span class="ic">${icon(ic)}</span><span class="l">${label}</span><span class="v" id="pt-${k}">--</span></div>`;

  function renderPatient() {
    const d = S.detail;
    const t = tiles.get(d.bed);
    const n2 = lastObs(d).news2;
    const r = d.record;
    const tags = [
      `EF ${d.ef}%`, `NYHA ${d.nyha}`, `Day ${d.day} of stay`, `${r.priorAdmissions} admissions in 12 months`,
      d.diabetic && "Type 2 diabetes", r.ckd && "Chronic kidney disease", r.hypertension && "Hypertension",
    ].filter(Boolean);
    const confCol = d.confidenceBand === "high" ? "var(--twin)" : d.confidenceBand === "moderate" ? "var(--warn)" : "var(--crit)";
    setHeader("patient", d);

    $("#view-patient").innerHTML = `
      <div class="pt">
        <div class="pt-main">
          <section class="card pt-head">
            <button class="iconbtn" id="pt-back" title="Back to the ward (Esc)" aria-label="Back to the ward">${icon("back")}</button>
            ${avatar(d, "lg")}
            <div class="pt-id">
              <h2>${esc(d.name)}</h2>
              <div class="meta">${d.age} years · ${d.sex === "F" ? "Female" : "Male"} · ${esc(d.dx)}</div>
              <div class="chips">${tags.map((x) => `<span class="pill sm">${esc(x)}</span>`).join("")}</div>
            </div>
            <div class="facts">
              <div><span class="l">Bed</span><b class="big">${pad2(d.bed)}</b></div>
              <div><span class="l">Early-warning score</span><span class="pill ${n2cls(n2)}">NEWS2 ${n2}</span></div>
              <div><span class="l">Twin reads</span>${qpill(d.quadrant)}</div>
              <div><span class="l">Twin confidence</span><b class="big" style="color:${confCol}">${Math.round(d.confidence * 100)}%</b></div>
            </div>
            <div class="pt-nav">
              <button class="iconbtn" id="pt-prev" title="Previous bed" aria-label="Previous bed">${icon("back")}</button>
              <button class="iconbtn" id="pt-next" title="Next bed" aria-label="Next bed">${icon("next")}</button>
            </div>
          </section>

          <div class="pt-live">
            <section class="card">
              <div class="card-h"><h3>Live monitor</h3><small>virtual patient, driven by the twin between charted readings</small></div>
              <div class="strip tall">${waveHTML([...WAVES3, ["resp", "Resp"]])}</div>
            </section>
            <section class="card vcards">
              ${vcard("hr", "heart", "Heart rate")}${vcard("bp", "drop", "Blood pressure")}
              ${vcard("spo2", "o2", "SpO<sub>2</sub>")}${vcard("rr", "lungs", "Respiratory rate")}
            </section>
            <section class="card twin-est">
              <div class="card-h"><h3>Twin estimate</h3><span class="pill twin sm">not measured</span></div>
              <div class="rings">
                <div class="rg"><div id="ring-ci"></div><span class="l">Cardiac index</span><span class="t">cold below 2.2</span></div>
                <div class="rg"><div id="ring-pcwp"></div><span class="l">Filling pressure</span><span class="t">wet above 18</span></div>
              </div>
              <p class="note">A ward never measures either. The twin infers both from the chart, JVP, oedema, weight and capillary refill.</p>
            </section>
          </div>

          <nav class="tabs" role="tablist">${TABS.map(([k, l]) =>
            `<button class="tab ${S.tab === k ? "on" : ""}" role="tab" aria-selected="${S.tab === k}" data-tab="${k}">${l}</button>`).join("")}</nav>
          <div id="pt-panel"></div>
        </div>

        <aside class="card pt-side">
          <div class="card-h"><h3>Ward 4B</h3><small>${S.ward ? S.ward.beds.length : ""} beds · by priority</small></div>
          <label class="search inset">${icon("search")}<input id="pt-q" type="search" placeholder="Find a bed or name" autocomplete="off"></label>
          <div class="side-list" id="side-list"></div>
        </aside>
      </div>`;

    $("#pt-back").addEventListener("click", goWall);
    const order = S.ward ? orderedBeds().map((b) => b.bed) : [d.bed];
    const i = order.indexOf(d.bed);
    $("#pt-prev").addEventListener("click", () => openPatient(order[(i - 1 + order.length) % order.length]));
    $("#pt-next").addEventListener("click", () => openPatient(order[(i + 1) % order.length]));
    $$("#view-patient .tab").forEach((b) => b.addEventListener("click", () => {
      S.tab = b.dataset.tab;
      $$("#view-patient .tab").forEach((x) => {
        x.classList.toggle("on", x === b);
        x.setAttribute("aria-selected", x === b);
      });
      renderTab();
    }));
    $("#pt-q").addEventListener("input", renderSideList);
    renderSideList();

    // Reuse the tile's virtual patient so the traces stay in step with the ward.
    const vp = t ? t.vp : Waves.patient(d.bed);
    $$("#view-patient .strip canvas").forEach((cv) => Waves.attach(cv, vp, cv.dataset.ch, 120));

    updatePatientLive();
    Waves.refresh();
    renderTab();
  }

  function renderSideList() {
    const host = $("#side-list");
    if (!host || !S.ward) return;
    const q = ($("#pt-q").value || "").trim().toLowerCase();
    const rows = S.ward.beds.filter((b) => !q || `${b.name} ${b.bed} ${pad2(b.bed)} ${b.etiology}`.toLowerCase().includes(q));
    host.innerHTML = rows.map((b) => {
      const n2 = lastObs(b).news2;
      return `<button class="srow ${b.bed === S.bed ? "on" : ""}" data-bed="${b.bed}">
        ${avatar(b, "sm")}
        <span><b>${esc(b.name)}</b><span class="m">Bed ${pad2(b.bed)} · NEWS2 ${n2}</span></span>
        <span class="pill q sm" data-q="${b.quadrant.code}">${b.quadrant.code}</span></button>`;
    }).join("") || `<div class="quiet">No match.</div>`;
    host.querySelectorAll("[data-bed]").forEach((n) => n.addEventListener("click", () => openPatient(+n.dataset.bed)));
  }

  function updatePatientLive() {
    const d = S.detail;
    const b = S.ward && S.ward.beds.find((x) => x.bed === d.bed);
    if (!b || !$("#pt-hr")) return;
    const s = stateAt(b, S.clock);
    const set = (k, html, cls) => {
      const n = $(`#pt-${k}`);
      if (n.innerHTML !== html) n.innerHTML = html;
      n.parentElement.className = `vc ${cls}`;
    };
    set("hr", `${fmt(s.hr)}<small>bpm</small>`, flag("hr", s.hr));
    set("bp", `${fmt(s.sbp)}/${fmt(s.dbp)}<small>(${fmt(s.map)}) mmHg</small>`, flag("sbp", s.sbp));
    set("spo2", `${fmt(s.spo2)}<small>%</small>`, flag("spo2", s.spo2));
    set("rr", `${fmt(s.rr)}<small>/min</small>`, flag("rr", s.rr));
    Charts.ring($("#ring-ci"), {
      value: s.ci, lo: 0.8, hi: 4.2, threshold: 2.2, bad: (v) => v < 2.2, badColor: "var(--crit)",
      goodColor: "var(--twin)", fmt: (v) => v.toFixed(2), unit: "L/min/m²", size: 132,
    });
    Charts.ring($("#ring-pcwp"), {
      value: s.pcwp, lo: 4, hi: 40, threshold: 18, bad: (v) => v > 18, badColor: "var(--warn)",
      goodColor: "var(--twin)", fmt: (v) => Math.round(v), unit: "mmHg", size: 132,
    });
  }

  function renderTab() {
    const panel = $("#pt-panel");
    if (!panel) return;
    ({ forecast: tabForecast, trends: tabTrends, record: tabRecord, discharge: tabDischarge })[S.tab](panel);
  }

  // ---- forecast
  function tabForecast(panel) {
    const d = S.detail;
    const now = stateAt(d, S.clock);
    panel.innerHTML = `
      <div class="cols two">
        <section class="card">
          <div class="card-h"><h3>Haemodynamic map</h3><small>next ${S.horizon} h if nothing changes</small></div>
          <div id="qmap"></div>
          <div class="legend">
            <span><i style="background:var(--crit)"></i>If nothing changes</span>
            <span style="color:var(--ok)"><i class="dash"></i><span style="color:var(--ink-3)">Best single agent</span></span>
            <span><i style="background:var(--twin);width:9px;height:9px;border-radius:50%"></i>Now</span>
          </div>
          <p style="margin-top:14px"><b>${esc(d.profile.label)}.</b> ${esc(d.profile.description)}</p>
          <p class="guideline"><b>Guideline:</b> ${esc(d.profile.therapy)}</p>
        </section>
        <section class="card">
          <div class="card-h"><h3>Wait or act</h3><small>simulated on this patient's twin</small></div>
          <div id="wa-body"></div>
        </section>
      </div>
      <div class="cols two">
        <section class="card">
          <div class="card-h"><h3>Why the twin thinks so</h3></div>
          <ul class="why">${d.explain.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        </section>
        <section class="card">
          <div class="card-h"><h3>Every guideline option</h3><small>ranked by projected severity</small></div>
          <table class="data"><thead><tr><th>Decision</th><th>Cold-wet</th><th class="num-c">End CI</th><th class="num-c">End PCWP</th></tr></thead>
          <tbody>${[...d.options].sort((a, b) => a.severity - b.severity).map((o) => `
            <tr><td>${esc(o.label)}</td>
            <td>${o.deadline == null ? '<span class="pill sm ok">No crossing</span>' : `<span class="pill sm crit">From ${hhmm(o.deadline)}</span>`}</td>
            <td class="num-c">${fmt(o.ci, 2)}</td><td class="num-c">${fmt(o.pcwp)}</td></tr>`).join("")}</tbody></table>
        </section>
      </div>`;

    Charts.quadrant($("#qmap"), { path: d.forecast, best: d.bestPath, now: { pcwp: now.pcwp, ci: now.ci } });
    renderWaitAct();
  }

  function renderWaitAct() {
    const d = S.detail;
    const body = $("#wa-body");
    if (!body) return;
    if (!d.agents.length) {
      body.innerHTML = `<div class="callout">Compensated. Guideline therapy for this profile is oral optimisation — nothing on the acute formulary changes the next ${S.horizon} hours.</div>`;
      return;
    }
    body.innerHTML = `
      <div class="agentbar" role="radiogroup">${d.agents.map((a) =>
        `<button role="radio" aria-checked="${a.key === S.agent}" class="${a.key === S.agent ? "on" : ""}" data-agent="${a.key}">${esc(a.name)}</button>`).join("")}</div>
      <div class="slider"><span>Delayed dose given in</span><input type="range" id="wa-delay" min="1" max="10" step="1" value="${S.delay}"><output id="wa-out">${S.delay} h</output></div>
      <div class="rationale">${esc((d.agents.find((a) => a.key === S.agent) || {}).rationale || "")}</div>
      <div class="outcomes" id="wa-out3"></div>
      <div id="wa-chart"></div>
      <div class="legend" id="wa-legend"></div>`;
    body.querySelectorAll("[data-agent]").forEach((b) =>
      b.addEventListener("click", () => { S.agent = b.dataset.agent; renderWaitAct(); }));
    let timer;
    $("#wa-delay").addEventListener("input", (e) => {
      S.delay = +e.target.value;
      $("#wa-out").textContent = `${S.delay} h`;
      clearTimeout(timer);
      timer = setTimeout(loadWhatIf, 140);
    });
    loadWhatIf();
  }

  async function loadWhatIf() {
    const d = S.detail;
    let w;
    try { w = await api(`whatif/${d.bed}`, { agent: S.agent, delay: S.delay, now: S.clock.toFixed(3) }); } catch (e) { return; }
    if (!$("#wa-out3")) return;
    const span = S.horizon + 2;
    // Hours spent past a threshold over the simulated window, from 0.25 h steps.
    const hoursWhere = (arr, pred) => arr.filter(pred).length * 0.25;
    // A cold-dry patient never goes cold-WET; their danger is time spent
    // hypoperfused.  Everyone else is judged on time spent cold-wet.
    const dry = d.quadrant.code === "L";
    const KEYS = [["do_nothing", "Do nothing"], ["act_later", `Give at ${hhmm(w.given)}`], ["act_now", "Give now"]];
    const score = {};
    const cells = KEYS.map(([k, label]) => {
      const x = w[k];
      let v, sub;
      if (dry) {
        const i6 = Math.min(x.ci.length - 1, 24);          // 6 h at 0.25 h steps
        score[k] = x.ci[i6];
        v = `CI ${x.ci[i6].toFixed(2)}`;
        sub = `in 6 h · cold ${hoursWhere(x.ci, (c) => c < 2.2).toFixed(1)} of ${span} h`;
      } else {
        const h = x.ci.reduce((n, c, i) => n + (c < 2.2 && x.pcwp[i] > 18 ? 0.25 : 0), 0);
        score[k] = -h;
        v = `${h.toFixed(1)} h`;
        sub = h < 0.01 ? `never cold-wet in the next ${span} h`
          : `cold-wet of the next ${span} h` + (x.deadline != null && x.deadline > w.now + 0.01 ? ` · from ${hhmm(x.deadline)}` : "");
      }
      return { k, label, v, sub };
    });
    const top = Math.max(...Object.values(score));
    const spread = top - Math.min(...Object.values(score));
    $("#wa-out3").innerHTML = cells.map((c) => {
      const best = spread > 0.01 && score[c.k] >= top - 1e-6;
      return `<div class="outcome ${best ? "best" : ""}" data-k="${c.k}">
        <div class="k"><i></i>${c.label}</div>
        <div class="v">${c.v}${best ? `<span class="pill sm ok">${dry ? "highest" : "fewest"}</span>` : ""}</div>
        <div class="s">${c.sub}</div></div>`;
    }).join("");
    const all = [...w.do_nothing.ci, ...w.act_now.ci, ...w.act_later.ci];
    Charts.lines($("#wa-chart"), {
      t0: w.do_nothing.t[0], t1: w.do_nothing.t[w.do_nothing.t.length - 1],
      lo: Math.max(0.6, Math.floor(Math.min(...all, 2.0) * 5) / 5), hi: Math.ceil(Math.max(...all, 2.6) * 5) / 5,
      threshold: { v: 2.2, label: "cold · CI 2.2" },
      markers: [{ t: w.given, label: "delayed dose", color: "var(--warn)" }],
      series: [
        { t: w.do_nothing.t, v: w.do_nothing.ci, color: "var(--crit)" },
        { t: w.act_later.t, v: w.act_later.ci, color: "var(--warn)", dash: "6 4" },
        { t: w.act_now.t, v: w.act_now.ci, color: "var(--ok)" },
      ],
    });
    $("#wa-legend").innerHTML = `<span><i style="background:var(--crit)"></i>Do nothing</span>
      <span style="color:var(--warn)"><i class="dash"></i><span style="color:var(--ink-3)">Give at ${hhmm(w.given)}</span></span>
      <span><i style="background:var(--ok)"></i>Give now</span><span>· cardiac index, L/min/m²</span>`;
  }

  // ---- trends
  function tabTrends(panel) {
    const d = S.detail;
    const band = d.band;
    const t1 = d.synced + S.horizon;
    const t0 = Math.max(0, d.synced - 20);
    const obs = d.obs;
    const last = lastObs(d);
    const now = stateAt(d, S.clock);
    const series = [
      { key: "hr", label: "Heart rate", unit: "bpm", color: "var(--wave-ecg)", lo: 50, hi: 150, v: last.hr, past: obs.map((o) => ({ t: o.t, v: o.hr })) },
      { key: "sbp", label: "Systolic pressure", unit: "mmHg", color: "var(--wave-art)", lo: 60, hi: 160, v: last.sbp, past: obs.map((o) => ({ t: o.t, v: o.sbp })) },
      { key: "spo2", label: "SpO<sub>2</sub>", unit: "%", color: "var(--wave-pleth)", lo: 75, hi: 100, v: last.spo2, past: obs.map((o) => ({ t: o.t, v: o.spo2 })) },
      { key: "rr", label: "Respiratory rate", unit: "/min", color: "var(--wave-resp)", lo: 8, hi: 40, v: last.rr, past: obs.map((o) => ({ t: o.t, v: o.rr })) },
      { key: "ci", label: "Cardiac index", unit: "L/min/m²", color: "var(--twin)", lo: 0.8, hi: 4.4, v: now.ci, d: 2, twin: true, past: [],
        thresholds: [{ v: 2.2, color: "var(--crit)" }] },
      { key: "pcwp", label: "Filling pressure", unit: "mmHg", color: "var(--twin)", lo: 5, hi: 40, v: now.pcwp, twin: true, past: [],
        thresholds: [{ v: 18, color: "var(--warn)" }] },
    ];
    panel.innerHTML = `
      <div class="callout" style="margin-bottom:18px">Dots are what the ward charted; the value shown is the last charted reading, at ${hhmm(last.t)}. The dashed line is the twin's forecast from that reading, with the band it cannot rule out. <b>Cardiac index and filling pressure are never measured on a ward — only the twin can show them.</b></div>
      <div class="cols three">${series.map((s) => `
        <section class="card trend-card">
          <div class="th"><h3>${s.label}</h3>${s.twin ? '<span class="pill twin sm">twin only</span>' : ""}
            <span class="val">${fmt(s.v, s.d || 0)}<small>${s.unit}</small></span></div>
          <div id="tr-${s.key}"></div>
        </section>`).join("")}</div>`;
    for (const s of series) {
      Charts.trend($(`#tr-${s.key}`), {
        color: s.color, lo: s.lo, hi: s.hi, t0, t1, now: S.clock,
        past: s.past.filter((p) => p.t >= t0),
        forecast: { t: d.forecast.t, v: d.forecast[s.key] },
        band: { t: band.t, lo: band[s.key].lo, hi: band[s.key].hi },
        thresholds: s.thresholds,
      });
    }
  }

  // ---- record
  function tabRecord(panel) {
    const d = S.detail;
    const r = d.record;
    const pgx = r.pgxFactor === 1 ? "Normal metaboliser — standard beta-blocker response."
      : r.pgxFactor > 1 ? `${r.cyp2d6[0].toUpperCase() + r.cyp2d6.slice(1)} metaboliser — metoprolol exposure is higher; the twin simulates a ${r.pgxFactor}× effect.`
      : `Ultrarapid metaboliser — metoprolol cleared fast; the twin simulates a ${r.pgxFactor}× effect.`;
    const comorb = [d.diabetic && "Type 2 diabetes", r.ckd && "Chronic kidney disease", r.hypertension && "Hypertension"].filter(Boolean);
    const tw = d.twin;
    const kv = (k, v, u) => `<div><span>${k}</span><b>${v}<small>${u}</small></b></div>`;
    panel.innerHTML = `
      <div class="cols three">
        <section class="card">
          <div class="card-h"><h3>Laboratory</h3><small>admission panel</small></div>
          <table class="data"><tbody>${r.labs.filter((l) => l[1] != null).map(([k, v, u, flagged]) =>
            `<tr><td>${k}</td><td class="num-c">${v}</td><td>${u}</td>
             <td style="text-align:right">${flagged ? '<span class="pill sm crit">Out of range</span>' : '<span class="pill sm ok">Normal</span>'}</td></tr>`).join("")}</tbody></table>
          <div class="card-h" style="margin:16px 0 8px"><h3>Comorbidity</h3></div>
          <div class="chips">${comorb.length ? comorb.map((x) => `<span class="pill">${x}</span>`).join("") : '<span class="sub">None recorded</span>'}</div>
        </section>
        <section class="card">
          <div class="card-h"><h3>Medication</h3><small>on the chart</small></div>
          <div class="chips">${r.meds.map((m) => `<span class="medchip">${icon("pill")}${esc(m)}</span>`).join("")}</div>
          <div class="card-h" style="margin:18px 0 8px"><h3>Pharmacogenomics</h3><small>CYP2D6</small></div>
          <div class="callout twin">${icon("dna")} ${esc(pgx)}</div>
        </section>
        <section class="card">
          <div class="card-h"><h3>What the twin fitted</h3><small>from ${tw.nObs} charted readings</small></div>
          <div class="kv">
            ${kv("Contractility", tw.emax, "mmHg/mL")}
            ${kv("Stroke volume", tw.sv, "mL")}
            ${kv("Ejection fraction", tw.ef, "%")}
            ${kv("Vascular resistance", tw.svr, "mmHg·min/L")}
            ${kv("Lung water", tw.lungWater, "mL")}
            ${kv("Contractility trend", `${tw.progression > 0 ? "−" : "+"}${Math.abs(tw.progression)}`, "% / h")}
          </div>
        </section>
      </div>
      <div class="cols two">
        <section class="card">
          <div class="card-h"><h3>Next 12 hours</h3><small>meals, doses and rounds</small></div>
          <ul class="timeline">${d.timeline.map((e) => `
            <li class="${e.done ? "done" : ""}"><span class="t">${hhmm(e.t)}</span><span class="k ${e.kind}"></span>
            <span class="l"><b>${esc(e.label)}</b><span>${esc(e.detail)}</span></span></li>`).join("")}</ul>
          ${d.metabolic ? `
          <div class="callout warn" style="margin-top:14px"><b>Meal and glucose · HbA1c ${d.metabolic.hba1c}%.</b> Evening ${esc(d.metabolic.from.toLowerCase())}: glucose excursion about <b>+${d.metabolic.peakFrom} mg/dL</b>. Swapping to ${esc(d.metabolic.to.toLowerCase())} cuts it to +${d.metabolic.peakTo} and saves ${d.metabolic.sodiumSaved} mg sodium — less sympathetic drive and less retained water for a heart that tolerates neither.</div>` : ""}
        </section>
        <section class="card">
          <div class="card-h"><h3>Handover brief</h3><small>assembled from twin state, not written by a language model</small></div>
          <pre class="brief">${esc(d.brief)}</pre>
        </section>
      </div>`;
  }

  // ---- after discharge: the twin at home
  const TRACKERS = [
    { k: "weight", label: "Weight", unit: "kg", icon: "scale", color: "var(--twin)", d: 1 },
    { k: "rhr", label: "Resting heart rate", unit: "bpm", icon: "heart", color: "var(--wave-ecg)", d: 0 },
    { k: "hrv", label: "HRV", unit: "ms", icon: "pulse", color: "var(--wave-art)", d: 0 },
    { k: "awake", label: "Awake in bed", unit: "min a night", icon: "moon", color: "var(--wave-resp)", d: 0 },
    { k: "sleep", label: "Sleep", unit: "h a night", icon: "clock", color: "var(--cool)", d: 1 },
    { k: "steps", label: "Steps", unit: "per day", icon: "steps", color: "var(--wave-pleth)", d: 0 },
  ];
  const PLAN_COLOR = { no_change: "var(--crit)", restrict: "var(--warn)", diuretic: "var(--ok)" };

  function tabDischarge(panel) {
    panel.innerHTML = `
      <div class="cols two" style="grid-template-columns:minmax(0,1fr) minmax(0,1.45fr)">
        <section class="card">
          <div class="card-h"><h3>The twin at home</h3><small>the ward-fitted heart, read through a band and a scale</small></div>
          <div class="seg" id="course" style="margin:2px 0 12px">
            <button data-v="recorded" class="${S.course === "recorded" ? "on" : ""}">As recorded</button>
            <button data-v="decompensating" class="${S.course === "decompensating" ? "on" : ""}">With simulated decompensation</button>
          </div>
          <div class="slider"><span>Today</span><input type="range" id="dc-day" min="8" max="30" value="30"><output id="dc-day-out">day –</output></div>
          <div id="dc-head"><div class="loading">Reading the band…</div></div>
        </section>
        <section class="card">
          <div class="card-h"><h3>What would change it</h3><small>the same forecast under each home plan</small></div>
          <div class="outcomes" id="dc-plans"></div>
          <div id="dc-fluid"></div>
          <div class="legend" id="dc-fluid-legend"></div>
          <div class="card-h" style="margin:18px 0 6px"><h3>When the twin would have called</h3><small>chance of decompensation within a week, as it said it each day</small></div>
          <div id="dc-history"></div>
          <div class="legend" id="dc-history-legend"></div>
        </section>
      </div>
      <section class="card">
        <div class="card-h"><h3>What the band and scale saw</h3><small>last 3 days against the first week home · every channel labelled real or simulated</small></div>
        <div class="trackers three" id="dc-trackers"></div>
      </section>`;
    panel.querySelectorAll("#course button").forEach((b) =>
      b.addEventListener("click", () => { S.course = b.dataset.v; S.homeDay = null; tabDischarge(panel); }));
    let timer;
    $("#dc-day").addEventListener("input", (e) => {
      S.homeDay = +e.target.value;
      $("#dc-day-out").textContent = `day ${S.homeDay}`;
      clearTimeout(timer);
      timer = setTimeout(loadHome, 120);
    });
    loadHome();
  }

  async function loadHome() {
    const extra = { course: S.course };
    if (S.homeDay) extra.upto = S.homeDay;
    let r;
    try { r = await api(`discharge/${S.detail.bed}`, extra); } catch (e) { return; }
    if (!$("#dc-head")) return;
    const e = r.estimate, today = r.today;
    const slider = $("#dc-day");
    slider.max = r.totalDays;
    slider.value = today;
    $("#dc-day-out").textContent = `day ${today} of ${r.totalDays}`;

    // headline
    const col = e.band === "high" ? "var(--crit)" : e.band === "moderate" ? "var(--warn)" : "var(--ok)";
    const when = e.daysToEvent == null || e.daysToEvent > 21 ? "No decompensation expected in the next 3 weeks"
      : e.daysToEvent < 0.5 ? "Decompensating now"
      : `Decompensation in about <b>${Math.round(e.daysToEvent)} days</b>`
        + (e.interval[0] != null && e.interval[1] != null ? ` <span class="sub">(${Math.round(e.interval[0])}–${Math.round(e.interval[1])})</span>` : "");
    const share = Math.max(0, Math.min(1, e.fluidL / r.heart.limitL));
    const call = r.firstCall ? `The twin first called on <b>day ${r.firstCall}</b>` : "The twin never called";
    const truth = r.trueEventDay ? ` · the simulated patient decompensates on <b>day ${r.trueEventDay}</b>` : "";
    $("#dc-head").innerHTML = `
      <div class="risk"><div id="dc-ring"></div>
        <div><div class="band" style="color:${col}">${e.band === "high" ? "Likely within a week" : e.band === "moderate" ? "Possible within a week" : "Unlikely within a week"}</div>
          <div style="margin-top:4px;font-size:14px">${when}</div>
          <div class="sub" style="margin-top:6px">${call}${truth}.</div></div></div>
      <div class="gauge-row">
        <div class="gl"><span>Fluid retained, twin estimate</span><b>${e.fluidL >= 0 ? "+" : ""}${e.fluidL.toFixed(1)} L <small>of this heart's ${r.heart.limitL.toFixed(1)} L</small></b></div>
        <div class="bar fluid"><i style="width:${(share * 100).toFixed(0)}%;background:${share > 0.7 ? "var(--crit)" : share > 0.4 ? "var(--warn)" : "var(--twin)"}"></i></div>
        <div class="sub">Trend ≈ <b>${e.rate >= 0 ? "+" : ""}${e.rate} mL/day</b> · filling pressure ≈ <b style="color:var(--twin)">${e.pcwp} mmHg</b>, home at ${r.heart.pcwp} mmHg</div>
      </div>
      <ul class="why" style="margin-top:12px">${e.drivers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <div class="callout" style="margin-top:12px">${icon("watch")} ${esc(r.source.label)}${r.source.onset ? ` Decompensation overlaid from day ${r.source.onset}, ${r.source.rate} mL/day, following this heart's physiology.` : ""}</div>`;
    Charts.ring($("#dc-ring"), { value: e.pEvent * 100, lo: 0, hi: 100, color: col, fmt: (v) => `${Math.round(v)}%`, unit: "within 7 days", size: 128 });

    // plans
    const hasEvent = (p) => p.daysToEvent != null && p.daysToEvent <= 21;
    $("#dc-plans").innerHTML = ["no_change", "restrict", "diuretic"].map((k) => {
      const p = r.plans[k];
      return `<div class="outcome" data-plan="${k}">
        <div class="k"><i style="background:${PLAN_COLOR[k]}"></i>${esc(p.label)}</div>
        <div class="v">${hasEvent(p) ? `${Math.max(0, Math.round(p.daysToEvent))} days` : "&gt; 3 weeks"}</div>
        <div class="s">${Math.round(p.p14 * 100)}% chance within 14 days</div></div>`;
    }).join("");

    const fut = r.plans.no_change.fluidL.map((_, i) => today + i);
    const past = r.pastL.map((_, i) => i + 1);
    const all = [...r.pastL, ...Object.values(r.plans).flatMap((p) => p.fluidL), r.heart.limitL, ...(r.trueL || [])]
      .filter((v) => v != null);
    const series = [];
    if (r.trueL) series.push({ t: r.days.map((d) => d.day), v: r.trueL, color: "var(--ink-3)", dash: "2 4", width: 1.6 });
    series.push({ t: past, v: r.pastL, color: "var(--twin)", width: 2.6 });
    for (const k of ["no_change", "restrict", "diuretic"]) {
      series.push({ t: fut, v: r.plans[k].fluidL, color: PLAN_COLOR[k], dash: "6 4" });
    }
    Charts.lines($("#dc-fluid"), {
      t0: 1, t1: today + 21, lo: Math.min(-0.5, Math.floor(Math.min(...all))), hi: Math.max(1, Math.ceil(Math.max(...all) * 2) / 2),
      height: 210, xstep: 5, xfmt: (d) => `day ${d}`, yfmt: (v) => `${v} L`,
      threshold: { v: r.heart.limitL, label: `this heart's limit · ${r.heart.limitL.toFixed(1)} L` },
      markers: [{ t: today, label: "today", color: "var(--ink-2)" }],
      series,
    });
    $("#dc-fluid-legend").innerHTML = `<span><i style="background:var(--twin)"></i>Twin's estimate so far</span>
      ${r.trueL ? `<span style="color:var(--ink-3)"><i class="dash"></i>Simulated truth</span>` : ""}
      <span style="color:var(--crit)"><i class="dash"></i><span style="color:var(--ink-3)">No change</span></span>
      <span style="color:var(--warn)"><i class="dash"></i><span style="color:var(--ink-3)">Fluid restriction</span></span>
      <span style="color:var(--ok)"><i class="dash"></i><span style="color:var(--ink-3)">Double diuretic</span></span>`;

    // alarm by day
    const marks = [{ t: today, label: "today", color: "var(--ink-2)" }];
    if (r.source.onset) marks.push({ t: r.source.onset, label: "retention starts", color: "var(--warn)" });
    if (r.trueEventDay) marks.push({ t: r.trueEventDay, label: "decompensates", color: "var(--crit)" });
    Charts.lines($("#dc-history"), {
      t0: 1, t1: r.totalDays, lo: 0, hi: 1, height: 130, xstep: 5, xfmt: (d) => `day ${d}`,
      yfmt: (v) => `${Math.round(v * 100)}%`,
      threshold: { v: 0.5, label: "alarm · 50%" }, markers: marks,
      series: [{ t: r.history.map((h) => h.day), v: r.history.map((h) => h.p), color: "var(--twin)" }],
    });
    $("#dc-history-legend").innerHTML = `<span>No alarm in the first week: it is spent learning this patient's own baseline.</span>`;

    // trackers
    const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
    const vals = (k, lo, hi) => r.days.filter((d) => d.day >= lo && d.day <= hi && d[k] != null).map((d) => d[k]);
    $("#dc-trackers").innerHTML = TRACKERS.map((m) => {
      const real = r.source.channels[m.k] === "real";
      const src = m.k === "weight" ? "Smart scale · simulated" : real ? "Fitbit · real record"
        : r.source.kind === "real" ? "Simulated · not in the public data" : "Simulated · fetch the data for real records";
      const now = mean(vals(m.k, today - 2, today)), base = mean(vals(m.k, 1, 7));
      const tint = `background:color-mix(in srgb, ${m.color} 13%, transparent);color:${m.color}`;
      if (!r.days.some((d) => d[m.k] != null)) {
        return `<div class="tracker"><div class="tt"><span class="ic" style="${tint}">${icon(m.icon)}</span>${m.label}</div>
          <div class="none">No readings in this record.</div><div class="src">${icon("watch")}${src}</div></div>`;
      }
      let delta = "";
      if (now != null && base != null) {
        const dv = m.k === "steps" || m.k === "hrv" ? `${Math.round((now / base - 1) * 100)}%` : (now - base).toFixed(m.d);
        delta = `<span class="pill sm delta">${/^-/.test(dv) ? "−" + dv.slice(1) : "+" + dv} vs week 1</span>`;
      }
      const show = now == null ? "--" : m.k === "steps" ? Math.round(now).toLocaleString() : now.toFixed(m.d);
      return `<div class="tracker">
        <div class="tt"><span class="ic" style="${tint}">${icon(m.icon)}</span>${m.label}<span class="pill sm ${real ? "ok" : ""}" style="margin-left:auto">${real ? "real" : "simulated"}</span></div>
        <div class="tv"><b>${show}</b><small>${m.unit}</small>${delta}</div>
        <div data-spark="${m.k}"></div>
        <div class="src">${icon(m.k === "weight" ? "scale" : "watch")}${src}</div></div>`;
    }).join("");
    for (const m of TRACKERS) {
      const host = $(`#dc-trackers [data-spark="${m.k}"]`);
      if (!host) continue;
      Charts.spark(host, {
        t: r.days.map((d) => d.day), v: r.days.map((d) => d[m.k]), color: m.color,
        marker: r.source.onset, today,
        fmt: m.k === "steps" ? (v) => `${(v / 1000).toFixed(1)}k` : (v) => v.toFixed(m.d),
      });
    }
  }

  // ------------------------------------------------------------- evidence
  async function renderEvidence() {
    const root = $("#view-evidence");
    if (!root.innerHTML) root.innerHTML = `<div class="loading">Loading evidence…</div>`;
    let ev;
    try { ev = await api("evidence"); } catch (e) { root.innerHTML = `<div class="loading">Evidence unavailable.</div>`; return; }
    const s = ev.summary;
    const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
    let head = "";
    if (s) {
      const cf = s.counterfactual;
      const w = s.state_recovery.ward;
      head = `
        <div class="stats">
          <section class="card stat"><div class="v">${pct(w.quadrant_acc)}</div><div class="l">Stevenson profile correct from a 4-hourly ward chart</div></section>
          <section class="card stat"><div class="v">${(cf.direction_accuracy * 100).toFixed(1)}%</div><div class="l">drug effects predicted in the right direction — ${cf.direction_n.toLocaleString()} checks against <b>simulated</b> patients; real-dose calibration pending</div></section>
          <section class="card stat"><div class="v">r ${cf.effect_ci_corr.toFixed(2)}</div><div class="l">predicted vs true change in cardiac index</div></section>
          <section class="card stat"><div class="v">${(cf.regret_news2_reflex / Math.max(cf.regret_twin, 1e-3)).toFixed(0)}×</div><div class="l">less decision regret than diuresing whenever NEWS2 fires</div></section>
        </div>`;
    }
    const curve = s ? `
      <section class="card">
        <div class="card-h"><h3>As an alarm</h3><small>sensitivity vs alarm burden · ${s.operating_curves.events} deteriorations</small></div>
        <div id="ev-curve"></div>
        <div class="legend"><span><i style="background:var(--ink-3)"></i>NEWS2, thresholds 9 → 3</span><span><i style="background:var(--twin)"></i>Twin, horizons 0 → 24 h</span><span>· x: alarms per patient-day · y: deteriorations caught before onset</span></div>
        <div class="callout warn" style="margin-top:12px"><b>Read this plainly: at matched alarm burden NEWS2 catches as many deteriorations or more.</b> The twin is not a better alarm and we do not claim it is — with one or two readings, a downward trend is not yet knowable. Prahari sits behind the early-warning score: NEWS2 says who is sick; the twin says what kind of sick, what to do, and what waiting costs.</div>
      </section>` : "";
    root.innerHTML = `
      ${head}
      <div class="cols two">
        ${curve}
        <section class="card">
          <div class="card-h"><h3>This ward, right now</h3><small>twin vs ground truth</small></div>
          <table class="data"><thead><tr><th>Bed</th><th>True</th><th>Twin</th><th class="num-c">CI true / twin</th><th class="num-c">PCWP true / twin</th><th class="num-c">Conf.</th></tr></thead>
          <tbody>${ev.rows.map((r) => `<tr><td><b>${pad2(r.bed)}</b></td><td><span class="pill q sm" data-q="${r.trueQ}">${r.trueQ}</span></td>
            <td>${r.trueQ === r.twinQ ? `<span class="pill q sm" data-q="${r.twinQ}">${r.twinQ}</span>` : `<span class="pill sm solid crit">${r.twinQ} · wrong</span>`}</td>
            <td class="num-c">${r.trueCI} / ${r.twinCI}</td><td class="num-c">${r.truePCWP} / ${r.twinPCWP}</td><td class="num-c">${Math.round(r.confidence * 100)}%</td></tr>`).join("")}</tbody></table>
          <p class="sub" style="margin-top:12px">Ground truth exists only because the ward is simulated. The full method and every number are reproduced by <code>scripts/run_experiments.py</code>.</p>
        </section>
      </div>
      ${s && (s.real_mimic || s.home) ? `<div class="cols two">${realMimicCard(s.real_mimic)}${homeCard(s.home)}</div>` : ""}`;
    if (s) {
      const oc = s.operating_curves;
      Charts.lines($("#ev-curve"), {
        t0: 1.4, t1: 3.7, lo: 0.3, hi: 1.0, height: 230, xstep: 0.5,
        xfmt: (v) => v.toFixed(1), yfmt: (v) => `${Math.round(v * 100)}%`,
        series: [
          { t: oc.news2.map((x) => x.alarms_per_patient_day), v: oc.news2.map((x) => x.sensitivity), color: "var(--ink-3)" },
          { t: oc.prahari.map((x) => x.alarms_per_patient_day), v: oc.prahari.map((x) => x.sensitivity), color: "var(--twin)" },
        ],
      });
    }
  }

  // Real-data panels for the evidence page.
  function realMimicCard(r) {
    if (!r) return "";
    const f = (x, d = 2) => (x == null ? "—" : x.toFixed(d));
    const rows = [["cvp", "Central venous pressure", "mmHg"], ["ci", "Cardiac index", "L/min/m²"],
                  ["pa_dia", "Wedge vs PA diastolic", "mmHg"]]
      .map(([k, label, unit]) => {
        const x = r.held_out[k];
        return `<tr><td>${label}</td><td class="num-c">${x.stays}</td><td class="num-c">${x.n}</td>
          <td class="num-c">${f(x.r)}</td><td class="num-c">${f(x.bias, 1)} ${unit}</td></tr>`;
      }).join("");
    const fc = r.forecast.map;
    return `<section class="card">
      <div class="card-h"><h3>Real patients</h3><span class="pill cool sm">MIMIC-IV demo</span><small>${r.cohort.stays} heart-failure ICU stays</small></div>
      <table class="data"><thead><tr><th>Twin vs withheld measurement</th><th class="num-c">Stays</th><th class="num-c">Points</th><th class="num-c">r</th><th class="num-c">Bias</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="sub" style="margin-top:12px">Next-round forecast of mean arterial pressure over ${r.forecast.forecasts} rounds: twin ${fc.twin.toFixed(1)} mmHg error vs ${fc.persistence.toFixed(1)} for "no change"${fc.twin_no_drugs <= fc.twin ? ` — and knowing which drugs were started did not help (${fc.twin_no_drugs.toFixed(1)} without them): drug effect sizes are not yet calibrated to real dosing` : ""}.</p>
      <div class="callout warn">${r.held_out.ci.bias > 0 && r.held_out.cvp.bias < 0
        ? "<b>Estimates move with real measurements, but read real ICU patients as less sick than they are</b> — output high, filling pressure low."
        : "<b>Estimates are compared with real measurements the twin never saw.</b>"}
        Few stays have these measurements; this shows direction and calibration, not clinical validity.</div>
    </section>`;
  }

  function homeCard(h) {
    if (!h) return "";
    const pc = (x) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
    const sb = h.simulated.band_and_scale, so = h.simulated.band_only;
    const row = (x, setting) => `<tr${x === sb.twin || x === so.twin ? ' class="hl"' : ""}><td>${esc(x.label)}</td><td>${setting}</td>
      <td class="num-c">${pc(x.false_alarm_rate)}</td><td class="num-c">${x.detected} / ${x.eligible}</td>
      <td class="num-c">${x.median_lead_days == null ? "—" : x.median_lead_days.toFixed(1)}</td></tr>`;
    const at = h.at_alarm, real = h.real;
    return `<section class="card">
      <div class="card-h"><h3>The twin at home</h3><span class="pill twin sm">after discharge</span><small>${h.cohort.patients} simulated patients, each sent home stable and retaining · real wear noise</small></div>
      <table class="data"><thead><tr><th>Detector</th><th>Data</th><th class="num-c">False alarms</th><th class="num-c">Caught ahead</th><th class="num-c">Days ahead</th></tr></thead>
      <tbody>${row(sb.weight_3d, "scale")}${row(sb.weight_dry, "scale")}${row(sb.drift, "band + scale")}${row(sb.twin, "band + scale")}
        ${row(so.drift, "band only")}${row(so.twin, "band only")}</tbody></table>
      <p class="sub" style="margin-top:12px">At its first alarm the twin's forecast was off by ${at.days_to_event_mae.toFixed(1)} days on average; the true day fell in its 10–90% range ${pc(at.interval_coverage)} of the time. From the ward fit alone it places each heart's decompensation point within a median ${Math.round(h.event_fluid.median_abs_ml)} mL.</p>
      <div class="callout warn">${real ? `On ${real.users} real volunteers' records, via Apple Health export, the twin false-alarmed on <b>${pc(real.band_and_scale.twin.false_alarm_rate)}</b> of days with a scale and <b>${pc(real.band_only.twin.false_alarm_rate)}</b> on the band alone (drift score ${pc(real.band_only.drift.false_alarm_rate)}). ` : ""}<b>With a scale it is as good as the best weight rule, slightly earlier; on the band alone it catches far more but false-alarms too often to use by itself.</b> Every simulated decompensation here is fluid build-up, which flatters weight.</div>
    </section>`;
  }

  // ------------------------------------------------------------- handover
  async function renderHandover() {
    const root = $("#view-handover");
    let text;
    try { text = await api("handover", null, true); } catch (e) { text = "Unavailable."; }
    const beds = S.ward ? S.ward.beds : [];
    root.innerHTML = `
      <div class="cols" style="grid-template-columns:minmax(0,1fr) 380px;align-items:start">
        <section class="card doc">
          <div class="card-h"><h3>Ward 4B · handover at ${hhmm(S.clock)}</h3><small>assembled from twin state · every figure computed · no language model wrote this</small>
            <span class="grow"></span><span id="ho-actions"><button class="btn" id="ho-print">${icon("print")}Print</button></span></div>
          <pre class="brief">${esc(text)}</pre>
          <p class="sub" style="margin-top:12px">Verify at the bedside.</p>
        </section>
        <aside class="card" id="ho-glance">
          <div class="card-h"><h3>At a glance</h3><small>in handover order</small></div>
          <div class="queue-list">${beds.map((b) => {
            const n2 = lastObs(b).news2;
            const st = tileState(b);
            return `<button class="qrow ${st === "alert" ? "crit" : st === "watch" ? "warn" : ""}" data-bed="${b.bed}">
              ${avatar(b, "sm")}
              <span><span class="qh"><b>Bed ${pad2(b.bed)}</b>· ${esc(b.name)}</span>
                <span class="qflow"><span class="pill sm ${n2cls(n2)}">NEWS2 ${n2}</span>${qpill(b.quadrant, "sm")}</span>
                <span class="qact">${planLine(b)}</span></span></button>`;
          }).join("")}</div>
        </aside>
      </div>`;
    $("#ho-print").addEventListener("click", () => window.print());
    root.querySelectorAll("[data-bed]").forEach((n) => n.addEventListener("click", () => openPatient(+n.dataset.bed)));
  }

  // ---------------------------------------------------------------- theme
  function applyTheme(t) {
    S.theme = t === "night" ? "night" : "day";
    document.documentElement.dataset.theme = S.theme;
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    Waves.palette({ ecg: v("--wave-ecg"), art: v("--wave-art"), pleth: v("--wave-pleth"), resp: v("--wave-resp") });
    const b = $("#btn-theme");
    b.querySelector("[data-icon]").innerHTML = icon(S.theme === "night" ? "sun" : "moon");
    b.querySelector(".lab").textContent = S.theme === "night" ? "Day" : "Night";
  }

  // ---------------------------------------------------------------- init
  function segment(id, fn) {
    $$(`#${id} button`).forEach((b) => b.addEventListener("click", () => {
      $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b));
      fn(b.dataset.v);
    }));
  }

  function bind() {
    $$("[data-icon]").forEach((n) => (n.innerHTML = icon(n.dataset.icon)));
    $("#btn-live").addEventListener("click", () => setLive(!S.live));
    $("#btn-theme").addEventListener("click", () => {
      applyTheme(S.theme === "night" ? "day" : "night");
      store.set("prahari.theme", S.theme);
    });
    segment("speed", (v) => { S.speed = +v; });
    segment("cadence", (v) => { S.cadence = +v; S.reveal = null; sync(true); });
    segment("filter", (v) => { S.filter = v; applyFilter(); });
    segment("sort", (v) => { S.sort = v; if (S.ward) renderWall(); });
    segment("layout", (v) => { S.layout = v; applyFilter(); });
    $$("#layout button").forEach((x) => x.classList.toggle("on", x.dataset.v === S.layout));
    $("#ward-q").addEventListener("input", (e) => { S.q = e.target.value; applyFilter(); });
    $("#queue").addEventListener("click", (e) => {
      const b = e.target.closest("[data-bed]");
      if (b) openPatient(+b.dataset.bed);
    });
    window.addEventListener("hashchange", route);
    let rz;
    window.addEventListener("resize", () => {
      clearTimeout(rz);
      rz = setTimeout(() => {
        if (S.view === "patient" && S.detail && S.tab !== "discharge") renderTab();
        if (S.view === "evidence") renderEvidence();
      }, 250);
    });
    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea")) return;
      if (e.key === " ") { e.preventDefault(); setLive(!S.live); }
      else if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "Escape") goWall();
    });
  }

  async function init() {
    bind();
    applyTheme(S.theme);
    setLive(false);
    await sync(true);
    route();
    requestAnimationFrame(loop);
  }

  init();
})();
