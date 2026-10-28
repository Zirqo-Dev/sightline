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

  function sanitize(value) {
    return String(value ?? "").replace(ADDRESS, "").replace(/[ \t]{2,}/g, " ").replace(/\s+([,.;])/g, "$1").trim();
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
  function chip(text, cls, extra) {
    const s = sanitize(text).replace(/_/g, " ");
    if (!s) return "";
    return `<span class="chip ${cls || ""}"${extra || ""}>${esc(s)}</span>`;
  }
  function tagChip(value) {
    const s = safeTag(value);
    return s ? `<span class="chip tag">${esc(s)}</span>` : "";
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
  function panel(title, tag, body, ico) {
    return `<section class="panel"><h3><span class="ttl">${icon(ico || "layers")}${esc(title)}</span><span class="tag">${esc(tag || "")}</span></h3>${body}</section>`;
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
    const counts = { lp_pulled: 0, ahead_of_dump: dumpMoves.length, dev_sold: 0, serial_launcher: 0 };
    vetoTokens.forEach(t => (t.vetoes || []).forEach(v => { if (counts[v.code] != null && v.code !== "ahead_of_dump") counts[v.code] += 1; }));
    const devs = (d.devs && Array.isArray(d.devs.entries)) ? d.devs.entries : [];
    return { flags, top, byTicker, vetoTokens, dumpMoves, counts, devs, brief: d.brief || null, whale: d.whale || null, liq: d.liq || null, big: d.big || null };
  }
  function briefBits(b, desk) {
    if (!b) return null;
    const learned = [].concat(b.learned || [], b.highlights || [], b.rules_learned || []).map(x => (typeof x === "string" ? x : x && (x.text || x.rule || ""))).filter(Boolean);
    return { date: b.date, desk, headline: b.headline || "", learned, score: b.scoreboard || null };
  }
  function cardanoHtml(data) {
    const v = cardanoView(data);
    const brief = briefBits(v.brief, "Cardano");
    const briefHtml = brief
      ? panel("Today's brief", brief.date || "solada", feedItem(`${chip("Cardano", "ok")}<span>${esc(when(brief.date) || brief.date || "")}</span>`, brief.headline, brief.learned), "feed")
      : panel("Today's brief", "solada", empty("No brief published yet", "solada/briefs has no dated file on desk-data or main."), "feed");
    const head = tiles([
      tile("Hard vetoes", String(v.vetoTokens.length), { count: v.vetoTokens.length, digits: 0, hint: "research flags, not orders" }),
      tile("Ahead of dump", String(v.counts.ahead_of_dump), { count: v.counts.ahead_of_dump, digits: 0, hint: "lp pull then sell" }),
      tile("Dev cautions", String(v.devs.filter(e => e && (e.risk === "caution" || e.risk === "high" || freshFunded(e))).length), { digits: 0, hint: "includes fresh funded" }),
      tile("Snek top 10", String(v.top.length), { count: v.top.length, digits: 0, hint: v.top.length ? "latest board" : "not published" })
    ]);
    const topRows = v.top.map(r => {
      const tk = String(r.ticker || "").toUpperCase();
      const kinds = Array.from(new Set(v.byTicker[tk] || [])).slice(0, 4);
      const pol = /^[0-9a-f]{6,12}$/.test(String(r.policy_short || "")) ? r.policy_short : "";
      return {
        rank: esc(r.rank ?? ""),
        ticker: `<b>${tx(r.ticker) || "–"}</b>${pol ? ` <span class="dim mono">${esc(pol)}</span>` : ""}`,
        level: r.level != null && r.level !== "" ? chip(String(r.level), "") : "–",
        flags: kinds.length ? kinds.map(k => chip(k, /rug|dump|pull|exit/i.test(k) ? "bad" : "")).join("") : "–",
        ada: esc(r.ada_side_depth == null ? "–" : fmt(r.ada_side_depth, 0)),
        holders: esc(r.holders == null ? "–" : fmt(r.holders, 0))
      };
    });
    const topHtml = topRows.length ? table(
      [{ key: "rank", label: "#" }, { key: "ticker", label: "Ticker" }, { key: "level", label: "Lvl" }, { key: "flags", label: "Flags" }, { key: "ada", label: "ADA side", num: true }, { key: "holders", label: "Holders", num: true }],
      topRows
    ) : empty("Top 10 not published yet", "Waiting for cardano_top10, a liquidity token list, or the Cardano pulse.");
    const vetoRows = [];
    v.vetoTokens.forEach(t => (t.vetoes || []).forEach(code => {
      const c = code && code.code;
      const ev = (code && code.evidence) || {};
      vetoRows.push({
        ticker: tx(t.ticker) || "–",
        code: chip(c || "veto", c === "lp_pulled" ? "bad" : "caution"),
        count: "1",
        reason: `${esc(VETO_REASON[c] || "Research veto")} ${tagChip(ev.dev_tag) || tagChip(ev.actor_tag) || ""}`.trim()
      });
    }));
    v.dumpMoves.forEach(m => vetoRows.push({
      ticker: tx(m.ticker) || "–",
      code: chip("ahead_of_dump", "bad"),
      count: "1",
      reason: `${esc(VETO_REASON.ahead_of_dump)} ${tagChip(m.actor_tag)}`.trim()
    }));
    const countLine = `<div class="filters">${chip("lp_pulled " + v.counts.lp_pulled, "bad")}${chip("ahead_of_dump " + v.counts.ahead_of_dump, "bad")}${chip("dev_sold " + v.counts.dev_sold, "caution")}${chip("serial_launcher " + v.counts.serial_launcher, "caution")}</div>`;
    const vetoHtml = vetoRows.length ? countLine + table(
      [{ key: "ticker", label: "Ticker" }, { key: "code", label: "Code" }, { key: "count", label: "Count", num: true }, { key: "reason", label: "Reason" }],
      vetoRows.slice(0, 40)
    ) : countLine + empty("No hard vetoes", "vetoes.json and ahead-of-dump marks are empty.");
    const devRows = v.devs.map(e => ({
      ticker: tx(e.ticker) || "–",
      risk: e.risk ? chip(e.risk, e.risk === "high" ? "bad" : e.risk === "low" ? "ok" : "caution") : "–",
      tag: tagChip(e.dev_tag) || "–",
      fresh: freshFunded(e) ? chip("fresh funded", "caution") : "–",
      serial: esc([e.serial_3h, e.serial_7d].every(x => x == null) ? "–" : `${e.serial_3h ?? "–"} / ${e.serial_7d ?? "–"}`),
      sold: esc(e.dev_sold_ada == null ? "–" : fmt(e.dev_sold_ada, 0))
    }));
    const devHtml = devRows.length ? table(
      [{ key: "ticker", label: "Ticker" }, { key: "risk", label: "Risk" }, { key: "tag", label: "Dev" }, { key: "fresh", label: "Fresh funded" }, { key: "serial", label: "3h / 7d" }, { key: "sold", label: "Sold ADA", num: true }],
      devRows.slice(0, 40)
    ) : empty("Dev checks not published", "solada-prefilter/dev_checks.json is missing.");
    const whaleRows = ((v.whale && v.whale.rows) || []).slice(-30).reverse().map(r => {
      const token = String(r.token || "");
      const name = token && token.length <= 24 && !/(?:stake|addr)(?:_test)?1|0x[0-9a-f]{8,}/i.test(token) ? token : "";
      const flow = num(r.ada_leg);
      return {
        when: esc(when(r.time) || "–"),
        kind: chip(r.kind || "move", ""),
        token: tx(name) || "–",
        flow: flow == null ? "–" : esc((flow > 0 ? "+" : "") + fmt(flow, 0) + " ADA"),
        tag: tagChip(r.counterparty_tag) || tagChip(v.whale && v.whale.whale_tag) || "–"
      };
    });
    const whaleHtml = whaleRows.length ? table(
      [{ key: "when", label: "When" }, { key: "kind", label: "Kind" }, { key: "token", label: "Token" }, { key: "flow", label: "ADA flow", num: true }, { key: "tag", label: "Tag" }],
      whaleRows
    ) : empty("Whale ledger not published", "Moves show as role labels or w- hashes. Balances stay off this page.");
    const pools = {};
    ((v.liq && v.liq.pools) || []).forEach(p => { if (p && p.ticker) pools[String(p.ticker).toUpperCase()] = p.asof || p.last_checked; });
    const moves = ((v.liq && v.liq.moves) || []).slice().reverse();
    const moveRows = moves.slice(0, 40).map(m => {
      const checked = m.last_checked || m.asof || pools[String(m.ticker || "").toUpperCase()] || (v.liq && v.liq.at);
      return {
        when: esc(when(m.time) || "–"),
        ticker: tx(m.ticker) || "–",
        kind: chip(m.kind || "move", m.ahead_of_dump ? "bad" : ""),
        dex: tx(m.dex) || "–",
        ada: esc(m.ada == null ? "–" : fmt(m.ada, 0)),
        checked: esc(when(checked) || "–"),
        tag: tagChip(m.actor_tag) || "–"
      };
    });
    const moveHtml = moveRows.length ? table(
      [{ key: "when", label: "When" }, { key: "ticker", label: "Ticker" }, { key: "kind", label: "Kind" }, { key: "dex", label: "DEX" }, { key: "ada", label: "ADA", num: true }, { key: "checked", label: "Last checked" }, { key: "tag", label: "Tag" }],
      moveRows
    ) : empty("No liquidity moves", "solada-prefilter/liquidity_moves.json is missing.");
    const swaps = [];
    moves.filter(m => m && /swap/i.test(m.kind || "")).forEach(m => swaps.push({ time: m.time, ticker: m.ticker, direction: m.direction || m.kind, ada: m.ada, tag: m.actor_tag }));
    ((v.big && v.big.moves) || []).forEach(m => swaps.push({ time: m.time, ticker: m.ticker, direction: m.direction, ada: m.ada, tag: m.tag }));
    swaps.sort((a, b) => (num(b.ada) || 0) - (num(a.ada) || 0));
    const swapRows = swaps.slice(0, 20).map(m => ({
      when: esc(when(m.time) || "–"),
      ticker: tx(m.ticker) || "–",
      dir: chip(m.direction || "swap", /out|sell/i.test(m.direction || "") ? "bad" : "ok"),
      ada: esc(m.ada == null ? "–" : fmt(m.ada, 0)),
      tag: tagChip(m.tag) || "–"
    }));
    const swapHtml = swapRows.length ? table(
      [{ key: "when", label: "When" }, { key: "ticker", label: "Ticker" }, { key: "dir", label: "Direction" }, { key: "ada", label: "ADA", num: true }, { key: "tag", label: "Tag" }],
      swapRows
    ) : empty("No big swaps", "Pool swaps and big_moves.json are empty.");
    return `<div class="stack">${briefHtml}${head}${panel("Snek top 10", "flag chips", topHtml, "table")}${panel("Hard vetoes", "not a buy signal", vetoHtml, "flag")}<div class="split">${panel("Dev checks", "fresh funded", devHtml, "flag")}${panel("Whale ledger", safeTag(v.whale && v.whale.whale_tag) || "tag only", whaleHtml, "feed")}</div>${panel("Liquidity moves", "last checked", moveHtml, "chart")}${panel("Big swaps", "pool prints", swapHtml, "chart")}</div>`;
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
      <a class="card" data-desk="solada" href="#/cardano"><h3>Cardano</h3><p>${tx((pack.cardanoBrief && pack.cardanoBrief.headline) || "Brief-first Snek feed.")}</p><span class="go">Open tab</span></a>
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
    const [health, paper, positions, tradesText, log, pulse, sPaper, flags, vetoes, devs, whale, liq, big, sIdx, nPulse, nLog, nIdx] = await Promise.all([
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
    const cardano = { log, pulse, paper: sPaper, flags, vetoes, devs, whale, liq, big, brief: cBrief && { date: cBrief.date, headline: cBrief.headline, learned: cBrief.learned, scoreboard: cBrief.score } };
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
