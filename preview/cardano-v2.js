/* Cardano tab v2. Kill-line board, money flow, launch radar, clusters, heat.
   Reads verdicts, flags, kill status, and the whale ledger. Optional log.cardano_v2
   fills sections that are not in those files yet. Wallet text is a w- tag or a
   4-char label. Everything else is dropped before it reaches the page. */
(function (root) {
  const ENTRY_HOLDERS = 120;
  const ENTRY_POOL = 25000;
  const SWARM_NOTE = "When this group jumps in, it's usually a swarm launch → Pass. Worth watching only if a token keeps real holders after they leave.";
  const CLUSTER_NOTE = [
    ["Coordinated buys", "many wallets bought together at launch, likely one group."],
    ["Repeat early buyers", "same wallets show up first on many launches."],
    ["Sniper full exit", "bought in the first block, then sold it all."],
    ["Holder concentration", "one wallet owns a big slice, it can crash the price."]
  ];

  function render(t, data, v) {
    const v2 = asObj(data && data.cardano_v2) || asObj(v.log && v.log.cardano_v2) || {};
    const brief = t.briefBits ? t.briefBits(v.brief, "Cardano") : null;
    const briefBar = brief
      ? `<p class="slim-brief">${t.esc(t.when(brief.date) || "")} · ${t.tx(brief.headline)}</p>`
      : `<p class="slim-brief">No brief published yet.</p>`;
    return `<div class="stack cv2">
      <div class="pagehead"><div><h1>Cardano</h1><div class="sub">Kill lines first · paper research · w- tags only</div>${briefBar}</div></div>
      ${learnedSection(t, v)}
      ${boardSection(t, v, v2)}
      ${moneySection(t, v, v2)}
      ${radarSection(t, v2)}
      ${clusterSection(t, v, v2)}
      ${heatSection(t, v, v2)}
      ${outcomeSection(t, v)}
      ${moreSection(t, v)}
    </div>`;
  }

  function asObj(x) {
    return x && typeof x === "object" && !Array.isArray(x) ? x : null;
  }
  function clean(t, s) {
    return t.sanitize(String(s ?? ""))
      .replace(/\bu(?=[0-9a-z]*\d)[0-9a-z]{4,16}\b/gi, "")
      .replace(/\s{2,}/g, " ")
      .replace(/\s+([,.;])/g, "$1")
      .trim();
  }
  function txt(t, s) {
    const c = clean(t, s);
    return c ? t.esc(c) : "";
  }
  function compact(t, n) {
    const v = t.num(n);
    if (v == null) return "";
    const sign = v < 0 ? "-" : "";
    const a = Math.abs(v);
    const trim = (x, d) => x.toFixed(d).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
    if (a >= 1e6) return sign + trim(a / 1e6, a >= 1e8 ? 0 : a >= 1e7 ? 1 : a >= 1e6 * 10 ? 2 : 3) + "M";
    if (a >= 1e3) {
      const x = a / 1e3;
      return sign + (x >= 100 ? String(Math.round(x)) : x.toFixed(1).replace(/\.0$/, "")) + "k";
    }
    return sign + t.fmt(a, a >= 100 ? 0 : 1);
  }
  function ksMetric(m) {
    const s = String(m || "").toLowerCase();
    if (s === "ada_side" || s === "pool_ada" || s === "pool") return "pool_ada";
    if (s === "mcap" || s === "mcap_ada") return "mcap";
    if (s === "holders" || s === "holder") return "holders";
    if (s === "curve" || s === "curve_pct") return "curve";
    return s;
  }
  function metricLabel(m) {
    const k = ksMetric(m);
    if (k === "holders") return "Holders";
    if (k === "mcap") return "Mcap";
    if (k === "curve") return "Curve";
    if (k === "pool_ada") return "Pool ADA side";
    return "Line";
  }
  /* Board: solada and snek keep their badges. new/unknown means a brand-new token
     from another pad. established, minswap, missing, or anything else is no badge.
     Radar rows are new launches, so a missing pad there still says New launch. */
  function padBadge(t, p, opt) {
    const radar = !!(opt && opt.radar);
    const s = String(p == null ? "" : p).toLowerCase().trim();
    if (s === "solada") return "Solada";
    if (s === "snek" || s === "snek.fun" || s === "snekfun") return "Snek.fun";
    if (s === "new" || s === "unknown") return "New launch";
    if (radar && !s) return "New launch";
    return "";
  }
  function verdictTone(v) {
    const s = String(v || "").toLowerCase();
    if (/lottery|candidate|\bcall\b|^green\b|^l3\b|pass k/.test(s)) return "g";
    if (/watch|yellow|caution|^l2\b/.test(s)) return "y";
    return "r";
  }
  const TONE_LABEL = { g: "🟢 Lottery-size candidate", y: "🟡 Watch", r: "🔴 Pass" };
  function parseLimits(text) {
    const re = /(on-chain\s+holders|pool\s+ada\s+side|ada\s+side|pool\s+mcap|mcap|holders|curve)\s+under\s+~?\s*([0-9]+(?:\.[0-9]+)?)\s*([kKmM])?/gi;
    const out = [];
    let m;
    const s = String(text || "");
    while ((m = re.exec(s))) {
      let n = parseFloat(m[2]);
      const u = (m[3] || "").toLowerCase();
      if (u === "k") n *= 1000;
      else if (u === "m") n *= 1e6;
      if (!isFinite(n) || n <= 0) continue;
      const label = m[1].toLowerCase();
      let metric = "pool_ada";
      if (/holder/.test(label)) metric = "holders";
      else if (/mcap/.test(label)) metric = "mcap";
      else if (/curve/.test(label)) metric = "curve";
      out.push({ metric, line: n });
    }
    return out;
  }
  function reading(t, metric, entry, top, ks) {
    const m = ksMetric(metric);
    if (ks && ksMetric(ks.metric) === m && t.num(ks.value) != null) return t.num(ks.value);
    if (m === "pool_ada") return t.num(entry.pool_ada) != null ? t.num(entry.pool_ada) : t.num(top && top.ada_side_depth);
    if (m === "holders") return t.num(entry.holders) != null ? t.num(entry.holders) : t.num(top && top.holders);
    if (m === "mcap") return t.num(entry.mcap_ada) != null ? t.num(entry.mcap_ada) : t.num(top && top.mcap_ada);
    if (m === "curve") return t.num(entry.curve_pct) != null ? t.num(entry.curve_pct) : t.num(top && top.curve_pct);
    return null;
  }
  function explicitLimit(t, entry) {
    if (t.num(entry.kill_limit) != null) return t.num(entry.kill_limit);
    if (t.num(entry.kill_threshold) != null) return t.num(entry.kill_threshold);
    if (typeof entry.kill_line === "number") return entry.kill_line;
    return null;
  }
  function killBar(t, entry, top, ks) {
    const metric = entry.kill_metric ? ksMetric(entry.kill_metric) : "";
    const limit = explicitLimit(t, entry);
    const given = t.num(entry.kill_value);
    const pctGiven = t.num(entry.pct_from_line);
    if (metric && limit != null && (given != null || pctGiven != null)) {
      const value = given != null ? given : limit * (1 + pctGiven / 100);
      const pct = pctGiven != null ? pctGiven : (value - limit) / limit * 100;
      return { metric, value, line: limit, pct };
    }
    const limits = parseLimits(typeof entry.kill_line === "string" ? entry.kill_line : "");
    if (ks && t.num(ks.line) != null && ksMetric(ks.metric)) {
      const m = ksMetric(ks.metric);
      if (!limits.some(x => x.metric === m)) limits.push({ metric: m, line: t.num(ks.line) });
    }
    let best = null;
    limits.forEach(lim => {
      const val = reading(t, lim.metric, entry, top, ks);
      if (val == null || !(lim.line > 0)) return;
      const pct = (val - lim.line) / lim.line * 100;
      if (!best || pct < best.pct) best = { metric: lim.metric, value: val, line: lim.line, pct };
    });
    return best;
  }
  function barGeom(bar) {
    if (!bar || bar.value == null || !(bar.line > 0)) return null;
    const scale = Math.max(bar.value, bar.line * 2, 1);
    return {
      fill: Math.max(0, Math.min(100, Math.round(bar.value / scale * 100))),
      tick: Math.max(0, Math.min(100, Math.round(bar.line / scale * 100))),
      risk: bar.pct < 15
    };
  }
  function pctHtml(pct) {
    const n = Math.round(pct);
    if (pct < 0 && n === 0) return `<b class="cv2-bad">just under</b>`;
    if (pct > 0 && n === 0) return `<b class="cv2-bad">just above</b>`;
    if (n < 0) return `<b class="cv2-bad">${Math.abs(n)}% under</b>`;
    if (n === 0) return `<b class="cv2-bad">on the line</b>`;
    if (n < 15) return `<b class="cv2-bad">only ${n}% above</b>`;
    return `${n}% above`;
  }
  function whaleLine(t, entry) {
    const s = clean(t, entry.whale_line || entry.whale_activity || "");
    if (!s || /^none\b/i.test(s) || /^no tracked/i.test(s) || /^no moves/i.test(s) || /^no new\b/i.test(s)) return "";
    return s;
  }
  function whyLines(t, entry, whale) {
    const list = Array.isArray(entry.why) ? entry.why : Array.isArray(entry.reasons) ? entry.reasons : [];
    const out = [];
    list.forEach(item => {
      const s = clean(t, item);
      if (!s || s === whale || out.length >= 3) return;
      out.push(s);
    });
    return out;
  }
  function topMap(t, rows) {
    const map = {};
    (rows || []).forEach(r => {
      const name = t.tickerName(r && r.ticker);
      if (name) map[name.toUpperCase()] = r;
    });
    return map;
  }
  function killMap(t, kills) {
    const map = {};
    if (!kills || typeof kills !== "object" || Array.isArray(kills)) return map;
    Object.keys(kills).forEach(k => {
      const name = t.tickerName(k);
      if (name && kills[k] && typeof kills[k] === "object") map[name.toUpperCase()] = kills[k];
    });
    return map;
  }
  function boardEntries(t, v) {
    const written = v.log && Array.isArray(v.log.verdicts) ? v.log.verdicts.filter(r => r && typeof r === "object") : [];
    if (written.length) return written;
    const tops = topMap(t, v.top);
    const kills = killMap(t, v.kills);
    const names = [];
    const push = n => { if (n && names.indexOf(n.toUpperCase()) < 0) names.push(n.toUpperCase()); };
    (v.top || []).forEach(r => push(t.tickerName(r.ticker)));
    Object.keys(kills).forEach(push);
    return names.map(key => {
      const top = tops[key] || {};
      const kill = kills[key] || {};
      const name = t.tickerName(top.ticker) || key;
      return {
        ticker: name,
        verdict: kill.hit === true ? "pass" : "watch",
        launchpad: top.launchpad || "",
        pool_ada: t.num(top.ada_side_depth),
        holders: t.num(top.holders),
        mcap_ada: t.num(top.mcap_ada),
        kill_metric: kill.metric || "",
        kill_value: t.num(kill.value),
        kill_limit: t.num(kill.line),
        kill_line: typeof kill.text === "string" ? kill.text : "",
        reasons: []
      };
    });
  }
  function boardSection(t, v, v2) {
    const tops = topMap(t, v.top);
    const kills = killMap(t, v.kills);
    const rows = boardEntries(t, v).map((entry, i) => {
      const name = t.tickerName(entry.ticker);
      if (!name) return null;
      const key = name.toUpperCase();
      const top = tops[key] || null;
      const ks = kills[key] || null;
      const bar = killBar(t, entry, top, ks);
      const geom = barGeom(bar);
      const tone = verdictTone(entry.verdict || entry.level);
      const whale = whaleLine(t, entry);
      return { name, tone, i, entry, bar, geom, whale, why: whyLines(t, entry, whale) };
    }).filter(Boolean);
    rows.sort((a, b) => {
      const ap = a.bar ? a.bar.pct : Infinity;
      const bp = b.bar ? b.bar.pct : Infinity;
      return ap - bp || a.i - b.i;
    });
    const cards = rows.map(r => boardCard(t, r)).join("");
    const body = rows.length
      ? `<div class="cv2-chips" role="group" aria-label="Verdict filter"><button type="button" class="cv2-chip on" data-cv2-filter="all">All</button><button type="button" class="cv2-chip" data-cv2-filter="g">🟢</button><button type="button" class="cv2-chip" data-cv2-filter="y">🟡</button><button type="button" class="cv2-chip" data-cv2-filter="r">🔴</button></div><div id="cv2-board">${cards}</div><p id="cv2-none" class="cv2-empty" hidden>No tokens with this verdict today.</p>`
      : `<p class="cv2-empty">No data yet. The board fills from <code>verdicts</code> on the run log.</p>`;
    return section("board", "GOOD TOKENS BOARD", "Closest to its kill line first (most at risk on top).", body);
  }
  function boardCard(t, r) {
    const badge = padBadge(t, r.entry.launchpad);
    const sent = sentence(t, r);
    const why = r.why.length ? `<ul>${r.why.map(w => `<li>${t.esc(w)}</li>`).join("")}</ul>` : "";
    const whale = r.whale ? `<div class="cv2-whale">🐋 ${t.esc(r.whale)}</div>` : "";
    let bar = "";
    if (r.geom) {
      const edge = r.geom.tick > 72 ? " right" : "";
      bar = `<div class="cv2-bar" data-fill="${r.geom.fill}" data-tick="${r.geom.tick}"><div class="cv2-fill${r.geom.risk ? " risk" : ""}" style="width:${r.geom.fill}%"></div><div class="cv2-tick${edge}" style="left:${r.geom.tick}%"></div></div>`;
    }
    return `<article class="cv2-card" data-v="${r.tone}" data-tk="${t.esc(r.name)}"><div class="cv2-row"><span class="cv2-tk">${t.esc(r.name)}</span><span class="cv2-pill ${r.tone}">${TONE_LABEL[r.tone]}</span>${badge ? `<span class="cv2-badge">${t.esc(badge)}</span>` : ""}</div>${bar}${sent}${why}${whale}</article>`;
  }
  function sentence(t, r) {
    if (r.bar) {
      return `<p class="cv2-sent">${metricLabel(r.bar.metric)} ${t.esc(compact(t, r.bar.value))} · kill line ${t.esc(compact(t, r.bar.line))} · ${pctHtml(r.bar.pct)}</p>`;
    }
    const prose = typeof r.entry.kill_line === "string" ? txt(t, r.entry.kill_line) : "";
    if (prose) return `<p class="cv2-sent">${prose}</p>`;
    if (t.num(r.entry.pool_ada) != null) return `<p class="cv2-sent">Pool ADA side ${t.esc(compact(t, r.entry.pool_ada))}</p>`;
    if (t.num(r.entry.holders) != null) return `<p class="cv2-sent">Holders ${t.esc(compact(t, r.entry.holders))}</p>`;
    return "";
  }

  function learnedSection(t, v) {
    const lines = Array.isArray(v.learned) ? v.learned.filter(Boolean) : [];
    if (!lines.length) return "";
    return `<section class="cv2-learned" data-cv2="changed"><h2>What changed this run</h2><ul>${lines.map(line => `<li>${t.esc(line)}</li>`).join("")}</ul></section>`;
  }
  function netSpark(points) {
    const nums = (points || []).map(p => p && p.n).filter(n => typeof n === "number" && isFinite(n));
    if (nums.length < 2) return "";
    const min = Math.min.apply(null, nums);
    const max = Math.max.apply(null, nums);
    const span = max - min || 1;
    const w = 84, h = 28;
    const d = nums.map((n, i) => {
      const x = 2 + (i / (nums.length - 1)) * (w - 4);
      const y = h - 4 - ((n - min) / span) * (h - 8);
      return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
    const up = nums[nums.length - 1] >= nums[0];
    return `<svg class="cv2-spark ${up ? "up" : "dn"}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}"/></svg>`;
  }
  function netCards(t, whales) {
    const rows = (whales || []).filter(w => w && w.net_flow_ada != null && isFinite(w.net_flow_ada));
    if (!rows.length) return "";
    return `<div class="cv2-nets">${rows.map(w => {
      const n = w.net_flow_ada;
      const cls = n > 0 ? "up" : n < 0 ? "dn" : "";
      const sign = n > 0 ? "+" : "";
      return `<article class="cv2-net"><div><b>${t.esc(w.codename || "Whale")}</b><span class="${cls}">net ${sign}${t.esc(t.fmt(n, 0))} ADA</span></div>${netSpark(w.history)}</article>`;
    }).join("")}</div>`;
  }
  function outcomeSection(t, v) {
    const rows = Array.isArray(v.outcomes) ? v.outcomes : [];
    if (!rows.length) return "";
    const cards = rows.map(r => {
      const pump = r.pump || 0;
      const rug = r.rug || 0;
      const donut = t.donut ? t.donut(pump, rug, r.label) : "";
      return `<article class="cv2-out">${donut}<p class="cv2-outn"><span class="up">${t.esc(t.fmt(pump, 0))} pump</span><span class="dn">${t.esc(t.fmt(rug, 0))} rug</span></p></article>`;
    }).join("");
    return section("outcomes", "FLAG OUTCOMES", "Pump and rug counts after each flag. Research only.", `<div class="cv2-outs">${cards}</div>`);
  }
  function section(id, title, sub, body) {
    return `<section class="cv2-sec" data-cv2="${id}"><h2 class="cv2-h">// ${title}</h2>${sub ? `<p class="cv2-sub">${sub}</p>` : ""}${body}</section>`;
  }
  function emptyLine(msg) {
    return `<p class="cv2-empty">${msg}</p>`;
  }

  function rowMs(t, r) {
    const ms = new Date(t.timeOf ? t.timeOf(r.time || r.at || r.t) : (r.time || r.at || "")).getTime();
    return isFinite(ms) ? ms : NaN;
  }
  function deriveRotation(t, v) {
    const rows = ((v.whale && v.whale.rows) || []).filter(r => r && typeof r === "object");
    let end = 0;
    rows.forEach(r => { const ms = rowMs(t, r); if (isFinite(ms) && ms > end) end = ms; });
    const start = end ? end - 864e5 : 0;
    const windowRows = rows.filter(r => {
      const ms = rowMs(t, r);
      return !end || (isFinite(ms) && ms >= start && ms <= end + 1000);
    });
    const flows = {};
    const add = (token, dir, ada) => {
      const name = t.tickerName(token);
      // Derived rows only. Pool ids (LP:minswap_v2) are not tickers. Schema rows can still name a phrase.
      if (!name || !/^[A-Za-z][A-Za-z0-9-]{0,15}$/.test(name) || name.toUpperCase() === "ADA") return;
      if (ada == null || !isFinite(ada) || ada <= 0) return;
      const row = flows[name] || { token: name, inn: 0, out: 0 };
      if (dir === "out") row.out += ada; else row.inn += ada;
      flows[name] = row;
    };
    windowRows.forEach(r => {
      const kind = String(r.kind || "");
      const token = r.token;
      const ada = t.num(r.ada_leg);
      const stamp = String(r.time || "");
      let dir = null;
      if (/lp_remove|sell/.test(kind)) dir = "out";
      else if (/lp_add|(^|_)buy/.test(kind)) dir = "in";
      else if (/transfer_out/.test(kind)) dir = ada != null ? "out" : null;
      else if (/transfer_in/.test(kind)) dir = ada != null ? "in" : null;
      else if (/order_fill|swap/.test(kind)) {
        const near = windowRows.filter(s => s !== r && String(s.time || "") === stamp && /order_open/.test(String(s.kind || "")));
        const name = t.tickerName(token);
        const openedToken = name && near.some(s => t.tickerName(s.token) === name);
        const openedAda = near.some(s => String(s.token || "").toUpperCase() === "ADA");
        if (openedToken) dir = "out";
        else if (openedAda) dir = "in";
        else if (t.num(r.qty) != null && t.num(r.qty) < 0) dir = "out";
        else if (ada != null) dir = "in";
      }
      if (dir) add(token, dir, ada);
    });
    const tag = t.safeTag(v.whale && v.whale.whale_tag);
    const extra = [].concat((v.big && v.big.moves) || [], (v.log && v.log.liquidity_flows && v.log.liquidity_flows.big_moves) || []);
    extra.forEach(m => {
      if (!tag || t.safeTag(m.tag) !== tag) return;
      const dir = /out|sell/.test(String(m.direction || "")) ? "out" : "in";
      add(m.ticker, dir, t.num(m.ada));
    });
    const list = Object.keys(flows).map(k => flows[k]);
    return {
      out: list.filter(r => r.out > 0).sort((a, b) => b.out - a.out).slice(0, 6).map(r => ({ token: r.token, detail: "", ada: r.out })),
      inn: list.filter(r => r.inn > 0).sort((a, b) => b.inn - a.inn).slice(0, 6).map(r => ({ token: r.token, detail: "", ada: r.inn })),
      from: start ? new Date(start).toISOString() : "",
      to: end ? new Date(end).toISOString() : "",
      derived: true
    };
  }
  function normFlows(t, list) {
    return (list || []).map(item => {
      if (!item || typeof item !== "object") return null;
      const token = t.tickerName(item.token || item.ticker);
      if (!token) return null;
      return { token, detail: clean(t, item.detail || ""), ada: t.num(item.ada) };
    }).filter(Boolean).slice(0, 8);
  }
  function normTrends(t, list) {
    const ok = { accumulating: 1, trimming: 1, exiting: 1, flat: 1 };
    return (list || []).map(item => {
      if (!item || typeof item !== "object") return null;
      const token = t.tickerName(item.token || item.ticker);
      const trend = String(item.trend || "").toLowerCase();
      if (!token || !ok[trend]) return null;
      return { token, trend, detail: clean(t, item.detail || "") };
    }).filter(Boolean).slice(0, 8);
  }
  function trendsFrom(out, inn) {
    const map = {};
    out.forEach(r => { map[r.token] = { token: r.token, out: r.ada || 0, inn: 0 }; });
    inn.forEach(r => {
      const row = map[r.token] || { token: r.token, out: 0, inn: 0 };
      row.inn = r.ada || 0;
      map[r.token] = row;
    });
    return Object.keys(map).map(k => {
      const r = map[k];
      let trend = "flat";
      if (r.out > 0 && r.inn === 0 && r.out >= 500) trend = "exiting";
      else if (r.out > r.inn * 1.25 && r.out > 0) trend = "trimming";
      else if (r.inn > r.out * 1.25 && r.inn > 0) trend = "accumulating";
      return { token: r.token, trend, detail: "" };
    }).slice(0, 8);
  }
  function takeawayFrom(out, inn) {
    const o = out.slice(0, 3).map(r => r.token).join(", ");
    const i = inn.slice(0, 3).map(r => r.token).join(", ");
    if (o && i) return "Selling " + o + " → rotating into " + i + ".";
    if (o) return "Selling " + o + ".";
    if (i) return "Adding to " + i + ".";
    return "";
  }
  function whoName(t, v, block) {
    const named = block && t.animalName(block.who);
    if (named) return named;
    const tag = t.safeTag(v.whale && v.whale.whale_tag);
    const labeled = tag ? t.labelTag(tag) : "";
    return labeled || "Whale";
  }
  function rotationModel(t, v, v2) {
    const block = asObj(v2.rotation);
    if (block) {
      const out = normFlows(t, block.out);
      const inn = normFlows(t, block.in || block.inn);
      return {
        who: whoName(t, v, block),
        window: clean(t, block.window || ""),
        from: block.from || "",
        to: block.to || "",
        out, inn,
        takeaway: clean(t, block.takeaway || "") || takeawayFrom(out, inn),
        trends: normTrends(t, block.trends).length ? normTrends(t, block.trends) : trendsFrom(out, inn)
      };
    }
    const d = deriveRotation(t, v);
    return {
      who: whoName(t, v, null),
      window: "",
      from: d.from,
      to: d.to,
      out: d.out,
      inn: d.inn,
      takeaway: takeawayFrom(d.out, d.inn),
      trends: trendsFrom(d.out, d.inn)
    };
  }
  function flowBars(t, list, dir) {
    if (!list.length) return "";
    const max = Math.max.apply(null, list.map(r => r.ada || 0).concat([1]));
    return list.map(r => {
      const w = r.ada ? Math.max(8, Math.round(r.ada / max * 100)) : 10;
      const detail = r.detail ? " " + t.esc(r.detail) : "";
      const ada = r.ada != null ? `<span>~${t.esc(t.fmt(r.ada, r.ada >= 100 ? 0 : 1))}${r.ada >= 100 ? " ADA" : ""}</span>` : "";
      const sm = (r.ada == null || r.ada < max * 0.12) ? " sm" : "";
      return `<div class="cv2-fb ${dir}${sm}" style="--w:${w}%"><b>${t.esc(r.token)}</b>${detail}${ada}</div>`;
    }).join("");
  }
  function trendTags(t, list) {
    if (!list.length) return "";
    const meta = {
      accumulating: ["up", "▲", "accumulating"],
      trimming: ["dn", "▼", "trimming"],
      exiting: ["ex", "✖", "exiting"],
      flat: ["flat", "▶", ""]
    };
    const rank = { accumulating: 0, flat: 1, trimming: 2, exiting: 3 };
    const rows = list.slice().sort((a, b) => rank[a.trend] - rank[b.trend]);
    return `<div class="cv2-trend">${rows.map(r => {
      const m = meta[r.trend];
      const tail = r.detail || m[2];
      return `<span class="${m[0]}">${m[1]} ${t.esc(r.token)}${tail ? " " + t.esc(tail) : ""}</span>`;
    }).join("")}</div>`;
  }
  function rotationCard(t, rot) {
    if (!rot.out.length && !rot.inn.length) return emptyLine("No data yet. Money out / money in fills from the whale ledger, or from <code>cardano_v2.rotation</code>.");
    const n = Math.min(6, Math.max(rot.out.length, rot.inn.length, 1));
    const arrows = Array(n).fill("<i></i>").join("");
    const whenBits = [];
    if (rot.window) whenBits.push(t.esc(rot.window));
    else if (rot.from || rot.to) whenBits.push("Last 24h");
    if (rot.from && rot.to) whenBits.push(t.esc(t.when(rot.from) || "") + " → " + t.esc(t.when(rot.to) || ""));
    const take = rot.takeaway ? `<div class="cv2-take">➜ ${t.esc(rot.takeaway)}</div>` : "";
    return `<article class="cv2-card"><div class="cv2-row"><span class="cv2-tk sm">🐋 ${t.esc(rot.who)} rotation</span></div>${whenBits.length ? `<p class="cv2-sub">${whenBits.join(" · ")}</p>` : ""}<div class="cv2-flow"><div class="cv2-col"><div class="cv2-ch out">MONEY OUT</div>${flowBars(t, rot.out, "out") || emptyLine("No data yet")}</div><div class="cv2-mid">${arrows}</div><div class="cv2-col"><div class="cv2-ch in">MONEY IN</div>${flowBars(t, rot.inn, "in") || emptyLine("No data yet")}</div></div>${take}${trendTags(t, rot.trends)}</article>`;
  }

  function safeWallets(t, list) {
    const out = [];
    (Array.isArray(list) ? list : []).forEach(item => {
      const s = t.safeTag(item);
      if (s && /^w-[0-9a-f]{8}$/.test(s) && out.indexOf(s) < 0) out.push(s);
    });
    return out;
  }
  function deriveSwarms(t, flags) {
    const wallets = {};
    (flags || []).forEach(f => {
      if (!f || f.kind !== "repeat_early_buyer") return;
      const w = t.safeTag(f.wallet);
      if (!w || !/^w-/.test(w)) return;
      const launches = (f.launches || []).map(n => t.tickerName(n)).filter(Boolean);
      if (launches.length < 2) return;
      wallets[w] = launches;
    });
    const ids = Object.keys(wallets);
    if (ids.length < 3) return [];
    const used = {};
    const groups = [];
    ids.slice().sort((a, b) => wallets[b].length - wallets[a].length).forEach(seed => {
      if (used[seed] || groups.length >= 3) return;
      const set = {};
      wallets[seed].forEach(n => { set[n] = 1; });
      const members = [seed];
      ids.forEach(other => {
        if (other === seed || used[other]) return;
        const share = wallets[other].filter(n => set[n]).length;
        if (share >= 2 && share / wallets[other].length >= 0.5) {
          members.push(other);
          wallets[other].forEach(n => { set[n] = 1; });
        }
      });
      if (members.length < 3) return;
      members.forEach(m => { used[m] = 1; });
      const counts = {};
      members.forEach(m => wallets[m].forEach(n => { counts[n] = (counts[n] || 0) + 1; }));
      const last = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b)).slice(0, 8);
      groups.push({
        name: "Swarm group " + "ABC"[groups.length],
        label: "repeat early buyers",
        wallets: members.length,
        last_seen: last,
        launches: last.slice(0, 6).map(n => ({ ticker: n, at: "" })),
        note: SWARM_NOTE
      });
    });
    return groups;
  }
  function swarmModel(t, v, v2) {
    if (Array.isArray(v2.swarms)) {
      return v2.swarms.map(g => {
        if (!g || typeof g !== "object") return null;
        const name = clean(t, g.name || "Swarm group");
        if (!name || name.length > 40) return null;
        const count = Array.isArray(g.wallets) ? safeWallets(t, g.wallets).length : (t.num(g.wallets) != null ? t.num(g.wallets) : null);
        const last = (Array.isArray(g.last_seen) ? g.last_seen : []).map(n => t.tickerName(n)).filter(Boolean).slice(0, 8);
        const launches = (Array.isArray(g.launches) ? g.launches : []).map(item => {
          if (!item || typeof item !== "object") return null;
          const ticker = t.tickerName(item.ticker);
          if (!ticker) return null;
          return { ticker, at: item.at || "" };
        }).filter(Boolean).slice(0, 8);
        return { name, label: clean(t, g.label || "repeat early buyers"), wallets: count, last_seen: last, launches, note: clean(t, g.note || "") || SWARM_NOTE, example: g.example === true };
      }).filter(Boolean).slice(0, 4);
    }
    return deriveSwarms(t, v.flags);
  }
  function swarmCards(t, groups) {
    if (!groups.length) return emptyLine("No data yet. Swarm groups fill from repeat early-buyer flags, or from <code>cardano_v2.swarms</code>.");
    return groups.map(g => {
      const seen = g.last_seen.length ? g.last_seen.join(", ") : "";
      const count = g.wallets != null ? `${t.esc(t.fmt(g.wallets, 0))} wallets` : "";
      const head = [count, seen ? "last seen in " + seen : ""].filter(Boolean).join(" · ");
      const tl = g.launches.length ? `<p class="cv2-sub">Last launches${g.example ? ' <span class="cv2-ex">example times</span>' : ""}</p><div class="cv2-tl">${g.launches.map((item, i) => `<div class="${i === 0 ? "new" : ""}"><b>${t.esc(item.ticker)}</b>${item.at ? t.esc(t.when(item.at) || "") : ""}</div>`).join("")}</div>` : "";
      return `<article class="cv2-card"><div class="cv2-row"><span class="cv2-tk sm">👥 ${t.esc(g.name)}</span><span class="cv2-pill r">${t.esc(g.label || "repeat early buyers")}</span></div>${head ? `<p class="cv2-sent">${t.esc(head)}</p>` : ""}${tl}<div class="cv2-legend">${t.esc(g.note)}</div></article>`;
    }).join("");
  }
  function sniperModel(t, v, v2) {
    const block = asObj(v2.snipers) || asObj(v2.sniper_exits);
    if (block) {
      const tickers = (Array.isArray(block.tickers) ? block.tickers : []).map(n => t.tickerName(n)).filter(Boolean).slice(0, 8);
      return { tickers, note: clean(t, block.note || "") || "first-block buyers fully exited." };
    }
    const tickers = [];
    (v.flags || []).forEach(f => {
      if (!f || f.kind !== "sniper_full_exit") return;
      const name = t.tickerName(f.ticker);
      if (name && tickers.indexOf(name) < 0) tickers.push(name);
    });
    return { tickers: tickers.slice(0, 8), note: "first-block buyers fully exited." };
  }
  function sniperCard(t, sn) {
    if (!sn.tickers.length) return emptyLine("No data yet. Sniper exits fill from sniper flags, or from <code>cardano_v2.snipers</code>.");
    return `<article class="cv2-card"><div class="cv2-row"><span class="cv2-tk sm">🎯 Sniper exits</span><span class="cv2-pill y">sold everything</span></div><p class="cv2-sent">${t.esc(sn.tickers.join(", "))}: ${t.esc(sn.note)}</p></article>`;
  }
  function hotModel(t, v, v2, rot) {
    if (Array.isArray(v2.hot)) {
      return v2.hot.map(item => {
        if (!item || typeof item !== "object") return null;
        const ticker = t.tickerName(item.ticker);
        const line = clean(t, item.line || "");
        if (!ticker || !line) return null;
        return { ticker, line, note: clean(t, item.note || "") };
      }).filter(Boolean).slice(0, 3);
    }
    const swarmish = {};
    (v.flags || []).forEach(f => {
      if (!f) return;
      if (f.kind === "coordinated_buys" || f.kind === "fresh_wallet_swarm" || f.kind === "identical_size_buys") {
        const name = t.tickerName(f.ticker);
        if (name) swarmish[name.toUpperCase()] = name;
      }
    });
    const hits = [];
    (rot.inn || []).forEach(r => {
      if (!swarmish[r.token.toUpperCase()] || hits.length >= 2) return;
      const ada = r.ada != null ? `~${t.fmt(r.ada, 0)} ADA` : "size not published";
      hits.push({
        ticker: r.token,
        line: `${rot.who} added ${ada} and a coordinated group bought at launch.`,
        note: "Both overlap here. Watch whether holders stay after the group leaves."
      });
    });
    return hits;
  }
  function hotCards(t, rows) {
    if (!rows.length) return emptyLine("No data yet. Hot is a token where the whale and a swarm overlap, from <code>cardano_v2.hot</code> when the desk publishes it.");
    return rows.map(r => `<article class="cv2-card cv2-hotcard"><div class="cv2-row"><span class="cv2-tk sm">🔥 Hot right now</span></div><p class="cv2-sent"><b class="cv2-name">${t.esc(r.ticker)}</b>: ${t.esc(r.line)}</p>${r.note ? `<p class="cv2-sub">${t.esc(r.note)}</p>` : ""}</article>`).join("");
  }
  function moneySection(t, v, v2) {
    const rot = rotationModel(t, v, v2);
    return section("money", "WHERE THE MONEY IS HEADING", "Who is moving money, and where it is going.",
      netCards(t, v.whales) + rotationCard(t, rot) + swarmCards(t, swarmModel(t, v, v2)) + sniperCard(t, sniperModel(t, v, v2)) + hotCards(t, hotModel(t, v, v2, rot)));
  }

  function entryProgress(t, holders, pool, barH, barP) {
    const parts = [];
    if (holders != null && barH > 0) parts.push(Math.min(1, holders / barH));
    if (pool != null && barP > 0) parts.push(Math.min(1, pool / barP));
    if (!parts.length) return null;
    return parts.reduce((a, b) => a + b, 0) / parts.length;
  }
  function radarSection(t, v2) {
    const barH = t.num(v2.entry_bar && v2.entry_bar.holders) || ENTRY_HOLDERS;
    const barP = t.num(v2.entry_bar && v2.entry_bar.pool_ada) || ENTRY_POOL;
    const pads = Array.isArray(v2.launchpads) ? v2.launchpads : [];
    const rows = Array.isArray(v2.launches) ? v2.launches : [];
    const stats = pads.length ? `<div class="cv2-stats">${pads.slice(0, 4).map(p => {
      const label = (p.label && clean(t, p.label)) || padBadge(t, p.id || p.name) || "Other";
      const launches = t.num(p.launches) != null ? t.num(p.launches) : t.num(p.launches_7d);
      const grad = t.num(p.graduated) != null ? t.num(p.graduated) : t.num(p.graduated_7d);
      const big = launches === 0 || launches == null ? t.esc(launches == null ? "–" : "0") : `${t.esc(t.fmt(launches, 0))} / ${t.esc(t.fmt(grad || 0, 0))}`;
      const cap = launches ? "launches / graduated" : "launches";
      return `<div class="cv2-stat"><b>${big}</b>${t.esc(label)}<br>${cap}</div>`;
    }).join("")}</div><p class="cv2-sub">Last 7 days.</p>` : emptyLine("No data yet. 7-day launchpad counts go in <code>cardano_v2.launchpads</code>.");
    const cards = rows.length ? rows.slice(0, 12).map(row => radarCard(t, row, barH, barP)).filter(Boolean).join("") : emptyLine("No data yet. Launch rows go in <code>cardano_v2.launches</code>.");
    const sub = `Entry bar: ${t.fmt(barH, 0)} holders AND ${compact(t, barP)} ADA pool side, with no swarm flags.`;
    return section("radar", "NEW LAUNCH RADAR", sub, stats + cards);
  }
  function radarCard(t, row, barH, barP) {
    if (!row || typeof row !== "object") return "";
    const name = t.tickerName(row.ticker);
    if (!name) return "";
    const holders = t.num(row.holders);
    const pool = t.num(row.pool_ada);
    const swarm = row.swarm === true;
    const sniper = row.sniper === true;
    const prog = entryProgress(t, holders, pool, barH, barP);
    const passed = holders != null && pool != null && holders >= barH && pool >= barP && !swarm && !sniper;
    let status = "";
    let cls = "";
    if (swarm) {
      status = sniper ? "Swarm launch + first-block sniper → Pass" : "Swarm launch → Pass";
      cls = "pass";
    } else if (passed) {
      status = "🚨 Getting traction · passed the bar, no swarm flags";
      cls = "go";
    } else if (prog != null) {
      status = `Watching · ${Math.round(prog * 100)}% of the way to the bar`;
      cls = "watch";
    } else status = "Watching";
    const width = passed ? 100 : prog == null ? 0 : Math.max(4, Math.round(prog * 100));
    const age = clean(t, row.age || "");
    const ageHtml = age && age.length <= 16 ? t.esc(age) : "–";
    const ex = flag => row[flag] === true ? ' <span class="cv2-ex">example</span>' : "";
    const holdTxt = holders == null ? "–" : t.fmt(holders, 0);
    const poolTxt = pool == null ? "–" : compact(t, pool);
    const badge = padBadge(t, row.launchpad, { radar: true });
    const grad = row.graduated === true ? `<span class="cv2-badge">graduated</span>` : "";
    const example = row.example === true ? `<span class="cv2-ex">example</span>` : "";
    return `<article class="cv2-card${passed ? " cv2-hotcard" : ""}"><div class="cv2-row"><span class="cv2-tk">${t.esc(name)}</span>${badge ? `<span class="cv2-badge">${t.esc(badge)}</span>` : ""}${grad}${example}</div><div class="cv2-kv"><div>Age<b>${ageHtml}</b></div><div>Holders<b>${t.esc(holdTxt)}${ex("holders_example")}</b></div><div>Pool ADA<b>${t.esc(poolTxt)}${ex("pool_example")}</b></div></div><div class="cv2-bar"><div class="cv2-fill${passed ? " go" : ""}" style="width:${width}%"></div></div><div class="cv2-st ${cls}">${t.esc(status)}</div></article>`;
  }

  function clusterFromFlags(t, flags) {
    const by = {};
    const ensure = name => {
      const key = name.toUpperCase();
      return by[key] || (by[key] = { ticker: name, wallets: [], kinds: {}, pct: null, repeat: 0 });
    };
    (flags || []).forEach(f => {
      if (!f) return;
      if (f.kind === "coordinated_buys" || f.kind === "fresh_wallet_swarm" || f.kind === "identical_size_buys") {
        const name = t.tickerName(f.ticker);
        if (!name) return;
        const row = ensure(name);
        row.kinds.coordinated = true;
        safeWallets(t, t.flagTags(f)).forEach(tag => { if (row.wallets.indexOf(tag) < 0) row.wallets.push(tag); });
      } else if (f.kind === "sniper_full_exit") {
        const name = t.tickerName(f.ticker);
        if (!name) return;
        const row = ensure(name);
        row.kinds.sniper = true;
        safeWallets(t, t.flagTags(f)).forEach(tag => { if (row.wallets.indexOf(tag) < 0) row.wallets.push(tag); });
      } else if (f.kind === "holder_concentration") {
        const name = t.tickerName(f.ticker);
        if (!name) return;
        const row = ensure(name);
        row.kinds.concentration = true;
        const pct = t.num(f.top_addr_pct) != null ? t.num(f.top_addr_pct) : t.num(f.top_cluster_pct);
        if (pct != null) row.pct = pct;
        const tag = t.safeTag(f.top_cluster);
        if (tag && /^w-/.test(tag) && row.wallets.indexOf(tag) < 0) row.wallets.push(tag);
      } else if (f.kind === "repeat_early_buyer") {
        const w = t.safeTag(f.wallet);
        (f.launches || []).forEach(n => {
          const name = t.tickerName(n);
          if (!name) return;
          const row = ensure(name);
          row.repeat += 1;
          if (w && /^w-/.test(w) && row.wallets.indexOf(w) < 0) row.wallets.push(w);
        });
      }
    });
    return Object.keys(by).map(k => by[k]).filter(r => r.kinds.coordinated || r.kinds.sniper || r.kinds.concentration).map(r => {
      let tone = "y";
      let pill = "sniper full exit";
      let summary = "A first-minute buyer sold everything";
      if (r.kinds.coordinated) {
        tone = "r";
        pill = "🔴 coordinated buys";
        summary = "Coordinated buys" + (r.repeat ? ` + ${r.repeat} repeat early buyers` : "");
      } else if (r.kinds.concentration && !r.kinds.sniper) {
        tone = "r";
        pill = "holder concentration";
        summary = r.pct != null ? `Top wallet holds ${t.fmt(r.pct, 1)}% of supply` : "One wallet holds a big slice of supply";
      } else if (r.kinds.sniper) {
        pill = "sniper full exit";
        summary = "A first-minute buyer sold everything";
      }
      return { ticker: r.ticker, tone, pill, summary, wallets: r.wallets.slice(0, 12) };
    }).sort((a, b) => (a.tone === b.tone ? a.ticker.localeCompare(b.ticker) : (a.tone === "r" ? -1 : 1))).slice(0, 12);
  }
  function clusterModel(t, v, v2) {
    if (Array.isArray(v2.clusters)) {
      return v2.clusters.map(c => {
        if (!c || typeof c !== "object") return null;
        const ticker = t.tickerName(c.ticker);
        const summary = clean(t, c.summary || "");
        if (!ticker || !summary) return null;
        const tone = c.tone === "y" || c.tone === "g" ? c.tone : "r";
        return { ticker, tone, pill: clean(t, c.pill || "flag"), summary, wallets: safeWallets(t, c.wallets).slice(0, 12) };
      }).filter(Boolean).slice(0, 12);
    }
    return clusterFromFlags(t, v.flags);
  }
  function clusterSection(t, v, v2) {
    const rows = clusterModel(t, v, v2);
    const cards = rows.length ? rows.map(r => {
      const wallets = r.wallets.length ? `<details><summary>show wallets (${r.wallets.length})</summary>${r.wallets.map(w => `<span class="cv2-w">${t.esc(w)}</span>`).join("")}</details>` : "";
      return `<article class="cv2-card"><div class="cv2-row"><span class="cv2-tk">${t.esc(r.ticker)}</span><span class="cv2-pill ${r.tone}">${t.esc(r.pill)}</span></div><p class="cv2-sent">${t.esc(r.summary)}</p>${wallets}</article>`;
    }).join("") : emptyLine("No data yet. One card per token once flags name a launch.");
    const legend = `<div class="cv2-legend">${CLUSTER_NOTE.map(pair => `<div><b>${pair[0]}</b>: ${pair[1]}</div>`).join("")}</div>`;
    return section("clusters", "EARLY-BUYER CLUSTERS", "One card per token. Tap to see the wallets.", cards + legend);
  }

  function heatLabel(t, tag) {
    const s = String(tag || "").trim().toLowerCase();
    if (/^[0-9a-f]{4}$/.test(s)) return s;
    const w = t.safeTag(s);
    const m = w && w.match(/^w-([0-9a-f]{8})$/);
    return m ? m[1].slice(0, 4) : "";
  }
  function heatFromFlags(t, flags) {
    const early = ["repeat_early_buyer", "watch_tags_first_block", "coordinated_buys", "fresh_wallet_swarm", "identical_size_buys"];
    const cell = {};
    const cols = {};
    (flags || []).forEach(f => {
      if (!f || early.indexOf(f.kind) < 0) return;
      const launches = f.kind === "repeat_early_buyer" && Array.isArray(f.launches) && f.launches.length ? f.launches : (f.ticker ? [f.ticker] : []);
      const tags = t.flagTags(f).filter(tag => /^w-/.test(tag));
      const ada = t.flagAda(f);
      launches.forEach(launch => {
        const col = t.tickerName(launch);
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
      if (!f || f.kind !== "sniper_full_exit") return;
      const col = t.tickerName(f.ticker);
      t.flagTags(f).forEach(tag => { if (col && /^w-/.test(tag)) snipers[tag + "|" + col] = true; });
    });
    const colList = Object.keys(cols).sort((a, b) => cols[b] - cols[a] || a.localeCompare(b)).slice(0, 12);
    const score = {};
    Object.keys(cell).forEach(k => { score[cell[k].tag] = (score[cell[k].tag] || 0) + (cell[k].ada || cell[k].n); });
    const rowList = Object.keys(score).sort((a, b) => score[b] - score[a]).slice(0, 24);
    return {
      tokens: colList,
      rows: rowList.map(tag => {
        const cells = {};
        colList.forEach(col => {
          const hit = cell[tag + "|" + col];
          if (!hit) return;
          cells[col] = { ada: hit.ada || 0, n: hit.n, sniper: !!snipers[tag + "|" + col] };
        });
        return { label: heatLabel(t, tag), cells };
      }).filter(r => r.label)
    };
  }
  function heatFromSchema(t, block) {
    const tokens = (block.tokens || []).map(n => t.tickerName(n)).filter(Boolean).slice(0, 16);
    const rows = (block.wallets || block.rows || []).map(row => {
      if (!row || typeof row !== "object") return null;
      const label = heatLabel(t, row.tag || row.label);
      if (!label) return null;
      const cells = {};
      const src = row.cells && typeof row.cells === "object" ? row.cells : {};
      tokens.forEach(col => {
        const c = src[col];
        if (!c || typeof c !== "object") return;
        const ada = t.num(c.ada);
        const n = t.num(c.n);
        if (ada == null && n == null && c.sniper !== true) return;
        cells[col] = { ada: ada || 0, n: n || 0, sniper: c.sniper === true };
      });
      return { label, cells };
    }).filter(Boolean).slice(0, 24);
    return { tokens, rows };
  }
  function heatLevel(weight, max) {
    if (!weight) return 0;
    const p = weight / (max || 1);
    if (p >= 0.7) return 3;
    if (p >= 0.3) return 2;
    return 1;
  }
  function heatSection(t, v, v2) {
    const model = asObj(v2.heat) ? heatFromSchema(t, v2.heat) : heatFromFlags(t, v.flags);
    if (!model.rows.length || !model.tokens.length) {
      return section("heat", "HEAT GRID", "Wallet × launch, ADA spent. Swipe sideways for more tokens.", emptyLine("No data yet. The grid fills when flags name a w- tag and a launch."));
    }
    let max = 1;
    model.rows.forEach(r => model.tokens.forEach(c => {
      const cell = r.cells[c];
      if (!cell) return;
      max = Math.max(max, cell.ada || cell.n || 0);
    }));
    const head = model.tokens.map(c => `<th>${t.esc(c)}</th>`).join("");
    const body = model.rows.map((r, i) => {
      const cells = model.tokens.map(c => {
        const hit = r.cells[c];
        if (!hit || (!(hit.ada || hit.n) && !hit.sniper)) return `<td class="e">·</td>`;
        const weight = hit.ada || hit.n || 0;
        const level = heatLevel(weight, max);
        const text = hit.ada ? compact(t, hit.ada) : (hit.n ? String(hit.n) : "·");
        const ring = hit.sniper ? " ring" : "";
        return `<td class="h${level}${ring}">${t.esc(text)}</td>`;
      }).join("");
      return `<tr class="${i >= 8 ? "cv2-more" : ""}"><th class="pin">${t.esc(r.label)}</th>${cells}</tr>`;
    }).join("");
    const extra = model.rows.length > 8;
    const toggle = extra ? `<button type="button" class="cv2-tog" data-cv2-heat data-n="${model.rows.length}">Show all wallets (${model.rows.length})</button>` : "";
    const table = `<div class="cv2-heatwrap" id="cv2-heat"><table class="cv2-heat"><thead><tr><th class="pin">wallet</th>${head}</tr></thead><tbody>${body}</tbody></table></div>${toggle}<div class="cv2-legend"><div>Brighter blue = more ADA spent. · = did not buy. A plain count is how many early buys, when ADA was not recorded.</div><div><span class="cv2-bad">Red ring</span> = sniper full exit (bought first, sold everything).</div><div>Top 8 wallets shown; 4-char tags are short wallet labels.</div></div>`;
    return section("heat", "HEAT GRID", "Wallet × launch, ADA spent. Swipe sideways for more tokens.", table);
  }

  function moreSection(t, v) {
    const risk = v.risk && v.risk.length ? `<div class="risk-legend"><b>Risk score</b>${v.risk.map(t.riskBadge).join("")}</div>` : "";
    const flagged = t.flaggedThisRun(v.log, { new: v.flags || [] }, v.kills);
    const flagRow = flagged.length ? `<div class="flag-legend"><b>Flagged this run</b></div><div class="filters">${flagged.map(f => `<span class="chip ${f.tone === "red" ? "bad" : "caution"}">${t.esc(f.ticker)}</span>`).join("")}</div>` : "";
    const devs = (v.devs || []).length ? `<div class="dev-cards">${v.devs.slice(0, 16).map(e => {
      const name = t.tickerName(e.ticker) || "–";
      return `<article class="dev-card"><b class="mono">${t.esc(name)}</b><span class="risk-score ${t.riskTone(e.risk)}">${t.esc(t.sanitize(e.risk) || "unscored")}</span> ${t.freshFunded(e) ? t.chip("fresh funded", "caution") : ""}${t.tagChip(e.dev_tag) || ""}</article>`;
    }).join("")}</div>` : "";
    const books = [t.paperBoard(v.paper), t.paperBoard(v.pump)];
    const bookN = books[0].open + books[0].closed + books[1].open + books[1].closed;
    const bookHtml = bookN ? `<div class="donuts">${t.donut(books[0].wins, books[0].losses, "Solada " + (books[0].unit || "ADA"))}${t.donut(books[1].wins, books[1].losses, "Pump " + (books[1].unit || ""))}</div><p class="dim">Open ${books[0].open + books[1].open} · closed ${books[0].closed + books[1].closed}</p>` : "";
    const bits = [];
    if (risk || flagRow) bits.push(`<div class="cv2-moreblock">${risk}${flagRow}</div>`);
    if (devs) bits.push(devs);
    if (bookHtml) bits.push(bookHtml);
    if (!bits.length) return "";
    return `<details class="cv2-fold"><summary>More from this run</summary>${bits.join("")}</details>`;
  }

  root.CardanoBoard = { render };
})(typeof window !== "undefined" ? window : globalThis);
