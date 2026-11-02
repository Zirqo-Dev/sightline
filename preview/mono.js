/* Four Sightline pages. Research and paper only.
   Reads the pack preview/app.js already loaded. No third-party calls.
   Layout rhythm follows baseline-desk: header card, one large figure, two stacks. */
(function (root) {
  const FRESH_MS = 26 * 3600 * 1000;
  const FAIL_AMBER = 0.1;
  const EST_PADS = { established: 1, minswap: 1 };

  function page(routeId, pack, api) {
    const id = routeId || "home";
    const p = pack || {};
    if (id === "cardano") return cardanoPage(p, api);
    if (id === "solana") return solanaPage(p, api);
    if (id === "nft") return nftPage(p, api);
    return homePage(p, api);
  }

  function cols(left, right) {
    return `<div class="cols"><div class="col">${left}</div><div class="col">${right}</div></div>`;
  }

  function homePage(pack, api) {
    const log = (pack.cardano && pack.cardano.log) || {};
    const lanes = cardanoLanes(log, pack.cardano && pack.cardano.kills);
    const closest = lanes.moving || lanes.runners[0] || null;
    const picks = collectPicks(pack.cardano && pack.cardano.paper);
    const adaKey = picks.length ? picks[0].ticker : "No pick";
    const adaSub = closest
      ? closest.ticker + " " + (closest.pct == null ? "" : signed(closest.pct, 1) + "%") + " · closest passed name"
      : "Nothing cleared rule k + y";
    const books = (pack.solanaModel && pack.solanaModel.bookRows) || [];
    const f = books[0] || {};
    const fPct = f.exp == null ? "–" : signed(f.exp * 100, 1) + "%";
    const solSub = (f.n || 0) + " closes" + (f.rule ? " · " + f.rule : "") + " · " + ((pack.solanaModel && pack.solanaModel.day14Verdict) || "NOT_FINAL");
    const nftRows = pricedNft(pack, api);
    const top = nftRows.find(r => r.usd != null) || nftRows[0];
    const nftKey = top && top.usd != null ? usdText(top.usd) : (nftRows.length ? String(nftRows.length) : "–");
    const nftSub = top ? top.name + (top.usd == null ? "" : "") : "No KING names";
    const left = linkCard("#/cardano", "Cardano", "Sentinel", adaKey, adaSub)
      + linkCard("#/solana", "Solana", "Day-14 · F", fPct, solSub);
    const right = linkCard("#/nft", "NFT", "KING", nftKey, nftSub)
      + freshBriefs(api, pack);
    return cols(left, right);
  }

  function cardanoPage(pack, api) {
    const log = (pack.cardano && pack.cardano.log) || {};
    const lanes = cardanoLanes(log, pack.cardano && pack.cardano.kills);
    const left = sentinel(api, pack, lanes) + runners(api, lanes) + established(api, lanes);
    const right = paperTests(api, pack) + whales(api, log) + changed(api, pack) + buzz(api, pack.cardano && pack.cardano.buzz, "cardano");
    return cols(left, right);
  }

  function solanaPage(pack, api) {
    const left = solanaBooks(pack) + openPositions(api, pack) + netGross(pack);
    const right = tradesLog(api, pack) + buzz(api, pack.cardano && pack.cardano.buzz, "solana");
    return cols(left, right);
  }

  function nftPage(pack, api) {
    return `<div class="cols"><div class="wide">${nft(api, pack)}</div></div>`;
  }

  function pricedNft(pack, api) {
    const rows = (api.nftRows ? api.nftRows(pack.nftPulse, pack.nftLog) : []).filter(r => r && !r.example);
    fillNftFloors(rows, pack.nftPulse);
    return rows.map(r => ({ name: r.name, usd: toUsd(r.floor, r.unit, pack.prices) })).filter(r => r.name);
  }
  function linkCard(href, title, kicker, figure, sub) {
    const down = /−|-/.test(String(figure));
    return `<a class="c" href="${href}"><div class="h"><h2>${esc(title)}</h2><span class="k">${esc(kicker)}</span></div><p class="bal ${down ? "dn" : ""}">${esc(figure)}</p><p class="pnl mu">${esc(sub)}</p></a>`;
  }

  function status(routeId, pack, api) {
    const log = pack && pack.cardano && pack.cardano.log;
    const asof = (routeId === "solana" && pack && pack.health && (pack.health.generated_at || pack.health.asof)) || (log && log.asof);
    const clock = asof ? clockLine(asof) : "";
    const updated = asof && api.ago ? api.ago(asof) : "";
    const bits = [updated ? updated : clock].filter(Boolean).join(" · ");
    const fail = cardanoFailure(pack && pack.cardano && pack.cardano.devs);
    const warn = routeId === "home" && fail != null && fail >= FAIL_AMBER
      ? `<br><span class="w">Cardano feed ${esc(pct1(fail * 100))} failures</span>`
      : "";
    return (bits || "Sightline") + warn;
  }

  function sentinel(api, pack, lanes) {
    const log = (pack.cardano && pack.cardano.log) || {};
    const picks = collectPicks(pack.cardano && pack.cardano.paper);
    const closest = lanes.moving || lanes.runners[0] || null;
    const when = clockShort(log.asof);
    let body;
    if (picks.length) {
      body = picks.map(p => coinRow(api, p.ticker, pill(p.test === "EST-01" ? "watch" : "passed · moving", "a"), esc(clip(p.why, 140)), pctHtml(p.pct), "")).join("");
    } else {
      const sub = closest
        ? "Nothing cleared rule k + y this run. Closest name below."
        : "Nothing cleared rule k + y this run.";
      body = `<div class="row"><div class="gr"><div class="nm">No Sentinel pick right now</div><div class="sub">${esc(sub)}</div></div></div>`;
      if (closest) body += coinRow(api, closest.ticker, closest.pillHtml, esc(closest.sentinelSub || closest.sub), pctHtml(closest.pct), "");
    }
    return card("Sentinel's pick", when ? "Cardano · " + when : "Cardano", body, "hero span");
  }

  function runners(api, lanes) {
    const rows = lanes.runners.slice(0, 5).map(r => coinRow(api, r.ticker, r.pillHtml, esc(r.sub), pctHtml(r.pct), "")).join("");
    const more = lanes.runners.slice(5, 10);
    const allPass = more.every(r => !r.watch && !r.near);
    const moreLine = more.length
      ? `<div class="lane">+${more.length} more · ${esc(more.map(r => r.ticker).join(" "))}${allPass ? " — all pass" : ""}</div>`
      : "";
    const radar = lanes.radar ? `<div class="lane dim">${esc(lanes.radar)}</div>` : "";
    const empty = rows || `<div class="row"><div class="gr"><div class="nm">No fresh names this run</div></div></div>`;
    return card("Runners · fresh launch", "target 10–25× · 24h", empty + moreLine + radar, "");
  }

  function established(api, lanes) {
    const rows = lanes.established.slice(0, 5).map(r => coinRow(api, r.ticker, r.pillHtml, esc(r.sub), pctHtml(r.pct), "")).join("");
    const rest = lanes.established.slice(5);
    const more = rest.length
      ? `<div class="lane">+${rest.length} context · ${esc(rest.map(r => r.ticker).join(" "))}</div>`
      : "";
    const empty = rows || `<div class="row"><div class="gr"><div class="nm">No high-cap names this run</div></div></div>`;
    return card("Established · high cap", "kill-line distance · 24h", empty + more, "");
  }

  function paperTests(api, pack) {
    const log = (pack.cardano && pack.cardano.log) || {};
    const paper = pack.cardano && pack.cardano.paper;
    const pump = pack.cardano && pack.cardano.pump;
    const sw = testById(paper, "SW-01");
    const est = testById(paper, "EST-01");
    const pending = ((sw && sw.comparison_group) || []).filter(g => g && g.status !== "ticket");
    const swNames = pending.map(g => g.ticker).filter(Boolean);
    const gate = gateLabel(log, pending);
    const swBody = `<div class="box"><div class="lab">SW-01 · swarm runner</div><div class="nm" style="margin-top:4px">${(sw && (sw.tickets || []).length) ? esc((sw.tickets || []).map(t => t.ticker).filter(Boolean).join(", ")) : "No ticket"}</div><div class="sub" style="white-space:normal">${esc(swNames.length ? swNames.length + " swarm launches on watch: " + swNames.slice(0, 4).join(", ") + (gate ? " · gates due " + gate : "") : "No swarm launch on the watch.")}</div><div class="bar"><i style="width:${(sw && (sw.tickets || []).length) ? 40 : 0}%"></i></div></div>`;
    const check = est && Array.isArray(est.check_log) && est.check_log.length ? est.check_log[est.check_log.length - 1] : null;
    const checked = (check && check.checked) || (log.est01_check && log.est01_check.universe) || [];
    const qualified = (check && check.qualified) || [];
    const note = (check && check.note) || (log.est01_check && log.est01_check.result) || "";
    const holderOnly = /holder/i.test(note) && /only|unverifiable/i.test(note);
    const width = qualified.length ? 100 : holderOnly ? 75 : checked.length ? 40 : 0;
    const estNames = (qualified.length ? qualified : checked).slice(0, 4).join(", ");
    const estBody = `<div class="box"><div class="lab">EST-01 · established</div><div class="nm" style="margin-top:4px">${estNames ? esc(estNames) + " " + pill("watch", "a") : "No ticket"}</div><div class="sub" style="white-space:normal">${esc(clip(note || "No 7 PM check logged yet.", 180))}</div><div class="bar"><i style="width:${width}%"></i></div></div>`;
    const book = bookLine(paper, pump);
    const gates = gate ? "next gates " + gate : "paper only";
    return card("Paper tests", esc(gates), `<div class="two">${swBody}${estBody}</div><div class="sub" style="margin-top:8px;white-space:normal">${esc(book)}</div>`, "span");
  }

  function whales(api, log) {
    const list = Array.isArray(log.whales) ? log.whales : [];
    const rows = list.slice(0, 4).map(w => {
      const name = api.tx ? api.tx(w.codename) : esc(w.codename);
      if (!name) return "";
      const net = num(w.net_flow_ada);
      const right = net == null ? "–" : (Math.abs(net) < 1 ? "net ~0 ₳" : signedAda(net));
      const cls = net > 1 ? "up" : "mu";
      const line = clip(api.sanitize ? api.sanitize(w.line || w.role || "") : (w.line || ""), 96);
      return `<div class="row"><div class="gr"><div class="nm">${name}</div><div class="sub">${esc(line)}</div></div><div class="r m ${cls}">${esc(right)}</div></div>`;
    }).join("");
    const swarm = (log.cardano_v2 && Array.isArray(log.cardano_v2.swarms) && log.cardano_v2.swarms[0]) || null;
    const swarmRow = swarm ? `<div class="row"><div class="gr"><div class="nm">${esc(swarm.name || "Swarm")}</div><div class="sub">${esc([swarm.wallets ? swarm.wallets + " wallets" : "", ((swarm.last_seen || []).slice(0, 4).join(" "))].filter(Boolean).join(" · "))}</div></div><div class="r m mu">early buyers</div></div>` : "";
    const flow = flowLine(log);
    const body = (rows || `<div class="row"><div class="gr"><div class="nm">No codenames this run</div></div></div>`) + swarmRow + (flow ? `<p class="t" style="margin-top:8px">${flow}</p>` : "");
    return card("Whale codenames", "last 24h", body, "");
  }

  function modelOf(pack) {
    return (pack && pack.solanaModel) || { bookRows: [], opens: [], openCount: 0, activity: [], net: null, gross: null };
  }
  /* Expectancy is day14_verdict.py: total realised pnl / (0.5 * n). Shown as a percent per trade, never the SOL sum. */
  function expPct(book) {
    if (!book || book.exp == null || !isFinite(book.exp)) return null;
    return book.exp * 100;
  }
  function solanaBooks(pack) {
    const model = modelOf(pack);
    const books = model.bookRows || [];
    const f = books.find(b => b.id === "F") || books[0] || {};
    const pct = expPct(f);
    const cls = pct == null ? "" : pct < 0 ? "dn" : pct > 0 ? "up" : "";
    const rule = f.rule === "STOP" ? pill("STOP", "d") : f.rule ? pill(f.rule, "") : "";
    const verdict = model.day14Verdict || "NOT_FINAL";
    const others = books.filter(b => b !== f).map(b => {
      const p = expPct(b);
      const c = p == null ? "" : p < 0 ? "dn" : p > 0 ? "up" : "";
      const name = b.id === "R" ? "R random" : b.id === "S" ? "S shadow" : b.id;
      return `<div class="book"><div class="lab">${esc(name)}</div><div class="big ${c}">${esc(p == null ? "–" : signed(p, 1) + "%")}</div><div class="sub">${esc(String(b.n || 0))} closes</div></div>`;
    }).join("");
    const body = `<p class="lab">F desk · expectancy</p><p class="bal ${cls}">${esc(pct == null ? "–" : signed(pct, 1) + "%")}</p><p class="pnl">over ${esc(String(f.n || 0))} closes ${rule}</p><p class="verdict">${esc(verdict)} until Nov 11, 10:00 AM ET</p>${others ? `<div class="vs">${others}</div>` : ""}`;
    return card("Day-14 books", "same rule as the verdict script", body, "");
  }
  function openPositions(api, pack) {
    const opens = modelOf(pack).opens || [];
    const rows = opens.slice(0, 8).map(p => {
      const pct = markPct(p);
      const reason = clip(api.sanitize ? api.sanitize(p.reason) : p.reason, 72);
      return coinRow(api, p.ticker, esc(p.book || ""), esc(reason), pct == null ? "–" : signed(pct, 1) + "%", pct < 0 ? "dn" : pct > 0 ? "up" : "mu");
    }).join("");
    return card("Open positions", opens.length + " paper", rows || `<p class="sub">No open paper positions.</p>`, "");
  }
  function tradesLog(api, pack) {
    const rows = (modelOf(pack).activity || []).filter(a => /sell|exit|close/i.test(a.action || "")).slice(0, 8);
    const body = rows.map(a => {
      const reason = clip(api.sanitize ? api.sanitize(a.reason) : a.reason, 80) || "no reason logged";
      const pnl = a.pnl;
      return `<div class="row"><div class="gr"><div class="nm">${esc(api.sanitize ? api.sanitize(a.symbol) : a.symbol)} ${esc(a.book || "")}</div><div class="sub">${esc(reason)}</div></div><div class="r m ${pnl < 0 ? "dn" : pnl > 0 ? "up" : "mu"}">${esc(signed(pnl, 3))} SOL</div></div>`;
    }).join("");
    return card("Trades log", "exit reason", body || `<p class="sub">No closed fills yet.</p>`, "");
  }
  function netGross(pack) {
    const model = modelOf(pack);
    const netCls = model.net < 0 ? "dn" : model.net > 0 ? "up" : "";
    const grossCls = model.gross < 0 ? "dn" : model.gross > 0 ? "up" : "";
    const body = `<div class="vs"><div class="book"><div class="lab">Net SOL</div><div class="big ${netCls}">${esc(signed(model.net, 3))}</div></div><div class="book"><div class="lab">Gross SOL</div><div class="big ${grossCls}">${esc(signed(model.gross, 3))}</div></div></div><p class="sub" style="margin-top:8px;white-space:normal">Closed paper fills. This is not the day-14 expectancy.</p>`;
    return card("Net vs gross", "SOL, not the verdict %", body, "");
  }
  function freshBriefs(api, pack) {
    const briefs = (pack.briefs || []).slice().sort((a, b) => String(b.date || b.source_asof || "").localeCompare(String(a.date || a.source_asof || "")));
    const fresh = briefs.filter(briefIsFresh);
    const body = fresh.length
      ? fresh.slice(0, 3).map(b => `<p class="t"><span class="lab">${esc(b.desk || "Desk")} · ${esc(whenStamp(b.date) || "")}</span><br>${esc(clip(api.sanitize ? api.sanitize(b.headline) : b.headline, 280))}</p>`).join("")
      : `<p class="sub">No brief from the last day.</p>`;
    const stale = briefs.find(b => !briefIsFresh(b));
    const note = stale ? `<p class="t mu" style="font-size:12px">Earlier briefs stay in the archive. Latest hidden ${esc(whenStamp(stale.date) || stale.date || "")}. <span class="p">stale</span></p>` : "";
    return card("Latest briefs", "fresh only", body + note, "");
  }
  function briefIsFresh(b) {
    const raw = b && b.source_asof ? b.source_asof : (b && b.date ? b.date + "T12:00:00Z" : "");
    const ms = Date.parse(raw);
    return Number.isFinite(ms) && (Date.now() - ms) < FRESH_MS;
  }
  function changed(api, pack) {
    const log = (pack.cardano && pack.cardano.log) || {};
    const asof = log.asof;
    const fresh = asof && (Date.now() - Date.parse(asof)) < FRESH_MS;
    const learned = (log.learned_or_changed || []).filter(x => typeof x === "string").map(x => api.sanitize ? api.sanitize(x) : x).filter(Boolean);
    const headline = api.sanitize ? api.sanitize(log.headline || "") : (log.headline || "");
    const text = !fresh ? "No fresh desk note in the last day." : (learned.length ? learned.join(" ") : headline);
    return card("What changed this run", fresh && asof ? whenStamp(asof) : "Cardano", `<p class="t">${esc(text)}</p>`, "");
  }

  function buzz(api, doc, chain) {
    const tokens = ((doc && doc.tokens) || []).filter(t => t && (!chain || String(t.chain || "").toLowerCase() === chain) && /^[A-Za-z][A-Za-z0-9]{1,14}$/.test(String(t.ticker || "")) && String(t.ticker).toLowerCase() !== "unknown");
    tokens.sort((a, b) => (b.mentions_1h || 0) - (a.mentions_1h || 0));
    const live = tokens.length > 0;
    const rows = tokens.slice(0, 5).map(t => {
      const ratio = t.velocity && (t.velocity.ratio_1h_vs_7d_median != null ? t.velocity.ratio_1h_vs_7d_median : t.velocity.ratio_1h_vs_prev_hr);
      const ratioLabel = t.velocity && t.velocity.ratio_1h_vs_7d_median != null ? "7-day median" : "prior hour";
      const sub = `${t.mentions_1h == null ? "–" : t.mentions_1h} posts / 1h` + (ratio == null ? "" : ` · ${trim(ratio)}× ${ratioLabel}`);
      return coinRow(api, String(t.ticker).toUpperCase(), "", esc(sub), "measured", "mu");
    }).join("");
    const missing = live ? "" : `<div class="sub" style="margin-top:6px">${doc && doc.tokens ? "No posts for this desk in the latest file." : "x_buzz.json is not on this origin yet."}</div>`;
    const meta = live ? (chain || "") : (doc && doc.tokens ? "none this run" : "no file yet");
    return card(`X buzz ${pill("measured, not a signal", "")}`, meta, (rows || `<div class="row"><div class="gr"><div class="nm">No measured posts</div></div></div>`) + missing, "");
  }

  function nft(api, pack) {
    const prices = pack.prices;
    const rows = (api.nftRows ? api.nftRows(pack.nftPulse, pack.nftLog) : []).filter(r => r && !r.example);
    fillNftFloors(rows, pack.nftPulse);
    const watches = rows.filter(r => /watch/i.test(r.status || ""));
    let shown = rows.slice(0, 8);
    watches.forEach(w => { if (shown.indexOf(w) < 0) shown.push(w); });
    const live = shown.length > 0 && prices && prices.prices;
    const body = shown.map(r => {
      const usd = toUsd(r.floor, r.unit, prices);
      const watch = /watch/i.test(r.status || "");
      const tag = r.status ? pill(watch ? "watch" : "pass", watch ? "a" : "") : "";
      const note = r.chain ? String(r.chain) : "";
      const right = usd == null ? "–" : usdText(usd);
      return coinRow(api, r.name, tag, esc(note), right, "");
    }).join("");
    const asof = prices && prices.asof;
    const source = prices && prices.source;
    const px = prices && prices.prices && prices.prices.ETH;
    const meta = (source ? esc(source) : "no price file") + (asof ? " · " + esc(whenStamp(asof)) : "");
    const age = pack.nftLog && pack.nftLog.asof;
    const foot = `${rows.length || 0} names` + (px ? ` · ETH ${esc(usdText(px))}` : "") + (age ? ` · desk feed ${esc(whenStamp(age))}` : "");
    const missing = live ? "" : `<div class="sub" style="margin-top:6px">Floors stay blank until a stored USD price covers the coin.</div>`;
    const empty = body || `<div class="row"><div class="gr"><div class="nm">No KING names</div></div></div>`;
    return card("NFT · KING watchlist", `<span class="k">${meta}</span>`, empty + missing + `<div class="sub" style="margin-top:6px;white-space:normal">${foot}</div>`, "");
  }

  function desk(api, pack) {
    const log = (pack.cardano && pack.cardano.log) || {};
    const asof = log.asof;
    const fresh = asof && (Date.now() - Date.parse(asof)) < FRESH_MS;
    const learned = (log.learned_or_changed || []).filter(x => typeof x === "string").map(x => api.sanitize ? api.sanitize(x) : x).filter(Boolean);
    const headline = api.sanitize ? api.sanitize(log.headline || "") : (log.headline || "");
    let body;
    if (fresh && (learned.length || headline)) {
      const text = learned.length ? learned.map(esc).join(" ") : esc(headline);
      body = `<p class="t"><span class="lab">Cardano · this run</span><br>${text}</p>`;
    } else {
      body = `<p class="t">No fresh desk note in the last day.</p>`;
    }
    const briefs = (pack.briefs || []).slice().sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    const runDay = etDay(asof);
    const stale = briefs.filter(b => b.date && runDay && b.date < runDay);
    const newestStale = stale[0];
    if (newestStale) {
      const days = briefAgeDays(newestStale);
      body += `<p class="t mu" style="font-size:11px">Earlier briefs hidden (older than a day). Latest archived ${esc(whenStamp(newestStale.date) || newestStale.date)}${days != null ? " · " + days + "d" : ""}. <span class="p">stale</span></p>`;
    }
    const day = fresh && asof ? whenStamp(asof) : "archive";
    return card("Latest from the desk", "fresh only · " + esc(day || ""), body, "span");
  }

  function cardanoLanes(log, kills) {
    const top = Array.isArray(log.cardano_top10) ? log.cardano_top10 : [];
    const topSet = {};
    top.forEach(r => { if (r && r.ticker) topSet[String(r.ticker).toUpperCase()] = r; });
    const verdicts = Array.isArray(log.verdicts) ? log.verdicts : [];
    const byTicker = {};
    verdicts.forEach(v => {
      const tk = cleanTicker(v && v.ticker);
      if (!tk || byTicker[tk]) return;
      byTicker[tk] = v;
    });
    const trends = trendMap(log);
    function rowFor(ticker, seed) {
      const v = byTicker[ticker] || {};
      const topRow = topSet[ticker] || {};
      const pct = firstPct(log.levels && log.levels[ticker], (v.reasons || []).join(" "), topRow.note, seed && seed.note);
      const dist = distance(v, (kills && kills[ticker]) || null);
      const near = dist.under;
      const verdict = String(v.verdict || seed && seed.level || "").toLowerCase();
      const watch = verdict === "watch" || /watch/i.test(verdict);
      let kind = "";
      let label = "pass";
      if (watch) { kind = "a"; label = "watch"; }
      else if (near) { kind = "d"; label = "near kill"; }
      else if (pct != null && pct > 0) { kind = "a"; label = "passed · moving"; }
      const holders = num(v.holders != null ? v.holders : topRow.holders);
      const pool = num(v.pool_ada != null ? v.pool_ada : topRow.ada_side_depth);
      const trend = trends[ticker];
      const who = whaleName(v.whale_activity) || (trend && trend.who) || "";
      const sub = laneSub({ holders, pool, dist, who, trend: trend && trend.trend, reasons: v.reasons || [], established: !!topSet[ticker] });
      return {
        ticker, pct, rank: num(topRow.rank), near, watch,
        pillHtml: pill(label, kind),
        sub,
        holders,
        sentinelSub: sentinelSub({ holders, pool, reasons: v.reasons || [], who, trend: trend && trend.trend }),
        verdict
      };
    }
    const established = top.map(r => rowFor(cleanTicker(r.ticker), r)).filter(r => r.ticker);
    const watches = established.filter(r => r.watch).sort((a, b) => (a.rank || 99) - (b.rank || 99));
    const rest = established.filter(r => !r.watch).sort((a, b) => (a.rank || 99) - (b.rank || 99));
    let estPicked = watches.concat(rest);
    const near = established.filter(r => r.near && !r.watch).sort((a, b) => (a.rank || 99) - (b.rank || 99));
    near.forEach(n => {
      if (estPicked.slice(0, 5).some(r => r.ticker === n.ticker)) return;
      estPicked = estPicked.filter(r => r.ticker !== n.ticker);
      estPicked.splice(Math.min(4, estPicked.length), 0, n);
    });
    const runners = [];
    Object.keys(byTicker).forEach(tk => {
      if (topSet[tk]) return;
      const v = byTicker[tk];
      const pad = String(v.launchpad || "").toLowerCase();
      if (EST_PADS[pad]) return;
      runners.push(rowFor(tk, null));
    });
    runners.sort((a, b) => {
      const rank = r => (r.pct != null && r.pct > 0 ? 2 : r.watch ? 0 : 1);
      const ar = rank(a), br = rank(b);
      if (ar !== br) return br - ar;
      return (b.holders || 0) - (a.holders || 0);
    });
    const moving = runners.filter(r => r.pct != null && r.pct > 0)[0] || null;
    return { runners, established: estPicked, moving, radar: radarLine(log, byTicker) };
  }

  function laneSub(o) {
    if (o.established && o.dist && o.dist.text) {
      const side = o.pool != null ? "Side " + compactAda(o.pool) + " ₳" : "";
      return [side, o.dist.text, o.who && o.trend ? o.who + " " + trendWord(o.trend) : ""].filter(Boolean).join(" · ");
    }
    if (o.who && o.trend) {
      const bits = [o.who + " " + trendWord(o.trend)];
      if (o.holders != null) bits.push(compactCount(o.holders) + " holders");
      return bits.join(" · ");
    }
    const bits = [];
    if (o.pool != null) bits.push("Pool " + compactAda(o.pool) + " ₳");
    if (o.holders != null) bits.push(compactCount(o.holders) + " h");
    const reason = (o.reasons || [])[0];
    if (!bits.length && reason) return clip(reason, 72);
    if (reason && /under|bar|falling/i.test(reason)) bits.push(clip(reason, 42));
    return bits.join(" · ");
  }

  function sentinelSub(o) {
    const bits = [];
    if (o.holders != null) bits.push("Holders " + compactCount(o.holders));
    if (o.pool != null) bits.push("side " + compactAda(o.pool) + " ₳");
    const reason = (o.reasons || [])[0];
    if (reason) bits.push(clip(reason, 64));
    if (o.who && o.trend) bits.push(o.who + " " + trendWord(o.trend));
    return bits.join(" · ");
  }

  function radarLine(log, byTicker) {
    const launches = (log.cardano_v2 && log.cardano_v2.launches) || [];
    if (!Array.isArray(launches) || !launches.length) return "";
    const scored = launches.map(L => ({ L, h: ageHours(L.age) })).sort((a, b) => (a.h == null ? 99 : a.h) - (b.h == null ? 99 : b.h));
    const hit = scored[0] && scored[0].L;
    if (!hit || !cleanTicker(hit.ticker)) return "";
    const tk = cleanTicker(hit.ticker);
    const flags = [hit.swarm ? "swarm" : "", hit.sniper ? "sniper" : ""].filter(Boolean).join(" + ");
    const v = byTicker[tk];
    const verdict = v && v.verdict ? String(v.verdict).toLowerCase() : "pass";
    const holders = hit.holders != null ? hit.holders + " h" : "";
    return "Launch radar: " + [tk, hit.age || "", holders, flags ? flags + " → " + verdict : verdict].filter(Boolean).join(" · ");
  }

  function distance(verdict, kill) {
    const blob = ((verdict && verdict.reasons) || []).join(" ");
    const above = blob.match(/~?(\d+(?:\.\d+)?)%\s+above\s+(?:the\s+)?(\d[\d,.]*k?)/i);
    const under = blob.match(/~?(\d+(?:\.\d+)?)%\s+under\s+(?:the\s+)?(\d[\d,.]*k?)/i);
    if (above) return { under: false, text: above[1] + "% above " + above[2] + " line" };
    if (under) {
      const pct = Number(under[1]);
      return { under: pct <= 10, text: under[1] + "% under " + under[2] };
    }
    const metric = String((kill && kill.metric) || "");
    if (kill && /ada|pool|side/i.test(metric) && num(kill.line) > 0 && num(kill.value) != null) {
      const pct = (num(kill.value) - num(kill.line)) / num(kill.line) * 100;
      const word = pct >= 0 ? "above" : "under";
      return { under: pct < 0 && pct > -10, text: trim(Math.abs(pct)) + "% " + word + " " + compactAda(num(kill.line)) + " line" };
    }
    return { under: false, text: "" };
  }

  function flowLine(log) {
    const trends = (log.cardano_v2 && log.cardano_v2.rotation && log.cardano_v2.rotation.trends) || [];
    if (!Array.isArray(trends) || !trends.length) return "";
    const buckets = { accumulating: [], trimming: [], exiting: [], "re-entering": [] };
    trends.forEach(t => {
      const tk = cleanTicker(t && t.token);
      const key = String(t && t.trend || "");
      if (tk && buckets[key]) buckets[key].push(tk);
    });
    const parts = [];
    if (buckets.accumulating.length) parts.push("▲ " + esc(buckets.accumulating.join(" · ")) + " accumulating");
    if (buckets["re-entering"].length) parts.push("▲ " + esc(buckets["re-entering"].join(" · ")) + " re-entering");
    if (buckets.trimming.length) parts.push("▼ " + esc(buckets.trimming.join(" · ")) + " trimming");
    if (buckets.exiting.length) parts.push("✖ " + esc(buckets.exiting.join(" · ")) + " exiting");
    return parts.join("&nbsp;&nbsp;");
  }

  function trendMap(log) {
    const who = (log.cardano_v2 && log.cardano_v2.rotation && log.cardano_v2.rotation.who) || "";
    const out = {};
    const trends = (log.cardano_v2 && log.cardano_v2.rotation && log.cardano_v2.rotation.trends) || [];
    trends.forEach(t => {
      const tk = cleanTicker(t && t.token);
      if (tk) out[tk] = { trend: t.trend, who: whaleName(who) };
    });
    return out;
  }

  function bookLine(paper, pump) {
    const a = bookBits(paper);
    const b = bookBits(pump);
    const win = a.closed ? Math.round(a.wins / a.closed * 100) : 0;
    return `Cardano paper book: ${a.open} open · ${a.closed} closed · ${win}% win · ${signed(a.pnl, 1)} ₳ (Solada) · ${signed(b.pnl, 1)} ₳-eq (Pump.fun)`;
  }

  function bookBits(paper) {
    const closed = (paper && paper.closed) || [];
    const open = (paper && paper.open) || [];
    let pnl = 0, wins = 0;
    closed.forEach(t => {
      const res = (t && t.result) || {};
      pnl += num(res.pnl) || 0;
      if ((num(res.pct) || 0) > 0) wins += 1;
    });
    return { open: open.length, closed: closed.length, wins, pnl };
  }

  function collectPicks(paper) {
    const out = [];
    ((paper && paper.tests) || []).forEach(test => {
      ((test && test.tickets) || []).forEach(ticket => {
        if (!ticket || ticket.closed_at) return;
        const st = String(ticket.status || "open").toLowerCase();
        if (st === "closed" || st === "exited") return;
        const tk = cleanTicker(ticket.ticker || ticket.token);
        if (!tk) return;
        const why = Array.isArray(ticket.thesis) ? ticket.thesis[0] : (ticket.thesis || "");
        out.push({ test: String(test.id || "").toUpperCase(), ticker: tk, why: String(why || ""), pct: num(ticket.result && ticket.result.pct) });
      });
    });
    return out;
  }

  function testById(paper, id) {
    const tests = paper && Array.isArray(paper.tests) ? paper.tests : [];
    return tests.find(t => t && String(t.id).toUpperCase() === id) || null;
  }

  function gateLabel(log, pending) {
    const actions = (log.paper_actions || []).join(" ");
    const m = actions.match(/gates?[^\d]*([A-Z][a-z]{2}\s+\d{1,2})/);
    if (m) return m[1];
    const due = (pending || []).map(g => g.gate_check_due).filter(Boolean)[0];
    return due ? String(due) : "";
  }

  function cardanoFailure(devs) {
    const cov = devs && devs.coverage;
    if (!cov) return null;
    const tracked = num(cov.tracked);
    const skipped = Array.isArray(cov.skipped) ? cov.skipped.length : num(cov.skipped);
    if (!(tracked > 0) || skipped == null) return null;
    return skipped / tracked;
  }

  function toUsd(floor, unit, prices) {
    const n = num(floor);
    if (n == null) return null;
    const u = String(unit || "").toUpperCase();
    if (u === "USD" || u === "USDC" || u === "USDT") return n;
    const px = prices && prices.prices && num(prices.prices[u]);
    if (px == null) return null;
    return n * px;
  }

  function briefFresh(b) {
    const ms = Date.parse(b && b.date && /^\d{4}-\d{2}-\d{2}$/.test(b.date) ? b.date + "T23:59:59Z" : (b && (b.source_asof || b.date)) || "");
    return Number.isFinite(ms) && (Date.now() - ms) < FRESH_MS;
  }
  function briefAgeDays(b) {
    const ms = Date.parse((b && b.date) || "");
    if (!Number.isFinite(ms)) return null;
    return Math.max(0, Math.round((Date.now() - ms) / 86400000));
  }

  function coinRow(api, ticker, pillHtml, sub, right, rightCls) {
    let logo = api.assetLogo ? api.assetLogo(ticker, ticker, false) : "";
    if (logo.indexOf("<img") === 0) logo = logo.replace("<img ", '<img onerror="this.remove()" ');
    return `<div class="row">${logo}<div class="gr"><div class="nm">${esc(ticker)} ${pillHtml || ""}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div><div class="r m ${rightCls || ""}">${right || ""}</div></div>`;
  }
  function normKey(s) {
    return String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }
  function fillNftFloors(rows, pulse) {
    const latest = (pulse && pulse.latest) || [];
    const by = {};
    latest.forEach(r => { if (r && r.ticker) by[normKey(r.ticker)] = r; });
    rows.forEach(row => {
      if (row.floor != null) return;
      const keys = [normKey(row.slug), normKey(row.name)].filter(Boolean);
      let hit = null;
      keys.forEach(k => { if (!hit && by[k]) hit = by[k]; });
      if (!hit) {
        hit = latest.find(r => {
          const t = normKey(r.ticker);
          return t && keys.some(k => k && (k.indexOf(t) >= 0 || t.indexOf(k) >= 0));
        }) || null;
      }
      if (hit && hit.floor != null) {
        row.floor = hit.floor;
        if (hit.unit) row.unit = hit.unit;
      }
    });
  }
  function etDay(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    try {
      return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Toronto", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
    } catch (e) {
      return "";
    }
  }
  function card(title, meta, body, extra) {
    const k = meta && String(meta).indexOf("<") >= 0 ? meta : (meta ? `<span class="k">${esc(meta)}</span>` : "");
    return `<section class="c ${extra || ""}"><div class="h"><h2>${title}</h2>${k}</div>${body}</section>`;
  }
  function pill(text, kind) {
    return `<span class="p${kind ? " " + kind : ""}">${esc(text)}</span>`;
  }
  function pctHtml(n) {
    if (n == null || !isFinite(n)) return `<span class="mu">–</span>`;
    const cls = n > 0 ? "up" : n < 0 ? "dn" : "mu";
    return `<span class="${cls}">${esc(signed(n, 1))}%</span>`;
  }
  function firstPct() {
    for (let i = 0; i < arguments.length; i++) {
      const src = String(arguments[i] || "");
      const m = src.match(/([+−-]?\d+(?:\.\d+)?)\s*%\s*(?:in\s*)?24h/i) || src.match(/price\s*([+−-]?\d+(?:\.\d+)?)%/i);
      if (m) {
        const n = Number(m[1].replace("−", "-").replace("+", ""));
        if (isFinite(n)) return n;
      }
    }
    return null;
  }
  function markPct(p) {
    if (!p || !(p.entry > 0) || p.mark == null) return null;
    return (p.mark / p.entry - 1) * 100;
  }
  function whaleName(text) {
    const s = String(text || "");
    const m = s.match(/\b([A-Z][a-z]+\s+[A-Z][a-z]+)/);
    return m ? m[1] : "";
  }
  function trendWord(trend) {
    if (trend === "accumulating" || trend === "accum") return "accum";
    if (trend === "trimming" || trend === "trim") return "trim";
    if (trend === "exiting" || trend === "exit") return "exit";
    if (trend === "re-entering") return "buy";
    return "";
  }
  function ageHours(age) {
    const s = String(age || "").trim().toLowerCase();
    if (s.charAt(0) === "<") return 0.5;
    const m = s.match(/(\d+(?:\.\d+)?)\s*(d|h)/);
    if (!m) return null;
    return m[2] === "d" ? Number(m[1]) * 24 : Number(m[1]);
  }
  function cleanTicker(v) {
    const name = String(v || "").trim().replace(/^\$/, "");
    if (!/^[A-Za-z][A-Za-z0-9]{0,15}$/.test(name)) return "";
    return name.toUpperCase();
  }
  function num(v) {
    if (typeof v === "boolean" || v == null || v === "") return null;
    const n = typeof v === "number" ? v : Number(String(v).replace(/,/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  function signed(n, d) {
    if (n == null || !isFinite(n)) return "–";
    const body = trim(Math.abs(n), d);
    return (n > 0 ? "+" : n < 0 ? "−" : "") + body;
  }
  function signedAda(n) {
    const body = compactAda(Math.abs(n));
    return (n > 0 ? "+" : n < 0 ? "−" : "") + body + " ₳";
  }
  function compactAda(n) {
    if (n == null || !isFinite(n)) return "–";
    const a = Math.abs(n);
    if (a >= 1e6) return trim(a / 1e6, 3) + "M";
    if (a >= 1e3) return trim(a / 1e3, a >= 10000 ? 1 : 1) + "k";
    return trim(a, 0);
  }
  function compactCount(n) {
    if (n == null || !isFinite(n)) return "–";
    return trim(n, n >= 100 ? 0 : 0);
  }
  function trim(n, d) {
    const digits = d == null ? (Math.abs(n) >= 100 ? 0 : Math.abs(n) >= 10 ? 1 : 1) : d;
    return Number(n).toFixed(digits).replace(/\.0$/, "");
  }
  function pct1(n) { return trim(n, 1) + "%"; }
  function pct0(n) { return trim(n, 0); }
  function usdText(n) {
    const sign = n < 0 ? "−" : "";
    const a = Math.abs(n);
    const body = a >= 1000 ? Math.round(a).toLocaleString("en-US") : (a >= 100 ? String(Math.round(a)) : a.toFixed(2));
    return sign + "$" + body;
  }
  function clip(s, n) {
    const t = String(s || "").replace(/\s+/g, " ").trim();
    return t.length > n ? t.slice(0, n - 1).trim() + "…" : t;
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function clockLine(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const date = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "America/Toronto" }).replace(/,/g, "");
    const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" });
    return date + " · " + time + " ET";
  }
  function clockShort(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" }) + " ET";
  }
  function whenStamp(iso) {
    if (!iso) return "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
      const [y, m, d] = iso.split("-").map(Number);
      return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
    }
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" }) + " ET";
  }

  root.SightlineMono = { page, status, cardanoFailure, toUsd, briefFresh };
})(typeof window !== "undefined" ? window : globalThis);
