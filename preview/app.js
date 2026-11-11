/* Sightline preview. Paper and research only.
   Desk JSON/CSV is read the same way as the live dashboard: raw.githubusercontent
   desk-data first, this origin (main) as fallback, newer stamp wins.
   Identifiers render only as role labels or w- hashes. Wallet addresses, stake
   keys, and holdings are dropped before they reach the DOM. */
(function (root) {
  const DESK_DATA = root.SIGHTLINE_DESK_DATA != null
    ? String(root.SIGHTLINE_DESK_DATA)
    : "https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/";
  const CONFIG_NAMES = /(^|\/)(panels|panel|config|index|icons)\.json$/;
  const ADDRESS = /\b(?:(?:stake|addr)(?:_test)?1[0-9a-z]{2,}(?:(?:\.{2,3}|\u2026)[0-9a-z]+)?|0x[0-9a-f]{8,}|[1-9A-HJ-NP-Za-km-z]{32,44}|[0-9a-f]{56,})\b/gi;
  const W_HASH = /^w-[0-9a-f]{8}$/i;
  const ROLE = /^[a-z][a-z0-9_-]{0,31}$/;
  const HIDDEN_KEYS = /balance|holding|position|lovelace|wallet|address|stake|seed|secret|private/i;
  const NAV = [
    { id: "home", href: "#/", label: "Home", desk: "home" },
    { id: "cardano", href: "#/cardano", label: "Cardano", desk: "solada" },
    { id: "solana", href: "#/solana", label: "Solana", desk: "solana" },
    { id: "nft", href: "#/nft", label: "NFT", desk: "nft" }
  ];

  function isDeskData(path) {
    const p = String(path).replace(/^\/|^\.\//, "");
    if (/^(https?:)?\/\//.test(p) || !p.endsWith(".json")) return false;
    if (/(^|\/)forensics\/panel\.json$/.test(p)) return true;
    if (p === "x_buzz.json") return true;
    if (!p.includes("/") || CONFIG_NAMES.test(p)) return false;
    if (/^(dashboard|schema|scripts|tools|preview|\.github)\//.test(p)) return false;
    return true;
  }
  function deskDataUrl(path) {
    if (!DESK_DATA || !isDeskData(path)) return null;
    return DESK_DATA.replace(/\/?$/, "/") + String(path).replace(/^\/|^\.\//, "") + "?ts=" + Date.now();
  }
  function originUrl(path) {
    return String(path).replace(/^\//, "");
  }
  function stampOf(j) {
    if (!j || typeof j !== "object" || Array.isArray(j)) return NaN;
    for (const k of ["asof", "updated_at", "generated_at", "date", "at"]) {
      if (j[k]) {
        const t = new Date(j[k]).getTime();
        if (isFinite(t)) return t;
      }
    }
    return NaN;
  }
  function pickNewer(branch, mainCopy) {
    if (branch == null) return mainCopy;
    if (mainCopy == null) return branch;
    const a = stampOf(branch), b = stampOf(mainCopy);
    return isFinite(b) && (!isFinite(a) || b > a) ? mainCopy : branch;
  }
  const getJson = u => fetch(u, { cache: "no-cache" }).then(r => (r.ok ? r.json() : null)).catch(() => null);
  const jsonCache = {};
  function get(path) {
    const u = originUrl(path);
    if (!(u in jsonCache)) {
      const d = deskDataUrl(path);
      jsonCache[u] = d ? Promise.all([getJson(d), getJson(u)]).then(([a, b]) => pickNewer(a, b)) : getJson(u);
    }
    return jsonCache[u];
  }
  const textCache = {};
  function getDeskRootText(name) {
    const clean = String(name).replace(/^\/|^\.\//, "");
    if (clean !== "x_buzz_history.jsonl") return Promise.resolve(null);
    if (!(clean in textCache)) {
      const getT = u => fetch(u, { cache: "no-cache" }).then(r => (r.ok ? r.text() : null)).catch(() => null);
      const remote = DESK_DATA ? DESK_DATA.replace(/\/?$/, "/") + clean + "?ts=" + Date.now() : null;
      textCache[clean] = (remote ? getT(remote) : Promise.resolve(null)).then(t => (t != null ? t : getT(originUrl(clean))));
    }
    return textCache[clean];
  }
  function getText(path) {
    const clean = String(path).replace(/^\/|^\.\//, "");
    if (!(clean in textCache)) {
      const getT = u => fetch(u, { cache: "no-cache" }).then(r => (r.ok ? r.text() : null)).catch(() => null);
      const d = DESK_DATA && /\.(csv|txt)$/.test(clean) && clean.includes("/")
        ? DESK_DATA.replace(/\/?$/, "/") + clean + "?ts=" + Date.now() : null;
      textCache[clean] = (d ? getT(d) : Promise.resolve(null)).then(t => (t != null ? t : getT(originUrl(clean))));
    }
    return textCache[clean];
  }

  let leadCodename = "";
  function sanitize(value) {
    let s = String(value ?? "");
    s = s.replace(/\$[A-Za-z0-9_]{2,32}/g, " ");
    s = s.replace(/@[A-Za-z0-9_]{2,32}/g, " ");
    s = s.replace(/founder_whale_move/gi, (leadCodename || "Whale") + " move");
    s = s.replace(/founder_pct/gi, " ");
    s = s.replace(/founder_whale/gi, leadCodename || "whale");
    s = s.replace(/\bfounder\b/gi, " ");
    s = s.replace(ADDRESS, "");
    return s.replace(/[ \t]{2,}/g, " ").replace(/\s+([,.;])/g, "$1").replace(/\(\s*\)/g, "").trim();
  }
  function safeTag(value) {
    const s = String(value ?? "").trim();
    if (!s) return null;
    if (W_HASH.test(s)) return "w-" + s.slice(2).toLowerCase();
    const low = s.toLowerCase();
    if (!ROLE.test(low)) return null;
    if (/^u[0-9a-z]{3,}$/.test(low)) return null;
    if (/stake|addr|wallet|seed|secret|key/.test(low)) return null;
    return low;
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function tx(s) { return esc(sanitize(s)); }
  function num(v) {
    if (typeof v === "boolean" || v == null || v === "") return null;
    const n = typeof v === "number" ? v : Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function fmt(n, d) {
    if (n == null || !isFinite(n)) return "–";
    return Number(n).toLocaleString("en-US", { maximumFractionDigits: d == null ? 1 : d, minimumFractionDigits: 0 });
  }
  function timeOf(v) {
    const n = num(v);
    if (n != null && n > 1e9 && n < 1e12) return new Date(n * 1000).toISOString();
    return v;
  }
  function when(t) {
    if (t == null || t === "") return "";
    const s = String(timeOf(t));
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
      const [y, m, d] = s.split("-").map(Number);
      return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
    }
    const d = new Date(s);
    if (isNaN(d)) return sanitize(s);
    return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" }) + " ET";
  }
  function ago(t) {
    const d = new Date(timeOf(t));
    if (isNaN(d)) return "";
    const s = Math.max(0, Date.now() - d.getTime()) / 1000;
    if (s < 90) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function freshClass(t) {
    const d = new Date(timeOf(t));
    if (isNaN(d)) return "";
    const h = (Date.now() - d.getTime()) / 36e5;
    if (h <= 2) return "ok";
    if (h <= 24) return "warn";
    return "bad";
  }
  function freshTone(t) {
    const d = new Date(timeOf(t));
    if (isNaN(d.getTime())) return "";
    const h = (Date.now() - d.getTime()) / 36e5;
    if (h <= 2) return "up";
    if (h <= 26) return "amb";
    return "dn";
  }
  function builtEt(t) {
    const d = new Date(timeOf(t));
    if (isNaN(d.getTime())) return "";
    const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "America/Toronto" }).formatToParts(d);
    let hh = (parts.find(p => p.type === "hour") || {}).value || "";
    const mm = (parts.find(p => p.type === "minute") || {}).value || "";
    if (hh === "24") hh = "00";
    if (!hh || !mm) return "";
    return hh + ":" + mm + " ET";
  }
  function freshnessHtml(picks) {
    const doc = picks || {};
    const lines = [
      ["Cardano", doc.cardano && doc.cardano.asof, false],
      ["Solana", doc.solana && doc.solana.asof, false],
      ["NFT", doc.nft && doc.nft.asof, false],
      ["built", doc.generated_at, true]
    ];
    return lines.map(([name, at, built]) => {
      const text = built ? ("built " + (builtEt(at) || "–")) : (name + " " + (when(at) || "–"));
      return `<span class="${freshTone(at)}">${esc(text)}</span>`;
    }).join("<br>");
  }
  function parseCsv(text) {
    if (!text) return [];
    const lines = String(text).split(/\r?\n/).filter(Boolean);
    if (!lines.length) return [];
    const split = l => {
      const out = []; let cur = "", q = false;
      for (const c of l) {
        if (c === '"') q = !q;
        else if (c === "," && !q) { out.push(cur); cur = ""; }
        else cur += c;
      }
      out.push(cur);
      return out;
    };
    const head = split(lines[0]);
    return lines.slice(1).map(split).filter(r => r.join(",") !== head.join(",")).map(r => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
  }
  function prefersReduced() {
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function icon(name) {
    const p = {
      pulse: '<path d="M2 8h2l2-4 3 8 2-4h3"/>',
      chart: '<path d="M2 12l3-4 3 2 4-6 2 3"/><path d="M2 14h12"/>',
      table: '<path d="M2 3h12v10H2z"/><path d="M2 7h12M6 7v6"/>',
      flag: '<path d="M3 2v12"/><path d="M3 3h8l-1.5 2L11 7H3"/>',
      feed: '<path d="M3 3h10v8H3z"/><path d="M5 6h6M5 8.5h4"/>',
      layers: '<path d="M8 2l6 3-6 3L2 5z"/><path d="M2 8l6 3 6-3"/><path d="M2 11l6 3 6-3"/>',
      image: '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 11l3-3 2 2 2-3 3 4"/>',
      book: '<path d="M3 2.5h4.5A2.5 2.5 0 0 1 10 5v8.5H5.2A2.2 2.2 0 0 0 3 15.7z"/><path d="M13 2.5H8.5"/>'
    }[name] || '<circle cx="8" cy="8" r="3"/>';
    return `<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">${p}</svg>`;
  }

  function pill(k, v, cls) {
    return `<span class="pill ${cls || ""}">${esc(k)} <b>${esc(v)}</b></span>`;
  }
  function statusPills(info) {
    const i = info || {};
    const fresh = i.fresh ? ago(i.fresh) : "–";
    const fail = i.failure == null ? "–" : fmt(i.failure * 100, 1) + "%";
    const fcls = i.failure == null ? "" : i.failure >= 0.1 ? "bad" : "ok";
    const vcls = i.frozen === true ? "ok" : i.frozen === false ? "warn" : "";
    const dot = i.fresh ? freshClass(i.fresh) : "";
    return [
      pill("mode", i.mode || "–", ""),
      pill("version", i.version || "–", vcls),
      `<span class="pill ${dot}"><i class="pulse ${i.fresh ? "" : "idle"}" aria-hidden="true"></i>updated <b>${esc(fresh)}</b></span>`,
      pill("failures", fail, fcls)
    ].join("");
  }
  function displayKind(kind) {
    const raw = String(kind || "");
    if (raw === "founder_whale_move" || raw === "whale_move") return (leadCodename || "Whale") + " move";
    if (raw === "founder_whale" || raw === "founder_pct") return "";
    return sanitize(raw.replace(/_/g, " "));
  }
  function chip(text, cls, extra) {
    const s = sanitize(text).replace(/_/g, " ");
    if (!s) return "";
    return `<span class="chip ${cls || ""}"${extra || ""}>${esc(s)}</span>`;
  }
  function tagChip(value) {
    const shown = labelTag(value);
    return shown ? `<span class="chip tag">${esc(shown)}</span>` : "";
  }
  function spark(values) {
    const nums = (values || []).filter(n => typeof n === "number" && isFinite(n));
    if (nums.length < 2) return "";
    const w = 84, h = 28;
    const min = Math.min.apply(null, nums), max = Math.max.apply(null, nums);
    const span = max - min || 1;
    const d = nums.map((n, i) => {
      const x = (i / (nums.length - 1)) * (w - 4) + 2;
      const y = h - 3 - ((n - min) / span) * (h - 8);
      return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
    const cls = nums[nums.length - 1] >= nums[0] ? "up" : "down";
    return `<svg class="spark ${cls}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}"/></svg>`;
  }
  function tile(k, text, opts) {
    const o = opts || {};
    const cls = o.cls ? ` ${o.cls}` : "";
    const attrs = o.count == null ? "" : ` data-count="${o.count}" data-digits="${o.digits == null ? 1 : o.digits}" data-prefix="${esc(o.prefix || "")}" data-suffix="${esc(o.suffix || "")}" data-sign="${o.sign ? "1" : ""}" data-text="${esc(text)}"`;
    return `<article class="tile"><div class="k">${esc(k)}</div><div class="v${cls}"${attrs}>${esc(text)}</div>${o.hint ? `<div class="hint">${esc(o.hint)}</div>` : ""}${spark(o.series)}</article>`;
  }
  function tiles(list) { return `<div class="tiles">${list.join("")}</div>`; }
  function panel(title, tag, body, ico, hint) {
    const tip = hint ? `<span class="hint-tip" tabindex="0" title="${esc(hint)}" aria-label="${esc(hint)}">i</span>` : "";
    const tagHtml = tag ? `<span class="tag">${esc(tag)}</span>` : "";
    return `<section class="panel"><h3><span class="ttl">${icon(ico || "layers")}${esc(title)}${tip}</span>${tagHtml}</h3>${body}</section>`;
  }
  function empty(title, detail) {
    return `<div class="empty">${icon("feed")}<strong>${esc(title)}</strong><span>${esc(detail || "Nothing published on desk-data or main yet.")}</span></div>`;
  }
  function table(columns, rows) {
    if (!rows.length) return "";
    const head = columns.map(c => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
    const body = rows.map(r => `<tr>${columns.map(c => `<td class="${c.num ? "num" : ""}">${r[c.key] ?? "–"}</td>`).join("")}</tr>`).join("");
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function feedItem(meta, title, bullets) {
    const list = (bullets || []).filter(Boolean).slice(0, 4);
    return `<article class="feed-item"><div class="meta">${meta}</div><h4>${tx(title) || "Untitled brief"}</h4>${list.length ? `<ul>${list.map(b => `<li>${tx(b)}</li>`).join("")}</ul>` : ""}</article>`;
  }
  function skeleton() {
    return `<div class="skel-block" aria-hidden="true"><div class="tiles"><div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div></div><div class="skel skel-panel"></div><div class="skel skel-panel"></div></div>`;
  }
  function lineChart(net, gross) {
    const n = net || [], g = gross || [];
    if (n.length < 2) return empty("No equity curve yet", "Closed paper trades will draw net and gross here.");
    const w = 720, h = 220, pad = 16;
    const all = n.concat(g).filter(x => isFinite(x));
    const min = Math.min.apply(null, all), max = Math.max.apply(null, all);
    const span = max - min || 1;
    const path = arr => arr.map((v, i) => {
      const x = pad + (i / (arr.length - 1)) * (w - pad * 2);
      const y = pad + (1 - (v - min) / span) * (h - pad * 2);
      return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
    const area = path(n) + ` L${(w - pad).toFixed(1)} ${(h - pad).toFixed(1)} L${pad} ${(h - pad).toFixed(1)} Z`;
    const grids = [0.25, 0.5, 0.75].map(p => {
      const y = pad + p * (h - pad * 2);
      return `<line class="grid" x1="${pad}" y1="${y}" x2="${w - pad}" y2="${y}"/>`;
    }).join("");
    return `<div class="legend"><span><i></i>net</span><span><i class="gross"></i>gross</span></div>
      <svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Net and gross paper equity">
        <defs><linearGradient id="pv-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="rgba(20,241,149,.28)"/><stop offset="1" stop-color="rgba(153,69,255,0)"/></linearGradient></defs>
        ${grids}<path class="area" d="${area}"/><path class="gross" d="${path(g)}"/><path class="net" d="${path(n)}"/>
      </svg>`;
  }
  function animateNumbers(scope) {
    if (!scope || !scope.querySelectorAll) return;
    scope.querySelectorAll("[data-count]").forEach(el => {
      const target = Number(el.getAttribute("data-count"));
      const digits = Number(el.getAttribute("data-digits") || 1);
      const prefix = el.getAttribute("data-prefix") || "";
      const suffix = el.getAttribute("data-suffix") || "";
      const sign = el.getAttribute("data-sign") === "1";
      const finalText = el.getAttribute("data-text") || el.textContent;
      const paint = n => {
        if (n === target) { el.textContent = finalText; return; }
        const shown = (sign && n > 0 ? "+" : "") + prefix + fmt(n, digits) + suffix;
        el.textContent = shown;
      };
      if (!isFinite(target) || prefersReduced() || typeof requestAnimationFrame !== "function") {
        el.textContent = finalText;
        return;
      }
      const t0 = (typeof performance !== "undefined" ? performance.now() : Date.now());
      const dur = 720;
      (function frame(now) {
        const p = Math.min(1, (now - t0) / dur);
        const e = 1 - Math.pow(1 - p, 3);
        paint(target * e);
        if (p < 1) requestAnimationFrame(frame);
        else paint(target);
      })(t0);
    });
  }

  function grossOf(sell, rows) {
    const frac = num(sell.frac) ?? 1;
    const exitPx = num(sell.quote_px);
    const buys = rows.filter(t => t.book === sell.book && t.mint && t.mint === sell.mint && /buy|enter|open/i.test(t.action) && (num(t.time) == null || num(sell.time) == null || num(t.time) <= num(sell.time)));
    const buy = buys.length ? buys[buys.length - 1] : null;
    const entryPx = buy ? num(buy.quote_px) : null;
    const size = buy ? (num(buy.sol) || 0.5) : 0.5;
    if (entryPx && exitPx && entryPx > 0) return size * frac * (exitPx / entryPx - 1);
    const net = num(sell.pnl) || 0;
    const fee = num(sell.fee_sol) || 0;
    const hair = ((num(sell.haircut_pct) || 0) / 100) * (num(sell.sol) || 0);
    return net + fee + hair;
  }
  function tradeNet(t) {
    const n = num(t.net_pnl);
    return n != null ? n : num(t.pnl);
  }
  function tradeGross(t, rows) {
    const g = num(t.gross_pnl);
    if (g != null) return g;
    if (tradeNet(t) == null) return null;
    return grossOf(t, rows);
  }
  function modelOf(t) { const m = String((t && t.cost_model) || "").trim(); return m || "pre-v3"; }
  function solanaWindows(rows, nowMs) {
    const out = {};
    [7, 28].forEach(days => {
      const cut = (nowMs || Date.now()) / 1000 - days * 86400;
      const sells = (rows || []).filter(t => t.action === "sell" && tradeNet(t) != null && num(t.time) != null && num(t.time) >= cut);
      const counted = sells.filter(t => modelOf(t) === "v3-exit-aware");
      const older = sells.filter(t => modelOf(t) !== "v3-exit-aware");
      const labels = {};
      older.forEach(t => { const k = modelOf(t); labels[k] = (labels[k] || 0) + 1; });
      out[days] = {
        closes: counted.length,
        net: counted.reduce((s, t) => s + (tradeNet(t) || 0), 0),
        older: older.length,
        olderNet: older.reduce((s, t) => s + (tradeNet(t) || 0), 0),
        labels
      };
    });
    return out;
  }
  /* Local icons from dashboard/icons/icons.json (same file as dashboard/core.js).
     A monogram, a missing entry, or a remote URL falls back to the initials circle.
     The page never requests a third-party image. */
  let ICONS = { coins: {}, nfts: {}, nft_names: {} };
  function applyIcons(doc) {
    if (doc && typeof doc === "object") ICONS = doc;
  }
  function logoHue(name) {
    let h = 0;
    const s = String(name || "");
    for (let i = 0; i < s.length; i++) h = (h * 33 + s.charCodeAt(i)) >>> 0;
    return "hsl(" + (h % 360) + " 24% 64%)";
  }
  function initialsLogo(label, sm) {
    const words = String(label || "?").match(/[A-Za-z0-9]+/g) || ["?"];
    const ini = (words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)).toUpperCase();
    const cls = sm ? "sl-logo sm" : "sl-logo";
    return `<span class="${cls}" style="background:${logoHue(label)}" aria-hidden="true">${esc(ini)}</span>`;
  }
  function iconRecord(kind, key) {
    const k = String(key || "");
    if (!k) return null;
    if (kind === "nft") {
      const names = ICONS.nft_names || {};
      return (ICONS.nfts || {})[k] || (ICONS.nfts || {})[names[k.toLowerCase()]] || null;
    }
    return (ICONS.coins || {})[k.toUpperCase()] || null;
  }
  function localSrc(entry) {
    if (!entry || entry.source === "monogram" || !entry.src) return "";
    const src = String(entry.src);
    if (/^https?:/i.test(src) || src.indexOf("//") === 0) return "";
    return src;
  }
  function assetLogo(key, label, sm) {
    const src = localSrc(iconRecord("coin", key)) || localSrc(iconRecord("nft", key)) || localSrc(iconRecord("nft", label));
    if (!src) return initialsLogo(label || key, sm);
    const cls = sm ? "sl-logo sm" : "sl-logo";
    const px = sm ? 18 : 22;
    return `<img class="${cls}" src="${esc(src)}" alt="" width="${px}" height="${px}">`;
  }
  /* Frozen day-14 rule from solana-desk/specs/day14_verdict.py.
     A close is one positions.json row. Expectancy is pnl / (0.5 * n), using
     the sell pnl, never net_pnl. Entries opened in the void window and closes
     after the cutoff do not count. The verdict stays NOT_FINAL until the
     cutoff run also reconciles with trades.csv. */
  const DAY14_MIN = 20;
  const DAY14_STOP = -0.15;
  const DAY14_TICKET = 0.5;
  const DAY14_CUTOFF = "2026-11-11T15:00:00Z";
  const DAY14_VOID_START = "2026-10-31T01:30:00Z";
  const DAY14_VOID_END = "2026-11-01T00:00:00Z";
  function day14Unix(text) {
    if (typeof text !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(text)) return null;
    const ms = Date.parse(text);
    return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
  }
  function day14Int(v) {
    if (v == null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? Math.trunc(n) : null;
  }
  function day14Select(closed, cutoffTs, voidStart, voidEnd) {
    const counted = [], voided = [], late = [];
    (closed || []).forEach(row => {
      const opened = day14Int(row && row.opened);
      const done = day14Int(row && row.closed);
      if (opened == null || done == null) return;
      if (voidStart <= opened && opened < voidEnd) { voided.push(row); return; }
      if (done > cutoffTs) { late.push(row); return; }
      counted.push(row);
    });
    return { counted, voided, late };
  }
  function day14Decide(n, total) {
    const exp = n <= 0 ? null : total / (DAY14_TICKET * n);
    const stop = n >= DAY14_MIN && exp != null && exp < DAY14_STOP;
    return { rule: stop ? "STOP" : "CONTINUE", exp };
  }
  function day14Book(positions, book, cutoffTs, voidStart, voidEnd) {
    const closed = ((((positions || {}).books || {})[book] || {}).closed) || [];
    const sel = day14Select(closed, cutoffTs, voidStart, voidEnd);
    let total = 0;
    sel.counted.forEach(row => { total += Number(row.pnl) || 0; });
    const d = day14Decide(sel.counted.length, total);
    return { id: book, n: sel.counted.length, pnl: sel.counted.length ? total : null, exp: d.exp, rule: d.rule };
  }
  function day14IdsUnique(closed) {
    const ids = (closed || []).map(row => row && row.id);
    return ids.length === new Set(ids).size && ids.every(Boolean);
  }
  function day14BuildCloses(trades) {
    const lots = {};
    const built = [];
    let ok = true;
    (trades || []).forEach(row => {
      if (!row || row.book !== "F" || (row.action !== "buy" && row.action !== "sell")) return;
      const mint = row.mint || "";
      const ts = day14Int(row.time);
      if (ts == null) { ok = false; return; }
      if (row.action === "buy") {
        (lots[mint] = lots[mint] || []).push({ mint, opened: ts, closed: null, pnl: 0, frac: 0 });
        return;
      }
      if (row.pnl == null || row.pnl === "") { ok = false; return; }
      const queue = lots[mint];
      if (!queue || !queue.length) { ok = false; return; }
      const lot = queue[0];
      const pnl = Number(row.pnl);
      const frac = Number(row.frac || 0);
      if (!Number.isFinite(pnl) || !Number.isFinite(frac)) { ok = false; return; }
      lot.pnl += pnl;
      lot.frac += frac;
      lot.closed = ts;
      if (lot.frac >= 1 - 1e-4) built.push(queue.shift());
    });
    return { built, ok };
  }
  function day14Reconciles(closed, trades) {
    const built = day14BuildCloses(trades);
    if (!built.ok || !day14IdsUnique(closed)) return false;
    if (built.built.length !== closed.length) return false;
    const used = {};
    for (let r = 0; r < closed.length; r++) {
      const row = closed[r];
      let hit = null;
      for (let i = 0; i < built.built.length; i++) {
        if (used[i]) continue;
        const lot = built.built[i];
        if (lot.mint !== (row.mint || "")) continue;
        if (lot.opened !== day14Int(row.opened) || lot.closed !== day14Int(row.closed)) continue;
        if (Math.abs(lot.pnl - Number(row.pnl)) > 1e-8) continue;
        hit = i;
        break;
      }
      if (hit == null) return false;
      used[hit] = 1;
    }
    return true;
  }
  function day14Report(positions, trades) {
    const cutoffTs = day14Unix(DAY14_CUTOFF);
    const voidStart = day14Unix(DAY14_VOID_START);
    const voidEnd = day14Unix(DAY14_VOID_END);
    const books = ["F", "R", "S"].map(id => day14Book(positions, id, cutoffTs, voidStart, voidEnd));
    const fClosed = ((((positions || {}).books || {}).F || {}).closed) || [];
    const reasons = [];
    const last = positions && positions.last_run_ts;
    const lastTs = last != null && last !== "" ? day14Int(last) : null;
    if (lastTs == null) reasons.push("last_run_missing");
    else if (cutoffTs != null && lastTs < cutoffTs) reasons.push("last_run_before_cutoff");
    if (!day14IdsUnique(fClosed)) reasons.push("close_ids_not_unique");
    if (!day14Reconciles(fClosed, trades || [])) reasons.push("closes_do_not_reconcile");
    const final = reasons.length === 0;
    const f = books[0];
    const verdict = final ? f.rule : "NOT_FINAL";
    const gap = f.exp != null && books[1].exp != null ? (f.exp - books[1].exp) * 100 : null;
    let line = "Too early";
    if (f.n >= DAY14_MIN && f.exp != null) {
      const side = f.exp < DAY14_STOP ? "below the −15% line" : "clear of the −15% line";
      const verdictBit = verdict === "NOT_FINAL" ? "VERDICT NOT_FINAL until 2026-11-11 15:00Z" : "VERDICT " + verdict;
      line = side + " · rule " + f.rule + " · " + verdictBit;
    }
    return { books, gap, line, verdict, rule: f.rule, final, reasons };
  }
  function solanaModel(paper, trades, positions, nowMs) {
    const rows = Array.isArray(trades) ? trades : [];
    const sells = rows.filter(t => /sell|exit|close/i.test(t.action) && tradeNet(t) != null).slice().sort((a, b) => (num(a.time) || 0) - (num(b.time) || 0));
    const opens = [];
    if (paper && Array.isArray(paper.open)) {
      paper.open.forEach(p => opens.push({
        ticker: p.ticker || p.symbol || "–",
        book: p.book || "",
        size: num(p.size),
        entry: p.entry && num(p.entry.px),
        mark: num(p.last_mark),
        pnl: p.result && num(p.result.pnl),
        frac: num(p.frac) ?? 1,
        reason: String(p.thesis || "").replace(/_/g, " ")
      }));
    } else if (positions && positions.books) {
      for (const name of ["F", "R", "S"]) {
        const open = (positions.books[name] || {}).open || {};
        Object.keys(open).forEach(id => {
          const p = open[id] || {};
          opens.push({ ticker: p.symbol || "–", book: name, size: 0.5, entry: num(p.entry), mark: num(p.last_mark), pnl: num(p.pnl), frac: num(p.frac) ?? 1, reason: p.why || "" });
        });
      }
    }
    const openNet = opens.reduce((s, p) => s + (p.pnl || 0), 0);
    const openGross = opens.reduce((s, p) => {
      if (p.entry && p.mark && p.entry > 0) return s + (p.size || 0.5) * (p.frac || 1) * (p.mark / p.entry - 1);
      return s + (p.pnl || 0);
    }, 0);
    let realized = null;
    if (paper && paper.books) {
      realized = ["F", "R", "S"].reduce((s, n) => s + (num((paper.books[n] || {}).realized_pnl) || 0), 0);
    }
    if (realized == null) realized = sells.reduce((s, t) => s + (tradeNet(t) || 0), 0);
    const grossClosed = sells.reduce((s, t) => s + (tradeGross(t, rows) || 0), 0);
    const net = realized + openNet;
    const gross = grossClosed + openGross;
    const wins = sells.filter(t => num(t.pnl) > 0).length;
    const winRate = sells.length ? wins / sells.length : null;
    let netEq = 30, grossEq = 30;
    const netSeries = [netEq], grossSeries = [grossEq];
    const byBook = { F: [10], R: [10], S: [10] };
    const bookEq = { F: 10, R: 10, S: 10 };
    const refillRows = rows.filter(t => t.action === "refill" && num(t.sol) != null);
    const curve = sells.concat(refillRows).sort((a, b) => (num(a.time) || 0) - (num(b.time) || 0));
    const refillMarks = [];
    curve.forEach(t => {
      if (t.action === "refill") {
        const add = num(t.sol) || 0;
        netEq += add;
        netSeries.push(netEq);
        grossSeries.push(grossEq);
        const b = t.book;
        if (bookEq[b] != null) {
          bookEq[b] += add;
          byBook[b].push(bookEq[b]);
          refillMarks.push({ book: b, added: add, at: timeOf(t.time), reason: String(t.why || "") });
        }
        return;
      }
      const g = tradeGross(t, rows) || 0;
      const n = tradeNet(t) || 0;
      netEq += n; grossEq += g;
      netSeries.push(netEq); grossSeries.push(grossEq);
      const b = t.book;
      if (bookEq[b] != null) { bookEq[b] += n; byBook[b].push(bookEq[b]); }
    });
    if (opens.length) { netSeries.push(netEq + openNet); grossSeries.push(grossEq + openGross); }
    const names = { F: "Filtered", R: "Random", S: "Safety" };
    const books = ["F", "R", "S"].map(id => {
      const src = (paper && paper.books && paper.books[id]) || (positions && positions.books && positions.books[id]) || {};
      return {
        id, name: names[id],
        equity: num(src.equity),
        realized: num(src.realized_pnl),
        open: src.open != null && !Array.isArray(src.open) && typeof src.open === "object" ? Object.keys(src.open).length : num(src.open),
        closed: Array.isArray(src.closed) ? src.closed.length : num(src.closed),
        series: byBook[id]
      };
    });
    const activity = rows.map(t => ({
      time: timeOf(t.time), book: t.book || "", symbol: t.symbol || "", action: t.action || "", reason: (t.why || "").replace(/_/g, " "), pnl: tradeNet(t), model: modelOf(t)
    }));
    ((paper && paper.summaries) || []).forEach(s => activity.push({ time: s.at, book: "", symbol: "", action: s.type || "skip", reason: s.note || "", pnl: null, model: "" }));
    activity.sort((a, b) => String(b.time || "").localeCompare(String(a.time || "")));
    const verdict = day14Report(positions, rows);
    const bookRows = verdict.books;
    return { net, gross, openCount: opens.length, winRate, netSeries, grossSeries, books, refills: refillMarks, opens, activity: activity.slice(0, 24), closed: sells.length, windows: solanaWindows(rows, nowMs), bookRows, gap: verdict.gap, day14: verdict.line, day14Verdict: verdict.verdict, day14Rule: verdict.rule };
  }
  function solSigned(n, d) {
    if (n == null || !isFinite(n)) return `<span class="sl-flat">–</span>`;
    const cls = n > 0 ? "sl-up" : n < 0 ? "sl-down" : "sl-flat";
    const body = (n > 0 ? "+" : n < 0 ? "−" : "") + fmt(Math.abs(n), d == null ? 3 : d);
    return `<span class="${cls}">${esc(body)}</span>`;
  }
  function solPct(frac) {
    if (frac == null || !isFinite(frac)) return `<span class="sl-flat">–</span>`;
    const n = frac * 100;
    const cls = n > 0 ? "sl-up" : n < 0 ? "sl-down" : "sl-flat";
    const body = (n > 0 ? "+" : n < 0 ? "−" : "") + fmt(Math.abs(n), 1) + "%";
    return `<span class="${cls}">${esc(body)}</span>`;
  }
  function solanaHtml(model, health) {
    const m = model || solanaModel(null, [], null);
    const names = { F: "Desk", R: "Random", S: "Shadow" };
    const rows = (m.bookRows || []).map(b => `<tr>
      <td><b>${esc(b.id)}</b> <span class="sl-meta">${esc(names[b.id] || "")}</span></td>
      <td class="num">${solSigned(b.pnl, 3)}</td>
      <td class="num">${solPct(b.exp)}</td>
      <td class="num">${esc(String(b.n))}</td>
    </tr>`).join("");
    const gap = m.gap == null ? "–" : ((m.gap > 0 ? "+" : m.gap < 0 ? "−" : "") + fmt(Math.abs(m.gap), 1) + " pp");
    const gapCls = m.gap > 0 ? "sl-up" : m.gap < 0 ? "sl-down" : "sl-flat";
    const refillBits = (m.refills || []).map(r => esc(r.book) + " +" + fmt(r.added, 3) + " SOL");
    const refillWhy = (m.refills && m.refills[0] && m.refills[0].reason) || "";
    const refillLine = refillBits.length ? `<p class="sl-note sl-left">Cash refill, not a trade: ${refillBits.join(", ")}. ${esc(refillWhy)}. The equity line jumps by the cash added. P&amp;L does not.</p>` : "";
    const curves = (m.books || []).map(b => `<div class="sl-kv"><span class="sl-k">${esc(b.id)} equity</span><b>${spark(b.series) || "–"}</b></div>`).join("");
    const books = `<section class="sl-box"><header class="sl-box-h"><span class="sl-box-title">Books <em>F / R / S</em></span></header>
      <table class="sl-table"><thead><tr><th>Book</th><th class="num">P&amp;L</th><th class="num">Exp/tr</th><th class="num">N</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="sl-note sl-left">F vs R <b class="${gapCls}">${esc(gap)}</b> · F exp ${solPct(m.bookRows && m.bookRows[0] && m.bookRows[0].exp)} · ${esc(m.day14 || "Too early")} · ${esc(String((m.bookRows && m.bookRows[0] && m.bookRows[0].n) || 0))} F closes</p>
      ${curves ? `<div class="sl-kvs">${curves}</div>` : ""}${refillLine}
    </section>`;
    const h = health || {};
    const gateBits = [
      ["Checks", num(h.checks)],
      ["Leads", num(h.leads_this_run)],
      ["Skips", num(h.eligibility_skips)],
      ["Runs", num(h.runs) != null && num(h.expected_runs) != null ? h.runs + "/" + h.expected_runs : num(h.runs)]
    ].filter(pair => pair[1] != null && pair[1] !== "");
    const gate = gateBits.length ? `<section class="sl-box"><header class="sl-box-h"><span class="sl-box-title">Gate <em>this desk file</em></span></header><div class="sl-kvs">${gateBits.map(pair => `<div class="sl-kv"><span class="sl-k">${esc(pair[0])}</span><b>${esc(String(pair[1]))}</b></div>`).join("")}</div></section>` : `<section class="sl-box"><p class="sl-empty">No gate counts in the health file.</p></section>`;
    const opens = (m.opens || []).map(p => {
      let pct = null;
      if (p.entry && p.mark && p.entry > 0) pct = (p.mark / p.entry - 1) * 100;
      const cls = pct == null ? "sl-flat" : pct > 0 ? "sl-up" : pct < 0 ? "sl-down" : "sl-flat";
      const pctText = pct == null ? "–" : (pct > 0 ? "+" : pct < 0 ? "−" : "") + fmt(Math.abs(pct), 1) + "%";
      return `<div class="sl-row"><span class="sl-rank">${esc(p.book || "")}</span>${assetLogo(p.ticker, p.ticker, false)}<span class="sl-id"><span class="sl-id-top"><span class="sl-tk">${tx(p.ticker) || "–"}</span></span><span class="sl-sub">${tx(p.reason) || ""}</span></span><span class="sl-end"><span class="${cls}">${esc(pctText)}</span></span></div>`;
    }).join("");
    const openBlock = `<section class="sl-box"><header class="sl-box-h"><span class="sl-box-title">Open positions <em>${m.openCount}</em></span></header>${opens || `<p class="sl-empty">No open paper positions.</p>`}</section>`;
    const closes = (m.activity || []).filter(a => /sell|exit|close/i.test(a.action)).slice(0, 8).map(a => `<div class="sl-line"><span class="sl-tk">${tx(a.symbol) || "–"}</span><span class="sl-side">${esc(a.book || "")} · ${tx(a.reason) || "–"}</span>${a.pnl == null ? "<span class=\"sl-flat\">–</span>" : solSigned(a.pnl, 3)}</div>`).join("");
    const closeBlock = `<section class="sl-box"><header class="sl-box-h"><span class="sl-box-title">Recent closes <em>exit reason</em></span></header>${closes || `<p class="sl-empty">No closed fills yet.</p>`}</section>`;
    const fail = num(h.data_failure_rate);
    const healthBlock = `<section class="sl-box"><header class="sl-box-h"><span class="sl-box-title">Data health</span></header><div class="sl-kvs">
      <div class="sl-kv"><span class="sl-k">Data ok</span><b>${fail == null ? "–" : esc(fmt((1 - fail) * 100, 0) + "%")}</b></div>
      <div class="sl-kv"><span class="sl-k">Runs</span><b>${num(h.runs) == null ? "–" : esc(String(h.runs))}${num(h.expected_runs) == null ? "" : esc("/" + h.expected_runs)}</b></div>
      <div class="sl-kv"><span class="sl-k">Void day</span><b>${h.void_day ? "yes" : h.void_day === false ? "no" : "–"}</b></div>
    </div></section>`;
    const first = (m.opens || [])[0];
    const status = first ? `${m.openCount} open · ${sanitize(first.ticker)} · ${sanitize(first.reason || "paper")}` : (m.openCount ? m.openCount + " open paper tickets" : "No open paper tickets");
    return `<div class="stack sl-tab"><div class="sl-mascot"><p class="sl-status">${esc(status)}</p></div>${books}${gate}${openBlock}${closeBlock}${healthBlock}<p class="sl-note">Research only · you trade elsewhere</p></div>`;
  }

  function flagsFor(flags) {
    const list = [];
    if (!flags || typeof flags !== "object") return list;
    ["new", "recent"].forEach(k => (flags[k] || []).forEach(f => { if (f && f.kind) list.push(f); }));
    return list;
  }
  function top10Rows(log, pulse, liq) {
    let rows = [];
    if (log && Array.isArray(log.cardano_top10)) rows = log.cardano_top10.slice();
    else if (log && Array.isArray(log.history)) {
      const hit = log.history.slice().reverse().find(h => h && Array.isArray(h.cardano_top10) && h.cardano_top10.length);
      if (hit) rows = hit.cardano_top10.slice();
    }
    if (!rows.length && liq && Array.isArray(liq.tokens)) {
      rows = liq.tokens.map(t => ({ ticker: t.ticker, rank: t.rank, policy_short: t.policy_short }));
    }
    if (!rows.length && pulse && Array.isArray(pulse.points)) {
      const latest = new Map();
      pulse.points.forEach(p => {
        if (!p || (p.chain && p.chain !== "cardano")) return;
        const prev = latest.get(p.ticker);
        if (!prev || String(p.t || "") > String(prev.t || "")) latest.set(p.ticker, p);
      });
      rows = Array.from(latest.values()).map(p => ({
        ticker: p.ticker,
        ada_side_depth: p.metrics && p.metrics.ada_side,
        holders: p.metrics && (p.metrics.holders_onchain != null ? p.metrics.holders_onchain : p.metrics.holders),
        mcap_ada: p.metrics && p.metrics.mcap_ada
      })).sort((a, b) => (b.ada_side_depth || 0) - (a.ada_side_depth || 0)).slice(0, 10);
      rows.forEach((r, i) => { r.rank = i + 1; });
    }
    return rows.slice().sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99)).slice(0, 10);
  }
  function freshFunded(row) {
    if (!row || typeof row !== "object") return false;
    if (row.fresh_funded === true || row.funded_fresh === true) return true;
    const bag = row.cautions || row.caution || row.flags;
    if (Array.isArray(bag) && bag.some(x => String(x).toLowerCase() === "fresh_funded")) return true;
    if (typeof bag === "string" && bag.toLowerCase() === "fresh_funded") return true;
    return false;
  }
  const VETO_REASON = {
    lp_pulled: "LP pull met the ahead-of-dump floors",
    ahead_of_dump: "Pull followed by a same-party sell inside the window",
    dev_sold: "Dev sell reached the desk minimum",
    serial_launcher: "Serial launches crossed the caution line"
  };
  function retiredRiskKey() {
    return String.fromCharCode(115, 117, 114, 105);
  }
  function riskSourceLabel(source) {
    const s = sanitize(source);
    if (!s) return "";
    if (s === "dev_checks") return "dev_checks";
    if (s === "scanner" || s === retiredRiskKey()) return "risk scanner";
    return s;
  }
  function riskTone(risk) {
    const s = String(risk || "").toLowerCase();
    if (!s || s === "none" || s === "null") return "s-none";
    if (/red|high|rug/.test(s)) return "s-high";
    if (/yellow|caution|med|mid/.test(s)) return "s-mid";
    if (/low|green|ok/.test(s)) return "s-low";
    return "s-none";
  }
  function riskBag(log) {
    if (!log || typeof log !== "object") return null;
    const cur = log.risk_check;
    if (cur && typeof cur === "object") return cur;
    const old = log[retiredRiskKey()];
    if (old && typeof old === "object") return old;
    return null;
  }
  function riskList(log) {
    const bag = riskBag(log);
    if (!bag) return [];
    return Object.keys(bag).map(ticker => {
      const row = bag[ticker] || {};
      const tags = Array.isArray(row.tags) ? row.tags.map(t => sanitize(t)).filter(t => t && t.length <= 28) : [];
      return { ticker: sanitize(ticker), risk: sanitize(row.risk), tone: riskTone(row.risk), tags, source: riskSourceLabel(row.source) };
    }).filter(r => r.ticker);
  }
  function riskTip(row) {
    if (!row) return "No risk score";
    const score = row.risk ? "Risk score: " + row.risk : "No risk score";
    return [score].concat(row.tags || []).concat(row.source ? [row.source] : []).filter(Boolean).join(" · ");
  }
  const CORE_NAMES = ["ASCEND", "STRIKE"];
  function orderPoolRows(rows, log, pulse) {
    const list = (rows || []).map(r => Object.assign({}, r));
    CORE_NAMES.forEach(name => {
      if (list.some(r => String(r.ticker || "").toUpperCase() === name)) return;
      const series = depthSeries(log, pulse, name);
      list.push({ ticker: name, ada_side_depth: series.length ? series[series.length - 1] : null, rank: "" });
    });
    const core = CORE_NAMES.map(name => list.find(r => String(r.ticker || "").toUpperCase() === name)).filter(Boolean);
    const rest = list.filter(r => CORE_NAMES.indexOf(String(r.ticker || "").toUpperCase()) < 0)
      .sort((a, b) => (num(b.ada_side_depth) || 0) - (num(a.ada_side_depth) || 0));
    return core.concat(rest);
  }
  function flagTone(kind) {
    const k = String(kind || "");
    if (/rug|sniper_full_exit|dump|kill/.test(k)) return "red";
    if (/coordinated|preseeder|fresh_wallet|identical_size|swarm/.test(k)) return "amber";
    return "";
  }
  function flaggedThisRun(log, flagsDoc, kills) {
    const map = {};
    const put = (ticker, tone) => {
      const name = tickerName(ticker);
      if (!name || !tone) return;
      const row = map[name] || { ticker: name, tone: tone };
      if (tone === "red") row.tone = "red";
      map[name] = row;
    };
    ((flagsDoc && flagsDoc.new) || []).forEach(f => {
      const tone = flagTone(f.kind);
      const names = [];
      if (f.ticker) names.push(f.ticker);
      (f.launches || []).forEach(n => names.push(n));
      names.forEach(n => put(n, tone));
    });
    if (kills && typeof kills === "object") {
      Object.keys(kills).forEach(k => { if (kills[k] && kills[k].hit === true) put(k, "red"); });
    }
    ((log && log.kill_hits) || []).forEach(line => {
      const m = String(line).match(/^([A-Z][A-Z0-9]{1,15})\b/);
      if (m) put(m[1], "red");
    });
    return Object.keys(map).map(k => map[k]).sort((a, b) => (a.tone === b.tone ? a.ticker.localeCompare(b.ticker) : (a.tone === "red" ? -1 : 1)));
  }
  function riskBadge(row) {
    if (!row || !row.ticker) return "";
    return `<span class="risk-score ${row.tone}" title="${esc(riskTip(row))}">${esc(row.ticker)}</span>`;
  }
  const CODENAME_KEYS = ["codename", "code_name", "animal", "nickname", "alias", "public_name", "display_name"];
  function animalName(value) {
    const t = String(value || "").trim();
    if (!t || t.length > 40 || !/^[A-Za-z][A-Za-z' -]{1,40}$/.test(t)) return "";
    if (/founder|seller|buyer|cluster|sniper|dumper|launcher|\$|@/i.test(t)) return "";
    return t;
  }
  function whaleCodenames(log) {
    const map = {};
    const wallets = log && log.whale_watch && log.whale_watch.wallets;
    const list = Array.isArray(wallets) ? wallets : wallets && typeof wallets === "object" ? Object.keys(wallets).map(k => Object.assign({ tag: k }, wallets[k])) : [];
    list.forEach(w => {
      if (!w || typeof w !== "object") return;
      let tag = null;
      const pre = String(w.prefix || "").trim();
      if (/^w-[0-9a-f]{8}$/i.test(pre)) tag = safeTag(pre);
      Object.keys(w).forEach(k => {
        if (k === "prefix") return;
        const v = w[k];
        if (typeof v === "string" && /^w-[0-9a-f]{8}$/i.test(v.trim())) tag = safeTag(v);
      });
      let name = "";
      CODENAME_KEYS.forEach(k => { if (!name && w[k] != null) name = animalName(w[k]); });
      if (tag && name) map[tag] = name;
    });
    return map;
  }
  let whaleNames = {};
  function labelTag(tag) {
    const s = safeTag(tag);
    if (!s) return "";
    if (whaleNames[s]) return whaleNames[s];
    if (/^w-[0-9a-f]{8}$/.test(s)) return s;
    if (/founder/i.test(s)) return "";
    return s;
  }
  function snapshotRows(node) {
    if (!node || typeof node !== "object") return [];
    if (Array.isArray(node.whales) && node.whales.length) return node.whales.filter(w => w && typeof w === "object");
    if (node.founder_whale && typeof node.founder_whale === "object") return [node.founder_whale];
    return [];
  }
  function rowNet(row, whale) {
    const n = num(row && row.net_flow_ada);
    if (n == null) return null;
    const name = animalName(row.codename) || animalName(row.handle);
    const tag = safeTag(row.tag) || safeTag(row.address) || "";
    if (whale.codename && name && name === whale.codename) return n;
    if (whale.tag && tag && tag === whale.tag) return n;
    return null;
  }
  function netFor(node, whale) {
    const rows = snapshotRows(node);
    if (!rows.length) return null;
    for (let i = 0; i < rows.length; i++) {
      const n = rowNet(rows[i], whale);
      if (n != null) return n;
    }
    if (rows.length === 1 && whale.fromOld) {
      const n = num(rows[0].net_flow_ada);
      if (n != null) return n;
    }
    return null;
  }
  function publicWhales(log) {
    if (!log || typeof log !== "object") return [];
    let fromOld = false;
    let raw = Array.isArray(log.whales) ? log.whales.filter(w => w && typeof w === "object") : [];
    if (!raw.length && log.founder_whale && typeof log.founder_whale === "object") {
      raw = [log.founder_whale];
      fromOld = true;
    }
    const whales = raw.map(row => {
      const name = animalName(row.codename) || animalName(row.handle) || "";
      const tag = safeTag(row.tag) || safeTag(row.address) || "";
      let codename = name || (tag && whaleNames[tag]) || "";
      if (!codename && fromOld && Object.keys(whaleNames).length === 1) codename = whaleNames[Object.keys(whaleNames)[0]];
      if (!codename) codename = "Whale";
      const role = row.role === "lead" || row.role === "watch" ? row.role : (codename === "Grumpy Walrus" ? "lead" : "watch");
      return { codename, role, tag: /^w-/.test(tag) ? tag : "", fromOld, net_flow_ada: num(row.net_flow_ada) };
    });
    const nodes = [{ node: log, t: log.asof }].concat((Array.isArray(log.history) ? log.history : []).map(h => ({ node: h, t: h && h.asof })));
    whales.forEach(w => {
      const pts = [];
      nodes.forEach(item => {
        const n = netFor(item.node, w);
        if (n == null) return;
        pts.push({ t: String(item.t || ""), n });
      });
      pts.sort((a, b) => String(a.t).localeCompare(String(b.t)));
      const dedup = [];
      pts.forEach(p => {
        if (dedup.length && dedup[dedup.length - 1].t === p.t) dedup[dedup.length - 1] = p;
        else dedup.push(p);
      });
      w.history = dedup.slice(-12);
    });
    return whales;
  }
  function learnedLines(log) {
    const raw = log && log.learned_or_changed;
    if (!Array.isArray(raw)) return [];
    return raw.map(item => sanitize(typeof item === "string" ? item : item && (item.text || item.line || ""))).filter(Boolean).slice(0, 8);
  }
  function outcomeRows(log) {
    const bag = log && log.flag_outcomes;
    if (!bag || typeof bag !== "object" || Array.isArray(bag)) return [];
    const whaleKey = Object.prototype.hasOwnProperty.call(bag, "whale_move") ? "whale_move"
      : Object.prototype.hasOwnProperty.call(bag, "founder_whale_move") ? "founder_whale_move" : "whale_move";
    const keys = ["coordinated_buys", "fresh_wallet_swarm", "sniper_full_exit", "holder_concentration", whaleKey];
    return keys.map(key => {
      const row = bag[key] && typeof bag[key] === "object" ? bag[key] : {};
      const pump = num(row.pump);
      const rug = num(row.rug);
      return { label: displayKind(key), pump: pump == null ? 0 : pump, rug: rug == null ? 0 : rug };
    });
  }
  function publicFounder(fw) {
    if (!fw || typeof fw !== "object") return null;
    const tag = safeTag(fw.address) || safeTag(fw.tag) || safeTag(fw.w_tag);
    return {
      tag: tag && /^w-[0-9a-f]{8}$/.test(tag) ? tag : "",
      net_flow_ada: num(fw.net_flow_ada),
      summary: fw.summary,
      line: fw.line
    };
  }
  function tickerName(v) {
    const s = sanitize(v);
    if (!s || s.length > 24 || /(?:stake|addr)1|0x[0-9a-f]{8,}/i.test(s)) return "";
    return s;
  }
  function flagTags(f) {
    const bag = [].concat(f.tag || [], f.tags || [], f.wallet || [], f.wallets || []);
    const out = [];
    bag.forEach(t => { const s = safeTag(t); if (s && out.indexOf(s) < 0) out.push(s); });
    return out;
  }
  function flagAda(f) {
    return num(f.ada) != null ? num(f.ada) : num(f.ada_equiv) != null ? num(f.ada_equiv) : num(f.ada_each);
  }
  function depthSeries(log, pulse, ticker) {
    const tk = String(ticker || "").toUpperCase();
    const fromPulse = [];
    ((pulse && pulse.points) || []).forEach(p => {
      if (!p || String(p.ticker || "").toUpperCase() !== tk) return;
      if (p.chain && p.chain !== "cardano") return;
      const m = p.metrics || {};
      const v = num(m.ada_side);
      if (v != null) fromPulse.push(v);
    });
    if (fromPulse.length >= 2) return fromPulse;
    const fromHist = [];
    ((log && log.history) || []).forEach(h => {
      ((h && h.cardano_top10) || []).forEach(row => {
        if (String(row.ticker || "").toUpperCase() !== tk) return;
        const v = num(row.ada_side_depth);
        if (v != null) fromHist.push(v);
      });
    });
    const cur = ((log && log.cardano_top10) || []).find(r => String(r.ticker || "").toUpperCase() === tk);
    if (cur && num(cur.ada_side_depth) != null) fromHist.push(num(cur.ada_side_depth));
    return fromHist;
  }
  function killFor(kills, ticker) {
    if (!kills || typeof kills !== "object") return null;
    const key = Object.keys(kills).find(k => k.toUpperCase() === String(ticker || "").toUpperCase());
    const row = key ? kills[key] : null;
    if (!row || typeof row !== "object") return null;
    return { line: num(row.line), value: num(row.value), hit: row.hit === true, metric: sanitize(row.metric), text: sanitize(row.text) };
  }
  function heatModel(flags) {
    const early = (flags || []).filter(f => f.kind === "repeat_early_buyer" || f.kind === "watch_tags_first_block" || f.kind === "coordinated_buys");
    const cell = {};
    const cols = {};
    early.forEach(f => {
      const tags = flagTags(f);
      const launches = Array.isArray(f.launches) && f.launches.length ? f.launches : (f.ticker ? [f.ticker] : []);
      const ada = flagAda(f);
      launches.forEach(launch => {
        const col = tickerName(launch);
        if (!col) return;
        cols[col] = (cols[col] || 0) + 1;
        tags.forEach(tag => {
          const k = tag + "|" + col;
          const prev = cell[k] || { tag, col, n: 0, ada: 0 };
          prev.n += 1;
          if (ada != null) prev.ada += ada;
          cell[k] = prev;
        });
      });
    });
    const snipers = {};
    (flags || []).forEach(f => {
      if (f.kind !== "sniper_full_exit") return;
      const col = tickerName(f.ticker);
      flagTags(f).forEach(tag => { if (col) snipers[tag + "|" + col] = true; });
    });
    const colList = Object.keys(cols).sort((a, b) => cols[b] - cols[a]).slice(0, 12);
    const rowScore = {};
    Object.keys(cell).forEach(k => { rowScore[cell[k].tag] = (rowScore[cell[k].tag] || 0) + cell[k].n; });
    const rowList = Object.keys(rowScore).sort((a, b) => rowScore[b] - rowScore[a]).slice(0, 14);
    return { colList, rowList, cell, snipers };
  }
  function sankeyFlows(whale, big, log) {
    const tag = safeTag(whale && whale.whale_tag);
    const flows = {};
    const add = (token, dir, ada) => {
      const name = tickerName(token);
      if (!name || name === "ADA" || ada == null || !isFinite(ada) || ada === 0) return;
      const row = flows[name] || { token: name, inn: 0, out: 0 };
      if (dir === "out") row.out += Math.abs(ada); else row.inn += Math.abs(ada);
      flows[name] = row;
    };
    ((whale && whale.rows) || []).forEach(r => {
      const q = num(r.qty);
      const ada = num(r.ada_leg);
      const kind = String(r.kind || "");
      let dir = q != null ? (q < 0 ? "out" : "in") : "";
      if (!dir && /out|remove|sell/.test(kind)) dir = "out";
      if (!dir && /in|add|buy|fill/.test(kind)) dir = "in";
      if (dir) add(r.token, dir, ada != null ? ada : (q != null ? 1 : null));
    });
    const extra = []
      .concat((big && big.moves) || [])
      .concat((log && log.liquidity_flows && log.liquidity_flows.big_moves) || []);
    extra.forEach(m => {
      if (tag && safeTag(m.tag) !== tag) return;
      const dir = /out|sell/.test(String(m.direction || "")) ? "out" : "in";
      add(m.ticker, dir, num(m.ada));
    });
    const list = Object.keys(flows).map(k => flows[k]);
    return {
      tag: tag || "",
      out: list.filter(r => r.out > 0).sort((a, b) => b.out - a.out).slice(0, 6),
      inn: list.filter(r => r.inn > 0).sort((a, b) => b.inn - a.inn).slice(0, 6)
    };
  }
  function paperBoard(paper) {
    const closed = (paper && paper.closed) || [];
    const open = (paper && paper.open) || [];
    let wins = 0, losses = 0, pnl = 0;
    closed.forEach(c => {
      const pct = c && c.result && num(c.result.pct);
      const p = c && c.result && num(c.result.pnl);
      if ((pct != null ? pct : p) > 0) wins += 1; else losses += 1;
      if (p != null) pnl += p;
    });
    return { wins, losses, pnl, open: open.length, closed: closed.length, unit: sanitize(paper && paper.unit) };
  }
  function cardanoView(data) {
    const d = data || {};
    const flags = flagsFor(d.flags);
    const top = top10Rows(d.log, d.pulse, d.liq);
    const byTicker = {};
    flags.forEach(f => {
      const tk = String(f.ticker || "").toUpperCase();
      if (!tk) return;
      (byTicker[tk] = byTicker[tk] || []).push(f.kind);
    });
    const vetoTokens = (d.vetoes && Array.isArray(d.vetoes.tokens)) ? d.vetoes.tokens : [];
    const dumpMoves = ((d.liq && d.liq.moves) || []).filter(m => m && m.ahead_of_dump);
    const counts = Object.assign({ lp_pulled: 0, ahead_of_dump: dumpMoves.length, dev_sold: 0, serial_launcher: 0 }, (d.vetoes && d.vetoes.counts) || {});
    if (!(d.vetoes && d.vetoes.counts)) {
      vetoTokens.forEach(t => (t.vetoes || []).forEach(v => { if (counts[v.code] != null && v.code !== "ahead_of_dump") counts[v.code] += 1; }));
    }
    const devs = (d.devs && Array.isArray(d.devs.entries)) ? d.devs.entries : [];
    const risk = riskList(d.log);
    const whales = publicWhales(d.log);
    const oldOnly = !(d.log && Array.isArray(d.log.whales) && d.log.whales.length);
    const founder = publicFounder(oldOnly && d.log ? d.log.founder_whale : null);
    return { flags, top, byTicker, vetoTokens, dumpMoves, counts, devs, risk, founder, whales, learned: learnedLines(d.log), outcomes: outcomeRows(d.log), brief: d.brief || null, whale: d.whale || null, liq: d.liq || null, big: d.big || null, log: d.log || null, pulse: d.pulse || null, kills: d.kills || null, pump: d.pump || null, paper: d.paper || null, vetoes: d.vetoes || null };
  }
  function briefBits(b, desk) {
    if (!b) return null;
    const learned = [].concat(b.learned || [], b.highlights || [], b.rules_learned || []).map(x => (typeof x === "string" ? x : x && (x.text || x.rule || ""))).filter(Boolean);
    return { date: b.date, desk, headline: b.headline || "", learned, score: b.scoreboard || null };
  }
  function donut(pump, rug, label) {
    const p = pump || 0, r = rug || 0, total = p + r;
    const R = 28, C = 2 * Math.PI * R;
    const pumpLen = total ? (p / total) * C : 0;
    const rugLen = total ? (r / total) * C : 0;
    const ring = total
      ? `<circle cx="36" cy="36" r="${R}" fill="none" stroke="rgba(255,93,115,.85)" stroke-width="8" stroke-dasharray="${rugLen.toFixed(2)} ${(C - rugLen).toFixed(2)}" stroke-dashoffset="${(-pumpLen).toFixed(2)}" transform="rotate(-90 36 36)"/>
         <circle cx="36" cy="36" r="${R}" fill="none" stroke="rgba(62,224,162,.9)" stroke-width="8" stroke-dasharray="${pumpLen.toFixed(2)} ${(C - pumpLen).toFixed(2)}" transform="rotate(-90 36 36)"/>`
      : `<circle cx="36" cy="36" r="${R}" fill="none" stroke="rgba(255,255,255,.16)" stroke-width="8" stroke-dasharray="3 4"/>`;
    return `<div class="donut"><svg viewBox="0 0 72 72" width="72" height="72" aria-hidden="true">${ring}<text x="36" y="40" text-anchor="middle" fill="#e8eef6" font-size="11" font-family="ui-monospace,monospace">${total ? Math.round(100 * p / total) + "%" : "–"}</text></svg><b>${esc(label)}</b><span>${total ? p + " pump · " + r + " rug" : "no scores yet"}</span></div>`;
  }
  function killSpark(values, kill) {
    const nums = (values || []).filter(n => typeof n === "number" && isFinite(n));
    if (nums.length < 2) return "";
    const line = kill && kill.line != null ? kill.line : null;
    const all = line == null ? nums : nums.concat([line]);
    const min = Math.min.apply(null, all), max = Math.max.apply(null, all);
    const span = max - min || 1;
    const w = 220, h = 52;
    const d = nums.map((n, i) => {
      const x = 4 + (i / (nums.length - 1)) * (w - 8);
      const y = h - 6 - ((n - min) / span) * (h - 14);
      return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
    const ky = line == null ? null : h - 6 - ((line - min) / span) * (h - 14);
    const rule = ky == null ? "" : `<line x1="4" y1="${ky.toFixed(1)}" x2="${w - 4}" y2="${ky.toFixed(1)}" stroke="${kill.hit ? "#ff5d73" : "#ffb020"}" stroke-width="1.2" stroke-dasharray="4 3"/>`;
    return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Pool depth"><path d="${d}" fill="none" stroke="#3d8bff" stroke-width="2.2"/>${rule}</svg>`;
  }
  function gauge(kill) {
    if (!kill || kill.line == null || kill.value == null) return "";
    const ratio = kill.line === 0 ? 0 : Math.max(0, Math.min(1.35, kill.value / kill.line));
    const ang = Math.PI * (1 - Math.min(ratio, 1));
    const x = 50 + Math.cos(ang) * 36;
    const y = 46 - Math.sin(ang) * 36;
    const large = ratio >= 1 ? 1 : 0;
    return `<div class="gauge ${kill.hit ? "hit" : ""}"><svg viewBox="0 0 100 58" aria-hidden="true"><path d="M14 46 A36 36 0 0 1 86 46" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="6" stroke-linecap="round"/><path d="M14 46 A36 36 0 ${large} 1 ${x.toFixed(1)} ${y.toFixed(1)}" fill="none" stroke="${kill.hit ? "#ff5d73" : "#3d8bff"}" stroke-width="6" stroke-linecap="round"/></svg><div class="nm">${esc(kill.metric || "line")}</div><div class="dim">${esc(fmt(kill.value, 0))} / ${esc(fmt(kill.line, 0))}</div></div>`;
  }
  function heatHtml(model) {
    if (!model.rowList.length || !model.colList.length) return empty("No early-buyer clusters yet", "The heat grid fills when flags name a w- tag and a launch.");
    let i = 0;
    const head = model.colList.map(c => `<th><span>${esc(c)}</span></th>`).join("");
    const body = model.rowList.map(tag => `<tr><th class="rowlab">${esc(labelTag(tag) || tag)}</th>${model.colList.map(col => {
      const hit = model.cell[tag + "|" + col];
      const n = hit ? hit.n : 0;
      const level = n >= 3 ? 3 : n;
      const ring = model.snipers[tag + "|" + col] ? " ring" : "";
      const label = hit && hit.ada ? fmt(hit.ada, 0) : n ? String(n) : "";
      const delay = (i++ * 18);
      return `<td class="h${level}${ring}" style="animation-delay:${delay}ms" title="${esc(tag + " · " + col + (hit && hit.ada ? " · " + fmt(hit.ada, 0) + " ADA" : " · " + n + " hits"))}">${esc(label)}</td>`;
    }).join("")}</tr>`).join("");
    return `<div class="heat-wrap"><table class="heat"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table></div><p class="dim">Color is how often the tag showed up. A number is ADA when the flag recorded it. A red ring is a sniper full exit.</p>`;
  }
  function sankeyHtml(flow) {
    const outs = flow.out, inns = flow.inn;
    if (!outs.length && !inns.length) return empty("No rotation to draw", "Whale ledger deltas and whale-tagged moves are empty. Balances stay off this page.");
    const h = 250, w = 640, mid = w / 2;
    const slot = (list, x) => list.map((row, i) => {
      const y = 28 + (list.length === 1 ? 90 : (i / (list.length - 1)) * 180);
      return Object.assign({ x, y }, row);
    });
    const L = slot(outs, 16), R = slot(inns, w - 150);
    const max = Math.max(1, ...outs.map(r => r.out), ...inns.map(r => r.inn));
    const paths = [];
    L.forEach(src => {
      const sw = 2 + (src.out / max) * 16;
      paths.push(`<path class="flow" d="M${src.x + 120} ${src.y} C ${mid - 40} ${src.y}, ${mid - 20} 120, ${mid} 120" stroke="rgba(255,93,115,.85)" stroke-width="${sw.toFixed(1)}"/>`);
    });
    R.forEach(dst => {
      const sw = 2 + (dst.inn / max) * 16;
      paths.push(`<path class="flow" d="M${mid} 120 C ${mid + 20} 120, ${mid + 40} ${dst.y}, ${dst.x} ${dst.y}" stroke="rgba(62,224,162,.9)" stroke-width="${sw.toFixed(1)}"/>`);
    });
    const labels = L.map(r => `<text x="${r.x}" y="${r.y + 4}" fill="#ffc6d0" font-size="11" font-family="ui-monospace,monospace">${esc(r.token)} ${esc(fmt(r.out, 0))}</text>`).concat(
      R.map(r => `<text x="${r.x}" y="${r.y + 4}" fill="#c8ffe6" font-size="11" font-family="ui-monospace,monospace">${esc(r.token)} ${esc(fmt(r.inn, 0))}</text>`)
    );
    const who = labelTag(flow.tag) || "Whale";
    return `<svg class="sankey" viewBox="0 0 ${w} ${h}" role="img" aria-label="Whale rotation">${paths.join("")}<circle cx="${mid}" cy="120" r="22" fill="#0c1526" stroke="#3d8bff" stroke-width="2"/><text x="${mid}" y="124" text-anchor="middle" fill="#3d8bff" font-size="10" font-family="ui-monospace,monospace">${esc(who)}</text>${labels.join("")}</svg><p class="dim">Left is ADA that left with a token. Right is ADA that came back in. Width is the flow, not a balance.</p>`;
  }
  function flowMapHtml(log, liq) {
    const pools = (log && log.liquidity_flows && log.liquidity_flows.pools) || [];
    const moves = []
      .concat((log && log.liquidity_flows && log.liquidity_flows.big_moves) || [])
      .concat((liq && liq.moves) || []);
    if (!pools.length && !moves.length) return empty("No liquidity map yet", "Pool deltas and big moves are missing.");
    const max = Math.max(1, ...pools.map(p => Math.abs(num(p.delta_24h_ada) || 0)));
    const byTicker = {};
    moves.forEach(m => {
      const tk = tickerName(m.ticker);
      if (!tk) return;
      const dir = /out|sell|remove/.test(String(m.direction || m.kind || "")) ? "out" : "in";
      (byTicker[tk] = byTicker[tk] || { inn: 0, out: 0 });
      if (dir === "out") byTicker[tk].out += 1; else byTicker[tk].inn += 1;
    });
    const rows = (pools.length ? pools : Object.keys(byTicker).map(t => ({ ticker: t, delta_24h_ada: (byTicker[t].inn - byTicker[t].out) }))).slice(0, 12);
    return rows.map(p => {
      const tk = tickerName(p.ticker) || "–";
      const d = num(p.delta_24h_ada) || 0;
      const pct = Math.min(100, Math.round(Math.abs(d) / max * 100));
      const arrows = byTicker[tk.toUpperCase()] || byTicker[tk] || { inn: 0, out: 0 };
      const arrow = `<span class="flow-arrows ${d < 0 || arrows.out > arrows.inn ? "out" : "in"}">${[0, 1, 2].map(() => "<i></i>").join("")}</span>`;
      return `<div class="flow-row"><b class="mono">${esc(tk)}</b><div class="divtrack"><div class="neg" style="width:${d < 0 ? pct : 0}%"></div><div class="pos" style="width:${d > 0 ? pct : 0}%"></div></div><span class="dim">${arrow} ${esc((d > 0 ? "+" : "") + fmt(d, 0))}</span></div>`;
    }).join("") + `<p class="dim">Bars are the 24h ADA-side change. Arrows mark enter and exit prints. Tags stay w- hashes.</p>`;
  }
  function cardanoHtml(data) {
    whaleNames = whaleCodenames(data && data.log);
    const named = publicWhales(data && data.log);
    const linked = (data && data.whale && safeTag(data.whale.whale_tag)) || (named[0] && named[0].tag) || "";
    leadCodename = (linked && whaleNames[linked]) || (named[0] && named[0].codename) || "";
    const v = cardanoView(data);
    if (!root.CardanoBoard) return `<p class="muted">Cardano board isn't installed.</p>`;
    return root.CardanoBoard.render({
      esc, tx, num, fmt, sanitize, tickerName, safeTag, labelTag, animalName,
      displayKind, flaggedThisRun, riskBadge, riskTone, chip, tagChip, freshFunded,
      paperBoard, donut, when, timeOf, briefBits, flagTags, flagAda,
      coinLogo: assetLogo
    }, data || {}, v);
  }

  function nftRows(pulse, log) {
    const latest = (pulse && Array.isArray(pulse.latest)) ? pulse.latest : [];
    const bySlug = {};
    latest.forEach(r => { if (r && r.ticker) bySlug[String(r.ticker).toLowerCase()] = r; });
    const points = (pulse && Array.isArray(pulse.points)) ? pulse.points : [];
    const vol = {};
    points.forEach(p => {
      if (!p || !p.ticker) return;
      const k = String(p.ticker).toLowerCase();
      const prev = vol[k];
      if (!prev || String(p.t || "") > String(prev.t || "")) vol[k] = p;
    });
    let watch = [];
    const runs = log && Array.isArray(log.runs) ? log.runs : [];
    const lists = runs.filter(r => r && Array.isArray(r.watchlist) && r.watchlist.length);
    lists.sort((a, b) => String(b.asof || "").localeCompare(String(a.asof || "")));
    if (lists.length) watch = lists[0].watchlist;
    const rows = [];
    if (watch.length) {
      watch.forEach(w => {
        const slug = String(w.slug || w.name || "").toLowerCase();
        const live = bySlug[slug] || bySlug[String(w.name || "").toLowerCase()] || {};
        const pt = vol[slug] || {};
        const metrics = pt.metrics || {};
        rows.push({
          name: w.name || w.slug || live.ticker || "–",
          slug: w.slug || "",
          chain: (w.chain || live.chain || "multi").toLowerCase(),
          status: w.status || live.role || "",
          floor: live.floor != null ? live.floor : metrics.floor,
          unit: live.unit || metrics.floor_symbol || "",
          volume: metrics.volume_1h,
          volUnit: metrics.floor_symbol || live.unit || "",
          sales: live.sales_1h != null ? live.sales_1h : metrics.sales_1h,
          checked: w.last_checked || "",
          example: false
        });
      });
    } else {
      latest.forEach(r => {
        const pt = vol[String(r.ticker || "").toLowerCase()] || {};
        const metrics = pt.metrics || {};
        rows.push({
          name: r.ticker, slug: r.ticker, chain: (r.chain || "multi").toLowerCase(), status: r.role || "",
          floor: r.floor, unit: r.unit || "", volume: metrics.volume_1h, volUnit: metrics.floor_symbol || r.unit || "",
          sales: r.sales_1h, checked: pulse.updated_at || "", example: false
        });
      });
    }
    return rows;
  }
  function nftExample() {
    return [
      { name: "Example Penguins", chain: "ethereum", status: "watch", floor: 1.2, unit: "ETH", volume: 4.2, volUnit: "ETH", sales: 3, checked: "", example: true },
      { name: "Example Reef", chain: "solana", status: "pass", floor: 12.5, unit: "SOL", volume: 80, volUnit: "SOL", sales: 11, checked: "", example: true },
      { name: "Example Tide", chain: "cardano", status: "watch", floor: 40, unit: "ADA", volume: 900, volUnit: "ADA", sales: 6, checked: "", example: true }
    ];
  }
  function asUsd(value, unit) {
    const n = num(value);
    if (n == null) return null;
    const u = String(unit || "").toUpperCase();
    if (u === "USD" || u === "USDC" || u === "USDT") return n;
    return null;
  }
  function usdText(n) {
    if (n == null || !isFinite(n)) return "–";
    const sign = n < 0 ? "−" : "";
    const a = Math.abs(n);
    const body = a >= 1000 ? (a / 1000).toFixed(a >= 10000 ? 0 : 1).replace(/\.0$/, "") + "K" : (a >= 100 ? String(Math.round(a)) : a.toFixed(2).replace(/\.00$/, ""));
    return sign + "$" + body;
  }
  function nftHtml(pulse, log, chain, paper) {
    const live = nftRows(pulse, log).filter(r => !r.example);
    const selected = chain && chain !== "all" ? chain : "all";
    const shown = (selected === "all" ? live : live.filter(r => r.chain === selected)).slice(0, 10);
    const picks = ((paper && paper.open) || []).filter(t => t && !t.closed_at && String(t.status || "open").toLowerCase() !== "closed");
    const pick = picks[0];
    const pickHtml = pick
      ? `<article class="sl-pick"><div class="sl-pick-top"><span class="sl-kicker">KING's pick</span><span class="sl-tk">${tx(pick.ticker || pick.name) || "–"}</span><span class="sl-grow"></span><span class="sl-flat">${esc(usdText(asUsd(pick.size, "USD")))}</span></div><p class="sl-pick-line">${tx(Array.isArray(pick.thesis) ? pick.thesis[0] : pick.thesis) || "Open paper ticket."}</p><div class="sl-pick-foot"><span>Research pick, not advice</span></div></article>`
      : `<p class="sl-empty">No KING pick right now</p>`;
    const rows = shown.map((r, i) => {
      const floor = asUsd(r.floorUsd != null ? r.floorUsd : r.floor, r.unit);
      const vol = asUsd(r.volumeUsd != null ? r.volumeUsd : r.volume, r.volUnit || r.unit);
      const watch = /watch/i.test(r.status || "");
      const pill = r.status ? `<span class="sl-pill ${watch ? "sl-pill-watch" : "sl-pill-pass"}">${esc(sanitize(r.status))}</span>` : "";
      const sub = [sanitize(r.chain), floor == null ? "floor not in USD" : "floor " + usdText(floor), vol == null ? "" : "24h " + usdText(vol)].filter(Boolean).join(" · ");
      return `<div class="sl-row"><span class="sl-rank">${i + 1}</span>${assetLogo(r.slug || r.name, r.name, false)}<span class="sl-id"><span class="sl-id-top"><span class="sl-tk">${tx(r.name) || "–"}</span>${pill}</span><span class="sl-sub">${esc(sub)}</span></span><span class="sl-end"><span class="sl-flat">${esc(floor == null ? "–" : usdText(floor))}</span></span></div>`;
    }).join("");
    const whenAt = when((log && log.asof) || (pulse && pulse.updated_at));
    const status = live.length ? live.length + " names. Values in USD when the file is already in dollars." : "No KING tape yet.";
    return `<div class="stack sl-tab"><div class="sl-mascot"><p class="sl-status">${esc(status)}</p><time class="sl-meta">${esc(whenAt || "")}</time></div>
      <section class="sl-box"><header class="sl-box-h"><span class="sl-box-title">Watchlist <em>USD only</em></span></header>${pickHtml}<div class="sl-rows">${rows || `<p class="sl-empty">No names with a published reading.</p>`}</div></section>
      <p class="sl-note">USD only · native coin prices are left blank · research only</p></div>`;
  }

  function briefsHtml(items) {
    const list = (items || []).slice().sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    const body = list.length
      ? list.map(b => `<article class="sl-box"><header class="sl-box-h"><span class="sl-box-title">${esc(b.desk || "Desk")}</span><span class="sl-meta">${esc(when(b.date) || "")}</span></header><p class="sl-pick-line">${tx(b.headline) || "Untitled brief"}</p>${(b.learned || []).slice(0, 3).map(line => `<p class="sl-sub">${tx(line)}</p>`).join("")}</article>`).join("")
      : `<p class="sl-empty">No briefs yet.</p>`;
    return `<div class="stack sl-tab"><div class="sl-mascot"><p class="sl-status">${list.length ? list.length + " briefs" : "No briefs published yet."}</p></div>${body}<p class="sl-note">Research only · you trade elsewhere</p></div>`;
  }

  function homeHtml(pack) {
    const sol = pack.solanaModel;
    const nftCount = nftRows(pack.nftPulse, pack.nftLog).length;
    const briefs = pack.briefs || [];
    const card = (href, desk, title, text) => `<a class="sl-box sl-home-card" data-desk="${desk}" href="${href}"><header class="sl-box-h"><span class="sl-box-title">${esc(title)}</span></header><p class="sl-pick-line">${text}</p></a>`;
    const cards = `<div class="sl-home-grid">
      ${card("#/cardano", "solada", "Cardano", tx((pack.cardanoBrief && pack.cardanoBrief.headline) || "Runners and established names. Sentinel picks only."))}
      ${card("#/solana", "solana", "Solana", `Paper books F / R / S${sol ? " · " + sol.openCount + " open" : ""}.`)}
      ${card("#/nft", "nft", "NFT", nftCount ? nftCount + " names. USD only." : "No watchlist file yet.")}
    </div>`;
    const feed = briefs.slice(0, 3).map(b => `<article class="sl-box"><header class="sl-box-h"><span class="sl-box-title">${esc(b.desk || "Desk")}</span><span class="sl-meta">${esc(when(b.date) || "")}</span></header><p class="sl-pick-line">${tx(b.headline) || ""}</p></article>`).join("")
      || `<p class="sl-empty">No briefs yet.</p>`;
    return `<div class="stack sl-tab"><div class="sl-mascot"><p class="sl-status">Paper research across Cardano, Solana, and NFT.</p></div>${cards}<section class="sl-box"><header class="sl-box-h"><span class="sl-box-title">Latest briefs</span></header>${feed}</section><p class="sl-note">Research only · you trade elsewhere</p></div>`;
  }

  function routeOf(hash) {
    const h = String(hash || "#/").replace(/^#/, "") || "/";
    const path = h.replace(/^\//, "");
    if (!path || path === "home") return NAV[0];
    if (path === "cardano" || path === "solada" || path === "desk/solada" || path === "desk/cardano") return NAV[1];
    if (path === "solana" || path === "desk/solana") return NAV[2];
    if (path === "nft" || path === "desk/nft") return NAV[3];
    if (path === "briefs") return NAV[0];
    return NAV[0];
  }
  function navHtml(active) {
    return NAV.map(t => `<a href="${t.href}" data-d="${t.desk}" class="${t.id === active ? "on" : ""}">${esc(t.label)}</a>`).join("");
  }

  const store = { ready: false, nftChain: "all" };
  function previewPage() {
    return typeof document !== "undefined" && typeof document.querySelector === "function" && !!document.querySelector(".preview-tag");
  }
  async function loadPack() {
    const wantBuzz = !!(root.SightlineBuzz && previewPage());
    const [health, paper, positions, tradesText, log, pulse, sPaper, flags, vetoes, devs, whale, liq, big, kills, pump, sIdx, sMan, nPulse, nLog, nPaper, nIdx, nMan, xbuzz, buzzHistory, icons, prices, picks] = await Promise.all([
      get("solana-desk/health.json"),
      get("solana-desk/paper.json"),
      get("solana-desk/positions.json"),
      getText("solana-desk/trades.csv"),
      get("solada/log.json"),
      get("solada/pulse.json"),
      get("solada/paper.json"),
      get("solada-prefilter/flags.json"),
      get("solada-prefilter/vetoes.json"),
      get("solada-prefilter/dev_checks.json"),
      get("solada-prefilter/whale_ledger.json"),
      get("solada-prefilter/liquidity_moves.json"),
      get("solada-prefilter/big_moves.json"),
      get("solada-prefilter/kill_status.json"),
      get("solada/pumpfun/paper.json"),
      get("solada/briefs/index.json"),
      get("solada/briefs/manifest.json"),
      get("nft/pulse.json"),
      get("nft/log.json"),
      get("nft/paper.json"),
      get("nft/briefs/index.json"),
      get("nft/briefs/manifest.json"),
      get("x_buzz.json"),
      wantBuzz ? getDeskRootText("x_buzz_history.jsonl") : Promise.resolve(null),
      get("dashboard/icons/icons.json"),
      get("dashboard/prices.json"),
      get("dashboard/picks.json")
    ]);
    if (icons && icons.coins) ICONS = icons;
    function briefDates(idx, manifest) {
      const set = {};
      const add = n => {
        const s = String(n || "").replace(/\.json$/, "");
        if (/^\d{4}-\d{2}-\d{2}$/.test(s)) set[s] = 1;
      };
      (Array.isArray(idx) ? idx : []).forEach(add);
      ((manifest && manifest.dates) || []).forEach(add);
      return Object.keys(set).sort();
    }
    async function briefsIn(idx, manifest, dir, desk) {
      const names = briefDates(idx, manifest);
      if (!names.length) return [];
      const files = await Promise.all(names.map(name => get(dir + name + ".json")));
      return files.map(file => briefBits(file, desk)).filter(Boolean);
    }
    const [cBriefs, nBriefs] = await Promise.all([
      briefsIn(sIdx, sMan, "solada/briefs/", "Cardano"),
      briefsIn(nIdx, nMan, "nft/briefs/", "NFT")
    ]);
    const cBrief = cBriefs.length ? cBriefs[cBriefs.length - 1] : null;
    const nBrief = nBriefs.length ? nBriefs[nBriefs.length - 1] : null;
    const briefs = cBriefs.concat(nBriefs).sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    const cardano = { log, pulse, paper: sPaper, pump, flags, vetoes, devs, whale, liq, big, kills, buzz: xbuzz, brief: cBrief && { date: cBrief.date, headline: cBrief.headline, learned: cBrief.learned, scoreboard: cBrief.score } };
    return {
      health, paper, positions, trades: parseCsv(tradesText),
      cardano, cardanoView: cardanoView(cardano), cardanoBrief: cBrief,
      solanaModel: solanaModel(paper, parseCsv(tradesText), positions),
      nftPulse: nPulse, nftLog: nLog, nftPaper: nPaper, briefs, prices, picks, buzz: wantBuzz ? xbuzz : null, buzzHistory
    };
  }
  function freshest(pack, route) {
    const times = [];
    const push = t => {
      const ms = new Date(timeOf(t)).getTime();
      if (isFinite(ms) && ms > Date.UTC(2020, 0, 1)) times.push(ms);
    };
    if (!pack) return null;
    if (route === "solana" || route === "home") {
      const h = pack.health || {};
      push(h.generated_at || h.asof);
      const run = h.last_run_utc;
      if (run && /^\d{8}T\d{4}/.test(String(run))) {
        const m = String(run).match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/);
        if (m) push(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00Z`);
      }
      (h.run_times_utc || []).forEach(push);
      push(pack.paper && pack.paper.generated_at);
    }
    if (route === "cardano" || route === "home") {
      push(pack.cardano && pack.cardano.log && pack.cardano.log.asof);
      push(pack.cardano && pack.cardano.pulse && pack.cardano.pulse.updated_at);
      push(pack.cardano && pack.cardano.devs && pack.cardano.devs.at);
      push(pack.cardano && pack.cardano.liq && pack.cardano.liq.at);
      push(pack.cardano && pack.cardano.vetoes && pack.cardano.vetoes.at);
      push(pack.cardanoBrief && pack.cardanoBrief.date);
    }
    if (route === "nft" || route === "home") {
      push(pack.nftPulse && pack.nftPulse.updated_at);
      push(pack.nftLog && pack.nftLog.asof);
    }
    if (route === "briefs" || route === "home") (pack.briefs || []).forEach(b => push(b.date));
    push(pack.buzz && pack.buzz.asof);
    if (!times.length) return null;
    return new Date(Math.max.apply(null, times)).toISOString();
  }
  function statusFor(pack, route) {
    const h = (pack && pack.health) || {};
    if (route === "solana") {
      const ver = h.cfg_version || (pack && pack.paper && pack.paper.cfg_version) || "–";
      return { mode: h.mode || "paper", version: ver, frozen: typeof h.frozen === "boolean" ? h.frozen : null, fresh: freshest(pack, route), failure: num(h.data_failure_rate) };
    }
    if (route === "cardano") {
      const devs = pack && pack.cardano && pack.cardano.devs;
      const skipped = devs && devs.coverage && Array.isArray(devs.coverage.skipped) ? devs.coverage.skipped.length : null;
      const tracked = devs && devs.coverage && num(devs.coverage.tracked);
      const failure = skipped != null && tracked ? skipped / tracked : (pack && pack.cardano && pack.cardano.vetoes && Array.isArray(pack.cardano.vetoes.stale) && pack.cardano.vetoes.stale.length ? 1 : null);
      return { mode: "research", version: devs && devs.phase != null ? "phase " + devs.phase : "prefilter", frozen: null, fresh: freshest(pack, route), failure };
    }
    if (route === "nft") {
      const sources = pack && pack.nftPulse && pack.nftPulse.sources;
      let bad = 0, n = 0;
      if (sources && typeof sources === "object") {
        Object.keys(sources).forEach(k => {
          const block = sources[k];
          if (block && typeof block === "object") Object.keys(block).forEach(name => { n += 1; if (String(block[name]).toLowerCase() !== "ok") bad += 1; });
        });
      }
      return { mode: "paper", version: "KING", frozen: null, fresh: freshest(pack, route), failure: n ? bad / n : null };
    }
    if (route === "briefs") return { mode: "research", version: "–", frozen: null, fresh: freshest(pack, route), failure: null };
    return { mode: "paper", version: h.cfg_version || "–", frozen: typeof h.frozen === "boolean" ? h.frozen : null, fresh: freshest(pack, route), failure: num(h.data_failure_rate) };
  }
  function render(route) {
    const view = document.getElementById("view");
    const nav = document.getElementById("nav");
    const status = document.getElementById("status");
    if (!view) return;
    document.body.dataset.desk = route.desk;
    document.title = "Sightline · " + route.label;
    if (nav) nav.innerHTML = navHtml(route.id);
    if (!store.ready) {
      if (document.body.dataset.layout === "mono") return;
      if (status) status.innerHTML = statusPills({ mode: "…", version: "…", fresh: null, failure: null });
      view.innerHTML = skeleton();
      return;
    }
    const p = store.pack;
    if (document.body.dataset.layout === "mono" && (root.SightlineV19 || root.SightlineMono)) {
      const api = { assetLogo, nftRows, ago, sanitize, tx };
      const sub = document.getElementById("subt");
      if (sub) sub.textContent = ({ home: "Research desks · Cardano · Solana · NFT", cardano: "Cardano desk · runners and established", solana: "Solana paper desk · F / R / S books", nft: "KING · NFT watchlist in USD" })[route.id] || "";
      let html = "";
      try {
        html = p.picks && root.SightlineV19 ? root.SightlineV19.page(route.id, p.picks, api) : root.SightlineMono.page(route.id, p, api);
      }
      catch (err) { html = empty("This desk couldn't render", "This view hit a display error and kept the raw feed off the page."); }
      view.innerHTML = html;
      const warn = document.getElementById("warn");
      if (warn && p.picks && root.SightlineV19) warn.innerHTML = root.SightlineV19.warn(p.picks, route.id);
      if (status) status.innerHTML = p.picks ? freshnessHtml(p.picks) : "<span>picks file missing</span>";
      const visible = view.innerText || "";
      if (/(?:stake|addr)(?:_test)?1[0-9a-z]{8,}/i.test(visible)) {
        view.innerHTML = empty("Privacy hold", "This view was cleared because a wallet or stake key reached the page.");
      }
      document.title = "Sightline";
      return;
    }
    if (status) status.innerHTML = statusPills(statusFor(store.pack, route.id));
    let html = "";
    try {
      if (route.id === "home") html = homeHtml(p);
      else if (route.id === "cardano") html = cardanoHtml(p.cardano);
      else if (route.id === "solana") html = solanaHtml(p.solanaModel, p.health);
      else if (route.id === "nft") html = nftHtml(p.nftPulse, p.nftLog, store.nftChain, p.nftPaper);
      else html = briefsHtml(p.briefs);
    } catch (err) {
      html = empty("This tab couldn't render", "This view hit a display error and kept the raw feed off the page.");
    }
    if (root.SightlineBuzz && previewPage()) {
      try { html = root.SightlineBuzz.decorate(route.id, html, p && p.buzz, p && p.buzzHistory) || html; }
      catch (err) { /* desk html stays; a missing buzz file is an empty state inside decorate */ }
    }
    view.innerHTML = html;
    const visible = view.innerText || "";
    if (/(?:stake|addr)(?:_test)?1[0-9a-z]{8,}/i.test(visible)) {
      view.innerHTML = empty("Privacy hold", "This view was cleared because a wallet or stake key reached the page.");
      return;
    }
    animateNumbers(view);
    if (root.SightlineBuzz && previewPage()) {
      try { root.SightlineBuzz.mount(view, route.id, p && p.buzz, p && p.buzzHistory); }
      catch (err) { /* counts and the detail tap are optional on top of the desk */ }
    }
  }
  function boot() {
    const draw = () => render(routeOf(location.hash));
    window.addEventListener("hashchange", draw);
    document.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      const board = document.getElementById("ada-board");
      if (!board || !board.classList.contains("sl-open")) return;
      const shut = board.querySelector("[data-ada-close]");
      if (shut) shut.click();
    });
    document.addEventListener("click", e => {
      const th = e.target.closest && e.target.closest("th[data-sort]");
      if (th && th.closest("table")) {
        const tableEl = th.closest("table");
        const idx = [...th.parentNode.children].indexOf(th);
        const tbody = tableEl.tBodies && tableEl.tBodies[0];
        if (!tbody) return;
        const dir = th.getAttribute("data-dir") === "1" ? -1 : 1;
        th.setAttribute("data-dir", dir === 1 ? "1" : "");
        const value = el => {
          const node = el.querySelector && el.querySelector("[data-v]");
          const raw = node ? node.getAttribute("data-v") : el.innerText;
          const n = Number(raw);
          return raw !== "" && isFinite(n) ? n : (el.innerText || "");
        };
        [...tbody.rows].sort((a, b) => {
          const av = value(a.cells[idx]), bv = value(b.cells[idx]);
          const c = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
          return c * dir;
        }).forEach(row => tbody.appendChild(row));
        return;
      }
      const fbtn = e.target.closest && e.target.closest("[data-cv2-filter]");
      if (fbtn) {
        const chips = fbtn.parentElement;
        if (chips) chips.querySelectorAll("[data-cv2-filter]").forEach(b => b.classList.toggle("on", b === fbtn));
        const f = fbtn.getAttribute("data-cv2-filter") || "all";
        const board = document.getElementById("cv2-board");
        if (!board) return;
        let n = 0;
        board.querySelectorAll("[data-v]").forEach(card => {
          const show = f === "all" || card.getAttribute("data-v") === f;
          card.hidden = !show;
          if (show) n += 1;
        });
        const none = document.getElementById("cv2-none");
        if (none) none.hidden = n > 0;
        return;
      }
      const heatBtn = e.target.closest && e.target.closest("[data-cv2-heat]");
      if (heatBtn) {
        const wrap = document.getElementById("cv2-heat");
        if (!wrap) return;
        const on = wrap.classList.toggle("all");
        const n = heatBtn.getAttribute("data-n") || "";
        heatBtn.textContent = on ? "Show top 8 only" : ("Show all wallets (" + n + ")");
        return;
      }
      const adaOpen = e.target.closest && e.target.closest("[data-ada-open]");
      if (adaOpen) {
        const board = document.getElementById("ada-board");
        const tk = adaOpen.getAttribute("data-ada-open");
        if (!board || !tk) return;
        e.preventDefault();
        board.querySelectorAll("[data-ada-sheet]").forEach(el => {
          el.hidden = el.getAttribute("data-ada-sheet") !== tk;
        });
        const hint = document.getElementById("ada-hint");
        if (hint) hint.hidden = true;
        board.classList.add("sl-open");
        const scrim = board.querySelector(".ada-scrim");
        if (scrim) scrim.hidden = false;
        return;
      }
      const adaShut = e.target.closest && e.target.closest("[data-ada-close]");
      if (adaShut) {
        const board = document.getElementById("ada-board");
        if (!board) return;
        e.preventDefault();
        board.classList.remove("sl-open");
        const hint = document.getElementById("ada-hint");
        if (hint) hint.hidden = false;
        board.querySelectorAll("[data-ada-sheet]").forEach(el => { el.hidden = true; });
        const scrim = board.querySelector(".ada-scrim");
        if (scrim) scrim.hidden = true;
        return;
      }
      const btn = e.target.closest && e.target.closest("[data-chain-filter]");
      if (!btn) return;
      store.nftChain = btn.getAttribute("data-chain-filter") || "all";
      if (routeOf(location.hash).id === "nft") render(routeOf(location.hash));
    });
    draw();
    loadPack().then(pack => { store.pack = pack; store.ready = true; draw(); }).catch(() => {
      store.pack = { solanaModel: solanaModel(null, [], null), cardano: {}, cardanoView: cardanoView({}), briefs: [], health: null, nftPulse: null, nftLog: null, buzz: null, buzzHistory: null };
      store.ready = true;
      draw();
    });
  }

  root.Preview = {
    sanitize, safeTag, parseCsv, solanaModel, solanaHtml, day14Report, cardanoView, cardanoHtml, nftRows, nftHtml, freshFunded, isDeskData, applyIcons, assetLogo, whaleHtml: function (doc) { return cardanoHtml({ whale: doc }); },
    devHtml: function (doc) { return cardanoHtml({ devs: doc }); }
  };
  if (typeof document !== "undefined" && document.getElementById && document.addEventListener) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
  }
})(typeof window !== "undefined" ? window : globalThis);
