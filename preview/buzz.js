/* Preview-only X buzz layer. Measured factor, not a signal.
   Reads x_buzz.json and, when desk-data has it, x_buzz_history.jsonl.
   A missing history file is an empty state. Hourly bars are the snapshot's
   hourly_24h array: they grow in once when the panel is drawn. Nothing here
   shifts, randomizes, or replays bars on a timer. Price is not in the file;
   the dashed line is a fixed placeholder. Full ids are never rendered. */
(function (root) {
  "use strict";

  const HUE = {
    RDOG: "#ff7a45",
    FLOATER: "#14F195",
    RSTR: "#9945FF",
    BIO: "#36d6ff",
    YIELDMOS: "#ffb020"
  };

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function scrub(s) {
    let t = String(s ?? "");
    t = t.replace(/\$[A-Za-z0-9_]{2,32}/g, " ");
    t = t.replace(/@[A-Za-z0-9_]{2,32}/g, " ");
    t = t.replace(/cockwhale/gi, " ");
    t = t.replace(/\bfounder\b/gi, " ");
    t = t.replace(new RegExp("\\b" + String.fromCharCode(115, 117, 114, 105) + "\\b", "gi"), " ");
    t = t.replace(/\b(?:stake|addr)(?:_test)?1[0-9a-z]{8,}\b/gi, "");
    t = t.replace(/\b0x[0-9a-f]{8,}\b/gi, "");
    t = t.replace(/\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g, "");
    t = t.replace(/\b[0-9a-f]{56,}\b/gi, "");
    return t.replace(/\s+/g, " ").replace(/\s+([,.;])/g, "$1").trim();
  }
  function fullAddr(s) {
    return /^(?:(?:stake|addr)(?:_test)?1[0-9a-z]{10,}|0x[0-9a-f]{8,}|[1-9A-HJ-NP-Za-km-z]{32,44}|[0-9a-f]{56,})$/i.test(s);
  }
  function shortId(value) {
    const raw = String(value ?? "").trim();
    if (!raw) return "";
    const stripped = raw.replace(/[….]+/g, "");
    if (stripped.length !== raw.length && raw.length <= 24 && stripped.length <= 16 && !fullAddr(stripped)) return raw;
    const compact = raw.replace(/\s+/g, "");
    if (fullAddr(compact) || compact.length > 12) {
      if (compact.length < 8) return "";
      return compact.slice(0, 4) + "…" + compact.slice(-4);
    }
    if (compact.length > 8) return compact.slice(0, 6) + "…";
    return compact;
  }
  function cleanTicker(value) {
    const s = String(value ?? "").trim();
    if (!/^[A-Za-z][A-Za-z0-9]{0,15}$/.test(s)) return "";
    return s.toUpperCase();
  }
  function numOrNull(v) {
    if (typeof v === "boolean" || v == null || v === "") return null;
    const n = typeof v === "number" ? v : Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function fmtInt(n) {
    return Math.round(Number(n) || 0).toLocaleString("en-US");
  }
  function fmtMedian(n) {
    if (n == null || !isFinite(n)) return "–";
    return Number(n).toLocaleString("en-US", { maximumFractionDigits: 1 });
  }
  function velocityText(ratio) {
    if (ratio == null || !isFinite(Number(ratio))) return "n/a";
    return Number(ratio).toFixed(2).replace(/\.?0+$/, "") + "x";
  }
  function hueOf(ticker) {
    const k = String(ticker || "").toUpperCase();
    if (HUE[k]) return HUE[k];
    let h = 0;
    for (let i = 0; i < k.length; i++) h = (h * 33 + k.charCodeAt(i)) >>> 0;
    return "hsl(" + (h % 360) + ",72%,58%)";
  }
  function motionOK() {
    try {
      const fn = (root && root.matchMedia) || (typeof matchMedia === "function" ? matchMedia : null);
      if (typeof fn !== "function") return false;
      return !fn("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }
  function hoursOf(raw) {
    const src = Array.isArray(raw) ? raw : [];
    const out = [];
    for (let i = 0; i < 24; i++) {
      const v = numOrNull(src[i]);
      out.push(v != null && v > 0 ? v : 0);
    }
    return out;
  }
  function isLow(raw) {
    if (!raw || typeof raw !== "object") return false;
    if (raw.confidence === "low") return true;
    if (raw.confidence === "high" || raw.confidence === "medium") return false;
    return raw.low === true;
  }
  function asofLabel(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    try {
      return d.toLocaleString("en-CA", { timeZone: "America/Toronto", hour12: false }) + " ET";
    } catch (e) {
      return "";
    }
  }
  function whenEt(iso) {
    const d = new Date(iso);
    if (!iso || isNaN(d.getTime())) return "";
    try {
      return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" }) + " ET";
    } catch (e) {
      return "";
    }
  }
  function historyRow(raw) {
    if (!raw || typeof raw !== "object") return null;
    const ticker = cleanTicker(raw.ticker);
    const chain = raw.chain === "solana" || raw.chain === "cardano" ? raw.chain : "";
    const asof = typeof raw.asof === "string" ? raw.asof : "";
    if (!ticker || !chain || !asof || typeof raw.alert !== "boolean") return null;
    const book = typeof raw.paper_position === "string" && /^[A-Z]$/.test(raw.paper_position) ? raw.paper_position : "";
    const mentions1h = numOrNull(raw.mentions_1h);
    const mentions24 = numOrNull(raw.mentions_24h);
    return {
      ticker, chain, asof, book, alert: raw.alert === true,
      mentions1h: mentions1h != null && mentions1h > 0 ? Math.round(mentions1h) : Math.max(0, Math.round(mentions1h || 0)),
      mentions24: Math.max(0, Math.round(mentions24 || 0)),
      ratio: numOrNull(raw.ratio_1h_vs_prev_hr),
      confidence: raw.confidence === "low" || raw.confidence === "medium" || raw.confidence === "high" ? raw.confidence : ""
    };
  }
  function historyModel(text) {
    if (text == null) return { rows: [], missing: true };
    const rows = [];
    String(text).split(/\r?\n/).forEach(line => {
      const trimmed = line.trim();
      if (!trimmed) return;
      let raw = null;
      try { raw = JSON.parse(trimmed); } catch (e) { return; }
      const row = historyRow(raw);
      if (row) rows.push(row);
    });
    rows.sort((a, b) => String(a.asof).localeCompare(String(b.asof)));
    return { rows, missing: false };
  }
  function countHtml(value, kind, finalOverride) {
    const finalText = finalOverride || (kind === "vel" ? velocityText(value) : fmtInt(value));
    const shown = motionOK() ? (kind === "vel" ? "0x" : "0") : finalText;
    return `<span data-buzz-count="${value}" data-buzz-kind="${kind}" data-buzz-final="${esc(finalText)}">${esc(shown)}</span>`;
  }
  function heading(titleHtml) {
    return `<h2>${titleHtml}<span class="meas">measured, not a signal</span></h2>`;
  }
  function missingPanel(titleHtml) {
    return `<section class="buzz-panel" data-measured="not-a-signal">${heading(titleHtml)}<div class="buzz-empty" role="status"><strong>no buzz data yet</strong></div></section>`;
  }
  function stampHtml(token) {
    if (!token || token.ratio == null) return "";
    const text = "Buzz: " + token.mentions1h + "/h, " + velocityText(token.ratio);
    return `<span class="buzz-stamp" title="${esc(token.ticker + " · measured, not a signal")}" aria-label="${esc(token.ticker + " " + text)}">${esc(text)}</span>`;
  }
  function sampleOf(raw) {
    if (!raw || typeof raw !== "object") return null;
    return {
      posts: numOrNull(raw.posts),
      unique_authors: numOrNull(raw.unique_authors),
      median_followers: numOrNull(raw.median_followers),
      bot_like_share: numOrNull(raw.bot_like_share),
      authors_over_10k: numOrNull(raw.authors_over_10k),
      skipped: raw.skipped ? scrub(raw.skipped).slice(0, 160) : ""
    };
  }
  function buzzModel(doc) {
    if (!doc || typeof doc !== "object" || !Array.isArray(doc.tokens)) return null;
    const tokens = [];
    const byKey = {};
    doc.tokens.forEach(raw => {
      if (!raw || typeof raw !== "object") return;
      const ticker = cleanTicker(raw.ticker);
      const chain = raw.chain === "solana" || raw.chain === "cardano" ? raw.chain : "";
      if (!ticker || !chain) return;
      const ratio = raw.velocity && typeof raw.velocity === "object" ? numOrNull(raw.velocity.ratio_1h_vs_prev_hr) : null;
      const token = {
        ticker, chain, key: chain + ":" + ticker,
        id: shortId(raw.id_short),
        mentions24: Math.max(0, Math.round(numOrNull(raw.mentions_24h) || 0)),
        mentions1h: Math.max(0, Math.round(numOrNull(raw.mentions_1h) || 0)),
        hourly: hoursOf(raw.hourly_24h),
        ratio,
        low: isLow(raw),
        confidence: raw.confidence === "low" || raw.confidence === "medium" || raw.confidence === "high" ? raw.confidence : "",
        sample: sampleOf(raw.sample)
      };
      tokens.push(token);
      byKey[token.key] = token;
    });
    if (!tokens.length) return null;
    const sol = tokens.filter(t => t.chain === "solana").slice().sort((a, b) => (b.ratio || 0) - (a.ratio || 0));
    const ada = tokens.filter(t => t.chain === "cardano").slice().sort((a, b) => b.mentions1h - a.mentions1h);
    const top3 = tokens.filter(t => t.ratio != null).slice().sort((a, b) => b.ratio - a.ratio).slice(0, 3);
    const hottest = sol.find(t => t.ratio != null) || null;
    const botLike = doc.definitions && doc.definitions.bot_like ? scrub(doc.definitions.bot_like).slice(0, 140) : "";
    return { asof: typeof doc.asof === "string" ? doc.asof : "", asofLabel: asofLabel(doc.asof), tokens, sol, ada, top3, hottest, byKey, botLike };
  }
  function logoColor(routeId, model) {
    if (routeId !== "solana" || !model || !model.hottest) return "";
    return hueOf(model.hottest.ticker);
  }
  function fitWidths(fracs) {
    const clean = fracs.map(n => Math.max(0, Number(n) || 0));
    const sum = clean.reduce((a, b) => a + b, 0);
    if (sum <= 0) return clean.map(() => 0);
    const rounded = clean.map(n => Math.round((n / sum) * 100));
    const drift = 100 - rounded.reduce((a, b) => a + b, 0);
    let idx = 0;
    rounded.forEach((n, i) => { if (n > rounded[idx]) idx = i; });
    rounded[idx] += drift;
    return rounded.map(n => Math.max(0, n));
  }
  function pct(x) {
    return Math.round(x * 100) + "%";
  }
  function quality(token) {
    const s = (token && token.sample) || {};
    const authors = s.unique_authors == null ? null : Math.round(s.unique_authors);
    const over = s.authors_over_10k == null ? null : Math.round(s.authors_over_10k);
    const bot = s.bot_like_share;
    const canAuthors = authors != null && authors > 0 && over != null && over >= 0;
    const overShare = canAuthors ? over / authors : null;
    const botShare = bot != null && bot >= 0 ? Math.min(bot, 1) : null;
    let segments = [];
    let note = "";
    let basis = "none";
    if (botShare != null && overShare != null) {
      const normalRaw = 1 - botShare - overShare;
      const scaled = normalRaw < 0;
      const widths = fitWidths(scaled ? [botShare, 0, overShare] : [botShare, normalRaw, overShare]);
      segments = [
        { w: widths[0], text: "bot-like " + pct(botShare), bg: "var(--down)", fg: "#07060d" },
        { w: widths[1], text: "normal " + pct(Math.max(0, normalRaw)), bg: "var(--line2)", fg: "var(--text)" },
        { w: widths[2], text: ">10k " + String(over), bg: "var(--accent)", fg: "#07060d" }
      ];
      basis = "mixed";
      note = ">10k is " + over + " of " + authors + " unique authors (" + pct(overShare) + "). bot-like " + pct(botShare) + " is the published share of the posts sample; this file has no bot-like author count, so that share is not of unique authors. normal is the bar remainder.";
      if (scaled) note += " Segments are scaled to fit because those bases do not sum to 1.";
    } else if (overShare != null) {
      const widths = fitWidths([overShare, 1 - overShare]);
      segments = [
        { w: widths[0], text: ">10k " + String(over), bg: "var(--accent)", fg: "#07060d" },
        { w: widths[1], text: "other " + pct(1 - overShare), bg: "var(--line2)", fg: "var(--text)" }
      ];
      basis = "unique authors";
      note = ">10k and other are shares of " + authors + " unique authors (" + over + " of " + authors + "). bot-like was not measured, so it is not shown as a share.";
    } else if (botShare != null) {
      const widths = fitWidths([botShare, 1 - botShare]);
      segments = [
        { w: widths[0], text: "bot-like " + pct(botShare), bg: "var(--down)", fg: "#07060d" },
        { w: widths[1], text: "other " + pct(1 - botShare), bg: "var(--line2)", fg: "var(--text)" }
      ];
      basis = "posts sample";
      note = "bot-like is the published share of the posts sample. Unique-author counts are not in this sample, so these are not shares of unique authors.";
    } else {
      note = s.skipped || "no author sample yet";
    }
    return {
      segments, note, basis, overShare, botShare, authors, over,
      posts: s.posts == null ? null : Math.round(s.posts),
      median: s.median_followers
    };
  }
  function barsHtml(hourly) {
    const mx = Math.max.apply(null, [1].concat(hourly));
    return `<span class="buzz-bars" aria-hidden="true">${hourly.map((v, i) => {
      const z = !(v > 0);
      const h = z ? "3px" : Math.max(8, (v / mx) * 100).toFixed(1) + "%";
      return `<i${z ? ' class="z"' : ""}><b style="--h:${h};animation-delay:${i * 25}ms"></b></i>`;
    }).join("")}</span>`;
  }
  function solanaPanel(model) {
    if (!model) return missingPanel("Solana · <b>buzz sparkline</b>");
    if (!model.sol.length) {
      return `<section class="buzz-panel" data-measured="not-a-signal">${heading("Solana · <b>buzz sparkline</b>")}<div class="buzz-empty" role="status"><strong>No Solana rows in this file yet.</strong></div></section>`;
    }
    const hot = model.hottest;
    const hotBit = hot ? ` · logo follows hottest: <b style="color:${hueOf(hot.ticker)}">${esc(hot.ticker)}</b>` : "";
    const when = model.asofLabel ? ` · as of ${esc(model.asofLabel)}` : "";
    const rows = model.sol.map(t => {
      const hotChip = t.ratio != null && t.ratio >= 2;
      const vel = t.ratio == null ? "n/a" : countHtml(t.ratio, "vel");
      return `<button type="button" class="buzz-row" data-buzz-token="${esc(t.key)}" aria-pressed="false" aria-label="${esc(t.ticker + ", " + t.mentions1h + " per hour, velocity " + velocityText(t.ratio) + ", author quality")}"><span class="buzz-tk" style="color:${hueOf(t.ticker)}">${esc(t.ticker)}<small>${esc(t.id)}</small></span>${barsHtml(t.hourly)}<span class="vchip${hotChip ? " hot" : ""}">${vel}</span><span class="buzz-n">${countHtml(t.mentions1h, "int")}/h · ${countHtml(t.mentions24, "int")}/24h</span></button>`;
    }).join("");
    return `<section class="buzz-panel" data-measured="not-a-signal">${heading("Solana · <b>buzz sparkline</b>")}<p class="buzz-sub">24 hourly bars per shortlist row · 1h velocity vs previous hour · chip pulses at 2x or more · tap a row for author quality${hotBit}${when}</p><div class="buzz-list" role="group" aria-label="Solana shortlist buzz">${rows}</div><div id="buzz-detail" class="buzz-detail" hidden></div></section>`;
  }
  function cardanoPanel(model) {
    if (!model) return missingPanel("Cardano · <b>cluster heat grid — buzz layer</b>");
    if (!model.ada.length) {
      return `<section class="buzz-panel" data-measured="not-a-signal">${heading("Cardano · <b>cluster heat grid — buzz layer</b>")}<div class="buzz-empty" role="status"><strong>No Cardano rows in this file yet.</strong></div></section>`;
    }
    const peak = model.ada.reduce((m, t) => Math.max(m, t.mentions1h), 0);
    const denom = peak > 0 ? peak : 1;
    const when = model.asofLabel ? ` · as of ${esc(model.asofLabel)}` : "";
    const cells = model.ada.map((t, i) => {
      const a = peak > 0 ? t.mentions1h / denom : 0;
      const alpha = (0.06 + a * 0.8).toFixed(2);
      const bi = (0.05 + a * 0.3).toFixed(2);
      const bd = (5 - a * 2.5).toFixed(1) + "s";
      const sh = (a * 0.18).toFixed(2);
      return `<button type="button" class="buzz-cell${t.low ? " lowc" : ""}" data-buzz-token="${esc(t.key)}" data-confidence="${esc(t.confidence)}" aria-pressed="false" aria-label="${esc(t.ticker + ", " + t.mentions1h + " per hour" + (t.low ? ", low confidence" : "") + ", author quality")}" style="--bi:${bi};--bd:${bd};--sh:${sh};background-color:rgba(61,139,255,${alpha});animation-delay:${i * 50}ms">${t.low ? "<em>LOW CONF</em>" : ""}<b>${esc(t.ticker)}</b><span>${fmtInt(t.mentions1h)}/h · ${fmtInt(t.mentions24)}/24h</span><span class="buzz-id">${esc(t.id)}</span></button>`;
    }).join("");
    const strong = (0.06 + 0.8).toFixed(2);
    return `<section class="buzz-panel" data-measured="not-a-signal">${heading("Cardano · <b>cluster heat grid — buzz layer</b>")}<p class="buzz-sub">fill intensity = 1h mentions · hatched = low confidence (common-word / sparse query) · tap a cell for author quality${when}</p><div class="buzz-grid">${cells}</div><div class="buzz-legend"><span><i style="background:rgba(61,139,255,.06)"></i>0/h</span><span><i style="background:rgba(61,139,255,${strong})"></i>${fmtInt(peak)}/h</span><span><i class="hatch"></i>low confidence</span></div><div id="buzz-detail" class="buzz-detail" hidden></div></section>`;
  }
  function examplePrice(k) {
    const parts = [];
    for (let i = 0; i < 24; i++) {
      const x = (i / 23) * 300;
      const y = 50 + Math.sin(i / 3 + k) * 14 - i * 0.6;
      parts.push((i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1));
    }
    return parts.join(" ");
  }
  function buzzLine(hourly) {
    const mx = Math.max.apply(null, [1].concat(hourly));
    return hourly.map((v, i) => {
      const x = (i / 23) * 300;
      const y = 85 - (v / mx) * 75;
      return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
  }
  function homePanel(model) {
    if (!model) return missingPanel("Home · <b>buzz vs price</b> — top 3 fastest");
    if (!model.top3.length) {
      return `<section class="buzz-panel buzz-static" data-measured="not-a-signal">${heading("Home · <b>buzz vs price</b> — top 3 fastest")}<div class="buzz-empty" role="status"><strong>No velocity in this file yet.</strong></div></section>`;
    }
    const cards = model.top3.map((t, k) => `<div class="buzz-mini"><div class="buzz-tk">${esc(t.ticker)} <span class="buzz-tag">${esc(t.chain)}</span></div><svg viewBox="0 0 300 90" preserveAspectRatio="none" role="img" aria-label="${esc(t.ticker + " mentions per hour. Dashed line is an example price, not a market price.")}"><path d="${examplePrice(k)}" fill="none" stroke="#7d91a6" stroke-dasharray="4 4" stroke-width="1.5"/><text x="4" y="12" fill="#7d91a6" font-size="9" font-family="monospace">EXAMPLE PRICE</text><path d="${buzzLine(t.hourly)}" fill="none" stroke="${hueOf(t.ticker)}" stroke-width="2.2"/></svg>${stampHtml(t)}</div>`).join("");
    return `<section class="buzz-panel buzz-static" data-measured="not-a-signal">${heading("Home · <b>buzz vs price</b> — top 3 fastest")}<p class="buzz-sub">solid = X mentions/h (real, last 24h) · dashed grey = example price placeholder (price is not in this file)</p><div class="buzz-cols">${cards}</div><p class="buzz-sub">Briefs ${stampHtml(model.top3[0])}</p></section>`;
  }
  function briefsBanner(model) {
    if (!model) return missingPanel("Briefs · <b>buzz stamp</b>");
    if (!model.top3.length) {
      return `<section class="buzz-panel" data-measured="not-a-signal">${heading("Briefs · <b>buzz stamp</b>")}<div class="buzz-empty" role="status"><strong>No velocity in this file yet.</strong></div></section>`;
    }
    return `<section class="buzz-panel" data-measured="not-a-signal">${heading("Briefs · <b>buzz stamp</b>")}<p class="buzz-sub">Fastest current reading. ${stampHtml(model.top3[0])}</p></section>`;
  }
  function detailInner(token, model, history) {
    const q = quality(token);
    const def = (model && model.botLike) || "account under 30 days old or under 50 followers";
    const posts = q.posts == null ? "–" : fmtInt(q.posts);
    const authors = q.authors == null ? "–" : fmtInt(q.authors);
    const bar = q.segments.length ? `<div class="buzz-aq">${q.segments.map(seg => {
      const w = seg.w + "%";
      return `<div data-w="${w}" style="--w:${w};background:${seg.bg};color:${seg.fg}">${esc(seg.text)}</div>`;
    }).join("")}</div>` : "";
    const median = q.median == null
      ? `<div class="buzz-med">– <small>median followers</small></div>`
      : `<div class="buzz-med">${countHtml(q.median, "int", fmtMedian(q.median))} <small>median followers</small></div>`;
    return `<div class="buzz-detail-head"><h3>Detail · <b>${esc(token.ticker)}</b> author quality<span class="meas">measured, not a signal</span></h3><button type="button" class="buzz-close" data-buzz-close>Close</button></div><p class="buzz-sub">sample: ${posts} posts · ${authors} unique authors · bot-like = ${esc(def)}${token.id ? " · " + esc(token.id) : ""}</p>${bar}${median}<p class="buzz-sub">${esc(q.note)}</p>${timelineHtml(token, history)}`;
  }
  function timelineHtml(token, history) {
    if (!history || history.missing || !history.rows) {
      return `<p class="buzz-sub">Buzz timeline · no buzz history yet</p>`;
    }
    const rows = history.rows.filter(r => r.ticker === token.ticker && r.chain === token.chain).slice(-36);
    if (!rows.length) return `<p class="buzz-sub">Buzz timeline · no buzz history yet</p>`;
    const peak = Math.max.apply(null, [1].concat(rows.map(r => r.mentions1h)));
    const cols = rows.map((r, i) => {
      const h = Math.max(18, Math.round((r.mentions1h / peak) * 72));
      const tip = r.ticker + " · " + (whenEt(r.asof) || r.asof) + " · " + r.mentions1h + "/h · " + velocityText(r.ratio) + (r.book ? " · book " + r.book : "") + (r.alert ? " · alert" : "");
      return `<i class="run${r.alert ? " alert" : ""}" style="--h:${h}%;animation-delay:${i * 40}ms" title="${esc(tip)}"><b></b></i>`;
    }).join("");
    const alerts = rows.filter(r => r.alert).length;
    return `<div class="buzz-time"><p class="buzz-sub">Buzz timeline · ${rows.length} runs · dot marks an alert</p><div class="buzz-timeline" role="img" aria-label="${esc(token.ticker + " buzz history, " + rows.length + " runs, " + alerts + " alerts")}">${cols}</div></div>`;
  }
  function alertsPanel(history) {
    const head = heading("Home · <b>recent buzz alerts</b>");
    const shell = inner => `<section class="buzz-panel buzz-static" data-measured="not-a-signal">${head}${inner}</section>`;
    if (!history || history.missing) return shell(`<div class="buzz-empty" role="status"><strong>no buzz history yet</strong></div>`);
    const alerts = history.rows.filter(r => r.alert).slice().sort((a, b) => String(b.asof).localeCompare(String(a.asof))).slice(0, 8);
    if (!alerts.length) return shell(`<div class="buzz-empty" role="status"><strong>no buzz alerts yet</strong></div>`);
    const items = alerts.map(r => {
      const book = r.book ? `<span class="buzz-book">${esc(r.book)}</span>` : "";
      return `<li><b>${esc(r.ticker)}</b><span>${esc(whenEt(r.asof) || "")}</span><span>${fmtInt(r.mentions1h)}/h</span><span>${esc(velocityText(r.ratio))}</span>${book}</li>`;
    }).join("");
    return shell(`<p class="buzz-sub">Newest alert runs · ticker, time ET, 1h mentions, velocity, paper book if any</p><ul class="buzz-alerts">${items}</ul>`);
  }
  function insertInStack(html, block) {
    const token = '<div class="stack">';
    const at = html.indexOf(token);
    if (at < 0) return block + html;
    const i = at + token.length;
    return html.slice(0, i) + block + html.slice(i);
  }
  function insertBeforeSection(html, marker, block) {
    const at = html.indexOf(marker);
    if (at < 0) return html + block;
    const start = html.lastIndexOf("<section", at);
    if (start < 0) return block + html;
    return html.slice(0, start) + block + html.slice(start);
  }
  function decorate(routeId, html, doc, historyText) {
    let out = html == null ? "" : String(html);
    const model = buzzModel(doc);
    const history = historyModel(historyText);
    if (routeId === "solana") out = insertInStack(out, solanaPanel(model));
    else if (routeId === "cardano") {
      const marker = out.includes("Cluster heat</span>") ? "Cluster heat</span>" : 'data-cv2="heat"';
      out = insertBeforeSection(out, marker, cardanoPanel(model));
    }
    else if (routeId === "home") out = insertBeforeSection(out, "Latest briefs</span>", homePanel(model) + alertsPanel(history));
    else if (routeId === "briefs") out = insertInStack(out, briefsBanner(model));
    else return out;
    if ((routeId === "home" || routeId === "briefs") && model && model.top3[0]) {
      const stamp = stampHtml(model.top3[0]);
      if (stamp) out = out.replace(/class="feed-item"><div class="meta">/g, 'class="feed-item"><div class="meta">' + stamp);
    }
    return out;
  }
  function leaksId(html) {
    const doc = root.document || (typeof document !== "undefined" ? document : null);
    if (!doc || !doc.createElement) return false;
    const box = doc.createElement("div");
    box.innerHTML = html;
    const text = box.textContent || "";
    if (/(?:stake|addr)(?:_test)?1[0-9a-z]{8,}/i.test(text)) return true;
    if (/\b0x[0-9a-f]{8,}\b/i.test(text)) return true;
    if (/\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/.test(text)) return true;
    if (/\b[0-9a-f]{56,}\b/i.test(text)) return true;
    return false;
  }
  function animateCounts(scope) {
    if (!scope || !scope.querySelectorAll) return;
    const raf = (root && root.requestAnimationFrame) || (typeof requestAnimationFrame === "function" ? requestAnimationFrame : null);
    scope.querySelectorAll("[data-buzz-count]").forEach(el => {
      const target = Number(el.getAttribute("data-buzz-count"));
      const finalText = el.getAttribute("data-buzz-final") || el.textContent;
      const kind = el.getAttribute("data-buzz-kind") || "int";
      if (!isFinite(target) || !motionOK() || typeof raf !== "function") {
        el.textContent = finalText;
        return;
      }
      const nowFn = (root && root.performance && root.performance.now) ? root.performance.now.bind(root.performance) : (typeof performance !== "undefined" ? () => performance.now() : () => Date.now());
      const t0 = nowFn();
      const paint = n => { el.textContent = kind === "vel" ? velocityText(n) : fmtInt(n); };
      (function frame(now) {
        if (el.isConnected === false) return;
        const p = Math.min(1, (now - t0) / 1200);
        const e = 1 - Math.pow(1 - p, 3);
        if (p < 1) {
          paint(target * e);
          raf(frame);
        } else el.textContent = finalText;
      })(t0);
    });
  }
  function growBars(scope) {
    if (!scope || !scope.querySelectorAll) return;
    const bars = scope.querySelectorAll(".buzz-aq div");
    const timer = (root && root.setTimeout) || (typeof setTimeout === "function" ? setTimeout : null);
    bars.forEach((el, i) => {
      const w = el.getAttribute("data-w") || "0%";
      const apply = () => { if (el.isConnected !== false) el.style.setProperty("width", w, "important"); };
      if (!motionOK() || typeof timer !== "function") apply();
      else timer(apply, 500 + i * 700);
    });
  }
  const state = { wired: false, model: null, history: { rows: [], missing: true }, routeId: "", logoGen: 0 };
  function pageDoc() {
    return (root && root.document) || (typeof document !== "undefined" ? document : null);
  }
  function closeDetail(doc) {
    const host = doc.getElementById("buzz-detail");
    if (host) { host.hidden = true; host.innerHTML = ""; }
    doc.querySelectorAll("[data-buzz-token].on").forEach(el => {
      el.classList.remove("on");
      el.setAttribute("aria-pressed", "false");
    });
  }
  function onBuzzClick(e) {
    const doc = pageDoc();
    if (!doc || !e || !e.target || !e.target.closest) return;
    if (e.target.closest("[data-buzz-close]")) { closeDetail(doc); return; }
    const btn = e.target.closest("[data-buzz-token]");
    if (!btn) return;
    if (btn.classList.contains("on")) { closeDetail(doc); return; }
    const token = state.model && state.model.byKey[btn.getAttribute("data-buzz-token")];
    const host = doc.getElementById("buzz-detail");
    if (!token || !host) return;
    const html = detailInner(token, state.model, state.history);
    host.hidden = false;
    host.innerHTML = leaksId(html) ? '<div class="buzz-empty" role="status"><strong>no buzz data yet</strong></div>' : html;
    doc.querySelectorAll("[data-buzz-token]").forEach(el => {
      const on = el === btn;
      el.classList.toggle("on", on);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
    if (!leaksId(html)) {
      animateCounts(host);
      growBars(host);
    }
    if (host.scrollIntoView) host.scrollIntoView({ block: "nearest", behavior: motionOK() ? "smooth" : "auto" });
  }
  function applyLogo(routeId, model) {
    const doc = pageDoc();
    if (!doc || !doc.querySelector) return;
    const span = doc.querySelector(".logo span");
    const cur = doc.querySelector(".logo i");
    if (!span || !cur) return;
    const gen = ++state.logoGen;
    const color = logoColor(routeId, model);
    const run = () => {
      if (gen !== state.logoGen) return;
      if (!color) {
        span.style.color = "";
        cur.style.color = "";
        span.style.textShadow = "";
        span.removeAttribute("title");
        return;
      }
      span.style.color = color;
      cur.style.color = color;
      span.style.textShadow = "0 0 12px " + color;
      span.title = "hottest: " + model.hottest.ticker;
    };
    if (!color || !motionOK()) run();
    else {
      const timer = (root && root.setTimeout) || setTimeout;
      timer(run, 700);
    }
  }
  function mount(view, routeId, doc, historyText) {
    state.model = buzzModel(doc);
    state.history = historyModel(historyText);
    state.routeId = routeId;
    applyLogo(routeId, state.model);
    if (view) {
      animateCounts(view);
      growBars(view);
    }
    const page = pageDoc();
    if (!state.wired && page && page.addEventListener) {
      state.wired = true;
      page.addEventListener("click", onBuzzClick);
    }
  }

  root.SightlineBuzz = {
    model: buzzModel,
    quality,
    velocityText,
    shortId,
    logoColor,
    hueOf,
    solanaPanel,
    cardanoPanel,
    homePanel,
    detailInner,
    historyModel,
    alertsPanel,
    decorate,
    mount
  };
})(typeof window !== "undefined" ? window : globalThis);
