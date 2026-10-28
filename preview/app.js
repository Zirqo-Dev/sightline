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
    { id: "nft", href: "#/nft", label: "NFT", desk: "nft" },
    { id: "briefs", href: "#/briefs", label: "Briefs", desk: "briefs" }
  ];

  function isDeskData(path) {
    const p = String(path).replace(/^\/|^\.\//, "");
    if (/^(https?:)?\/\//.test(p) || !p.endsWith(".json")) return false;
    if (/(^|\/)forensics\/panel\.json$/.test(p)) return true;
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
    s = s.replace(/cockwhale/gi, " ");
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
    if (raw === "founder_whale_move") return (leadCodename || "Whale") + " move";
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
  function solanaModel(paper, trades, positions) {
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
    sells.forEach(t => {
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
      time: timeOf(t.time), book: t.book || "", symbol: t.symbol || "", action: t.action || "", reason: (t.why || "").replace(/_/g, " "), pnl: tradeNet(t)
    }));
    ((paper && paper.summaries) || []).forEach(s => activity.push({ time: s.at, book: "", symbol: "", action: s.type || "skip", reason: s.note || "", pnl: null }));
    activity.sort((a, b) => String(b.time || "").localeCompare(String(a.time || "")));
    return { net, gross, openCount: opens.length, winRate, netSeries, grossSeries, books, opens, activity: activity.slice(0, 24), closed: sells.length };
  }
  function solanaHtml(model) {
    const m = model || solanaModel(null, [], null);
    const signCls = n => n == null ? "" : n < 0 ? "down" : "up";
    const money = (n, d) => (n == null ? "–" : (n > 0 ? "+" : "") + fmt(n, d == null ? 4 : d));
    const head = tiles([
      tile("Net PnL", money(m.net, 4) + (m.net == null ? "" : " SOL"), { cls: signCls(m.net), count: m.net, digits: 4, sign: true, suffix: " SOL", series: m.netSeries, hint: "after haircut and fees" }),
      tile("Gross PnL", money(m.gross, 4) + (m.gross == null ? "" : " SOL"), { cls: signCls(m.gross), count: m.gross, digits: 4, sign: true, suffix: " SOL", series: m.grossSeries, hint: "price move, costs excluded" }),
      tile("Open positions", String(m.openCount), { count: m.openCount, digits: 0, hint: "paper tickets" }),
      tile("Win rate", m.winRate == null ? "–" : Math.round(m.winRate * 100) + "%", { count: m.winRate == null ? null : m.winRate * 100, digits: 0, suffix: "%", hint: m.closed ? m.closed + " closed fills" : "no closed fills" })
    ]);
    const books = `<div class="cards">${m.books.map(b => `<article class="card" data-desk="solana"><h3>${esc(b.id)} · ${esc(b.name)}</h3><p>Paper equity <b class="mono">${b.equity == null ? "–" : fmt(b.equity, 3) + " SOL"}</b></p><p>Realized <b class="${signCls(b.realized)}">${money(b.realized, 3)}</b> · open ${b.open == null ? "–" : b.open} · closed ${b.closed == null ? "–" : b.closed}</p>${spark(b.series)}</article>`).join("")}</div>`;
    const posRows = m.opens.map(p => ({
      token: `<b>${tx(p.ticker) || "–"}</b>`,
      book: esc(p.book || "–"),
      size: esc(p.size == null ? "–" : fmt(p.size, 2) + " SOL"),
      entry: esc(p.entry == null ? "–" : fmt(p.entry, 6)),
      mark: esc(p.mark == null ? "–" : fmt(p.mark, 6)),
      net: `<span class="${signCls(p.pnl)}">${esc(money(p.pnl, 4))}</span>`,
      reason: tx(p.reason) || "–"
    }));
    const actRows = m.activity.map(a => ({
      when: esc(when(a.time) || "–"),
      book: esc(a.book || "–"),
      token: tx(a.symbol) || "–",
      action: chip(a.action || "skip", /skip|void|halt/i.test(a.action) ? "caution" : ""),
      reason: tx(a.reason) || "–",
      net: a.pnl == null ? "–" : `<span class="${signCls(a.pnl)}">${esc(money(a.pnl, 4))}</span>`
    }));
    const pos = posRows.length ? table(
      [{ key: "token", label: "Token" }, { key: "book", label: "Book" }, { key: "size", label: "Paper size", num: true }, { key: "entry", label: "Entry", num: true }, { key: "mark", label: "Mark", num: true }, { key: "net", label: "Net", num: true }, { key: "reason", label: "Reason" }],
      posRows
    ) : empty("No open paper positions", "positions.json and paper.json have no open tickets.");
    const act = actRows.length ? table(
      [{ key: "when", label: "When" }, { key: "book", label: "Book" }, { key: "token", label: "Token" }, { key: "action", label: "Action" }, { key: "reason", label: "Reason" }, { key: "net", label: "Net", num: true }],
      actRows
    ) : empty("No trades or skips yet", "trades.csv is empty. Reasons show up as one line per fill or skip.");
    return `<div class="stack">${head}${panel("Net vs gross", "paper equity", lineChart(m.netSeries, m.grossSeries), "chart")}${panel("Books F / R / S", "0.5 SOL tickets", books, "layers")}${panel("Open positions", String(m.openCount), pos, "table")}${panel("Recent trades", "reason per fill", act, "feed")}</div>`;
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
  function suriTone(risk) {
    const s = String(risk || "").toLowerCase();
    if (!s || s === "none" || s === "null") return "s-none";
    if (/red|high|rug/.test(s)) return "s-high";
    if (/yellow|caution|med|mid/.test(s)) return "s-mid";
    if (/low|green|ok/.test(s)) return "s-low";
    return "s-none";
  }
  function suriList(log) {
    const suri = log && log.suri;
    if (!suri || typeof suri !== "object") return [];
    return Object.keys(suri).map(ticker => {
      const row = suri[ticker] || {};
      const tags = Array.isArray(row.tags) ? row.tags.map(t => sanitize(t)).filter(t => t && t.length <= 28) : [];
      return { ticker: sanitize(ticker), risk: sanitize(row.risk), tone: suriTone(row.risk), tags };
    }).filter(r => r.ticker);
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
  function suriBadge(row) {
    if (!row || !row.ticker) return "";
    const tip = [row.risk].concat(row.tags || []).filter(Boolean).join(" · ");
    return `<span class="suri ${row.tone}" title="${esc(tip)}">${esc(row.ticker)}</span>`;
  }
  const CODENAME_KEYS = ["codename", "code_name", "animal", "nickname", "alias", "public_name", "display_name"];
  function animalName(value) {
    const t = String(value || "").trim();
    if (!t || t.length > 40 || !/^[A-Za-z][A-Za-z' -]{1,40}$/.test(t)) return "";
    if (/founder|cockwhale|seller|buyer|cluster|sniper|dumper|launcher|\$|@/i.test(t)) return "";
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
    if (/founder|cockwhale/i.test(s)) return "";
    return s;
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
    const suri = suriList(d.log);
    const founder = publicFounder(d.log && d.log.founder_whale);
    return { flags, top, byTicker, vetoTokens, dumpMoves, counts, devs, suri, founder, brief: d.brief || null, whale: d.whale || null, liq: d.liq || null, big: d.big || null, log: d.log || null, pulse: d.pulse || null, kills: d.kills || null, pump: d.pump || null, paper: d.paper || null, vetoes: d.vetoes || null };
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
    const v = cardanoView(data);
    const linked = (v.whale && safeTag(v.whale.whale_tag)) || (v.founder && v.founder.tag) || "";
    leadCodename = (linked && whaleNames[linked]) || "";
    const brief = briefBits(v.brief, "Cardano");
    const suriBy = {};
    v.suri.forEach(s => { suriBy[s.ticker.toUpperCase()] = s; });
    const trigger = sanitize(v.log && v.log.trigger);
    const runAt = v.vetoes && v.vetoes.at;
    const stale = v.vetoes && Array.isArray(v.vetoes.stale) && v.vetoes.stale.length;
    const headChips = `<div class="head-chips">${trigger && trigger.length <= 32 ? chip("trigger " + trigger, "ok") : ""}${chip(runAt ? "last run " + (when(runAt) || "") : "no last run", stale ? "bad" : "ok")}${chip("lp " + (v.counts.lp_pulled || 0), "bad")}${chip("dump " + (v.counts.ahead_of_dump || 0), "caution")}</div>`;
    const briefBar = brief
      ? `<p class="slim-brief">${esc(when(brief.date) || "")} · ${tx(brief.headline)}</p>`
      : `<p class="slim-brief">No brief published yet.</p>`;
    const ordered = orderPoolRows(v.top, v.log, v.pulse);
    const maxDepth = Math.max(1, ...ordered.map(r => num(r.ada_side_depth) || 0));
    const depthBars = ordered.length ? `<div class="depth-list">${ordered.map(r => {
      const name = tickerName(r.ticker) || "–";
      const suri = suriBy[name.toUpperCase()];
      const tone = suri ? suri.tone : "s-none";
      const tip = suri && suri.risk ? "Suri: " + suri.risk : "No Suri score";
      const core = CORE_NAMES.indexOf(name.toUpperCase()) >= 0;
      const depth = num(r.ada_side_depth) || 0;
      const pct = depth ? Math.max(4, Math.round(depth / maxDepth * 100)) : 0;
      return `<div class="depth-row"><div class="depth-name"><i class="suri-dot ${tone}" title="${esc(tip)}"></i><span class="nm">${esc(name)}</span>${core ? '<span class="core-badge">Core</span>' : ""}</div><div class="depth-track"><i style="width:${pct}%"></i></div><span class="mono dim">${esc(depth ? fmt(depth, 0) : "–")}</span></div>`;
    }).join("")}</div>` : empty("No pool depths yet", "Bars appear when cardano_top10 or the pulse has an ADA side.");
    const outcomes = (v.log && v.log.flag_outcomes) || {};
    const kinds = Object.keys(outcomes);
    const donuts = kinds.length ? `<div class="donuts">${kinds.map(k => donut(num(outcomes[k] && outcomes[k].pump) || 0, num(outcomes[k] && outcomes[k].rug) || 0, displayKind(k))).join("")}</div>` : empty("No flag outcomes yet", "Pump and rug counts show up as rings once the desk scores them.");
    const sparks = ordered.slice(0, 6).map(r => {
      const name = tickerName(r.ticker);
      const series = depthSeries(v.log, v.pulse, name);
      const kill = killFor(v.kills, name);
      const overlay = kill && /ada|side|depth|pool/i.test(kill.metric || "") ? kill : null;
      const svg = killSpark(series, overlay);
      if (!svg) return "";
      return `<div class="killspark"><div class="mono">${esc(name)}${kill && kill.hit ? chip(" kill", "bad") : ""}</div>${svg}</div>`;
    }).filter(Boolean).join("");
    const sparkBlock = sparks || empty("No depth history yet", "Sparklines need two ADA-side readings from the pulse or the run history.");
    const gauges = v.kills ? Object.keys(v.kills).map(k => gauge(killFor(v.kills, k))).filter(Boolean).slice(0, 8).join("") : "";
    const gaugeBlock = gauges ? `<div class="gauges">${gauges}</div>` : empty("No kill gauges", "kill_status has no numeric lines yet.");
    const heat = heatHtml(heatModel(v.flags));
    const whaleTag = (v.whale && safeTag(v.whale.whale_tag)) || (v.founder && v.founder.tag) || "";
    const whaleName = labelTag(whaleTag) || "Whale";
    const whaleNote = v.founder
      ? `<p class="slim-brief"><b>${esc(whaleName)}</b> · net flow ${v.founder.net_flow_ada == null ? "–" : (v.founder.net_flow_ada > 0 ? "+" : "") + fmt(v.founder.net_flow_ada, 0) + " ADA"}</p>${v.founder.summary ? `<p class="slim-brief">${tx(v.founder.summary)}</p>` : ""}${v.founder.line ? `<p class="slim-brief">${tx(v.founder.line)}</p>` : ""}`
      : "";
    const sankey = sankeyHtml(sankeyFlows(v.whale, v.big, v.log));
    const flow = flowMapHtml(v.log, v.liq);
    const devCards = v.devs.length ? `<div class="dev-cards">${v.devs.slice(0, 16).map(e => {
      const name = tickerName(e.ticker) || "–";
      const tone = suriTone(e.risk);
      return `<article class="dev-card"><b class="mono">${esc(name)}</b><span class="suri ${tone}">${esc(sanitize(e.risk) || "unscored")}</span> ${freshFunded(e) ? chip("fresh funded", "caution") : ""}${tagChip(e.dev_tag) || ""}<div class="dim">${esc(e.serial_3h == null && e.serial_7d == null ? "" : "serial " + (e.serial_3h ?? "–") + " / " + (e.serial_7d ?? "–"))}</div></article>`;
    }).join("")}</div>` : empty("Dev checks not published", "dev_checks.json is missing.");
    const books = [paperBoard(v.paper), paperBoard(v.pump)];
    const bookHtml = `<div class="donuts">${donut(books[0].wins, books[0].losses, "Solada " + (books[0].unit || "ADA"))}${donut(books[1].wins, books[1].losses, "Pump " + (books[1].unit || ""))}</div><p class="dim">Open ${books[0].open + books[1].open} · closed ${books[0].closed + books[1].closed} · paper pnl ${fmt(books[0].pnl, 1)} ${esc(books[0].unit)} and ${fmt(books[1].pnl, 1)} ${esc(books[1].unit)}</p>`;
    const lessons = []
      .concat((v.log && v.log.learned_or_changed) || [])
      .concat((v.log && v.log.patterns) || [])
      .concat(((v.log && v.log.followups) || []).slice(0, 4).map(f => ({ pattern: typeof f === "string" ? f : (f && (f.text || f.note || "")) })));
    const lessonHtml = lessons.length ? lessons.slice(0, 6).map(item => {
      if (typeof item === "string") return feedItem(chip("note", ""), item, []);
      const title = item.pattern || item.rule || item.text || "";
      const bits = [item.outcome, item.evidence].map(x => typeof x === "string" ? x : "").filter(Boolean);
      return feedItem(chip(item.outcome ? "pattern" : "follow-up", ""), title, bits);
    }).join("") : empty("No lessons yet", "Patterns, learned notes, and follow-ups land here.");
    const recentFlags = v.flags.slice(0, 16).map(f => {
      const tags = flagTags(f).slice(0, 2).map(tagChip).join("");
      return `<span class="chip ${/exit|rug|dump/i.test(f.kind || "") ? "bad" : ""}">${esc(displayKind(f.kind))}${f.ticker ? " · " + esc(tickerName(f.ticker)) : ""}</span>${tags}`;
    }).join(" ");
    const flagged = flaggedThisRun(v.log, data && data.flags, v.kills);
    const flagRow = `<div class="flag-legend"><b>Flagged this run</b><span class="chip bad">rug or kill-line</span><span class="chip caution">coordinated launch</span></div><div class="filters">${flagged.length ? flagged.map(f => `<span class="chip ${f.tone === "red" ? "bad" : "caution"}">${esc(f.ticker)}</span>`).join("") : `<span class="dim">None on the latest run.</span>`}</div>`;
    const suriLegend = `<div class="suri-legend"><b>Suri risk</b><span class="suri s-low">green · lower</span><span class="suri s-mid">amber · caution</span><span class="suri s-high">red · high</span></div>`;
    const topRows = ordered.map(r => {
      const name = tickerName(r.ticker) || "–";
      const suri = suriBy[name.toUpperCase()];
      const depth = num(r.ada_side_depth);
      return {
        rank: `<span data-v="${esc(r.rank ?? "")}">${esc(r.rank ?? "")}</span>`,
        ticker: `<span class="depth-name"><i class="suri-dot ${suri ? suri.tone : "s-none"}"></i><b>${esc(name)}</b>${CORE_NAMES.indexOf(name.toUpperCase()) >= 0 ? '<span class="core-badge">Core</span>' : ""}</span>`,
        depth: `<span data-v="${depth == null ? "" : depth}">${esc(depth == null ? "–" : fmt(depth, 0))}</span>`,
        holders: `<span data-v="${num(r.holders) == null ? "" : num(r.holders)}">${esc(num(r.holders) == null ? "–" : fmt(r.holders, 0))}</span>`,
        delta: `<span data-v="${num(r.holders_delta) == null ? "" : num(r.holders_delta)}">${esc(num(r.holders_delta) == null ? "–" : fmt(r.holders_delta, 0))}</span>`
      };
    });
    const topTable = topRows.length ? table(
      [{ key: "rank", label: "#" }, { key: "ticker", label: "Ticker" }, { key: "depth", label: "ADA side", num: true }, { key: "holders", label: "Holders", num: true }, { key: "delta", label: "Holder Δ", num: true }],
      topRows
    ) : "";
    const sortable = topTable
      .replace(">#</th>", ' data-sort="rank">#</th>')
      .replace(">Ticker</th>", ' data-sort="t">Ticker</th>')
      .replace(">ADA side</th>", ' data-sort="d">ADA side</th>')
      .replace(">Holders</th>", ' data-sort="h">Holders</th>');
    return `<div class="stack">
      <div class="pagehead"><div><h1>Cardano</h1><div class="sub">Visuals first · paper research · w- tags only</div>${briefBar}</div>${headChips}</div>
      <div class="viz-grid">${panel("Pool ADA (one side)", "", flagRow + suriLegend + depthBars, "chart", "ADA in the token's deepest pool (Minswap); total TVL is ~2x.")}${panel("Flag hit rate", "pump vs rug", donuts, "flag")}</div>
      ${panel("Pool depth", "dashed kill line", `<div class="killsparks">${sparkBlock}</div>${gaugeBlock}`, "chart")}
      ${panel("Cluster heat", "early buyers", (recentFlags ? `<div class="filters">${recentFlags}</div>` : "") + heat, "flag")}
      <div class="viz-grid">${panel(whaleName, /^w-[0-9a-f]{8}$/.test(whaleTag) && whaleName !== whaleTag ? whaleTag : "", whaleNote + sankey, "chart")}${panel("Liquidity map", "enter / exit", flow, "chart")}</div>
      ${panel("Dev risk", "fresh funded", devCards, "flag")}
      ${panel("Paper books", "not orders", bookHtml, "layers")}
      ${panel("Lessons", "follow-ups", lessonHtml, "feed")}
      <details class="fold"><summary>Tables</summary>${sortable || empty("No top 10 table", "")}<div class="filters">${chip("lp_pulled " + (v.counts.lp_pulled || 0), "bad")}${chip("ahead_of_dump " + (v.counts.ahead_of_dump || 0), "bad")}${chip("dev_sold " + (v.counts.dev_sold || 0), "caution")}${chip("serial_launcher " + (v.counts.serial_launcher || 0), "caution")}</div></details>
    </div>`;
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
          name: r.ticker, chain: (r.chain || "multi").toLowerCase(), status: r.role || "",
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
  function nftHtml(pulse, log, chain) {
    const live = nftRows(pulse, log);
    const missing = !live.length;
    const rows = missing ? nftExample() : live;
    const selected = chain && chain !== "all" ? chain : "all";
    const shown = selected === "all" ? rows : rows.filter(r => r.chain === selected);
    const chains = Array.from(new Set(rows.map(r => r.chain))).filter(Boolean);
    const sales = shown.reduce((s, r) => s + (num(r.sales) || 0), 0);
    const floors = shown.filter(r => num(r.floor) != null).length;
    const units = Array.from(new Set(shown.map(r => r.volUnit).filter(Boolean)));
    const volSum = units.length === 1 ? shown.reduce((s, r) => s + (num(r.volume) || 0), 0) : null;
    const salesSeries = [];
    if (pulse && Array.isArray(pulse.points)) {
      const bucket = {};
      pulse.points.forEach(p => {
        if (!p || !p.t) return;
        if (selected !== "all" && p.chain !== selected) return;
        const hour = String(p.t).slice(0, 13);
        bucket[hour] = (bucket[hour] || 0) + (num(p.metrics && p.metrics.sales_1h) || 0);
      });
      Object.keys(bucket).sort().forEach(k => salesSeries.push(bucket[k]));
    }
    const filters = `<div class="filters"><button type="button" class="chip ${selected === "all" ? "on" : ""}" data-chain-filter="all">all</button>${chains.map(c => `<button type="button" class="chip ${selected === c ? "on" : ""}" data-chain="${esc(c)}" data-chain-filter="${esc(c)}">${esc(c)}</button>`).join("")}</div>`;
    const head = tiles([
      tile("Collections", String(shown.length), { count: shown.length, digits: 0, series: salesSeries, hint: missing ? "example layout" : "KING watch" }),
      tile("Chains", String(selected === "all" ? chains.length : 1), { count: selected === "all" ? chains.length : 1, digits: 0, hint: selected === "all" ? "filter below" : selected }),
      tile("Floors quoted", String(floors), { count: floors, digits: 0, hint: "names with a floor" }),
      tile("1h volume", volSum == null ? "mixed" : fmt(volSum, 1) + " " + (units[0] || ""), { count: volSum, digits: 1, suffix: volSum == null ? "" : " " + (units[0] || ""), hint: volSum == null ? "units differ by chain" : "one unit" })
    ]);
    const bodyRows = shown.map(r => ({
      name: `<b>${tx(r.name) || "–"}</b>`,
      chain: `<span class="chip" data-chain="${esc(sanitize(r.chain))}">${tx(r.chain)}</span>`,
      status: r.status ? chip(r.status, r.status === "watch" ? "caution" : r.status === "pass" ? "" : "ok") : "–",
      floor: esc(num(r.floor) == null ? "–" : fmt(r.floor, 3) + (r.unit ? " " + sanitize(r.unit) : "")),
      volume: esc(num(r.volume) == null ? "–" : fmt(r.volume, 2) + (r.volUnit ? " " + sanitize(r.volUnit) : "")),
      sales: esc(num(r.sales) == null ? "–" : fmt(r.sales, 0)),
      checked: esc(when(r.checked) || "–")
    }));
    const tableHtml = bodyRows.length ? table(
      [{ key: "name", label: "Collection" }, { key: "chain", label: "Chain" }, { key: "status", label: "Status" }, { key: "floor", label: "Floor", num: true }, { key: "volume", label: "1h volume", num: true }, { key: "sales", label: "1h sales", num: true }, { key: "checked", label: "Last checked" }],
      bodyRows
    ) : empty("No names on this chain", "Pick another chain, or wait for the next KING pulse.");
    const note = missing
      ? `<p class="example-note">Example data · not a live reading. nft/pulse.json and the watchlist were missing.</p>`
      : "";
    const intro = feedItem(
      `${chip("KING", "ok")}${chip("coming soon", "caution")}`,
      missing ? "Multichain NFT desk" : sanitize((log && log.headline) || "KING tape"),
      missing ? ["Placeholder layout for collections, floors, and per-chain filters.", "The desk is run by KING. Real files replace this example when they exist."] : ["Paper and public market stats only.", "Chain chips filter the watchlist. Volume is summed only when the unit matches."]
    );
    const emptyLive = missing ? empty("No NFT data file", "Run data from the NFT desk will fill this table. The example below is labeled.") : "";
    return `<div class="stack">${panel("NFT desk", "KING", intro + emptyLive, "image")}${head}${panel("Collections", missing ? "example" : "watchlist", note + filters + tableHtml, "table")}</div>`;
  }

  function briefsHtml(items) {
    const list = (items || []).slice().sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    const head = tiles([
      tile("Briefs", String(list.length), { count: list.length, digits: 0 }),
      tile("Desks", String(new Set(list.map(b => b.desk)).size), { digits: 0 }),
      tile("Latest", list[0] ? when(list[0].date) : "–", { hint: "newest file" }),
      tile("Paper books", "research", { hint: "not orders" })
    ]);
    const body = list.length
      ? list.map(b => feedItem(`${chip(b.desk || "desk", "")}<span>${esc(when(b.date) || "")}</span>`, b.headline, b.learned)).join("")
      : empty("No briefs yet", "solada/briefs and nft/briefs are empty.");
    return `<div class="stack">${head}${panel("Archive", "newest first", body, "book")}</div>`;
  }

  function homeHtml(pack) {
    const sol = pack.solanaModel;
    const ada = pack.cardanoView || { vetoTokens: [] };
    const nftCount = nftRows(pack.nftPulse, pack.nftLog).length;
    const briefs = pack.briefs || [];
    const head = tiles([
      tile("Desks", "3", { count: 3, digits: 0, hint: "Cardano · Solana · NFT" }),
      tile("Solana net", sol && sol.net != null ? (sol.net > 0 ? "+" : "") + fmt(sol.net, 3) + " SOL" : "–", { cls: sol && sol.net < 0 ? "down" : "up", count: sol && sol.net, digits: 3, sign: true, suffix: " SOL", series: sol && sol.netSeries, hint: "paper, after costs" }),
      tile("Cardano vetoes", String(ada ? ada.vetoTokens.length : 0), { count: ada ? ada.vetoTokens.length : 0, digits: 0, hint: "research only" }),
      tile("NFT names", String(nftCount), { count: nftCount, digits: 0, hint: nftCount ? "KING watch" : "no file yet" })
    ]);
    const cards = `<div class="cards">
      <a class="card" data-desk="solada" href="#/cardano"><h3>Cardano</h3><p>${tx((pack.cardanoBrief && pack.cardanoBrief.headline) || "Charts first. Pool ADA and the heat grid.")}</p>${(ada.suri || []).length ? `<div class="suri-legend"><b>Suri risk</b>${(ada.suri || []).slice(0, 6).map(suriBadge).join("")}</div>` : ""}<span class="go">Open tab</span></a>
      <a class="card" data-desk="solana" href="#/solana"><h3>Solana</h3><p>Calm terminal · F / R / S paper books${sol ? ` · ${sol.openCount} open` : ""}.</p><span class="go">Open tab</span></a>
      <a class="card" data-desk="nft" href="#/nft"><h3>NFT</h3><p>KING · multichain · coming soon. ${nftCount ? nftCount + " names on the watchlist." : "Placeholder until a pulse file lands."}</p><span class="go">Open tab</span></a>
    </div>`;
    const feed = briefs.slice(0, 3).map(b => feedItem(`${chip(b.desk || "", "")}<span>${esc(when(b.date) || "")}</span>`, b.headline, (b.learned || []).slice(0, 2))).join("")
      || empty("No briefs yet", "Daily notes from Cardano and NFT show up here.");
    return `<div class="stack"><div class="pagehead"><div><h1>All desks</h1><div class="sub">Preview · paper books · one accent per desk</div></div></div>${head}${cards}${panel("Latest briefs", "feed", feed, "book")}</div>`;
  }

  function routeOf(hash) {
    const h = String(hash || "#/").replace(/^#/, "") || "/";
    const path = h.replace(/^\//, "");
    if (!path || path === "home") return NAV[0];
    if (path === "cardano" || path === "solada" || path === "desk/solada" || path === "desk/cardano") return NAV[1];
    if (path === "solana" || path === "desk/solana") return NAV[2];
    if (path === "nft" || path === "desk/nft") return NAV[3];
    if (path === "briefs") return NAV[4];
    return NAV[0];
  }
  function navHtml(active) {
    return NAV.map(t => `<a href="${t.href}" data-d="${t.desk}" class="${t.id === active ? "on" : ""}">${esc(t.label)}</a>`).join("");
  }

  const store = { ready: false, nftChain: "all" };
  async function loadPack() {
    const [health, paper, positions, tradesText, log, pulse, sPaper, flags, vetoes, devs, whale, liq, big, kills, pump, sIdx, nPulse, nLog, nIdx] = await Promise.all([
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
      get("nft/pulse.json"),
      get("nft/log.json"),
      get("nft/briefs/index.json")
    ]);
    async function latestBrief(idx, dir, desk) {
      if (!Array.isArray(idx) || !idx.length) return null;
      const names = idx.map(n => String(n).endsWith(".json") ? String(n) : String(n) + ".json").sort();
      const file = await get(dir + names[names.length - 1]);
      return briefBits(file, desk);
    }
    const [cBrief, nBrief] = await Promise.all([
      latestBrief(sIdx, "solada/briefs/", "Cardano"),
      latestBrief(nIdx, "nft/briefs/", "NFT")
    ]);
    const briefs = [cBrief, nBrief].filter(Boolean);
    const cardano = { log, pulse, paper: sPaper, pump, flags, vetoes, devs, whale, liq, big, kills, brief: cBrief && { date: cBrief.date, headline: cBrief.headline, learned: cBrief.learned, scoreboard: cBrief.score } };
    return {
      health, paper, positions, trades: parseCsv(tradesText),
      cardano, cardanoView: cardanoView(cardano), cardanoBrief: cBrief,
      solanaModel: solanaModel(paper, parseCsv(tradesText), positions),
      nftPulse: nPulse, nftLog: nLog, briefs
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
      if (status) status.innerHTML = statusPills({ mode: "…", version: "…", fresh: null, failure: null });
      view.innerHTML = skeleton();
      return;
    }
    if (status) status.innerHTML = statusPills(statusFor(store.pack, route.id));
    const p = store.pack;
    let html = "";
    try {
      if (route.id === "home") html = homeHtml(p);
      else if (route.id === "cardano") html = cardanoHtml(p.cardano);
      else if (route.id === "solana") html = `<div class="pagehead"><div><h1>Solana</h1><div class="sub">Calm terminal · paper fills only · F / R / S</div></div></div>` + solanaHtml(p.solanaModel);
      else if (route.id === "nft") html = `<div class="pagehead"><div><h1>NFT</h1><div class="sub">KING · multichain · coming soon</div></div></div>` + nftHtml(p.nftPulse, p.nftLog, store.nftChain);
      else html = `<div class="pagehead"><div><h1>Briefs</h1><div class="sub">Cardano and NFT daily notes</div></div></div>` + briefsHtml(p.briefs);
    } catch (err) {
      html = empty("This tab couldn't render", "The preview hit a display error and kept the raw feed off the page.");
    }
    view.innerHTML = html;
    const visible = view.innerText || "";
    if (/(?:stake|addr)(?:_test)?1[0-9a-z]{8,}/i.test(visible)) {
      view.innerHTML = empty("Privacy hold", "This view was cleared because a wallet or stake key reached the page.");
      return;
    }
    animateNumbers(view);
  }
  function boot() {
    const draw = () => render(routeOf(location.hash));
    window.addEventListener("hashchange", draw);
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
      const btn = e.target.closest && e.target.closest("[data-chain-filter]");
      if (!btn) return;
      store.nftChain = btn.getAttribute("data-chain-filter") || "all";
      if (routeOf(location.hash).id === "nft") render(routeOf(location.hash));
    });
    draw();
    loadPack().then(pack => { store.pack = pack; store.ready = true; draw(); }).catch(() => {
      store.pack = { solanaModel: solanaModel(null, [], null), cardano: {}, cardanoView: cardanoView({}), briefs: [], health: null, nftPulse: null, nftLog: null };
      store.ready = true;
      draw();
    });
  }

  root.Preview = {
    sanitize, safeTag, parseCsv, solanaModel, solanaHtml, cardanoView, cardanoHtml, nftRows, nftHtml, freshFunded, isDeskData, whaleHtml: function (doc) { return cardanoHtml({ whale: doc }); },
    devHtml: function (doc) { return cardanoHtml({ devs: doc }); }
  };
  if (typeof document !== "undefined" && document.getElementById && document.addEventListener) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
  }
})(typeof window !== "undefined" ? window : globalThis);
