/* TartanGrid — shared frontend utilities (owner: B).
 *
 * Defines the global `TG` (CONTRACT §10):
 *   TG.api(path, {method, body})   -> parsed JSON, null on 204, throws Error{status, detail} on non-2xx
 *   TG.workerColor(workerId)       -> stable "hsl(...)" string
 *   TG.colorize(n, maxIter)        -> [r, g, b]; n == maxIter is black
 *   TG.fmtAgo(seconds), TG.fmtTime(iso)
 * plus page helpers (DOM builder, badges, polling, event feed, tile rendering).
 *
 * Mock mode: add `?mock=1` to any page URL. TG.api is then served by an in-memory
 * simulation (bottom of this file) that follows the CONTRACT response shapes, so the
 * UI can be developed before the backend exists. Mock pages show a "MOCK DATA" badge.
 */
(function () {
  "use strict";

  const TG = {};
  const qs = new URLSearchParams(location.search);
  TG.MOCK = qs.get("mock") === "1";
  TG.query = (name) => qs.get(name);

  /** Append ?mock=1 to internal links while in mock mode. */
  TG.link = (url) => (TG.MOCK ? url + (url.includes("?") ? "&" : "?") + "mock=1" : url);

  // ---------------------------------------------------------------- API ----

  TG.api = async function (path, opts = {}) {
    if (TG.MOCK) return Mock.handle(path, opts);
    const method = opts.method || (opts.body !== undefined ? "POST" : "GET");
    const init = { method, headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      throw makeError(0, null, "Network error: " + e.message);
    }
    if (res.status === 204) return null;
    const text = await res.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (_) { data = text; }
    }
    if (!res.ok) {
      const detail = data && typeof data === "object" && "detail" in data ? data.detail : data;
      throw makeError(res.status, detail, TG.formatDetail(detail) || res.statusText || "HTTP " + res.status);
    }
    return data;
  };

  function makeError(status, detail, message) {
    const err = new Error(message);
    err.status = status;
    err.detail = detail;
    return err;
  }

  /** Human-readable text for a FastAPI `detail` (string, or 422 list of {loc, msg}). */
  TG.formatDetail = function (detail) {
    if (detail == null) return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          if (!d || typeof d !== "object") return String(d);
          const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
          return (loc ? loc + ": " : "") + (d.msg || JSON.stringify(d));
        })
        .join("\n");
    }
    try { return JSON.stringify(detail); } catch (_) { return String(detail); }
  };

  // ------------------------------------------------------------- colors ----

  function hashString(s) {
    let h = 2166136261; // FNV-1a
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }
  // Worker colors: hashing alone collides too often with 3–5 nodes (two oranges kill the
  // "color by worker" demo). So pages call TG.assignWorkerColors(stats.workers): workers get
  // distinct palette colors in registration order — the same order on every page, hence the
  // same color everywhere. Unknown ids (or beyond the palette) fall back to a hash hue.
  const PALETTE = [
    [56, 189, 248], [251, 191, 36], [132, 204, 22], [167, 139, 250], [244, 114, 182],
    [45, 212, 191], [251, 146, 60], [96, 165, 250], [74, 222, 128], [251, 113, 133],
  ];
  const assigned = new Map(); // worker_id -> [r, g, b]
  TG.assignWorkerColors = function (workers) {
    const sorted = (workers || []).slice().sort((a, b) =>
      String(a.registered_at || "").localeCompare(String(b.registered_at || "")) ||
      String(a.worker_id).localeCompare(String(b.worker_id)));
    assigned.clear();
    sorted.forEach((w, i) => { if (i < PALETTE.length) assigned.set(w.worker_id, PALETTE[i]); });
  };
  function workerHue(id) {
    return hashString(String(id || "")) % 360;
  }
  TG.workerColor = (id) => {
    const c = assigned.get(id);
    return c ? `rgb(${c[0]}, ${c[1]}, ${c[2]})` : `hsl(${workerHue(id)}, 70%, 55%)`;
  };
  TG.workerColorAlpha = (id, a) => {
    const c = assigned.get(id);
    return c ? `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})` : `hsla(${workerHue(id)}, 70%, 55%, ${a})`;
  };

  // Gradient stops for escaped pixels: deep blue -> blue -> cyan -> white -> orange -> CMU red.
  const STOPS = [
    [0.0, [6, 10, 38]],
    [0.22, [22, 74, 160]],
    [0.42, [64, 196, 222]],
    [0.62, [244, 244, 240]],
    [0.8, [245, 152, 40]],
    [1.0, [196, 18, 48]],
  ];
  const lutCache = new Map();

  /** Uint8Array(3 * (maxIter + 1)) with an RGB triple per iteration count. */
  TG.colorLUT = function (maxIter) {
    let lut = lutCache.get(maxIter);
    if (lut) return lut;
    lut = new Uint8Array(3 * (maxIter + 1));
    const denom = Math.log(maxIter + 1);
    for (let n = 0; n < maxIter; n++) {
      const t = denom > 0 ? Math.log(n + 1) / denom : 0;
      let k = 1;
      while (k < STOPS.length - 1 && STOPS[k][0] < t) k++;
      const [t0, c0] = STOPS[k - 1];
      const [t1, c1] = STOPS[k];
      const f = t1 > t0 ? Math.min(1, Math.max(0, (t - t0) / (t1 - t0))) : 0;
      for (let ch = 0; ch < 3; ch++) lut[3 * n + ch] = Math.round(c0[ch] + (c1[ch] - c0[ch]) * f);
    }
    // n == maxIter (inside the set) stays [0, 0, 0]
    lutCache.set(maxIter, lut);
    return lut;
  };

  TG.colorize = function (n, maxIter) {
    const lut = TG.colorLUT(maxIter);
    const i = 3 * Math.max(0, Math.min(maxIter, n | 0));
    return [lut[i], lut[i + 1], lut[i + 2]];
  };

  /** Build an ImageData for one tile result (row-major ints, CONTRACT §4.4). */
  TG.tileImageData = function (data, w, h, maxIter) {
    const img = new ImageData(w, h);
    const px = img.data;
    const lut = TG.colorLUT(maxIter);
    const len = Math.min(data.length, w * h);
    for (let i = 0; i < len; i++) {
      let n = data[i] | 0;
      if (n < 0) n = 0; else if (n > maxIter) n = maxIter;
      const o = 4 * i, c = 3 * n;
      px[o] = lut[c]; px[o + 1] = lut[c + 1]; px[o + 2] = lut[c + 2]; px[o + 3] = 255;
    }
    return img;
  };

  /** CONTRACT §4.2 split, used by the single-device baseline. */
  TG.splitInputs = function (p) {
    const ts = p.tile_size, cols = Math.floor(p.width / ts), rows = Math.floor(p.height / ts);
    const out = [];
    for (let ty = 0; ty < rows; ty++) {
      for (let tx = 0; tx < cols; tx++) {
        out.push({
          tile_x: tx * ts, tile_y: ty * ts, tile_w: ts, tile_h: ts,
          image_width: p.width, image_height: p.height, max_iter: p.max_iter,
          x_min: p.x_min, x_max: p.x_max, y_min: p.y_min, y_max: p.y_max,
        });
      }
    }
    return out;
  };

  // ------------------------------------------------------------ formats ----

  TG.fmtAgo = function (seconds) {
    if (seconds == null || !isFinite(seconds)) return "—";
    const s = Math.max(0, seconds);
    if (s < 10) return s.toFixed(1) + "s ago";
    if (s < 60) return Math.round(s) + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m " + Math.round(s % 60) + "s ago";
    return Math.floor(s / 3600) + "h ago";
  };

  TG.fmtTime = function (iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleTimeString([], { hour12: false });
  };

  TG.fmtSeconds = (ms) => (ms == null || !isFinite(ms) ? "—" : (ms / 1000).toFixed(1) + " s");
  TG.fmtInt = (n) => (n == null ? "—" : Number(n).toLocaleString());

  // ---------------------------------------------------------------- DOM ----

  /** Safe element builder: strings become text nodes (never innerHTML for API data). */
  TG.el = function (tag, attrs, ...children) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false) continue;
        if (k === "class") e.className = v;
        else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
        else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
        else if (k === "text") e.textContent = v;
        else e.setAttribute(k, v === true ? "" : v);
      }
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      e.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return e;
  };
  TG.$ = (sel, root = document) => root.querySelector(sel);

  TG.typeBadge = (type) =>
    TG.el("span", { class: "badge type-" + (type === "native" ? "native" : "browser") },
      type === "native" ? "NATIVE" : "BROWSER");

  TG.statusBadge = (status) => TG.el("span", { class: "status status-" + status }, status);

  TG.workerDot = (id) => TG.el("span", { class: "dot", style: { background: TG.workerColor(id) } });

  /** Header navigation + footer. `active` is one of: dashboard | new | contribute | job. */
  TG.renderChrome = function (active) {
    const header = TG.$("header.site");
    if (header) {
      header.replaceChildren(
        TG.el("a", { class: "brand", href: TG.link("/") },
          TG.el("span", { class: "logo" }), "TartanGrid"),
        TG.el("nav", null,
          navLink("/", "Dashboard", active === "dashboard"),
          navLink("/new.html", "New Job", active === "new"),
          navLink("/contribute.html", "Contribute", active === "contribute")),
        TG.MOCK ? TG.el("span", { class: "mock-badge", title: "Simulated data — ?mock=1" }, "MOCK DATA") : null
      );
    }
    const footer = TG.$("footer.site");
    if (footer) {
      footer.textContent = "Only allowlisted templates run. Contributors can pause or leave at any time.";
    }
  };
  function navLink(href, label, on) {
    return TG.el("a", { href: TG.link(href), class: on ? "active" : null }, label);
  }

  /** Top-of-page error banner (null hides it). */
  TG.setBanner = function (msg) {
    let b = TG.$("#banner");
    if (!b) {
      b = TG.el("div", { id: "banner", class: "banner" });
      const main = TG.$("main") || document.body;
      main.prepend(b);
    }
    b.textContent = msg || "";
    b.hidden = !msg;
  };

  /** Repeating poll without overlap. Returns stop(). */
  TG.startPolling = function (fn, ms, onError) {
    let stopped = false, timer = null;
    const loop = async () => {
      if (stopped) return;
      try {
        await fn();
        if (onError) onError(null);
      } catch (e) {
        if (onError) onError(e);
        else console.warn(e);
      }
      if (!stopped) timer = setTimeout(loop, ms);
    };
    loop();
    return () => { stopped = true; clearTimeout(timer); };
  };

  // --------------------------------------------------------- event feed ----

  const ALERT_TYPES = new Set(["lease_expired", "task_rejected", "task_failed", "job_failed", "worker_offline", "task_error"]);
  const GOOD_TYPES = new Set(["job_completed", "worker_joined", "worker_back"]);
  const NOISY_TYPES = new Set(["task_leased", "task_done"]);
  const FLASH_TYPES = new Set(["worker_offline", "lease_expired"]);

  /** attempt number parsed from a task_leased message "(attempt N)"; 1 if absent. */
  TG.eventAttempt = function (ev) {
    const m = /\(attempt (\d+)\)/.exec(ev.message || "");
    return m ? parseInt(m[1], 10) : 1;
  };
  TG.isReassigned = (ev) => ev.type === "task_leased" && TG.eventAttempt(ev) > 1;

  /**
   * Live event list. `listEl` gets one row per event (newest first).
   * opts: { jobId, hideNoisyEl (checkbox), countEl, max }
   * Reassigned leases are never hidden: they are the point of the demo.
   */
  TG.EventFeed = class {
    constructor(listEl, opts = {}) {
      this.listEl = listEl;
      this.jobId = opts.jobId || null;
      this.hideNoisyEl = opts.hideNoisyEl || null;
      this.countEl = opts.countEl || null;
      this.max = opts.max || 100;
      this.since = 0;
      this.events = []; // newest first
      this.fresh = new Set();
      this.loaded = false;
      this.seen = new Set();
      this.inflight = null;
      if (this.hideNoisyEl) this.hideNoisyEl.addEventListener("change", () => this.render());
    }
    /** Concurrent callers share one request, so events are never fetched twice. */
    poll() {
      if (!this.inflight) this.inflight = this._poll().finally(() => { this.inflight = null; });
      return this.inflight;
    }
    async _poll() {
      for (let round = 0; round < 5; round++) {
        const limit = this.loaded ? 200 : this.max;
        let q = `/api/events?since=${this.since}&limit=${limit}`;
        if (this.jobId) q += `&job_id=${encodeURIComponent(this.jobId)}`;
        const r = await TG.api(q);
        const raw = (r && r.events) || [];
        const evs = raw.filter((e) => !this.seen.has(e.id));
        evs.forEach((e) => this.seen.add(e.id));
        if (this.loaded) evs.forEach((e) => this.fresh.add(e.id));
        for (const e of evs) this.events.unshift(e);
        if (this.events.length > this.max * 3) this.events.length = this.max * 3;
        if (r && r.next_since != null) this.since = r.next_since;
        this.loaded = true;
        if (raw.length < limit) break;
      }
      this.render();
    }
    render() {
      const hideNoisy = this.hideNoisyEl ? this.hideNoisyEl.checked : false;
      const shown = [];
      let hidden = 0;
      for (const e of this.events) {
        if (hideNoisy && NOISY_TYPES.has(e.type) && !TG.isReassigned(e)) { hidden++; continue; }
        if (shown.length < this.max) shown.push(e);
      }
      this.listEl.replaceChildren(...shown.map((e) => this.row(e)));
      if (this.countEl) this.countEl.textContent = hidden ? `(${hidden} routine events hidden)` : "";
      this.fresh.clear();
    }
    row(e) {
      const reassigned = TG.isReassigned(e);
      let cls = "ev";
      if (ALERT_TYPES.has(e.type)) cls += " ev-alert";
      else if (GOOD_TYPES.has(e.type)) cls += " ev-good";
      if (reassigned) cls += " ev-reassigned";
      if (this.fresh.has(e.id) && (FLASH_TYPES.has(e.type) || reassigned)) cls += " flash";
      return TG.el("li", { class: cls },
        TG.el("span", { class: "ev-time" }, TG.fmtTime(e.ts)),
        TG.el("span", { class: "ev-type" }, reassigned ? "REASSIGNED" : e.type),
        e.worker_id ? TG.workerDot(e.worker_id) : TG.el("span", { class: "dot dot-empty" }),
        TG.el("span", { class: "ev-msg" }, e.message || ""));
    }
  };

  // =================================================================== MOCK ==
  // In-memory simulation of the backend, used only with ?mock=1.
  // It follows CONTRACT §6 shapes and semantics (204/409/422/429, since=, attempts,
  // lazy reap, TTL 10 s, offline after 12 s). Timeline per page load:
  //   t=0  Browser-01, Browser-02 (1 slot each) and Native-Harry (2 slots) join
  //   t=0  job_mock01 (quick preset, 48 tiles) is created
  //   t≈6  Browser-02 leases a tile and then "crashes" (no result, no heartbeat)
  //        -> lease expires at ~t=16, tile is reassigned (attempt 2);
  //        worker_offline at ~t=18; the job finishes at ~t=20.
  // Tile data comes from a tiny mock kernel below (iterations capped for speed);
  // real computation lives in C's mandelbrot_kernel.js.

  const Mock = (function () {
    const TTL = 10, OFFLINE_AFTER = 12, MAX_ATTEMPTS = 3, MAX_ACTIVE = 2, TICK = 0.25;
    const PRESETS = [
      { id: "quick", label: "Quick test — full set (small)",
        params: { width: 512, height: 384, tile_size: 64, max_iter: 256, x_min: -2.2, x_max: 1.0, y_min: -1.2, y_max: 1.2 } },
      { id: "demo", label: "Demo — Seahorse Valley (heavy)",
        params: { width: 2048, height: 1536, tile_size: 128, max_iter: 8000, x_min: -0.8, x_max: -0.7, y_min: 0.05, y_max: 0.125 } },
    ];
    const T0 = Date.now() / 1000;
    let simT = T0, eventSeq = 0, taskSeq = 0, jobSeq = 0;
    const events = [], workers = [], jobs = [];
    const iso = (ts) => (ts == null ? null : new Date(ts * 1000).toISOString());
    const wById = (id) => workers.find((w) => w.id === id);

    function ev(ts, type, message, extra = {}) {
      const e = Object.assign({ id: ++eventSeq, ts, type, message, job_id: null, task_id: null, worker_id: null }, extra);
      events.push(e);
      return e.id;
    }
    function addWorker(ts, name, type, slots, crashAfter) {
      const w = { id: "wkr_" + (0x1a2b3c00 + workers.length).toString(16), name, worker_type: type, slots,
        paused: false, disconnected: false, offline_notified: false, last_heartbeat: ts, registered_at: ts,
        tasks_done: 0, crashAt: Infinity, crashAfter: crashAfter || Infinity };
      workers.push(w);
      ev(ts, "worker_joined", `${name} joined (${type}, ${slots} slot${slots > 1 ? "s" : ""})`, { worker_id: w.id });
      return w;
    }
    function createJob(ts, params, id) {
      const job = { id: id || "job_mock" + String(++jobSeq).padStart(2, "0"), params, status: "queued",
        created_at: ts, started_at: null, finished_at: null, tasks: [] };
      TG.splitInputs(params).forEach((input, idx) => {
        job.tasks.push({ id: ++taskSeq, idx, input, status: "pending", worker_id: null, attempts: 0,
          lease_expires: null, finish_at: null, done_by: null, done_seq: null, runtime_ms: null, data: null });
      });
      jobs.push(job);
      ev(ts, "job_created", `Job ${job.id} created (${job.tasks.length} tiles)`, { job_id: job.id });
      return job;
    }
    function workerStatus(w, now) {
      if (w.disconnected) return "left";
      if (now - w.last_heartbeat > OFFLINE_AFTER) return "offline";
      if (w.paused) return "paused";
      return "online";
    }
    const alive = (w, ts) => !w.disconnected && ts < w.crashAt;

    function mockKernel(inp) {
      const cap = Math.min(inp.max_iter, 300);
      const out = new Int32Array(inp.tile_w * inp.tile_h);
      const dx = (inp.x_max - inp.x_min) / inp.image_width;
      const dy = (inp.y_max - inp.y_min) / inp.image_height;
      for (let j = 0; j < inp.tile_h; j++) {
        const ci = inp.y_max - (inp.tile_y + j + 0.5) * dy;
        for (let i = 0; i < inp.tile_w; i++) {
          const cr = inp.x_min + (inp.tile_x + i + 0.5) * dx;
          let zr = 0, zi = 0, n = 0;
          while (n < cap && zr * zr + zi * zi <= 4) { const t = zr * zr - zi * zi + cr; zi = 2 * zr * zi + ci; zr = t; n++; }
          out[j * inp.tile_w + i] = n === cap ? inp.max_iter : n;
        }
      }
      return out;
    }

    function tick(ts) {
      // heartbeats
      for (const w of workers) {
        if (alive(w, ts)) {
          w.last_heartbeat = ts;
          if (w.offline_notified) { w.offline_notified = false; ev(ts, "worker_back", `${w.name} is back online`, { worker_id: w.id }); }
        }
      }
      reap(ts);
      // completions
      for (const job of jobs) {
        for (const t of job.tasks) {
          if (t.status !== "leased" || t.finish_at > ts) continue;
          const w = wById(t.worker_id);
          if (!w || !alive(w, t.finish_at)) continue; // crashed worker never reports
          t.status = "done"; t.done_by = w.id; t.runtime_ms = Math.round((t.finish_at - t.leased_at) * 1000);
          t.data = Array.from(mockKernel(t.input));
          t.done_seq = ev(ts, "task_done", `Task ${t.id} done by ${w.name} in ${(t.runtime_ms / 1000).toFixed(2)}s`,
            { job_id: job.id, task_id: t.id, worker_id: w.id });
          w.tasks_done++;
          if (job.tasks.every((x) => x.status === "done")) {
            job.status = "completed"; job.finished_at = ts;
            ev(ts, "job_completed", `Job ${job.id} completed (${job.tasks.length} tiles)`, { job_id: job.id });
          }
        }
      }
      // leases
      for (const w of workers) {
        if (!alive(w, ts) || w.paused) continue;
        let held = 0;
        for (const j of jobs) for (const t of j.tasks) if (t.status === "leased" && t.worker_id === w.id) held++;
        while (held < w.slots) {
          const pick = nextPending();
          if (!pick) break;
          const [job, t] = pick;
          const base = w.worker_type === "native" ? 1.2 : 2.0;
          t.status = "leased"; t.worker_id = w.id; t.attempts++; t.leased_at = ts;
          t.lease_expires = ts + TTL; t.finish_at = ts + base * (0.6 + Math.random() * 0.8);
          if (ts >= w.crashAfter && w.crashAt === Infinity) { t.finish_at = Infinity; w.crashAt = ts + TICK; }
          if (!job.started_at) {
            job.status = "running"; job.started_at = ts;
            ev(ts, "job_started", `Job ${job.id} started`, { job_id: job.id });
          }
          ev(ts, "task_leased", `Task ${t.id} leased to ${w.name}` + (t.attempts > 1 ? ` (attempt ${t.attempts})` : ""),
            { job_id: job.id, task_id: t.id, worker_id: w.id });
          held++;
        }
      }
    }
    function nextPending() {
      const active = jobs.filter((j) => j.status === "queued" || j.status === "running").sort((a, b) => a.created_at - b.created_at);
      for (const j of active) {
        const t = j.tasks.find((x) => x.status === "pending");
        if (t) return [j, t];
      }
      return null;
    }
    function reap(ts) {
      for (const job of jobs) {
        for (const t of job.tasks) {
          if (t.status !== "leased" || t.lease_expires >= ts) continue;
          const w = wById(t.worker_id);
          const wname = w ? w.name : t.worker_id;
          if (t.attempts < MAX_ATTEMPTS) {
            t.status = "pending";
            ev(ts, "lease_expired", `Task ${t.id} lease expired (${wname}) -> requeued`, { job_id: job.id, task_id: t.id, worker_id: t.worker_id });
          } else {
            t.status = "failed";
            ev(ts, "task_failed", `Task ${t.id} failed after ${t.attempts} attempts`, { job_id: job.id, task_id: t.id, worker_id: t.worker_id });
          }
          t.worker_id = null;
        }
        if ((job.status === "queued" || job.status === "running") && job.tasks.some((t) => t.status === "failed")) {
          job.status = "failed"; job.finished_at = ts;
          ev(ts, "job_failed", `Job ${job.id} failed`, { job_id: job.id });
        }
      }
      for (const w of workers) {
        if (!w.disconnected && !w.offline_notified && ts - w.last_heartbeat > OFFLINE_AFTER) {
          w.offline_notified = true;
          ev(ts, "worker_offline", `${w.name} offline (no heartbeat for ${OFFLINE_AFTER}s)`, { worker_id: w.id });
        }
      }
    }
    function advance() {
      const now = Date.now() / 1000;
      while (simT + TICK <= now) { simT += TICK; tick(simT); }
      return simT;
    }

    // initial world
    addWorker(T0, "Browser-01", "browser", 1);
    addWorker(T0, "Browser-02", "browser", 1, T0 + 6);
    addWorker(T0, "Native-Harry", "native", 2);
    createJob(T0, PRESETS[0].params, "job_mock01");

    // ---------------------------------------------------------- handlers --
    function http(status, detail) {
      throw makeError(status, detail, TG.formatDetail(detail) || "HTTP " + status);
    }
    function counts(job) {
      const c = { pending: 0, leased: 0, done: 0, failed: 0 };
      for (const t of job.tasks) c[t.status]++;
      return c;
    }
    function validate(params) {
      const errs = [];
      const allowed = ["width", "height", "tile_size", "max_iter", "x_min", "x_max", "y_min", "y_max"];
      const add = (loc, msg) => errs.push({ type: "value_error", loc: ["body", "params", loc], msg });
      if (!params || typeof params !== "object") { add("params", "Field required"); return errs; }
      for (const k of Object.keys(params)) if (!allowed.includes(k)) add(k, "Extra inputs are not permitted");
      for (const k of allowed) if (params[k] === undefined || params[k] === null || params[k] === "") add(k, "Field required");
      if (errs.length) return errs;
      const { width, height, tile_size, max_iter } = params;
      for (const k of ["width", "height", "tile_size", "max_iter"]) if (!Number.isInteger(params[k])) add(k, "Input should be a valid integer");
      if (![32, 64, 128].includes(tile_size)) add("tile_size", "Value error, tile_size must be 32, 64 or 128");
      for (const [k, v] of [["width", width], ["height", height]]) {
        if (v < 64 || v > 4096) add(k, `Value error, ${k} must be between 64 and 4096`);
        else if ([32, 64, 128].includes(tile_size) && v % tile_size) add(k, `Value error, ${k} must be a multiple of tile_size`);
      }
      if (max_iter < 1 || max_iter > 20000) add("max_iter", "Value error, max_iter must be between 1 and 20000");
      for (const k of ["x_min", "x_max", "y_min", "y_max"]) {
        if (typeof params[k] !== "number" || !isFinite(params[k]) || Math.abs(params[k]) > 4) add(k, "Value error, must be finite with |value| <= 4");
      }
      if (params.x_min >= params.x_max) add("x_max", "Value error, x_min must be < x_max");
      if (params.y_min >= params.y_max) add("y_max", "Value error, y_min must be < y_max");
      if (!errs.length && (width / tile_size) * (height / tile_size) > 4096) add("tile_size", "Value error, task_count must be <= 4096");
      return errs;
    }

    async function handle(path, opts = {}) {
      await new Promise((r) => setTimeout(r, 40 + Math.random() * 60));
      const now = advance();
      const url = new URL(path, location.origin);
      const p = url.pathname, q = url.searchParams;
      const method = opts.method || (opts.body !== undefined ? "POST" : "GET");
      let m;

      if (p === "/healthz") return { ok: true };
      if (p === "/api/presets") return JSON.parse(JSON.stringify(PRESETS));

      if (p === "/api/stats") {
        const order = { online: 0, paused: 1, offline: 2, left: 3 };
        const ws = workers.map((w) => {
          const status = workerStatus(w, now);
          const cur = [];
          for (const j of jobs) for (const t of j.tasks) if (t.status === "leased" && t.worker_id === w.id) cur.push(t.id);
          return { worker_id: w.id, name: w.name, worker_type: w.worker_type, status, slots: w.slots, has_gpu: false,
            tasks_done: w.tasks_done, last_heartbeat_age_s: Math.round((now - w.last_heartbeat) * 10) / 10,
            current_task_ids: cur, registered_at: iso(w.registered_at) };
        }).sort((a, b) => order[a.status] - order[b.status]);
        const totals = { online: 0, paused: 0, offline: 0, left: 0, active_jobs: 0, tasks_done: 0, slots_online: 0 };
        for (const w of ws) { totals[w.status]++; totals.tasks_done += w.tasks_done; if (w.status === "online") totals.slots_online += w.slots; }
        totals.active_jobs = jobs.filter((j) => j.status === "queued" || j.status === "running").length;
        return { server_time: iso(now), totals, workers: ws };
      }

      if (p === "/api/events") {
        const since = parseInt(q.get("since") || "0", 10) || 0;
        const limit = Math.min(500, parseInt(q.get("limit") || "100", 10) || 100);
        const jobId = q.get("job_id");
        let list = jobId ? events.filter((e) => e.job_id === jobId) : events;
        list = since > 0 ? list.filter((e) => e.id > since).slice(0, limit) : list.slice(-limit);
        const out = list.map((e) => {
          const w = e.worker_id ? wById(e.worker_id) : null;
          return { id: e.id, ts: iso(e.ts), type: e.type, job_id: e.job_id, task_id: e.task_id, worker_id: e.worker_id,
            worker_name: w ? w.name : null, message: e.message };
        });
        return { events: out, next_since: out.length ? out[out.length - 1].id : since };
      }

      if (p === "/api/jobs" && method === "GET") {
        const list = jobs.slice().sort((a, b) => b.created_at - a.created_at).slice(0, 20);
        return { jobs: list.map((j) => ({ job_id: j.id, status: j.status, task_count: j.tasks.length,
          done_count: counts(j).done, created_at: iso(j.created_at), params: Object.assign({}, j.params) })) };
      }

      if (p === "/api/jobs" && method === "POST") {
        const body = opts.body || {};
        if (body.task_type !== "mandelbrot_v1") http(422, [{ type: "literal_error", loc: ["body", "task_type"], msg: "Input should be 'mandelbrot_v1'" }]);
        const errs = validate(body.params);
        if (errs.length) http(422, errs);
        if (jobs.filter((j) => j.status === "queued" || j.status === "running").length >= MAX_ACTIVE) http(429, "too_many_active_jobs");
        const job = createJob(now, Object.assign({}, body.params));
        return { job_id: job.id, status: job.status, task_count: job.tasks.length };
      }

      if ((m = /^\/api\/jobs\/([^/]+)$/.exec(p)) && method === "GET") {
        const job = jobs.find((j) => j.id === decodeURIComponent(m[1]));
        if (!job) http(404, "job_not_found");
        const since = parseInt(q.get("since") || "0", 10) || 0;
        const tiles = job.tasks.filter((t) => t.status === "done" && t.done_seq > since).sort((a, b) => a.done_seq - b.done_seq);
        const per = new Map();
        for (const t of job.tasks) if (t.status === "done") per.set(t.done_by, (per.get(t.done_by) || 0) + 1);
        const done = job.status === "completed" || job.status === "failed";
        return {
          job_id: job.id, task_type: "mandelbrot_v1", params: Object.assign({}, job.params), status: job.status,
          task_count: job.tasks.length, counts: counts(job),
          created_at: iso(job.created_at), started_at: iso(job.started_at), finished_at: iso(job.finished_at),
          elapsed_ms: Math.round(((done ? job.finished_at : now) - job.created_at) * 1000),
          workers: [...per.entries()].map(([id, n]) => { const w = wById(id); return { worker_id: id, name: w.name, worker_type: w.worker_type, tiles_done: n }; }),
          tiles: tiles.map((t) => { const w = wById(t.done_by); return { task_id: t.id, idx: t.idx, tile_x: t.input.tile_x, tile_y: t.input.tile_y,
            tile_w: t.input.tile_w, tile_h: t.input.tile_h, worker_id: t.done_by, worker_type: w.worker_type,
            runtime_ms: t.runtime_ms, attempts: t.attempts, data: t.data }; }),
          leased: job.tasks.filter((t) => t.status === "leased").map((t) => ({ task_id: t.id, tile_x: t.input.tile_x, tile_y: t.input.tile_y,
            tile_w: t.input.tile_w, tile_h: t.input.tile_h, worker_id: t.worker_id, lease_expires_at: iso(t.lease_expires) })),
          next_since: tiles.length ? tiles[tiles.length - 1].done_seq : since,
        };
      }

      if (p === "/api/planner" && method === "POST") {
        await new Promise((r) => setTimeout(r, 700));
        const prompt = String((opts.body && opts.body.prompt) || "");
        if (!prompt.trim() || prompt.length > 500) http(422, [{ type: "string_too_long", loc: ["body", "prompt"], msg: "String should have 1-500 characters" }]);
        if (/unavailable/i.test(prompt)) http(503, "planner_unavailable");
        if (/invalid/i.test(prompt)) http(422, "planner_invalid_output");
        const heavy = /detail|heavy|deep|zoom/i.test(prompt);
        const light = /light|small tile/i.test(prompt);
        const params = Object.assign({}, heavy ? PRESETS[1].params : PRESETS[0].params);
        if (light) params.tile_size = 64;
        return { task_type: "mandelbrot_v1", params,
          explanation: (heavy ? "Deep Seahorse Valley zoom with a high iteration cap." : "Small full-set render for a quick run.") +
            (light ? " 64-px tiles keep each browser task short." : ""),
          source: "llm" };
      }

      http(404, "Not Found (mock)");
    }
    return { handle };
  })();

  // ------------------------------------------------- mock contributor ----
  // Stand-in for C's TartanContributor in mock mode only (same interface, CONTRACT §9).
  TG.MockContributor = class {
    constructor({ name, slots }) {
      this.name = name; this.slots = slots || 1; this.handlers = {};
      this.s = { state: "idle", workerId: null, slots: this.slots, tasksDone: 0, currentTaskIds: [], lastRuntimeMs: null, lastError: null };
      this.timer = null; this.nextTask = 1000;
    }
    on(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); }
    emit(type, v) { (this.handlers[type] || []).forEach((f) => f(v)); }
    set(patch) { Object.assign(this.s, patch); this.emit("status", Object.assign({}, this.s, { currentTaskIds: [...this.s.currentTaskIds] })); }
    async start() {
      this.set({ state: "connecting" });
      await new Promise((r) => setTimeout(r, 400));
      this.set({ state: "running", workerId: "wkr_mock" + Math.floor(Math.random() * 1e4).toString(16).padStart(4, "0") });
      this.emit("log", `registered as ${this.s.workerId} (mock)`);
      this.loop();
    }
    loop() {
      clearTimeout(this.timer);
      if (this.s.state !== "running") return;
      const id = this.nextTask++;
      this.set({ currentTaskIds: [id] });
      this.emit("log", `leased task ${id}`);
      const ms = 600 + Math.round(Math.random() * 900);
      this.timer = setTimeout(() => {
        this.set({ tasksDone: this.s.tasksDone + 1, lastRuntimeMs: ms, currentTaskIds: [] });
        this.emit("task_done", { taskId: id, jobId: "job_mock01", runtimeMs: ms });
        this.emit("log", `task ${id} done in ${(ms / 1000).toFixed(2)}s`);
        this.timer = setTimeout(() => this.loop(), 200);
      }, ms);
    }
    async pause() { this.set({ state: "paused" }); this.emit("log", "paused — finishing current task, no new leases"); }
    async resume() { this.set({ state: "running" }); this.emit("log", "resumed"); this.loop(); }
    async stop() { clearTimeout(this.timer); this.set({ state: "stopped", currentTaskIds: [] }); this.emit("log", "disconnected"); }
  };

  window.TG = TG;
})();
