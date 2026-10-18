/* Sightline core: data loading, panel registry, scoreboards, distance to kill.
   Panels live in dashboard/panels/*.js and call S.register. */
window.S = (function () {
  const BASE = window.SIGHTLINE_DATA != null ? String(window.SIGHTLINE_DATA) : "";
  const cache = {};

  function url(path) {
    if (/^(https?:)?\/\//.test(path)) return path;
    const clean = String(path).replace(/^\//, "");
    if (!BASE || BASE === "." || BASE === "./") return clean;
    return BASE.replace(/\/?$/, "/") + clean;
  }
  async function get(path) {
    const u = url(path);
    if (!(u in cache)) {
      cache[u] = fetch(u, { cache: "no-cache" }).then(r => (r.ok ? r.json() : null)).catch(() => null);
    }
    return cache[u];
  }
  function pick(obj, path) {
    if (!path) return obj;
    return String(path).split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
  }
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n, d = 1) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d }));
  const pct = n => (n == null || isNaN(n) ? "–" : `<span class="${n >= 0 ? "up" : "down"}">${n > 0 ? "+" : ""}${fmt(n)}%</span>`);
  function pnlNum(n) {
    if (n == null || isNaN(n)) return "–";
    if (n > 0) return "+" + fmt(n);
    if (n === 0) return "+0";
    return fmt(n);
  }
  function pnlClass(n) { return n < 0 ? "down" : "up"; }
  const when = t => {
    if (t == null || t === "") return "";
    const s = String(t);
    // A calendar date has no time. Parsing it as UTC midnight would show the previous evening in ET.
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
      const [y, m, d] = s.split("-").map(Number);
      return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
    }
    const d = t instanceof Date ? t : new Date(s);
    return isNaN(d) ? esc(s) : d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" }) + " ET";
  };
  function join(dir, p) {
    if (p == null) return p;
    if (/^(https?:)?\/\//.test(p)) return p;
    if (String(p).startsWith("/")) return String(p).slice(1);
    return (dir || "") + p;
  }

  const registry = {};
  function register(type, def) { registry[type] = def; }

  async function loadData(desk, spec) {
    if (spec.data == null) return null;
    const list = Array.isArray(spec.data) ? spec.data : [spec.data];
    const out = [];
    for (const p of list) {
      if (String(p).endsWith("/")) { out.push({ path: p, json: null, dir: true }); continue; }
      const j = await get(join(desk.dir, p));
      out.push({ path: p, json: pick(j, spec.path) });
    }
    return Array.isArray(spec.data) ? out : out[0].json;
  }
  async function renderPanel(desk, spec) {
    const def = registry[spec.type];
    const span = spec.span || (def && def.span) || 12;
    let body;
    if (!def) {
      body = `<div class="placeholder"><b>Panel type "${esc(spec.type)}" isn't installed yet.</b><br>Add <code>dashboard/panels/${esc(spec.type)}.js</code> (it calls <code>S.register</code>). The next build includes it. The rest of this page still works.</div>`;
    } else {
      try {
        body = await def.render({ desk, spec, opt: spec.options || {}, data: await loadData(desk, spec) });
      } catch (e) {
        body = `<div class="placeholder">This panel couldn't load (${esc(e.message)}).</div>`;
      }
    }
    return `<section class="panel span-${span}"><h3>${esc(spec.title || spec.type)}<span class="tag">${esc(spec.type)}</span></h3>${body}</section>`;
  }

  async function desks() {
    const idx = await get("desks.json");
    if (!idx || !Array.isArray(idx.desks)) return [];
    return idx.desks.map(d => ({
      ...d,
      dir: d.id + "/",
      rel: p => (p && String(p).startsWith(d.id + "/") ? String(p).slice(d.id.length + 1) : p)
    }));
  }
  function defaultPanels(d) {
    const books = (d.books || []).map(b => d.rel(b.paper)).filter(Boolean);
    const p = [];
    if (d.log) p.push({ type: "headline", title: "Latest run", data: d.rel(d.log) });
    if (books.length) {
      p.push(
        { type: "scoreboard", title: "Scoreboard", data: books },
        { type: "tickets", title: "Open tickets", data: books },
        { type: "closed", title: "Closed tickets", data: books },
        { type: "rules", title: "Rules learned", data: books, options: { limit: 8 } }
      );
    }
    if (d.briefs) p.push({ type: "briefs", title: "Daily briefs", data: d.rel(d.briefs) });
    return p;
  }
  async function panelsFor(d) {
    const m = await get(d.dir + "panels.json");
    return (m && Array.isArray(m.panels)) ? { panels: m.panels, custom: true } : { panels: defaultPanels(d), custom: false };
  }
  async function bookFiles(d) {
    const r = [];
    for (const b of d.books || []) {
      const j = await get(b.paper);
      if (j) r.push({ ...b, json: j });
    }
    return r;
  }
  async function briefsOf(d) {
    if (!d.briefs) return [];
    const list = await get(d.briefs + "index.json");
    if (!Array.isArray(list)) return [];
    const out = [];
    for (const n of list) {
      const name = String(n).endsWith(".json") ? n : n + ".json";
      const j = await get(d.briefs + name);
      if (j) out.push(j);
    }
    return out.sort((a, b) => String(b.date).localeCompare(String(a.date)));
  }

  /* P&L is totaled inside one unit only. byUnit is the only place those sums live. */
  function score(books) {
    let open = 0, closed = 0, wins = 0;
    const byUnit = {};
    const touch = unit => (byUnit[unit] || (byUnit[unit] = { pnl: 0, n: 0 }));
    for (const b of books) {
      const j = b.json || b;
      open += (j.open || []).length;
      const bookUnit = j.unit || "";
      const closedRows = j.closed || [];
      if (!closedRows.length && bookUnit) touch(bookUnit);
      for (const t of closedRows) {
        closed++;
        const r = t.result || {};
        const unit = r.unit || bookUnit || "—";
        const row = touch(unit);
        if (typeof r.pnl === "number") { row.pnl += r.pnl; row.n++; }
        if ((r.pnl ?? r.pct ?? 0) > 0) wins++;
      }
    }
    return {
      open, closed, wins,
      losses: closed - wins,
      winRate: closed ? wins / closed * 100 : null,
      byUnit
    };
  }
  function avgRet(j) {
    const pcts = [];
    for (const t of j.closed || []) {
      const p = t.result && t.result.pct;
      if (typeof p === "number") pcts.push(p);
    }
    if (!pcts.length) return null;
    return pcts.reduce((a, b) => a + b, 0) / pcts.length;
  }
  function pnlCards(sc) {
    const entries = Object.entries(sc.byUnit);
    if (!entries.length) return `<div class="card"><div class="k">P&amp;L</div><div class="v muted">–</div></div>`;
    return entries.map(([unit, row]) =>
      `<div class="card"><div class="k">P&amp;L · ${esc(unit)}</div><div class="v ${pnlClass(row.pnl)}">${pnlNum(row.pnl)}</div></div>`
    ).join("");
  }
  function metricCards(sc) {
    const win = sc.winRate == null ? "–" : fmt(sc.winRate, 0) + "%";
    return `<div class="card"><div class="k">Open</div><div class="v">${sc.open}</div></div>
      <div class="card"><div class="k">Closed</div><div class="v">${sc.closed}</div></div>
      <div class="card"><div class="k">Win rate</div><div class="v">${win}</div></div>
      ${pnlCards(sc)}`;
  }
  function unitNote(sc) {
    const units = Object.keys(sc.byUnit);
    if (units.length < 2) return "";
    return `<p class="muted small unit-note">Totals are per unit (${units.map(esc).join(", ")}). Different units are not added together.</p>`;
  }

  function currentMetrics(t) {
    const marks = Array.isArray(t.marks) ? t.marks : [];
    const last = marks.length ? marks[marks.length - 1] : {};
    const merged = Object.assign({}, t.entry || {}, last || {});
    const out = {};
    for (const [k, v] of Object.entries(merged)) {
      if (typeof v === "number" && isFinite(v)) out[k] = v;
    }
    return out;
  }
  function roomPct(cur, lim) {
    if (typeof cur !== "number" || typeof lim !== "number") return null;
    const denom = Math.abs(cur) > 1e-12 ? Math.abs(cur) : (Math.abs(lim) > 1e-12 ? Math.abs(lim) : null);
    if (denom == null) return null;
    return (cur - lim) / denom * 100;
  }
  function numericDistances(k, cur) {
    const ds = [];
    if (!k || typeof k !== "object") return ds;
    for (const [key, lim] of Object.entries(k)) {
      if (typeof lim !== "number") continue;
      const m = key.match(/^(.*)_(below|under|lt|above|over|gt)$/);
      if (!m) continue;
      const field = m[1];
      if (typeof cur[field] !== "number") continue;
      const aboveKill = /above|over|gt/.test(m[2]);
      const pct = aboveKill ? roomPct(lim, cur[field]) : roomPct(cur[field], lim);
      if (pct == null) continue;
      ds.push({ what: field.replace(/_/g, " "), cur: cur[field], lim, pct });
    }
    return ds;
  }
  const SYN = [
    [/\bfloor\b/, /floor/],
    [/\boffer\b|\bbid\b/, /offer|bid/],
    [/\bholders?\b/, /holder/],
    [/\bmcap\b|\bmarket\s*cap\b/, /mcap|market/],
    [/\bada\s*side\b/, /ada_side/],
    [/\bpool\b|\bquote\b/, /pool_quote|pool|quote/],
    [/\bcurve\b/, /curve/],
    [/\bprice\b/, /price/]
  ];
  function bestField(clause, cur, lim) {
    const c = clause.toLowerCase();
    let best = null, bestScore = 0;
    for (const [field, value] of Object.entries(cur)) {
      if (lim !== 0 && value !== 0) {
        const ratio = Math.abs(value / lim);
        if (ratio > 25 || ratio < 1 / 25) continue;
      }
      let score = 0;
      for (const tok of field.toLowerCase().split(/_+/)) {
        if (!/^[a-z0-9]{3,}$/.test(tok)) continue;
        if (new RegExp("\\b" + tok + "\\b").test(c)) score += 2;
      }
      for (const [cre, fre] of SYN) {
        if (cre.test(c) && fre.test(field)) score += 4;
      }
      if (score > bestScore) { bestScore = score; best = field; }
    }
    return bestScore > 0 ? best : null;
  }
  function labelFor(field, clause) {
    const c = clause.toLowerCase();
    if (/\bfloor\b/.test(c) && /floor/.test(field)) return "floor";
    if (/\boffer\b|\bbid\b/.test(c) && /offer|bid/.test(field)) return "offer";
    if (/\bholders?\b/.test(c) && /holder/.test(field)) return "holders";
    if (/\bmcap\b/.test(c) && /mcap/.test(field)) return "mcap";
    if (/\bada\s*side\b/.test(c) && /ada_side/.test(field)) return "ada side";
    if (/\bpool\b|\bquote\b/.test(c) && /pool|quote/.test(field)) return "pool";
    return field.replace(/_/g, " ");
  }
  function parseLimit(raw, suf) {
    const n = parseFloat(String(raw).replace(/,/g, ""));
    if (!isFinite(n)) return null;
    const mult = { k: 1e3, m: 1e6, b: 1e9 }[(suf || "").toLowerCase()] || 1;
    return n * mult;
  }
  function textDistances(text, cur) {
    const ds = [];
    if (!text) return ds;
    for (const clause of String(text).split(/\s+or\s+|\s*;\s*/i)) {
      const m = clause.match(/(?:<=?|\bbelow\b|\bunder\b)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kmb])?\b/i);
      if (!m) continue;
      const lim = parseLimit(m[1], m[2]);
      if (lim == null) continue;
      const field = bestField(clause, cur, lim);
      if (!field) continue;
      const pct = roomPct(cur[field], lim);
      if (pct == null) continue;
      ds.push({ what: labelFor(field, clause), cur: cur[field], lim, pct });
    }
    return ds;
  }
  /* Numeric kill fields win when any of them match a current reading.
     Otherwise the kill line's text is parsed ("floor < 0.70", "holders < 250", "mcap < 450k"). */
  function killDistance(t) {
    if (!t) return null;
    const cur = currentMetrics(t);
    const k = t.kill_line;
    let ds = numericDistances(k, cur);
    if (!ds.length) {
      const text = typeof k === "string" ? k : (k && k.text) || "";
      ds = textDistances(text, cur);
    }
    if (!ds.length) return null;
    ds.sort((a, b) => a.pct - b.pct);
    return ds[0];
  }
  async function allOpen(dlist) {
    const out = [];
    for (const d of dlist) {
      for (const b of await bookFiles(d)) {
        for (const t of b.json.open || []) out.push({ desk: d, book: b, t, kd: killDistance(t) });
      }
    }
    return out.sort((a, b) => (a.kd?.pct ?? 999) - (b.kd?.pct ?? 999));
  }

  return {
    get, pick, esc, fmt, pct, pnlNum, pnlClass, when, join, register, registry,
    renderPanel, desks, panelsFor, bookFiles, briefsOf, score, avgRet, pnlCards,
    metricCards, unitNote, killDistance, allOpen
  };
})();
