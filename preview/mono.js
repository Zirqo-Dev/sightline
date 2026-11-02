/* One-page Sightline. Research only. Renders the v17 section order from desk JSON.
   Prices come from prices.json. The browser does not call a price host. */
(function (root) {
  "use strict";

  function P() { return root.Preview || {}; }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function tx(s) {
    const clean = P().sanitize || function (v) { return String(v ?? ""); };
    return esc(clean(s));
  }
  function num(v) {
    if (typeof v === "boolean" || v == null || v === "") return null;
    const n = typeof v === "number" ? v : Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function compact(n) {
    const a = Math.abs(n);
    if (a >= 1e6) return (a / 1e6).toFixed(3).replace(/0+$/, "").replace(/\.$/, "") + "M";
    if (a >= 1000) return (a / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(Math.round(a));
  }
  function signedPct(n) {
    if (n == null || !isFinite(n)) return '<span class="mu">–</span>';
    const cls = n > 0 ? "up" : n < 0 ? "dn" : "mu";
    const body = (n > 0 ? "+" : n < 0 ? "−" : "") + (Math.round(Math.abs(n) * 10) / 10).toString().replace(/\.0$/, "") + "%";
    return '<span class="' + cls + '">' + esc(body) + "</span>";
  }
  function usdText(n) {
    if (n == null || !isFinite(n)) return "–";
    const a = Math.abs(n);
    const body = a >= 1000 ? "$" + Math.round(a).toLocaleString("en-US") : "$" + (a >= 100 ? a.toFixed(0) : a.toFixed(2).replace(/\.00$/, ""));
    return (n < 0 ? "−" : "") + body;
  }
  function clip(s, n) {
    const t = String(s || "").replace(/\s+/g, " ").trim();
    return t.length > n ? t.slice(0, n - 1).trim() + "…" : t;
  }
  function whyText(v) {
    return [].concat(v && v.why || [], v && v.reasons || []).join(" ");
  }
  function pricePct(v) {
    const m = whyText(v).match(/price\s+([+\-−]?\d+(?:\.\d+)?)\s*%/i);
    if (!m) return null;
    return Number(m[1].replace("−", "-"));
  }
  function passedMoving(v) {
    const blob = ((v && v.kill_line) || "") + " " + whyText(v);
    return /rule k/i.test(blob) && /rule y/i.test(blob) && /fail/i.test(blob);
  }
  function contextOnly(v) {
    return /context only/i.test((v && v.kill_line) || "") || /context only/i.test(whyText(v));
  }
  function nearKill(v) {
    const pct = num(v && v.pct_from_line);
    const metric = String((v && v.kill_metric) || "");
    return /pool|ada/.test(metric) && pct != null && pct < 0 && pct >= -8;
  }
  function pctLabel(pct) {
    const abs = Math.abs(pct);
    return (abs >= 10 ? String(Math.round(abs)) : String(Math.round(abs * 10) / 10)) + "%";
  }
  function pill(text, kind) {
    const cls = kind === "amber" ? "p a" : kind === "down" ? "p d" : "p";
    return '<span class="' + cls + '">' + esc(text) + "</span>";
  }
  function logo(ticker) {
    const fn = P().assetLogo;
    if (typeof fn === "function") return fn(ticker, ticker, false);
    return '<span class="sl-logo">' + esc(String(ticker || "?").slice(0, 2)) + "</span>";
  }
  function row(ticker, nameHtml, sub, end) {
    return '<div class="row">' + logo(ticker) + '<div class="gr"><div class="nm">' + nameHtml + '</div><div class="sub">' + sub + '</div></div><div class="r m">' + end + "</div></div>";
  }
  function ageMs(iso) {
    const t = Date.parse(iso || "");
    return Number.isFinite(t) ? Date.now() - t : Infinity;
  }
  function briefAge(date) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || "");
    if (!m) return Infinity;
    return Date.now() - Date.UTC(+m[1], +m[2] - 1, +m[3], 16, 0, 0);
  }
  function etClock(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const day = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "America/Toronto" }).format(d).replace(",", "");
    const time = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" }).format(d);
    return day + " · " + time + " ET";
  }
  function ago(iso) {
    const s = ageMs(iso) / 1000;
    if (!isFinite(s)) return "";
    if (s < 90) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m";
    if (s < 86400) return Math.round(s / 3600) + "h";
    return Math.round(s / 86400) + "d";
  }
  function failureRate(devs) {
    const cov = devs && devs.coverage;
    if (!cov) return null;
    const tracked = num(cov.tracked);
    const skipped = Array.isArray(cov.skipped) ? cov.skipped.length : num(cov.skipped);
    if (tracked == null || tracked <= 0 || skipped == null) return null;
    return skipped / tracked;
  }
  function freshest(pack) {
    const times = [];
    const push = t => { const ms = Date.parse(t || ""); if (Number.isFinite(ms)) times.push(ms); };
    const c = (pack && pack.cardano) || {};
    push(c.log && c.log.asof);
    push(c.devs && c.devs.at);
    push(c.whale && c.whale.at);
    push(c.liq && c.liq.at);
    push(pack && pack.buzz && pack.buzz.asof);
    push(pack && pack.prices && pack.prices.asof);
    const h = (pack && pack.health) || {};
    push(h.generated_at || h.asof);
    const run = h.last_run_utc;
    if (run && /^\d{8}T\d{4}/.test(String(run))) {
      const m = String(run).match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/);
      if (m) push(m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":00Z");
    }
    if (!times.length) return null;
    return new Date(Math.max.apply(null, times)).toISOString();
  }
  function bookStats(paper) {
    const closed = (paper && paper.closed) || [];
    const open = (paper && paper.open) || [];
    let wins = 0, pnl = 0;
    closed.forEach(c => {
      const res = (c && c.result) || {};
      const metric = num(res.pct) != null ? num(res.pct) : num(res.pnl);
      if (metric != null && metric > 0) wins += 1;
      if (num(res.pnl) != null) pnl += num(res.pnl);
    });
    return { wins, pnl, open: open.length, closed: closed.length };
  }
  function verdicts(log) {
    return (log && Array.isArray(log.verdicts) ? log.verdicts : []).filter(v => v && v.ticker);
  }
  function topSet(log) {
    const rows = (log && log.cardano_top10) || [];
    const order = {};
    rows.forEach((r, i) => { if (r && r.ticker) order[String(r.ticker).toUpperCase()] = r.rank || i + 1; });
    return order;
  }
  function runnerSub(v) {
    const bits = [];
    if (num(v.pool_ada) != null) bits.push("Pool " + compact(v.pool_ada) + " ₳");
    if (num(v.holders) != null) bits.push(num(v.holders) + " h");
    const whale = clip(v.whale_line || v.whale_activity || "", 52);
    if (whale && !/none tracked/i.test(whale)) bits.push(whale);
    else if (!bits.length) bits.push(clip(whyText(v), 72));
    return tx(bits.join(" · "));
  }
  function estSub(v) {
    const bits = [];
    const metric = String(v.kill_metric || "");
    const poolLine = /pool|ada_side/.test(metric);
    if (poolLine && num(v.pool_ada) != null) bits.push("Side " + compact(v.pool_ada) + " ₳");
    if (poolLine && num(v.pct_from_line) != null && num(v.kill_limit) != null) {
      const pct = num(v.pct_from_line);
      bits.push((pct < 0 ? pctLabel(pct) + " under " : pctLabel(pct) + " above ") + compact(v.kill_limit) + " line");
    } else if (num(v.holders) != null) bits.push(num(v.holders) + " h");
    else if (num(v.pool_ada) != null) bits.push("Side " + compact(v.pool_ada) + " ₳");
    if (/whale/.test(metric) && num(v.pct_from_line) != null && Math.abs(num(v.pct_from_line)) < 1) bits.push("on its sell line");
    const whale = clip(v.whale_line || v.whale_activity || "", 64);
    if (whale && !/none tracked/i.test(whale)) bits.push(whale);
    return tx(bits.join(" · "));
  }
  function nameBits(v, lane) {
    const moving = passedMoving(v);
    const watch = String(v.verdict || "").toLowerCase() === "watch";
    let tag = pill("pass", "");
    if (moving) tag = pill("passed · moving", "amber");
    else if (watch) tag = pill("watch", "amber");
    else if (lane === "established" && nearKill(v)) tag = pill("near kill", "down");
    return tx(v.ticker) + " " + tag;
  }

  function sentinel(log) {
    const list = verdicts(log);
    const open = ((log && log.paper_actions) || []).join(" ");
    const cleared = /cleared rule k/i.test(open) && !/nothing (cleared|passes)/i.test(open);
    const moving = list.filter(passedMoving);
    const closest = moving[0] || null;
    let head = '<div class="row"><div class="gr"><div class="nm">No Sentinel pick right now</div><div class="sub">Nothing cleared rule k + y this run. Closest name below.</div></div></div>';
    if (cleared && closest == null) head = "";
    const closestSub = closest ? String(closest.kill_line || whyText(closest)).replace(/^already failing:\s*/i, "") : "";
    const body = closest
      ? row(closest.ticker, nameBits(closest, "runner"), tx(clip(closestSub, 140)), signedPct(pricePct(closest)))
      : "";
    const when = log && log.asof ? etClock(log.asof) : "";
    return '<section class="c hero span" id="sentinel"><div class="h"><h2>Sentinel\'s pick</h2><span class="k">' + esc(when ? "Cardano · " + when : "Cardano") + "</span></div>" + head + body + "</section>";
  }
  function lanes(log) {
    const list = verdicts(log);
    const rank = topSet(log);
    const runners = list.filter(v => !rank[String(v.ticker).toUpperCase()] && v.launchpad !== "established" && /pass|watch/i.test(v.verdict || ""));
    runners.sort((a, b) => {
      const pin = (passedMoving(b) ? 1 : 0) - (passedMoving(a) ? 1 : 0);
      if (pin) return pin;
      return (num(b.pool_ada) || 0) - (num(a.pool_ada) || 0);
    });
    const shownR = runners.slice(0, 5);
    const more = runners.slice(5).filter(v => num(v.pool_ada)).slice(0, 5).map(v => v.ticker);
    const launches = (log && log.cardano_v2 && log.cardano_v2.launches) || [];
    launches.forEach(l => {
      if (more.length >= 5) return;
      if (l && l.swarm && l.ticker && !shownR.some(v => v.ticker === l.ticker) && more.indexOf(l.ticker) < 0) more.push(l.ticker);
    });
    const runnerRows = shownR.map(v => row(v.ticker, nameBits(v, "runner"), runnerSub(v), signedPct(pricePct(v)))).join("");
    const moreLine = more.length ? '<div class="lane">+' + more.length + " more · " + esc(more.join(" ")) + " — all pass</div>" : "";
    const radar = launches[0];
    let radarLine = "";
    if (radar && radar.ticker) {
      const bits = [radar.ticker, radar.age, num(radar.holders) != null ? radar.holders + " h" : ""].filter(Boolean);
      const flags = [radar.swarm ? "swarm" : "", radar.sniper ? "sniper" : ""].filter(Boolean).join(" + ");
      const verdict = (list.find(v => v.ticker === radar.ticker) || {}).verdict || "pass";
      radarLine = '<div class="lane mu">Launch radar: ' + esc(bits.join(" · ") + (flags ? " · " + flags : "") + " → " + verdict) + "</div>";
    }
    const established = list.filter(v => rank[String(v.ticker).toUpperCase()] && !contextOnly(v));
    established.sort((a, b) => (rank[a.ticker.toUpperCase()] || 99) - (rank[b.ticker.toUpperCase()] || 99));
    const picked = [];
    established.forEach(v => {
      if (nearKill(v) || /watch/i.test(v.verdict || "")) picked.push(v);
    });
    established.forEach(v => { if (picked.indexOf(v) < 0 && picked.length < 5) picked.push(v); });
    const shownE = picked.slice(0, 5).sort((a, b) => (rank[a.ticker.toUpperCase()] || 99) - (rank[b.ticker.toUpperCase()] || 99));
    const shownNames = {};
    shownE.forEach(v => { shownNames[v.ticker.toUpperCase()] = 1; });
    const context = Object.keys(rank).filter(name => !shownNames[name]).sort((a, b) => rank[a] - rank[b]);
    const estRows = shownE.map(v => row(v.ticker, nameBits(v, "established"), estSub(v), signedPct(pricePct(v)))).join("");
    const ctx = context.length ? '<div class="lane">+' + context.length + " context · " + esc(context.join(" ")) + "</div>" : "";
    return '<section class="c" id="runners"><div class="h"><h2>Runners · fresh launch</h2><span class="k">target 10–25x · 24h</span></div>' + runnerRows + moreLine + radarLine + "</section>"
      + '<section class="c" id="established"><div class="h"><h2>Established · high cap</h2><span class="k">kill-line distance · 24h</span></div>' + estRows + ctx + "</section>";
  }
  function paperTests(log, paper, pump) {
    const actions = (log && log.paper_actions) || [];
    const sw = actions.find(s => /^SW-01/i.test(s)) || "";
    const est = (log && log.est01_check) || {};
    const rows = Array.isArray(est.rows) ? est.rows : [];
    const names = rows.map(r => r.ticker).filter(Boolean).join(", ");
    const swTitle = /no qualifying|no ticket/i.test(sw) ? "No ticket" : (sw ? "Ticket" : "No ticket");
    const swSub = sw.replace(/^SW-01:\s*/i, "") || "No swarm launch has cleared the gate.";
    const checks = rows[0] || {};
    let passed = 0;
    if (num(checks.pct_from_line) != null && num(checks.pct_from_line) > 0) passed += 1;
    if (checks.whale) passed += 1;
    if (checks.pool_7d_pct != null) passed += 1;
    if (typeof checks.holders_7d === "number") passed += 1;
    const width = rows.length ? Math.round(passed / 4 * 100) : 0;
    const estSub = est.result || "No EST-01 row this run.";
    const a = bookStats(paper), b = bookStats(pump);
    const closed = a.closed + b.closed;
    const wins = a.wins + b.wins;
    const win = closed ? Math.round(100 * wins / closed) : 0;
    const pnl = n => (n < 0 ? "−" : n > 0 ? "+" : "") + (Math.round(Math.abs(n) * 10) / 10);
    const book = "Cardano paper book: " + (a.open + b.open) + " open · " + closed + " closed · " + win + "% win · " + pnl(a.pnl) + " ₳ (Solada) · " + pnl(b.pnl) + " ₳-eq (Pump.fun)";
    return '<section class="c span" id="paper"><div class="h"><h2>Paper tests</h2><span class="k">next gates Nov 2 · Nov 8</span></div><div class="two">'
      + '<div class="box"><div class="lab">SW-01 · swarm runner</div><div class="nm" style="margin-top:4px">' + esc(swTitle) + '</div><div class="sub">' + tx(swSub) + '</div><div class="bar"><i style="width:0"></i></div></div>'
      + '<div class="box"><div class="lab">EST-01 · established</div><div class="nm" style="margin-top:4px">' + esc(names || "No names") + " " + pill("watch", "amber") + '</div><div class="sub">' + tx(estSub) + '</div><div class="bar"><i style="width:' + width + '%"></i></div></div>'
      + '</div><div class="sub" style="margin-top:8px;white-space:normal">' + esc(book) + "</div></section>";
  }
  function whales(log) {
    const list = (log && Array.isArray(log.whales) ? log.whales : []).filter(w => w && w.codename);
    const rows = list.slice(0, 4).map(w => {
      const n = num(w.net_flow_ada);
      const quiet = /quiet|no moves/i.test(w.line || "");
      let end = "–";
      if (n == null) end = "–";
      else if (Math.abs(n) < 1) end = quiet ? "0 ₳" : "net ~0 ₳";
      else end = (n > 0 ? "+" : "−") + compact(Math.abs(n)) + " ₳";
      const cls = n > 1 ? "up" : "mu";
      return '<div class="row"><div class="gr"><div class="nm">' + tx(w.codename) + '</div><div class="sub">' + tx(clip(w.line || "", 110)) + '</div></div><div class="r m ' + cls + '">' + esc(end) + "</div></div>";
    }).join("");
    const swarm = (log && log.cardano_v2 && Array.isArray(log.cardano_v2.swarms) && log.cardano_v2.swarms[0]) || null;
    let swarmRow = "";
    if (swarm) {
      const seen = (swarm.last_seen || swarm.launches || []).map(x => (x && x.ticker) || x).filter(Boolean).slice(0, 4);
      swarmRow = '<div class="row"><div class="gr"><div class="nm">' + tx(swarm.name || "Early buyers") + '</div><div class="sub">' + esc((swarm.wallets || seen.length) + " wallets · " + seen.join(" ") + (seen.length ? " …" : "")) + '</div></div><div class="r m mu">' + tx(swarm.label || "early buyers") + "</div></div>";
    }
    const trends = (log && log.cardano_v2 && log.cardano_v2.rotation && log.cardano_v2.rotation.trends) || [];
    const group = kind => trends.filter(t => t && t.trend === kind).map(t => t.token);
    const flow = [
      group("accumulating").length ? "▲ " + group("accumulating").join(" · ") + " accumulating" : "",
      group("trimming").length ? "▼ " + group("trimming").join(" · ") + " trimming" : "",
      group("exiting").length ? "✖ " + group("exiting").join(" · ") + " exiting" : "",
      group("re-entering").length ? "▲ " + group("re-entering").join(" · ") + " re-entering" : ""
    ].filter(Boolean).join("   ");
    return '<section class="c" id="whales"><div class="h"><h2>Whale codenames</h2><span class="k">last 24h</span></div>' + rows + swarmRow
      + (flow ? '<p class="t" style="margin-top:8px">' + esc(flow) + "</p>" : "") + "</section>";
  }
  function solana(model, health) {
    const m = model || { bookRows: [], opens: [], activity: [] };
    const names = { F: "desk", R: "random", S: "shadow" };
    const body = (m.bookRows || []).map(b => "<tr><td>" + esc(b.id) + " " + esc(names[b.id] || "") + "</td><td class=\"" + (b.pnl < 0 ? "dn" : b.pnl > 0 ? "up" : "") + "\">" + esc(b.pnl == null ? "–" : (b.pnl < 0 ? "−" : "") + Math.abs(b.pnl).toFixed(3)) + "</td><td class=\"" + (b.exp < 0 ? "dn" : "") + "\">" + esc(b.exp == null ? "–" : (b.exp < 0 ? "−" : "") + (Math.abs(b.exp) * 100).toFixed(1) + "%") + "</td><td>" + esc(b.n) + "</td></tr>").join("");
    const stop = m.day14Rule === "STOP";
    const gap = m.gap == null ? "–" : ((m.gap < 0 ? "−" : m.gap > 0 ? "+" : "") + Math.abs(m.gap).toFixed(1) + " pp");
    const until = m.day14Verdict === "NOT_FINAL" ? "NOT_FINAL until Nov 11, 10:00 AM ET" : ("VERDICT " + (m.day14Verdict || ""));
    const counts = { F: 0, R: 0, S: 0 };
    (m.opens || []).forEach(p => { if (counts[p.book] != null) counts[p.book] += 1; });
    let best = null;
    (m.opens || []).forEach(p => {
      if (!(p.entry > 0) || p.mark == null) return;
      const pct = (p.mark / p.entry - 1) * 100;
      if (!best || Math.abs(pct) > Math.abs(best.pct)) best = { ticker: p.ticker, pct };
    });
    const last = (m.activity || []).find(a => /sell|exit|close/i.test(a.action || "") && a.reason);
    const fail = num(health && health.data_failure_rate);
    const ok = fail == null ? "–" : Math.round((1 - fail) * 100) + "%";
    const runs = (health && health.runs != null ? health.runs : "–") + "/" + (health && health.expected_runs != null ? health.expected_runs : "–");
    const bestBit = best ? best.ticker + " " + (best.pct > 0 ? "+" : "−") + Math.abs(best.pct).toFixed(1) + "%" : "no open mark";
    const netBit = "net " + (m.net < 0 ? "−" : "") + (m.net == null ? "–" : Math.abs(m.net).toFixed(2)) + " / gross " + (m.gross < 0 ? "−" : "") + (m.gross == null ? "–" : Math.abs(m.gross).toFixed(2)) + " SOL";
    const closeBit = last ? "last close " + last.symbol + " · " + clip(last.reason, 80) : "no recent close";
    const summary = (m.openCount || 0) + " open (" + counts.F + " F / " + counts.R + " R) · " + bestBit + " · " + netBit + " · " + closeBit + " · data OK " + ok + " · " + runs + " runs";
    return '<section class="c" id="solana"><div class="h"><h2>Solana · F / R / S</h2><span class="k">day-14 verdict script</span></div>'
      + '<table><tr><th>Book</th><th>P&amp;L SOL</th><th>Exp/tr</th><th>N</th></tr>' + body + "</table>"
      + '<div class="row" style="margin-top:6px"><div class="gr"><div class="nm">Rule: ' + esc(m.day14Rule || "–") + " " + (stop ? pill("F below −15%", "down") : "") + '</div><div class="sub">F vs R ' + esc(gap) + " · " + esc(until) + "</div></div></div>"
      + '<div class="sub" style="white-space:normal">' + tx(summary) + "</div></section>";
  }
  function buzz(doc) {
    const tokens = (doc && Array.isArray(doc.tokens)) ? doc.tokens.slice() : [];
    tokens.sort((a, b) => (b.mentions_1h || 0) - (a.mentions_1h || 0) || (b.mentions_24h || 0) - (a.mentions_24h || 0));
    const live = tokens.filter(t => (t.mentions_1h || 0) > 0).slice(0, 4);
    const badge = live.length ? "" : '<span class="ex">EXAMPLE DATA</span>';
    let rows = "";
    if (live.length) {
      rows = live.map(t => {
        const ratio = t.velocity && t.velocity.ratio_1h_vs_prev_hr;
        const ratioBit = ratio == null ? "" : " · " + ratio + "× prior hour";
        return row(t.ticker, tx(t.ticker), esc((t.mentions_1h || 0) + " posts / 1h" + ratioBit), '<span class="mu">measured</span>');
      }).join("");
    } else {
      rows = '<div class="sub" style="margin-top:6px">Live x_buzz.json not published yet (404).</div>';
    }
    return '<section class="c" id="buzz"><div class="h"><h2>X buzz <span class="p">measured, not a signal</span></h2>' + badge + "</div>" + rows + "</section>";
  }
  function normKey(s) { return String(s || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }
  function withFloors(rows, pulse) {
    const by = {};
    ((pulse && pulse.latest) || []).forEach(r => { if (r && r.ticker) by[normKey(r.ticker)] = r; });
    const keys = Object.keys(by);
    function hitFor(row) {
      const want = [normKey(row.slug), normKey(row.name)].filter(k => k.length >= 4);
      for (let i = 0; i < want.length; i++) if (by[want[i]]) return by[want[i]];
      for (let i = 0; i < want.length; i++) {
        const k = want[i];
        const name = keys.find(n => (n.indexOf(k) === 0 || k.indexOf(n) === 0) && Math.min(n.length, k.length) >= 5);
        if (name) return by[name];
      }
      return null;
    }
    return rows.map(r => {
      if (num(r.floor) != null) return r;
      const hit = hitFor(r);
      if (!hit || num(hit.floor) == null) return r;
      return Object.assign({}, r, { floor: hit.floor, unit: hit.unit || r.unit });
    });
  }
  function floorUsd(floor, unit, prices) {
    const n = num(floor);
    if (n == null) return null;
    const u = String(unit || "").toUpperCase();
    if (u === "USD" || u === "USDC" || u === "USDT") return n;
    const q = prices && prices.quotes && num(prices.quotes[u]);
    return q == null ? null : n * q;
  }
  function nft(pulse, log, paper, prices) {
    const rowsIn = withFloors((P().nftRows ? P().nftRows(pulse, log) : []).filter(r => r && !r.example), pulse);
    const live = rowsIn.length > 0 && prices && prices.quotes;
    const badge = live ? "" : '<span class="ex">EXAMPLE USD</span>';
    const picks = ((paper && paper.open) || []).filter(t => t && !t.closed_at);
    const rows = rowsIn.slice(0, 12).map(r => {
      const usd = floorUsd(r.floor, r.unit, prices);
      const watch = /watch/i.test(r.status || "");
      const tag = r.status ? pill(String(r.status).toLowerCase(), watch ? "amber" : "") : "";
      const sub = [r.chain, usd == null ? "floor not in the file" : ""].filter(Boolean).join(" · ");
      return row(r.slug || r.name, tx(r.name) + " " + tag, tx(sub), esc(usd == null ? "–" : usdText(usd)));
    }).join("");
    const when = (log && log.asof) || (pulse && pulse.updated_at) || "";
    const age = when ? ago(when) : "";
    const quoteLine = prices && prices.quotes
      ? ["ETH", "SOL", "ADA", "HYPE"].filter(s => num(prices.quotes[s]) != null).map(s => s + " " + usdText(prices.quotes[s])).join(" · ")
        + " · " + (prices.source || "stored quote") + (prices.asof ? " · " + etClock(prices.asof) : "")
      : "No stored USD quote yet.";
    const head = (picks.length ? "" : "No KING pick · ") + rowsIn.length + " names · desk feed last updated " + (etClock(when) || "–") + (age ? " (" + age + ")" : "");
    return '<section class="c" id="nft"><div class="h"><h2>NFT · KING watchlist</h2>' + badge + "</div>" + rows
      + '<div class="sub" style="margin-top:6px;white-space:normal">' + esc(head) + "</div>"
      + '<div class="sub" style="white-space:normal">' + esc(quoteLine) + "</div></section>";
  }
  function desk(pack) {
    const briefs = (pack && pack.briefs) || [];
    const fresh = briefs.filter(b => briefAge(b.date) < 36 * 3600 * 1000);
    const stale = briefs.filter(b => briefAge(b.date) >= 36 * 3600 * 1000);
    const log = pack && pack.cardano && pack.cardano.log;
    const logFresh = log && ageMs(log.asof) < 30 * 3600 * 1000;
    let body = "";
    if (fresh.length) {
      body = fresh.map(b => '<p class="t"><span class="lab">' + esc(b.desk || "Desk") + " · " + esc(b.date || "") + "</span><br>" + tx(b.headline) + "</p>").join("");
    } else if (logFresh && log.headline) {
      body = '<p class="t"><span class="lab">Cardano · this run</span><br>' + tx(log.headline) + "</p>";
    } else {
      body = '<p class="t">No fresh desk note.</p>';
    }
    let staleLine = "";
    if (stale.length) {
      const latest = stale.slice().sort((a, b) => String(b.date).localeCompare(String(a.date)))[0];
      const days = Math.round(briefAge(latest.date) / 86400000);
      const pillBit = fresh.length ? "" : ' <span class="p a">brief pipeline stalled</span>';
      staleLine = '<p class="t mu" style="font-size:11px">Last full daily briefs: ' + esc(latest.date) + " — hidden as stale (" + days + " days)." + pillBit + "</p>";
    }
    const stamp = (fresh[0] && fresh[0].date) || (logFresh && log.asof ? etClock(log.asof) : "–");
    return '<section class="c span" id="desk"><div class="h"><h2>Latest from the desk</h2><span class="k">fresh only · ' + esc(stamp) + "</span></div>" + body + staleLine + "</section>";
  }
  function status(pack) {
    if (!pack) return '<span class="m">loading…</span>';
    const iso = freshest(pack);
    const rate = failureRate(pack.cardano && pack.cardano.devs);
    const warn = rate != null && rate >= 0.25
      ? '<br><span class="w">Cardano feed ' + (rate * 100).toFixed(1).replace(/\.0$/, "") + "% failures</span>"
      : "";
    const clock = iso ? etClock(iso) : "";
    const updated = iso ? " · updated " + ago(iso) : "";
    return esc(clock) + esc(updated) + warn;
  }
  function html(pack) {
    const c = (pack && pack.cardano) || {};
    const log = c.log || null;
    return sentinel(log) + lanes(log) + paperTests(log, c.paper, c.pump) + whales(log)
      + solana(pack && pack.solanaModel, pack && pack.health)
      + buzz(pack && pack.buzz)
      + nft(pack && pack.nftPulse, pack && pack.nftLog, pack && pack.nftPaper, pack && pack.prices)
      + desk(pack);
  }
  root.SightlineMono = { html, status, floorUsd, passedMoving, failureRate };
})(typeof window !== "undefined" ? window : globalThis);
