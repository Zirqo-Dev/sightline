/* Renders the build-time picks file. Research and paper only. No buy buttons.
   The risk line is display only. It is not a score and it does not size a ticket. */
(function (root) {
  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function pill(text, cls) {
    if (!text) return "";
    return `<span class="p ${cls || ""}">${esc(text)}</span>`;
  }
  function logo(api, key) {
    if (!api || !api.assetLogo || !key) return "";
    const html = api.assetLogo(key, key, false);
    return html.indexOf("<img") === 0 ? html.replace("<img ", '<img onerror="this.remove()" ') : html;
  }
  function risk(row) {
    const r = (row && row.risk) || {};
    const swarm = r.swarm ? `<span class="rk bad">swarm</span>` : "";
    return `<div class="sig">${swarm}<span class="rk ${esc(r.kill_cls || "mu")}">${esc(r.kill || "kill distance not logged")}</span><span class="rk">${esc(r.whale || "no whale flow logged")}</span><span class="rk">${esc(r.holders || "holders not logged")}</span></div>`;
  }
  function pk(api, row, first) {
    if (!row) return "";
    return `<div class="row pk${first ? " f" : ""}">${logo(api, row.logo || row.ticker)}<div class="gr"><div class="nm">${esc(row.ticker)} ${pill(row.pill, row.pill_cls)}</div><div class="why"><b>WHY</b> ${esc(row.why)}</div><div class="chg"><b>CHANGE MY MIND</b> ${esc(row.change)}</div>${risk(row)}</div><div class="r m ${row.right_cls || "mu"}">${esc(row.right || "–")}</div></div>`;
  }
  function more(rows, api, label) {
    if (!rows || !rows.length) return "";
    return `<details><summary>+${rows.length} ${esc(label || "more")}</summary>${rows.map(r => pk(api, r, false)).join("")}</details>`;
  }
  function legend(doc) {
    return `<div class="legend">${esc(doc.legend || "")}</div>`;
  }
  function card(title, meta, body, cls) {
    return `<section class="c ${cls || ""}"><div class="h"><h2>${title}</h2><span class="k">${meta || ""}</span></div>${body}</section>`;
  }
  function missLine(row) {
    const miss = row.missing || [];
    if (!miss.length) return "every logged check passed";
    if (miss.length === 1) return "missing only the " + miss[0];
    return "missing " + miss.join(", ");
  }
  function checks(rows) {
    if (!rows || !rows.length) return "";
    return rows.map(r => {
      const bits = (r.checks || []).map(c => `<span class="ck ${c.pass ? "ok" : "no"}">${c.pass ? "pass" : "fail"} ${esc(c.label)}${c.detail ? " · " + esc(c.detail) : ""}</span>`).join("");
      return `<div class="row pk"><div class="gr"><div class="nm">${esc(r.ticker)} <span class="m">${esc(String(r.passed))}/${esc(String(r.total))}</span></div><div class="sub">${esc(missLine(r))}</div><div class="cks">${bits}</div></div></div>`;
    }).join("");
  }
  function paperCard(chain, status) {
    const s = status || {};
    const est = checks(s.est01);
    const sw = checks(s.sw01);
    const ticket = s.ticket || "No open ticket. A ticket is only a registered test's fixed 100 ADA paper.";
    const universe = s.universe ? `<p class="sub">${esc(s.universe)} That pool floor is the universe, not a fifth check.</p>` : "";
    const body = `<p class="lab">Not a pick, not a ranking</p><p class="t">${esc(ticket)}</p>${universe}`
      + `<div class="lab" style="margin-top:8px">EST-01 · whale ≥ 1,000 ADA / 72h · 25% above the kill line · holders flat or up over 7d · liquidity down no more than 10%</div>`
      + (est || `<p class="sub">No EST-01 near-miss on this chain.</p>`)
      + `<div class="lab" style="margin-top:8px">SW-01 · bonded pool · rule k · whale still net-buying after 24h</div>`
      + (sw || `<p class="sub">No SW-01 near-miss on this chain.</p>`);
    return card("Paper test status · " + esc(chain), "research · paper only", body, "paper span");
  }
  function head(rows, api, n) {
    const shown = (rows || []).slice(0, n);
    const rest = (rows || []).slice(n);
    return shown.map((r, i) => pk(api, r, i === 0)).join("") + more(rest, api);
  }

  function home(doc, api) {
    const c = doc.cardano || {};
    const s = doc.solana || {};
    const n = doc.nft || {};
    const f = ((s.books || [])[0]) || {};
    const books = c.books || {};
    const sol = (books.solada || {});
    const nftTop = (n.rows || []).find(r => r.right && r.right !== "floor n/a") || (n.rows || [])[0];
    const brief = (doc.briefs || []).map((b, i) => `<div class="row${i ? "" : " f"}" style="align-items:flex-start;padding:8px 0"><div class="gr"><div class="lab">${esc(b.desk)} · ${esc(b.date)}</div><p class="t" style="margin-top:2px">${esc(b.headline)}</p></div></div>`).join("");
    return paperCard("Cardano", c.paper_status)
      + paperCard("Solana", s.paper_status)
      + paperCard("NFT", n.paper_status)
      + card("Cardano", "Sentinel", `<div class="lab">Sentinel pick</div><div class="big">None</div><div class="sub">${esc((sol.open || 0) + " open · " + (sol.closed || 0) + " closed · " + (sol.pnl == null ? "–" : sol.pnl) + " ₳ paper")}</div>${head(c.runners, api, 1)}${more((c.established || []).slice(0, 3), api, "more watch")}<a class="a" href="#/cardano">Open Cardano →</a>`)
      + card("Solana", "day-14 verdict", `<div class="lab">F book · per trade</div><div class="big ${f.exp_pct < 0 ? "dn" : ""}">${esc(f.exp_pct == null ? "–" : (f.exp_pct < 0 ? "−" : "+") + Math.abs(f.exp_pct).toFixed(1) + "%")}</div><div class="sub">${esc((f.n || 0) + " closes · rule " + (f.rule || "–") + " · " + (s.verdict || "NOT_FINAL") + " until Nov 11, 10:00 AM ET")}</div>${head(s.opens, api, 1)}<a class="a" href="#/solana">Open Solana →</a>`)
      + card("NFT · KING", "floors in USD", `<div class="lab">Top floor</div><div class="big">${esc(nftTop ? nftTop.right : "–")}</div><div class="sub">${esc(nftTop ? nftTop.ticker + " · no KING pick" : "No names in the pulse")}</div>${head(n.rows, api, 1)}<a class="a" href="#/nft">Open NFT →</a>`)
      + card("Latest briefs", "from the desk log", brief || `<p class="sub">No briefs in the build.</p>`, "span")
      + card("Wallet race", "paper · display only", `<p class="t">Display only, untested. A lead in the race is luck until tested.</p><a class="a" href="#/races">Open the race →</a>`, "span");
  }

  function cardano(doc, api) {
    const c = doc.cardano || {};
    const books = c.books || {};
    const sol = books.solada || {};
    const pump = books.pump || {};
    const table = `<table style="margin-top:8px"><tr><th>Cardano book</th><th>Avg ret</th><th>P&amp;L</th></tr><tr><td>Solada</td><td class="${(sol.avg || 0) < 0 ? "dn" : ""}">${esc(sol.avg == null ? "–" : (sol.avg < 0 ? "−" : "+") + Math.abs(sol.avg).toFixed(1) + "%")}</td><td class="${(sol.pnl || 0) < 0 ? "dn" : ""}">${esc(sol.pnl == null ? "–" : sol.pnl + " ₳")}</td></tr><tr><td>Pump.fun</td><td class="${(pump.avg || 0) < 0 ? "dn" : ""}">${esc(pump.avg == null ? "–" : (pump.avg < 0 ? "−" : "+") + Math.abs(pump.avg).toFixed(1) + "%")}</td><td class="${(pump.pnl || 0) < 0 ? "dn" : ""}">${esc(pump.pnl == null ? "–" : pump.pnl + " ₳-eq")}</td></tr></table><div class="sub" style="margin-top:6px">${esc((sol.open || 0) + (pump.open || 0))} open · ${esc((sol.closed || 0) + (pump.closed || 0))} closed</div>`;
    const whales = (c.whales || []).map((w, i) => `<div class="row${i ? "" : " f"}"><div class="gr"><div class="nm">${esc(w.name)}</div><div class="sub">${esc(w.line)}</div></div><div class="r m ${w.cls || "mu"}">${esc(w.right)}</div></div>`).join("");
    return paperCard("Cardano", c.paper_status)
      + card("Sentinel's pick", "this run", `<div class="big">None right now</div><div class="sub">Nothing cleared rule k + y this run.</div>${head(c.runners, api, 1)}${legend(doc)}`, "hero span")
      + card("Runners · fresh launch", "10–25× lane · 24h", head(c.runners, api, 5))
      + card("Established · high cap", "kill-line distance · 24h", head(c.established, api, 5))
      + card("Paper books", "closed results", table)
      + card("Whale codenames", "last 24h", whales + (c.flow ? `<p class="t" style="margin-top:6px;font-size:12px">${esc(c.flow)}</p>` : ""))
      + card("Launch radar", "120 holders + 25k ₳", head(c.radar, api, 6) || `<p class="sub">No launch in this run.</p>`)
      + card("What changed this run", "desk log", `<p class="t">${esc(c.changed || "No note in this run.")}</p>`);
  }

  function solana(doc, api) {
    const s = doc.solana || {};
    const names = { F: "F desk", R: "R random", S: "S shadow" };
    const trs = (s.books || []).map(b => {
      const pct = b.exp_pct;
      const text = pct == null ? "–" : (pct < 0 ? "−" : "+") + Math.abs(pct).toFixed(1) + "%";
      const stop = b.rule === "STOP";
      return `<tr><td>${esc(names[b.id] || b.id)}</td><td class="${pct < 0 ? "dn" : ""}">${esc(text)}</td><td>${esc(String(b.n))}</td><td>${pill(b.rule, stop ? "d" : "")}</td></tr>`;
    }).join("");
    const f = (s.books || [])[0] || {};
    const r = (s.books || [])[1] || {};
    const why = `Random expectancy is ${r.exp_pct == null ? "–" : (r.exp_pct < 0 ? "−" : "+") + Math.abs(r.exp_pct).toFixed(1) + "%"} against the desk's ${f.exp_pct == null ? "–" : (f.exp_pct < 0 ? "−" : "+") + Math.abs(f.exp_pct).toFixed(1) + "%"}${s.gap_pp == null ? "" : " (" + s.gap_pp + " pp)"}. The day-14 verdict is ${s.verdict || "NOT_FINAL"}. This line does not size a ticket.`;
    const books = `<table><tr><th>Book</th><th>Exp/trade</th><th>Closes</th><th>Rule</th></tr>${trs}</table><div class="sub" style="margin-top:8px">F vs R ${esc(s.gap_pp == null ? "–" : String(s.gap_pp) + " pp")} · <b class="mu">${esc(s.verdict || "NOT_FINAL")} until Nov 11, 10:00 AM ET</b></div><div class="why" style="margin-top:6px"><b>WHY IT MATTERS</b>${esc(why)}</div>`;
    const trades = `<div class="tw"><table><tr><th>Time</th><th>Book · token</th><th>Reason</th><th>Net SOL</th></tr>${(s.trades || []).map(t => `<tr><td>${esc(t.time)}</td><td style="text-align:left">${esc(t.book)} · ${esc(t.ticker)}</td><td style="font-family:Archivo,system-ui,sans-serif;font-size:11.5px;color:#C7CCD3;white-space:normal">${esc(t.reason)}</td><td class="${t.pnl < 0 ? "dn" : t.pnl > 0 ? "up" : ""}">${esc(t.pnl == null ? "–" : (t.pnl < 0 ? "−" : "+") + Math.abs(t.pnl).toFixed(3))}</td></tr>`).join("")}</table></div>`;
    const net = `<div class="two"><div class="box"><div class="lab">Net</div><div class="big dn" style="font-size:22px">${esc(s.net == null ? "–" : (s.net < 0 ? "−" : "+") + Math.abs(s.net).toFixed(2))}</div><div class="sub">SOL after fees + haircut</div></div><div class="box"><div class="lab">Gross</div><div class="big dn" style="font-size:22px">${esc(s.gross == null ? "–" : (s.gross < 0 ? "−" : "+") + Math.abs(s.gross).toFixed(2))}</div><div class="sub">SOL price move only</div></div></div><div class="sub" style="margin-top:8px">${s.costs == null ? "" : "Costs " + esc(String(s.costs)) + " SOL · "}${esc(s.win_pct == null ? "" : s.win_pct + "% win over " + s.fills + " fills")}</div>`;
    const buzz = (s.buzz || []).map((b, i) => `<div class="row${i ? "" : " f"}">${logo(api, b.ticker)}<div class="gr"><div class="nm">${esc(b.ticker)}</div><div class="sub">${esc(b.sub)}</div></div><div class="r m mu">measured</div></div>`).join("");
    return paperCard("Solana", s.paper_status)
      + card("Books F / R / S", "day-14 verdict · % per trade", books, "hero span")
      + card("Net vs gross", "paper, after costs", net)
      + card("Open positions", (s.opens || []).length + " in the feed", head(s.opens, api, 3) + legend(doc))
      + card("Trades log", "reason per fill", trades || `<p class="sub">No sells in the feed.</p>`, "span")
      + card("X buzz " + pill("measured, not a score", ""), s.buzz_live ? "live file" : "feed missing", buzz || `<p class="sub">No Solana rows in x_buzz.json. Mentions are logged for a later backtest and are not a score.</p>`, "span");
  }

  function nft(doc, api) {
    const n = doc.nft || {};
    const px = n.prices || {};
    const note = `Native floors × ${(px.source || "stored prices")} ${px.asof || ""}` + (px.ETH ? ` · ETH $${px.ETH}` : "") + (px.SOL ? ` · SOL $${px.SOL}` : "");
    return paperCard("NFT", n.paper_status)
      + card("KING pick", "paper book", `<div class="big">None</div><div class="sub">${esc((n.open || 0) + " open · " + (n.closed || 0) + " closed · " + (n.pnl == null ? "–" : n.pnl))}</div>${head(n.rows, api, 1)}${legend(doc)}`, "hero")
      + card("Watchlist", esc(note), head(n.rows, api, 4));
  }

  function warn(doc, route) {
    const c = doc.cardano || {};
    if ((route === "home" || route === "cardano") && c.failure != null && c.failure >= 0.1) {
      const pct = (c.failure * 100).toFixed(1);
      return `<div class="warn">Cardano feed: ${esc(pct)}% of data calls failed this run (${esc(c.failure_skipped + " of " + c.failure_tracked)}). Some pools not re-read.</div>`;
    }
    const age = doc.nft && doc.nft.age_days;
    if (route === "nft" && age != null && age >= 1) {
      return `<div class="warn">KING pulse last ran ${esc(String(age))} day${age === 1 ? "" : "s"} ago. USD values use those native floors.</div>`;
    }
    return "";
  }

  function page(route, doc, api) {
    const id = route || "home";
    if (!doc) return `<p class="sub">Picks file is not in this build yet.</p>`;
    let body = "";
    if (id === "cardano") body = cardano(doc, api);
    else if (id === "solana") body = solana(doc, api);
    else if (id === "nft") body = nft(doc, api);
    else body = home(doc, api);
    return `<div class="grid">${body}</div>`;
  }

  root.SightlineV19 = { page, warn, risk };
})(typeof window !== "undefined" ? window : globalThis);
