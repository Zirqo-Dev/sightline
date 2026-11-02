/* Cardano tab. Two boards from desk files: fresh runners, then high-cap names.
   Sentinel cards render only for an open SW-01 or EST-01 ticket.
   A missing figure is left out or marked "?". Nothing here is a buy button. */
(function (root) {
  const K_HOLDERS = 120;
  const K_POOL = 25000;
  const EST_PAD = { established: 1, minswap: 1 };

  function render(t, data) {
    const d = data || {};
    const model = build(t, d);
    const runners = box(t, "runners", "Top 10 Runners", "Mostly new", model.runners, model.picks.filter(p => p.test === "SW-01"), model);
    const established = box(t, "established", "Top 10 Established", "High cap", model.established, model.picks.filter(p => p.test === "EST-01"), model);
    const sheets = model.sheets.map(row => sheet(t, row, model)).join("");
    const first = (model.runners[0] || model.established[0] || model.picks[0] || {}).ticker || "";
    const whyChip = first
      ? `<button type="button" class="sl-chip" data-ada-open="${t.esc(first)}">Why ${t.esc(first)}?</button>`
      : "";
    return `<div class="ada" id="ada-board">
      <div class="sl-mascot">
        <svg viewBox="0 0 40 40" width="18" height="18" aria-hidden="true"><path d="M27 3 L33 9" fill="none" stroke="#888" stroke-width="3" stroke-linecap="round"/><rect x="4" y="8" width="32" height="27" rx="12" fill="#000" stroke="#EAECEF" stroke-width="1.6"/><rect x="9" y="14" width="22" height="13" rx="6.5" fill="#cfcfd4"/><path d="M13 21q2.5-2.5 5 0M22 21q2.5-2.5 5 0" stroke="#000" stroke-width="2.2" fill="none" stroke-linecap="round"/></svg>
        <p class="sl-status">${t.esc(model.status)}</p>
        <time class="sl-meta"${model.asof ? ` datetime="${t.esc(model.asof)}"` : ""}>${t.esc(model.clock)}</time>
      </div>
      <div class="ada-grid">
        ${runners}
        ${established}
        <aside class="sl-sheet-host" id="ada-detail">
          <div class="sl-grab" aria-hidden="true"></div>
          <button type="button" class="sl-close" data-ada-close>Close</button>
          <p class="sl-empty" id="ada-hint">Tap a row for the checklist.</p>
          ${sheets}
        </aside>
      </div>
      <button type="button" class="sl-scrim" data-ada-close hidden aria-label="Close details"></button>
      <p class="sl-note">A Sentinel pick is a research pick, not advice. ▲ means holders and volume both rose over the last 6h.</p>
      <div class="sl-chips"><a class="sl-chip" href="#/briefs">Briefs</a>${whyChip}</div>
      <p class="sl-note">Research only · you trade elsewhere</p>
    </div>`;
  }

  function box(t, id, title, sub, rows, picks, model) {
    const cards = picks.length
      ? picks.map(p => pickCard(t, p, model)).join("")
      : `<p class="sl-empty">No Sentinel pick right now</p>`;
    const body = rows.length
      ? rows.map((row, i) => rowHtml(t, row, i + 1)).join("")
      : `<p class="sl-empty">${id === "runners" ? "No runners on this run." : "No established names on this run."}</p>`;
    const win = model.winLabel ? `${t.esc(model.winLabel)} · ` : "";
    return `<section class="sl-box" data-board="${id}">
      <header class="sl-box-h"><span class="sl-box-title">${t.esc(title)} <em>${t.esc(sub)}</em></span><span class="sl-meta">${win}${t.esc(model.clock)}</span></header>
      ${cards}
      <div class="sl-rows">${body}</div>
    </section>`;
  }

  function pickCard(t, pick, model) {
    const row = model.by[pick.ticker] || pick.row || { ticker: pick.ticker };
    const terms = pick.terms;
    const term = terms && terms.size ? `◇ ${t.esc(String(terms.size))} ₳ paper · ${t.esc(terms.label || "")}` : "";
    const blurb = pick.blurb || "Open paper ticket.";
    return `<article class="sl-pick" data-test="${t.esc(pick.test)}">
      <div class="sl-pick-top">
        <span class="sl-kicker">Sentinel's pick</span>
        ${logo(t, pick.ticker, true)}
        <span class="sl-tk">${t.esc(pick.ticker)}</span>
        <span class="sl-grow"></span>
        ${pctHtml(row.pct)}
      </div>
      <p class="sl-pick-line">${t.esc(blurb)}</p>
      <div class="sl-pick-foot"><span>${term}</span><button type="button" data-ada-open="${t.esc(pick.ticker)}">Why ›</button></div>
    </article>`;
  }

  function rowHtml(t, row, n) {
    const pill = row.pill ? `<span class="sl-pill ${row.pill.cls}">${t.esc(row.pill.text)}</span>` : "";
    const steam = row.steam ? `<span class="sl-pill sl-pill-accent" data-steam="1" title="Holders and volume both rose over the last 6h">▲</span>` : "";
    const sub = row.sub ? `<div class="sl-sub">${t.esc(row.sub)}</div>` : "";
    return `<button type="button" class="sl-row" data-ada-open="${t.esc(row.ticker)}" data-tk="${t.esc(row.ticker)}">
      <span class="sl-rank">${n}</span>
      ${logo(t, row.ticker, false)}
      <span class="sl-id"><span class="sl-id-top"><span class="sl-tk">${t.esc(row.ticker)}</span>${pill}${steam}</span>${sub}</span>
      <span class="sl-end">${spark(row.series, row.pct)}${pctHtml(row.pct)}</span>
    </button>`;
  }

  function sheet(t, row, model) {
    const pick = model.pickBy[row.ticker];
    const kvs = [
      kv("Holders", holdersKv(row)),
      kv("Pool ADA", row.pool == null ? "" : compact(row.pool)),
      kv("Kill dist", killKv(row.killPct)),
      kv("Launch", row.padLabel || "")
    ].filter(Boolean).join("");
    const why = pick ? `<div class="sl-why">
      <div class="sl-why-h"><span class="sl-kicker">Why Sentinel would bet</span>${dots(pick.score)}</div>
      <p>${t.esc(pick.blurb || "Open paper ticket. The log has no thesis line.")}</p>
      <div class="sl-why-f"><span>${t.esc(pick.conf || "")}${pick.terms && pick.terms.size ? ` · ◇ ${t.esc(String(pick.terms.size))} ₳ paper · ${t.esc(pick.terms.label || "")}` : ""}</span><span>Not advice</span></div>
    </div>` : "";
    const checks = checklist(t, row);
    const greens = checks.filter(c => c.state === "yes").length;
    const tract = traction(row);
    const head = `Why it's on radar · ${greens}/7 green${tract ? " · " + tract : ""}`;
    const rows = checks.map(c => `<div class="sl-check">${mark(c.state)}<span>${t.esc(c.label)}</span><span class="sl-side">${c.sideHtml || t.esc(c.side || "")}</span></div>`).join("");
    const hint = pick && pick.terms && pick.terms.size
      ? `<div class="sl-check">${mark("info")}<span>Paper hint: ${t.esc(String(pick.terms.size))} ₳ ticket · ${t.esc(pick.terms.label || "")}</span><span class="sl-side">${t.esc(pick.test)}</span></div>`
      : "";
    return `<article class="sl-sheet" data-ada-sheet="${t.esc(row.ticker)}" hidden>
      <header class="sl-sheet-h">
        ${logo(t, row.ticker, false)}
        <span class="sl-tk lg">${t.esc(row.ticker)}</span>
        ${row.pill ? `<span class="sl-pill ${row.pill.cls}">${t.esc(row.pill.text)}</span>` : ""}
        <span class="sl-grow"></span>
        ${pctHtml(row.pct)}
      </header>
      ${kvs ? `<div class="sl-kvs">${kvs}</div>` : ""}
      ${why}
      <p class="sl-section-label">${t.esc(head)}</p>
      ${rows}${hint}
    </article>`;
  }

  function checklist(t, row) {
    const flow = row.flow ? `${row.flow.buys}/${row.flow.sells}` : "";
    const kBits = [];
    if (row.holders != null) kBits.push(`${compact(row.holders)} holders`);
    if (row.pool != null) kBits.push(`${compact(row.pool)} ₳`);
    if (flow) kBits.push(`buys ${flow}`);
    const kLabel = kBits.length ? `k: ${kBits.join(" · ")}` : "k: holders, pool, 6h buys";
    const swarmLabel = row.swarmCount ? `Swarm flag: ${row.swarmCount} wallets` : "Swarm flag";
    const whaleLabel = row.whaleAfter;
    let whaleText = "Whale buying after 24h";
    let whaleState = "unk";
    let whaleSide = "";
    if (whaleLabel === "yes") { whaleState = "yes"; whaleSide = row.whaleName || ""; }
    else if (whaleLabel === "no") { whaleState = "no"; whaleSide = row.whaleName || ""; whaleText = "No whale buy after 24h"; }
    else if (whaleLabel === "early") { whaleState = "no"; whaleSide = "Too early"; }
    const devText = row.devSold === true ? "Dev sold" : "Dev hasn't sold";
    const devState = row.devSold == null ? "unk" : row.devSold ? "no" : "yes";
    let devLabel = devText;
    if (row.fresh) devLabel += " · fresh funded";
    const rugState = row.rugger == null ? "unk" : row.rugger ? "no" : "yes";
    const rugText = row.rugger ? "On rugger list" : "Not on rugger list";
    return [
      { state: tri(row.bonded), label: row.bonded === false ? "Still on the curve" : "Bonded to DEX pool", side: row.bondedSide || "" },
      { state: row.kState, label: kLabel, side: "Rule k" },
      { state: row.swarm === true ? "warn" : row.swarm === false ? "yes" : "unk", label: row.swarm === false ? "No swarm flag" : swarmLabel, side: row.swarm === true ? "flag only" : "" },
      { state: whaleState, label: whaleText, side: whaleSide },
      { state: devState, label: devLabel, side: row.devSide || "Dev check", sideHtml: row.devSideHtml || "" },
      { state: rugState, label: rugText, side: row.ruggerSide || "" }
    ];
  }

  function tri(v) {
    if (v === true) return "yes";
    if (v === false) return "no";
    return "unk";
  }
  function kv(k, v) {
    if (v == null || v === "") return "";
    return `<div class="sl-kv"><span class="sl-k">${k}</span><b>${v}</b></div>`;
  }
  function holdersKv(row) {
    if (row.holders == null) return "";
    const arrow = row.holdersDelta > 0 ? " ↑" : row.holdersDelta < 0 ? " ↓" : "";
    return compact(row.holders) + arrow;
  }
  function killKv(n) {
    if (n == null || !isFinite(n)) return "";
    const body = (n > 0 ? "+" : n < 0 ? "−" : "") + trimNum(Math.abs(n)) + "%";
    return body;
  }
  function traction(row) {
    if (row.holders == null || row.pool == null || row.swarm !== false) return "";
    if (row.holders >= K_HOLDERS && row.pool >= K_POOL && row.sniper !== true) return "Getting traction";
    return "";
  }
  function mark(state) {
    const ch = state === "yes" ? "✓" : state === "no" ? "×" : state === "warn" ? "!" : state === "info" ? "₳" : "?";
    return `<span class="sl-mark ${state}">${ch}</span>`;
  }
  function dots(score) {
    if (score == null) return "";
    let n = score;
    if (n <= 1) n = Math.round(n * 5);
    else n = Math.round(n);
    if (n < 0 || n > 5) return "";
    const cells = [0, 1, 2, 3, 4].map(i => `<i class="${i < n ? "on" : ""}"></i>`).join("");
    return `<span class="sl-dots" aria-label="${n} of 5">${cells}<em>${n}/5</em></span>`;
  }
  function pctHtml(pct) {
    if (!pct || pct.n == null || !isFinite(pct.n)) return `<span class="sl-pct sl-flat">–</span>`;
    const n = pct.n;
    const cls = n > 0 ? "sl-up" : n < 0 ? "sl-down" : "sl-flat";
    const body = (n > 0 ? "+" : n < 0 ? "−" : "") + trimNum(Math.abs(n));
    return `<span class="sl-pct ${cls}">${body}%</span>`;
  }
  function spark(series, pct) {
    const vals = (series || []).map(p => p.n).filter(n => n != null && isFinite(n));
    if (vals.length < 2 || vals.every(n => n === vals[0])) return "";
    const w = 54, h = 16;
    const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    const span = max - min || 1;
    const pts = vals.map((n, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = 1.5 + (1 - (n - min) / span) * (h - 3);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    const up = pct && pct.n != null ? pct.n > 0 : vals[vals.length - 1] >= vals[0];
    const color = up ? "#0ECB81" : "#F6465D";
    return `<svg class="sl-spark" width="54" height="16" viewBox="0 0 54 16" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.2" opacity=".55"/></svg>`;
  }
  function logo(t, ticker, sm) {
    if (typeof t.coinLogo === "function") return t.coinLogo(ticker, ticker, sm);
    const initials = String(ticker || "").slice(0, 2);
    const cls = sm ? "sl-logo sm" : "sl-logo";
    return `<span class="${cls}" style="background:${logoColor(ticker)}" aria-hidden="true">${t.esc(initials)}</span>`;
  }
  function logoColor(name) {
    let h = 0;
    const s = String(name || "");
    for (let i = 0; i < s.length; i++) h = (h * 33 + s.charCodeAt(i)) >>> 0;
    return "hsl(" + (h % 360) + " 24% 64%)";
  }

  function build(t, d) {
    const log = d.log || {};
    const v2 = asObj(log.cardano_v2) || {};
    const asof = log.asof || (d.pulse && d.pulse.updated_at) || (d.flags && d.flags.at) || (d.devs && d.devs.at) || "";
    const asofMs = stamp(asof);
    const picks = openPicks(t, d.paper, log);
    const pickBy = {};
    picks.forEach(p => { pickBy[p.ticker] = p; });
    const flags = flagIndex(d.flags);
    const devs = devIndex(d.devs);
    const vetoes = d.vetoes;
    const launches = indexLaunches(t, v2.launches);
    const groups = swarmGroups(t, v2.swarms);
    const trends = trendIndex(t, v2.rotation);
    const allow = codenames(t, log, v2);
    const lead = allow[0] || "";
    const prose = proseByTicker(t, log, allow);
    const series = seriesByTicker(t, log, d.pulse, asofMs);
    const flowPulse = pulseFlow(d.pulse);
    const by = {};

    function ensure(ticker, seed) {
      const tk = validTicker(t, ticker);
      if (!tk) return null;
      if (!by[tk]) by[tk] = blank(tk);
      const row = by[tk];
      if (seed) fill(t, row, seed);
      return row;
    }

    (log.cardano_top10 || []).forEach(r => {
      const row = ensure(r && r.ticker, r);
      if (!row) return;
      row.established = true;
      row.rank = num(r.rank);
      if (row.pool == null) row.pool = num(r.ada_side_depth);
    });
    (log.verdicts || []).forEach(r => {
      const row = ensure(r && r.ticker, r);
      if (!row) return;
      if (EST_PAD[String(r.launchpad || "").toLowerCase()]) row.established = true;
      else row.runner = true;
    });
    Object.keys(launches).forEach(tk => {
      const src = launches[tk];
      if (src.example) return;
      const row = ensure(tk, src);
      if (!row) return;
      if (!row.established) row.runner = true;
    });
    if (!Object.keys(by).length && d.devs && Array.isArray(d.devs.entries)) {
      d.devs.entries.forEach(r => ensure(r && r.ticker, r));
    }
    ((d.whale && d.whale.rows) || []).forEach(r => {
      if (!r || r.token == null) return;
      if (Object.keys(by).length > 80) return;
      const tk = validTicker(t, r.token);
      if (!tk || tk === "ADA" || tk === "LP") return;
      if (!by[tk] && !log.cardano_top10 && !(log.verdicts || []).length) ensure(tk, { ticker: tk });
    });

    const ledgerTrend = ledgerTrends(d.whale, asofMs);
    Object.keys(by).forEach(tk => {
      const row = by[tk];
      const launch = launches[tk];
      if (launch && !launch.example) {
        if (row.holders == null && !launch.holdersExample) row.holders = num(launch.holders);
        if (row.pool == null && !launch.poolExample) row.pool = num(launch.pool_ada);
        if (launch.swarm === true) row.swarm = true;
        if (launch.swarm === false && row.swarm == null) row.swarm = false;
        if (launch.sniper === true) row.sniper = true;
        if (launch.graduated === true) row.bonded = true;
        if (launch.graduated === false && row.bonded == null) row.bonded = false;
        row.ageH = row.ageH == null ? ageHours(launch.age) : row.ageH;
        if (!row.pad) row.pad = launch.launchpad;
      }
      const flag = flags[tk];
      if (flag) {
        if (flag.swarm) row.swarm = true;
        if (flag.swarmCount && !row.swarmCount) row.swarmCount = flag.swarmCount;
      }
      if (groups[tk]) {
        row.swarm = true;
        if (!row.swarmCount && groups[tk].wallets) row.swarmCount = groups[tk].wallets;
      }
      if (row.swarm == null && launch && launch.swarm === false && !flag) row.swarm = false;
      const dev = devs[tk];
      if (dev) {
        row.fresh = dev.fresh === true;
        if (dev.sold === true) row.devSold = true;
        else if (dev.sold === false) row.devSold = false;
        if (dev.sideHtml) row.devSideHtml = dev.sideHtml;
        else if (dev.side) row.devSide = dev.side;
      }
      row.rugger = ruggerOf(tk, vetoes, devs[tk]);
      if (row.rugger === true) row.ruggerSide = "List";
      else if (row.rugger === false) row.ruggerSide = "Log-only";
      const tr = trends[tk];
      if (tr) { row.whaleTrend = tr.trend; if (!row.whaleName) row.whaleName = tr.name || lead; }
      const led = ledgerTrend[tk];
      if (led && !row.whaleTrend) { row.whaleTrend = led; if (!row.whaleName) row.whaleName = lead; }
      const said = prose[tk];
      if (said) {
        if (!row.pct) row.pct = said.pct;
        if (!row.flow) row.flow = said.flow;
        if (said.whaleTrend && !row.whaleTrend) row.whaleTrend = said.whaleTrend;
        if (said.whaleName && !row.whaleName) row.whaleName = said.whaleName;
      }
      if (!row.pct) row.pct = explicitPct(row.raw) || pctFromSeries(series[tk], asofMs);
      if (!row.flow) row.flow = explicitFlow(row.raw) || (flowPulse[tk] || null);
      row.series = freshSeries(series[tk], asofMs);
      row.steam = steamOf(tk, d.pulse, d.big, asofMs, series[tk]);
      row.padLabel = padLabel(row.pad);
      row.killPct = killPct(row.raw);
      row.kState = kState(row);
      row.whaleAfter = whaleAfter(row, d.whale, launches[tk], asofMs);
      if (row.bonded == null) row.bonded = bondedOf(row, launch);
      row.bondedSide = row.bonded === true ? "DEX" : row.bonded === false ? "Curve" : "";
      row.pill = pillFor(row, pickBy[tk]);
      row.sub = secondary(row);
      const pick = pickBy[tk];
      if (pick) {
        pick.row = row;
        if (!pick.blurb) pick.blurb = blurbFrom(row, pick);
      }
    });

    picks.forEach(p => {
      if (!by[p.ticker]) {
        const row = blank(p.ticker);
        fill(t, row, p.ticket);
        row.pill = pillFor(row, p);
        row.sub = secondary(row);
        by[p.ticker] = row;
        p.row = row;
        if (!p.blurb) p.blurb = blurbFrom(row, p);
      }
    });

    let runners = Object.keys(by).map(k => by[k]).filter(r => !r.established && (r.runner || !r.established));
    let established = Object.keys(by).map(k => by[k]).filter(r => r.established);
    /* A name on the high-cap list stays there. Runners are everyone else the desk listed. */
    const estSet = {};
    established.forEach(r => { estSet[r.ticker] = 1; });
    runners = runners.filter(r => !estSet[r.ticker]);
    if (!established.length) {
      /* no high-cap list: leave runners as the non-established names already chosen */
    }
    runners.sort((a, b) => cmpPct(b, a) || (b.pool || 0) - (a.pool || 0) || a.ticker.localeCompare(b.ticker));
    established.sort((a, b) => {
      const ar = a.rank == null ? 99 : a.rank;
      const br = b.rank == null ? 99 : b.rank;
      if (ar !== br) return ar - br;
      return (b.mcap || b.pool || 0) - (a.mcap || a.pool || 0);
    });
    runners = runners.slice(0, 10);
    established = established.slice(0, 10);
    const shown = {};
    runners.concat(established).forEach(r => { shown[r.ticker] = r; });
    picks.forEach(p => { if (by[p.ticker]) shown[p.ticker] = by[p.ticker]; });
    const sheets = Object.keys(shown).map(k => shown[k]);
    const pendingSw = pendingCount(d.paper, picks);
    const moving = runners.filter(r => r.pill && r.pill.cls === "ada-move").length;
    const quiet = established.length > 0 && established.every(r => r.pct && Math.abs(r.pct.n) < 2) && !picks.some(p => p.test === "EST-01");
    const wins = runners.concat(established).map(r => r.pct && r.pct.window).filter(Boolean);
    const winLabel = wins.some(w => w === "24h") ? "24H" : wins.some(w => w === "6h") ? "6H" : wins.length ? "MOVE" : "";
    const status = statusText(t, {
      sw: picks.filter(p => p.test === "SW-01").length,
      est: picks.filter(p => p.test === "EST-01").length,
      pendingSw, moving, quiet,
      learned: (log.learned_or_changed || []).concat((d.brief && d.brief.learned) || []),
      headline: log.headline
    });
    return {
      runners, established, picks, pickBy, by, sheets, asof, clock: clock(asof), winLabel, status
    };
  }

  function blank(ticker) {
    return {
      ticker, raw: {}, established: false, runner: false, rank: null,
      holders: null, holdersDelta: null, pool: null, mcap: null, pad: "",
      pct: null, flow: null, series: [], swarm: null, swarmCount: 0, sniper: null,
      bonded: null, ageH: null, whaleName: "", whaleTrend: "", whaleAfter: null,
      devSold: null, fresh: false, devSide: "", devSideHtml: "", rugger: null,
      buzz: null, buzzSide: "", kState: "unk", pill: null, sub: ""
    };
  }
  function fill(t, row, src) {
    if (!src || typeof src !== "object") return;
    row.raw = src;
    const pad = String(src.launchpad || src.pad || "").toLowerCase();
    if (pad && !row.pad) row.pad = pad;
    if (row.holders == null) row.holders = num(src.holders);
    if (row.holders == null && String(src.kill_metric || "") === "holders") row.holders = num(src.kill_value);
    if (row.pool == null) row.pool = num(src.pool_ada);
    if (row.pool == null) row.pool = num(src.ada_side_depth);
    if (row.pool == null) row.pool = num(src.ada_side);
    if (row.pool == null && /pool|ada_side/.test(String(src.kill_metric || ""))) row.pool = num(src.kill_value);
    if (row.mcap == null) row.mcap = num(src.mcap_ada);
    const delta = num(src.holders_delta);
    if (delta != null) row.holdersDelta = delta;
    const verdict = String(src.verdict || "").toLowerCase();
    if (verdict && !row.verdict) row.verdict = verdict;
    if (src.swarm === true) row.swarm = true;
    if (src.swarm === false && row.swarm == null) row.swarm = false;
    if (src.graduated === true) row.bonded = true;
    if (src.graduated === false && row.bonded == null) row.bonded = false;
    if (src.bonded === true) row.bonded = true;
    if (src.bonded === false && row.bonded == null) row.bonded = false;
    const basis = String(src.pool_basis || src.basis || "").toLowerCase();
    if (basis === "geckoterminal" || basis === "dexscreener") row.bonded = true;
    if (basis === "solada-curve" || basis === "curve") row.bonded = false;
  }

  function pillFor(row, pick) {
    if (pick && pick.test === "SW-01") return { cls: "sl-pill-accent", text: "SW-01" };
    const v = String(row.verdict || "");
    if (/lottery|\bcall\b/.test(v)) return { cls: "sl-pill-lot", text: "Lottery" };
    if (/watch|yellow|caution/.test(v)) return { cls: "sl-pill-watch", text: "Watch" };
    if (/pass|ignore/.test(v) && row.pct && row.pct.n > 0) return { cls: "sl-pill-move", text: "Passed·moving" };
    if (/pass|ignore/.test(v)) return { cls: "sl-pill-pass", text: "Pass" };
    return null;
  }
  function secondary(row) {
    const trendWord = { accum: "accum", trim: "trim", exit: "exit", flat: "flat" };
    if (row.whaleName && trendWord[row.whaleTrend]) {
      let s = row.whaleName + " " + trendWord[row.whaleTrend];
      if (row.holders != null) s += " · " + compact(row.holders) + " h";
      return s;
    }
    if (row.rugger === true) return "Rugger list";
    if (row.devSold === true) return "Dev sold";
    const bits = [];
    if (row.pool != null) bits.push("Pool " + compact(row.pool) + " ₳");
    if (row.holders != null) bits.push(compact(row.holders) + " h");
    if (bits.length) return bits.join(" · ");
    if (row.fresh) return "Fresh funded";
    return "";
  }
  function kState(row) {
    const h = row.holders == null ? null : row.holders >= K_HOLDERS;
    const p = row.pool == null ? null : row.pool >= K_POOL;
    const f = row.flow ? row.flow.buys > row.flow.sells : null;
    if (h === false || p === false || f === false) return "no";
    if (h === true && p === true && f === true) return "yes";
    return "unk";
  }
  function bondedOf(row) {
    if (row.bonded === true || row.bonded === false) return row.bonded;
    return null;
  }
  function whaleAfter(row, whale, launch, asofMs) {
    const age = row.ageH;
    if (age != null && age < 24) return "early";
    const launchMs = launch && launch.at ? stamp(launch.at) : NaN;
    const buys = whaleBuysAfter(whale, row.ticker, launchMs);
    if (buys === true) return "yes";
    if (buys === false && age != null && age >= 24) return "no";
    if (row.whaleTrend === "accum" && age != null && age >= 24 && buys !== false) return null;
    return null;
  }
  function whaleBuysAfter(whale, ticker, launchMs) {
    const rows = (whale && whale.rows) || [];
    if (!rows.length || !isFinite(launchMs)) return null;
    const gate = launchMs + 24 * 36e5;
    let seen = false;
    let hit = false;
    rows.forEach(r => {
      if (!r || validRaw(r.token) !== ticker) return;
      const ms = stamp(r.time);
      if (!isFinite(ms) || ms < gate) return;
      const dir = legDir(r);
      if (!dir) return;
      seen = true;
      if (dir === "buy") hit = true;
    });
    if (!seen) return null;
    return hit;
  }
  function legDir(row) {
    const d = String(row.direction || "").toLowerCase();
    if (d === "buy" || d === "in") return "buy";
    if (d === "sell" || d === "out") return "sell";
    const kind = String(row.kind || "");
    if (/remove|sell/.test(kind)) return "sell";
    if (/^lp_add|buy/.test(kind)) return "buy";
    if (String(row.sold || "").toUpperCase() === "ADA") return "buy";
    if (row.sold && row.token && String(row.sold).toUpperCase() === String(row.token).toUpperCase()) return "sell";
    return "";
  }
  function blurbFrom(row, pick) {
    const bits = [];
    if (row.holdersDelta > 0) bits.push("holders +" + compact(row.holdersDelta));
    if (row.whaleName && row.whaleTrend === "accum") bits.push(row.whaleName + " buying");
    if (row.bonded === true) bits.push("bonded");
    if (row.kState === "yes") bits.push("k met");
    if (row.whaleTrend === "none" || row.whaleAfter === "no") bits.push("no whale yet");
    if (bits.length) return bits.join(" · ");
    return pick.note || "";
  }

  function openPicks(t, paper, log) {
    const out = [];
    const seen = {};
    const tests = (paper && Array.isArray(paper.tests) ? paper.tests : []).filter(x => x && typeof x === "object");
    const testById = {};
    tests.forEach(test => { testById[String(test.id || "").toUpperCase()] = test; });
    function push(testId, ticket) {
      const id = testId === "SW-01" || testId === "EST-01" ? testId : "";
      if (!id || !ticket || typeof ticket !== "object") return;
      if (!ticketOpen(ticket)) return;
      const tk = validTicker(t, ticket.ticker || ticket.token || ticket.name);
      if (!tk || seen[id + ":" + tk]) return;
      seen[id + ":" + tk] = 1;
      const test = testById[id];
      const thesis = []
        .concat(ticket.thesis || [], ticket.why || [], ticket.note || [], ticket.summary || [])
        .map(x => typeof x === "string" ? x : x && (x.text || x.line || ""))
        .map(s => clean(t, s))
        .filter(Boolean);
      const score = num(ticket.confidence != null ? ticket.confidence : ticket.score);
      out.push({
        test: id,
        ticker: tk,
        ticket,
        terms: termsOf(id, test, ticket),
        blurb: thesis[0] || "",
        note: "",
        score: score,
        conf: confLabel(ticket)
      });
    }
    tests.forEach(test => {
      const id = String(test.id || "").toUpperCase();
      (test.tickets || []).forEach(ticket => push(id, ticket));
    });
    ((paper && paper.open) || []).forEach(ticket => {
      const blob = [ticket.test, ticket.test_id, ticket.rule, ticket.id].join(" ").toUpperCase();
      if (blob.includes("SW-01")) push("SW-01", ticket);
      else if (blob.includes("EST-01")) push("EST-01", ticket);
    });
    ["picks", "open_picks", "sentinel_picks"].forEach(key => {
      const arr = log && log[key];
      if (!Array.isArray(arr)) return;
      arr.forEach(ticket => {
        const id = String((ticket && (ticket.test || ticket.test_id || ticket.id)) || "").toUpperCase();
        if (id.includes("SW-01")) push("SW-01", ticket);
        else if (id.includes("EST-01")) push("EST-01", ticket);
      });
    });
    return out;
  }
  function ticketOpen(ticket) {
    if (ticket.closed_at) return false;
    const st = String(ticket.status || "open").toLowerCase();
    return st !== "closed" && st !== "exited" && st !== "cancelled" && st !== "canceled";
  }
  function termsOf(id, test, ticket) {
    const blob = test ? [test.action, test.exit, test.title].filter(Boolean).join(" ") : "";
    let size = num(ticket && ticket.size);
    if (size == null) {
      const m = String(blob).match(/(\d+)\s*ADA/i);
      if (m) size = Number(m[1]);
    }
    let label = "";
    const hours = num(ticket && (ticket.window_hours != null ? ticket.window_hours : ticket.window_h));
    if (hours != null && hours >= 24 * 6) label = "7d exit";
    else if (hours != null && hours > 0) label = Math.round(hours) + "h exit";
    else if (/48\s*h/i.test(blob)) label = "48h exit";
    else if (/7\s*-?\s*day/i.test(blob)) label = "7d exit";
    else if (id === "SW-01") label = "48h exit";
    else if (id === "EST-01") label = "7d exit";
    if (size == null && (id === "SW-01" || id === "EST-01")) size = 100;
    return { size, label };
  }
  function confLabel(ticket) {
    const s = String(ticket.confidence_label || ticket.confidence_band || "").toLowerCase();
    if (s === "high" || s === "med" || s === "medium" || s === "low") return s === "medium" ? "med" : s;
    return "";
  }
  function pendingCount(paper, picks) {
    const open = {};
    picks.forEach(p => { if (p.test === "SW-01") open[p.ticker] = 1; });
    let n = 0;
    ((paper && paper.tests) || []).forEach(test => {
      if (String(test.id || "").toUpperCase() !== "SW-01") return;
      (test.comparison_group || []).forEach(row => {
        if (!row || typeof row !== "object") return;
        const tk = String(row.ticker || "").toUpperCase();
        if (!tk || open[tk]) return;
        const st = String(row.status || "").toLowerCase();
        if (st === "closed" || st === "exited") return;
        n += 1;
      });
    });
    return n;
  }

  function indexLaunches(t, list) {
    const map = {};
    (list || []).forEach(row => {
      if (!row || row.example === true) return;
      const tk = validTicker(t, row.ticker);
      if (!tk) return;
      map[tk] = {
        ticker: tk,
        launchpad: row.launchpad,
        holders: row.holders_example === true ? null : row.holders,
        holdersExample: row.holders_example === true,
        pool_ada: row.pool_example === true ? null : row.pool_ada,
        poolExample: row.pool_example === true,
        swarm: row.swarm === true ? true : row.swarm === false ? false : null,
        sniper: row.sniper === true,
        graduated: row.graduated === true ? true : row.graduated === false ? false : null,
        age: row.age,
        at: row.at || ""
      };
    });
    return map;
  }
  function swarmGroups(t, list) {
    const map = {};
    (list || []).forEach(g => {
      if (!g || typeof g !== "object") return;
      const n = typeof g.wallets === "number" ? g.wallets : 0;
      const names = [].concat(g.last_seen || [], (g.launches || []).map(x => x && (x.ticker || x)));
      names.forEach(name => {
        const tk = validTicker(t, name);
        if (!tk) return;
        map[tk] = { wallets: n };
      });
    });
    return map;
  }
  function trendIndex(t, rotation) {
    const map = {};
    if (!rotation || typeof rotation !== "object") return map;
    const name = t.animalName ? (t.animalName(rotation.who) || "") : "";
    (rotation.trends || []).forEach(row => {
      const tk = validTicker(t, row && (row.token || row.ticker));
      if (!tk) return;
      const trend = normTrend(row.trend);
      if (!trend) return;
      map[tk] = { trend, name };
    });
    return map;
  }
  function normTrend(v) {
    const s = String(v || "").toLowerCase();
    if (/accum|re-?enter/.test(s)) return "accum";
    if (/trim/.test(s)) return "trim";
    if (/exit/.test(s)) return "exit";
    if (/flat/.test(s)) return "flat";
    return "";
  }
  function flagIndex(doc) {
    const map = {};
    if (!doc || typeof doc !== "object") return map;
    ["new", "recent"].forEach(bucket => {
      (doc[bucket] || []).forEach(f => {
        if (!f || typeof f !== "object") return;
        const tk = validRaw(f.ticker);
        if (!tk) return;
        const kind = String(f.kind || "");
        const row = map[tk] || { swarm: false, swarmCount: 0 };
        if (kind === "coordinated_buys" || kind === "fresh_wallet_swarm") {
          row.swarm = true;
          const n = Array.isArray(f.wallets) ? f.wallets.length : (typeof f.wallets === "number" ? f.wallets : 0);
          if (n > row.swarmCount) row.swarmCount = n;
        }
        map[tk] = row;
      });
    });
    return map;
  }
  function devIndex(doc) {
    const map = {};
    const entries = doc && Array.isArray(doc.entries) ? doc.entries : [];
    entries.forEach(row => {
      const tk = validRaw(row && row.ticker);
      if (!tk || map[tk]) return;
      const soldAda = num(row.dev_sold_ada);
      let sold = null;
      if (row.late_rug === true || (soldAda != null && soldAda > 0)) sold = true;
      else if (soldAda === 0 && row.late_rug !== true) sold = false;
      let side = "";
      let sideHtml = "";
      const tag = String(row.dev_tag || "");
      if (/^w-[0-9a-f]{8}$/i.test(tag)) side = "Dev check";
      else if (/^[a-z][a-z0-9_-]{0,31}$/i.test(tag) && !/stake|addr|wallet|seed|secret|key/i.test(tag)) {
        sideHtml = `<span class="sl-side">${esc(tag.toLowerCase())}</span>`;
      }
      map[tk] = { sold, fresh: row.fresh_funded === true, side, sideHtml };
    });
    return map;
  }
  function ruggerOf(ticker, vetoes, dev) {
    if (!vetoes || typeof vetoes !== "object") return null;
    const tokens = Array.isArray(vetoes.tokens) ? vetoes.tokens : null;
    if (!tokens) return null;
    const row = tokens.find(x => x && validRaw(x.ticker) === ticker);
    if (row) {
      const codes = (row.vetoes || []).map(v => String((v && v.code) || v));
      return codes.some(c => /known_rugger/.test(c));
    }
    if (dev) return false;
    return null;
  }
  function leadName(t, log, v2) {
    const whales = Array.isArray(log.whales) ? log.whales : [];
    const lead = whales.find(w => w && w.role === "lead") || whales[0];
    const fromList = lead && t.animalName ? t.animalName(lead.codename) : "";
    if (fromList) return fromList;
    const rot = v2.rotation && t.animalName ? t.animalName(v2.rotation.who) : "";
    if (rot) return rot;
    const fw = log.founder_whale;
    if (fw && t.animalName && !(Array.isArray(log.whales) && log.whales.length)) return t.animalName(fw.codename) || "";
    return "";
  }
  function proseByTicker(t, log, allow) {
    const map = {};
    function take(ticker, text, whaleHint, scoped) {
      const tk = validTicker(t, ticker);
      const body = scoped ? sentencesAbout(text, tk) : text;
      if (!tk || !body) return;
      const row = map[tk] || {};
      const pct = parsePct(body);
      const flow = parseFlow(body, true);
      if (pct && !row.pct) row.pct = pct;
      if (flow && !row.flow) row.flow = flow;
      const whale = whaleFromText(t, body, whaleHint, allow);
      if (whale.trend && !row.whaleTrend) row.whaleTrend = whale.trend;
      if (whale.name && !row.whaleName) row.whaleName = whale.name;
      map[tk] = row;
    }
    (log.verdicts || []).forEach(v => {
      const blob = [].concat(v.why || [], v.reasons || [], v.whale_line || [], v.whale_activity || []).join(" · ");
      take(v.ticker, blob, "", false);
    });
    (log.cardano_top10 || []).forEach(r => take(r.ticker, r.note || "", "", false));
    const levels = log.levels || {};
    Object.keys(levels).forEach(tk => take(tk, levels[tk], "", false));
    const fw = log.founder_whale;
    if (fw && !(Array.isArray(log.whales) && log.whales.length)) {
      const name = t.animalName ? (t.animalName(fw.codename) || "") : "";
      const blob = clean(t, [fw.summary, fw.line].filter(Boolean).join(" · "));
      const re = /\b([A-Z][A-Z0-9]{1,15})\b/g;
      let m;
      while ((m = re.exec(blob))) take(m[1], blob, name, true);
    }
    (Array.isArray(log.whales) ? log.whales : []).forEach(w => {
      const name = t.animalName ? (t.animalName(w.codename) || "") : "";
      const blob = clean(t, w.line || "");
      if (!blob) return;
      const re = /\b([A-Z][A-Z0-9]{1,15})\b/g;
      let m;
      while ((m = re.exec(blob))) take(m[1], blob, name, true);
    });
    return map;
  }
  function sentencesAbout(text, ticker) {
    if (!ticker) return "";
    const re = new RegExp("\\b" + ticker + "\\b");
    return String(text || "").split(/[|;]+/).map(s => s.trim()).filter(s => re.test(s)).join(". ");
  }
  function codenames(t, log, v2) {
    const out = [];
    const add = v => {
      const n = t.animalName ? t.animalName(v) : "";
      if (n && out.indexOf(n) < 0) out.push(n);
    };
    (Array.isArray(log.whales) ? log.whales : []).forEach(w => add(w && w.codename));
    (v2.whales || []).forEach(w => add(w && w.codename));
    if (v2.rotation) add(v2.rotation.who);
    const wallets = log.whale_watch && log.whale_watch.wallets;
    (Array.isArray(wallets) ? wallets : []).forEach(w => add(w && w.codename));
    if (!(Array.isArray(log.whales) && log.whales.length) && log.founder_whale) add(log.founder_whale.codename);
    return out;
  }
  function whaleFromText(t, text, fallback, allow) {
    const s = clean(t, text);
    if (!s || /^none\b/i.test(s) || /^no tracked/i.test(s) || /^no moves/i.test(s) || /not a buy|isn.t a buy|wasn.t a buy|\bgift\b/i.test(s)) {
      return { name: "", trend: "" };
    }
    const list = allow || [];
    let name = "";
    list.forEach(n => { if (!name && s.toLowerCase().indexOf(n.toLowerCase()) >= 0) name = n; });
    if (!name && fallback && list.indexOf(fallback) >= 0) name = fallback;
    let trend = "";
    if (/exit|dumped|sold out|pulled liquidity/i.test(s)) trend = "exit";
    else if (/\btrim|sold|selling|\bsell\b/i.test(s)) trend = "trim";
    else if (/accum|buying|bought|added|\bbuy\b/i.test(s)) trend = "accum";
    else if (/\bflat\b|no trades|quiet/i.test(s)) trend = "flat";
    return { name, trend };
  }
  function ledgerTrends(whale, asofMs) {
    const map = {};
    const rows = (whale && whale.rows) || [];
    if (!isFinite(asofMs)) return map;
    const start = asofMs - 72 * 36e5;
    rows.forEach(r => {
      const tk = validRaw(r && r.token);
      if (!tk || tk === "ADA" || tk === "LP") return;
      const ms = stamp(r.time);
      if (!isFinite(ms) || ms < start || ms > asofMs + 36e5) return;
      const dir = legDir(r);
      if (!dir) return;
      const ada = Math.abs(num(r.ada_leg) || 0);
      const row = map[tk] || { buy: 0, sell: 0 };
      if (dir === "buy") row.buy += ada; else row.sell += ada;
      map[tk] = row;
    });
    const out = {};
    Object.keys(map).forEach(tk => {
      const row = map[tk];
      if (row.buy <= 0 && row.sell <= 0) return;
      if (row.buy > row.sell * 2) out[tk] = "accum";
      else if (row.sell > row.buy * 2) out[tk] = "trim";
    });
    return out;
  }

  function seriesByTicker(t, log, pulse, asofMs) {
    const map = {};
    function push(ticker, time, value) {
      const tk = validTicker(t, ticker);
      const ms = stamp(time);
      const n = num(value);
      if (!tk || !isFinite(ms) || n == null || n <= 0) return;
      (map[tk] = map[tk] || []).push({ ms, n });
    }
    (log.history || []).forEach(h => {
      (h && h.cardano_top10 || []).forEach(r => push(r.ticker, h.asof, r.price_ada != null ? r.price_ada : r.mcap_ada));
    });
    (log.cardano_top10 || []).forEach(r => push(r.ticker, log.asof, r.price_ada != null ? r.price_ada : r.mcap_ada));
    ((pulse && pulse.points) || []).forEach(p => {
      if (!p || (p.chain && p.chain !== "cardano")) return;
      const m = p.metrics || {};
      push(p.ticker, p.t, m.price_ada != null ? m.price_ada : m.mcap_ada);
    });
    Object.keys(map).forEach(tk => {
      map[tk].sort((a, b) => a.ms - b.ms);
      const dedup = [];
      map[tk].forEach(p => {
        if (dedup.length && dedup[dedup.length - 1].ms === p.ms) dedup[dedup.length - 1] = p;
        else dedup.push(p);
      });
      map[tk] = dedup;
    });
    return map;
  }
  function freshSeries(pts, asofMs) {
    if (!pts || pts.length < 2) return [];
    const end = isFinite(asofMs) ? asofMs : pts[pts.length - 1].ms;
    const cut = end - 14 * 24 * 36e5;
    const kept = pts.filter(p => p.ms >= cut && p.ms <= end + 36e5);
    if (kept.length < 2) return [];
    if (isFinite(asofMs) && end - kept[kept.length - 1].ms > 36 * 36e5) return [];
    return kept.slice(-24);
  }
  function pctFromSeries(pts, asofMs) {
    const fresh = freshSeries(pts, asofMs);
    if (!fresh || fresh.length < 2) return null;
    const last = fresh[fresh.length - 1];
    pts = fresh;
    let best = null;
    pts.forEach(p => {
      const h = (last.ms - p.ms) / 36e5;
      if (h >= 18 && h <= 30) {
        if (!best || Math.abs(h - 24) < Math.abs(best.h - 24)) best = { p, h, window: "24h" };
      }
    });
    if (!best) {
      pts.forEach(p => {
        const h = (last.ms - p.ms) / 36e5;
        if (h >= 4 && h <= 8) {
          if (!best || Math.abs(h - 6) < Math.abs(best.h - 6)) best = { p, h, window: "6h" };
        }
      });
    }
    if (!best || !best.p.n || last.n === best.p.n) return null;
    return { n: (last.n - best.p.n) / best.p.n * 100, window: best.window };
  }
  function pulseFlow(pulse) {
    const map = {};
    ((pulse && pulse.points) || []).forEach(p => {
      if (!p || (p.chain && p.chain !== "cardano")) return;
      const tk = validRaw(p.ticker);
      if (!tk) return;
      const m = p.metrics || {};
      const prev = map[tk];
      if (prev && String(prev.t) > String(p.t)) return;
      const buys = num(m.buys_h6 != null ? m.buys_h6 : m.buys_6h);
      const sells = num(m.sells_h6 != null ? m.sells_h6 : m.sells_6h);
      if (buys == null || sells == null) return;
      map[tk] = { t: p.t, buys, sells };
    });
    const out = {};
    Object.keys(map).forEach(tk => { out[tk] = { buys: map[tk].buys, sells: map[tk].sells }; });
    return out;
  }
  function steamOf(ticker, pulse, big, asofMs, pts) {
    if (!isFinite(asofMs)) return false;
    const holders = metricPoints(pulse, ticker, "holders");
    const hold = rose(holders, asofMs);
    if (!hold) return false;
    const volPts = metricPoints(pulse, ticker, "volume");
    if (rose(volPts, asofMs)) return true;
    return volumeRose(big, ticker, asofMs);
  }
  function metricPoints(pulse, ticker, key) {
    const out = [];
    ((pulse && pulse.points) || []).forEach(p => {
      if (!p || validRaw(p.ticker) !== ticker) return;
      if (p.chain && p.chain !== "cardano") return;
      const m = p.metrics || {};
      let n = null;
      if (key === "holders") n = num(m.holders_onchain != null ? m.holders_onchain : m.holders);
      else n = num(m.volume != null ? m.volume : m.vol_h6);
      const ms = stamp(p.t);
      if (n == null || !isFinite(ms)) return;
      out.push({ ms, n });
    });
    out.sort((a, b) => a.ms - b.ms);
    return out;
  }
  function rose(pts, asofMs) {
    if (!pts || pts.length < 2) return false;
    const fresh = pts.filter(p => asofMs - p.ms <= 9 * 36e5 && p.ms <= asofMs + 36e5);
    if (fresh.length < 2) return false;
    const last = fresh[fresh.length - 1];
    let prev = null;
    fresh.forEach(p => {
      const h = (last.ms - p.ms) / 36e5;
      if (h >= 4 && h <= 10) prev = p;
    });
    return !!(prev && last.n > prev.n);
  }
  function volumeRose(big, ticker, asofMs) {
    const moves = (big && big.moves) || [];
    if (!moves.length) return false;
    let later = 0, prior = 0, nLater = 0, newest = 0;
    moves.forEach(m => {
      if (!m || validRaw(m.ticker) !== ticker) return;
      const ms = stamp(m.time);
      const ada = Math.abs(num(m.ada) || 0);
      if (!isFinite(ms)) return;
      if (ms > newest) newest = ms;
      if (ms > asofMs - 6 * 36e5 && ms <= asofMs + 15 * 6e4) { later += ada; nLater += 1; }
      else if (ms > asofMs - 12 * 36e5 && ms <= asofMs - 6 * 36e5) prior += ada;
    });
    if (newest < asofMs - 9 * 36e5 || !nLater) return false;
    return later > prior;
  }

  function explicitPct(src) {
    if (!src || typeof src !== "object") return null;
    const keys = [
      ["change_24h", "24h"], ["pct_24h", "24h"], ["price_h24_pct", "24h"], ["h24_pct", "24h"],
      ["change24hPct", "24h"], ["price_24h_pct", "24h"],
      ["price_h6_pct", "6h"], ["h6_pct", "6h"], ["change_6h", "6h"]
    ];
    for (let i = 0; i < keys.length; i++) {
      const n = num(src[keys[i][0]]);
      if (n != null) return { n, window: keys[i][1] };
    }
    const extra = src.extra || {};
    const e = num(extra.change24hPct);
    if (e != null) return { n: e, window: "24h" };
    return null;
  }
  function explicitFlow(src) {
    if (!src) return null;
    const buys = num(src.buys_6h != null ? src.buys_6h : src.buys_h6);
    const sells = num(src.sells_6h != null ? src.sells_6h : src.sells_h6);
    if (buys == null || sells == null) return null;
    return { buys, sells };
  }
  function parsePct(text) {
    const s = String(text || "").replace(/[−–]/g, "-");
    let m = s.match(/price\s+([+-]?\d+(?:\.\d+)?)\s*%\s*(?:in\s*)?(24\s*h|h24|6\s*h|h6)?/i);
    if (m) return { n: Number(m[1]), window: /6/.test(m[2] || "") ? "6h" : (m[2] ? "24h" : "") };
    m = s.match(/([+-]?\d+(?:\.\d+)?)\s*%\s*(?:in\s*)?(24\s*h|h24)/i);
    if (m) return { n: Number(m[1]), window: "24h" };
    m = s.match(/(?:h24|24\s*h)\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\s*%/i);
    if (m) return { n: Number(m[1]), window: "24h" };
    m = s.match(/(?:h6|6\s*h)\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\s*%/i);
    if (m) return { n: Number(m[1]), window: "6h" };
    m = s.match(/([+-]?\d+(?:\.\d+)?)\s*%\s*(?:in\s*)?(?:6\s*h|h6)/i);
    if (m) return { n: Number(m[1]), window: "6h" };
    return null;
  }
  function parseFlow(text, require6h) {
    const s = String(text || "");
    if (require6h && !/6\s*h|\bh6\b/i.test(s)) return null;
    let m = s.match(/(\d+)\s*buys?\s*\/\s*(\d+)\s*sells?/i);
    if (m) return { buys: Number(m[1]), sells: Number(m[2]) };
    m = s.match(/buys?\s+(\d+)\s*\/\s*(\d+)/i);
    if (m) return { buys: Number(m[1]), sells: Number(m[2]) };
    return null;
  }
  function killPct(src) {
    if (!src) return null;
    const given = num(src.pct_from_line);
    if (given != null) return given;
    const value = num(src.kill_value);
    const limit = num(src.kill_limit != null ? src.kill_limit : src.kill_threshold);
    if (value == null || !(limit > 0)) return null;
    return (value - limit) / limit * 100;
  }
  function padLabel(pad) {
    const s = String(pad || "").toLowerCase();
    if (!s) return "";
    if (s === "solada") return "Solada";
    if (s === "snek" || s === "snek.fun" || s === "snekfun") return "Snek.fun";
    if (s === "new" || s === "unknown") return "New";
    if (s === "established") return "Established";
    if (s === "minswap") return "Minswap";
    if (!/^[a-z0-9.-]{2,16}$/.test(s)) return "";
    return s.slice(0, 1).toUpperCase() + s.slice(1);
  }
  function ageHours(age) {
    if (typeof age === "number" && isFinite(age)) return age;
    const s = String(age || "").trim().toLowerCase();
    if (!s) return null;
    const under = s.charAt(0) === "<";
    const m = s.match(/(\d+(?:\.\d+)?)\s*(d|h)/);
    if (!m) return null;
    let h = m[2] === "d" ? Number(m[1]) * 24 : Number(m[1]);
    if (under) h -= 0.1;
    return h;
  }
  function cmpPct(a, b) {
    const av = a.pct && a.pct.n != null ? a.pct.n : -Infinity;
    const bv = b.pct && b.pct.n != null ? b.pct.n : -Infinity;
    return av - bv;
  }
  function statusText(t, ctx) {
    const bits = [];
    if (ctx.sw) bits.push(ctx.sw + " SW-01 pick" + (ctx.sw > 1 ? "s" : ""));
    else if (ctx.pendingSw) bits.push(ctx.pendingSw + " swarm launch" + (ctx.pendingSw > 1 ? "es" : "") + " on the SW-01 watch");
    if (ctx.est) bits.push(ctx.est + " EST-01 pick" + (ctx.est > 1 ? "s" : ""));
    if (ctx.moving) bits.push(ctx.moving + " pass" + (ctx.moving > 1 ? "es" : "") + " still moving");
    if (ctx.quiet) bits.push("Established quiet.");
    if (bits.length) return bits.join(". ").replace(/\.\./g, ".");
    const learned = (ctx.learned || []).map(s => clean(t, typeof s === "string" ? s : "")).filter(Boolean)[0];
    if (learned) return clip(learned, 180);
    const head = clean(t, ctx.headline);
    if (head) return clip(head, 180);
    return "No Cardano board on this run.";
  }

  function validTicker(t, v) {
    const s = t.tickerName ? t.tickerName(v) : String(v || "");
    return validRaw(s);
  }
  function validRaw(v) {
    const name = String(v || "").trim().replace(/^\$/, "");
    if (!/^[A-Za-z][A-Za-z0-9]{0,15}$/.test(name)) return "";
    return name.toUpperCase();
  }
  function clean(t, s) {
    return t.sanitize(String(s ?? ""))
      .replace(/\bu(?=[0-9a-z]*\d)[0-9a-z]{4,16}\b/gi, "")
      .replace(/\bw-[0-9a-f]{8}\b/gi, "")
      .replace(/\s{2,}/g, " ")
      .replace(/\s+([,.;])/g, "$1")
      .trim();
  }
  function asObj(x) {
    return x && typeof x === "object" && !Array.isArray(x) ? x : null;
  }
  function num(v) {
    if (typeof v === "boolean" || v == null || v === "") return null;
    const n = typeof v === "number" ? v : Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function stamp(v) {
    if (v == null || v === "") return NaN;
    if (typeof v === "number" && v > 1e9 && v < 1e12) return v * 1000;
    const ms = Date.parse(String(v));
    return isFinite(ms) ? ms : NaN;
  }
  function clock(iso) {
    const ms = stamp(iso);
    if (!isFinite(ms)) return "";
    try {
      return new Date(ms).toLocaleString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" }) + " ET";
    } catch (e) {
      return "";
    }
  }
  function compact(n) {
    if (n == null || !isFinite(n)) return "";
    const sign = n < 0 ? "−" : "";
    const a = Math.abs(n);
    if (a >= 1e6) return sign + trimNum(a / 1e6) + "M";
    if (a >= 1e3) return sign + trimNum(a / 1e3) + "K";
    return sign + trimNum(a);
  }
  function trimNum(n) {
    const d = Math.abs(n) >= 100 ? 0 : Math.abs(n) >= 10 ? 1 : 1;
    return n.toFixed(d).replace(/\.0$/, "");
  }
  function clip(s, n) {
    const t = String(s || "");
    return t.length > n ? t.slice(0, n - 1).trim() + "…" : t;
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  root.CardanoBoard = {
    render: function (t, data) {
      return render(t || {}, data || {});
    }
  };
})(typeof window !== "undefined" ? window : globalThis);
