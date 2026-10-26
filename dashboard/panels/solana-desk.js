/* Solana Paper Desk (plan v6.5): health, latest leads, paper book, recent runs.
   Reads desk-data solana-desk/ via S.get (JSON) and S.getText (tasks.csv, trades.csv).
   Paper data only. Only token mints are shown (public); no wallet fields are read. */
(function () {
  const VOID = 0.10;
  function csv(text) {
    if (!text) return [];
    const lines = String(text).split(/\r?\n/).filter(Boolean);
    if (!lines.length) return [];
    const split = l => { const out = []; let cur = "", q = false; for (const c of l) { if (c === '"') q = !q; else if (c === "," && !q) { out.push(cur); cur = ""; } else cur += c; } out.push(cur); return out; };
    const head = split(lines[0]);
    return lines.slice(1).map(split).filter(r => r.join(",") !== head.join(",")).map(r => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
  }
  const runAt = id => { const m = String(id || "").match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/); return m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00Z` : null; };
  const short = m => String(m).length > 12 ? String(m).slice(0, 4) + "…" + String(m).slice(-4) : String(m);
  const MINT_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
  const links = m => MINT_RE.test(m) ? `<a href="https://dexscreener.com/solana/${S.esc(m)}" target="_blank" rel="noopener">DEX</a> <a href="https://www.geckoterminal.com/solana/tokens/${S.esc(m)}" target="_blank" rel="noopener">GT</a>` : "";
  const sol = n => n == null || isNaN(n) ? "–" : (n > 0 ? "+" : "") + S.fmt(n, 4);
  const num = v => (v === "" || v == null || isNaN(+v)) ? null : +v;

  function health(h, now) {
    if (!h) return `<p class="muted">health.json not published yet.</p>`;
    const at = runAt(h.last_run_utc) || (h.run_times_utc || []).slice(-1)[0];
    const dfr = num(h.data_failure_rate), w = dfr == null ? 0 : Math.min(100, dfr / (VOID * 1.5) * 100);
    const card = (k, v, cls) => `<div class="card"><div class="k">${S.esc(k)}</div><div class="v ${cls || ""}">${v}</div></div>`;
    return `<div class="cards sd-health">
      ${card("Last run", at ? `${S.when(at)} <span class="sd-dim">${S.esc(S.ago(at, now))}</span>` : "–")}
      ${card("Runs today", `${S.fmt(h.runs, 0)} / ${S.fmt(h.expected_runs, 0)}`, h.expected_runs && h.runs / h.expected_runs < 0.5 ? "" : "up")}
      ${card("Data failures", `${dfr == null ? "–" : S.fmt(dfr * 100) + "%"} <span class="sd-dim">void ≥ ${VOID * 100}%</span><span class="sd-void"><i style="width:${w.toFixed(0)}%" class="${dfr >= VOID ? "bad" : ""}"></i><b style="left:${(100 / 1.5).toFixed(1)}%"></b></span>`, dfr >= VOID ? "down" : "up")}
      ${card("Max gap", h.max_gap_min != null ? `${S.fmt(h.max_gap_min)} min` : "–", h.max_gap_min > 45 ? "down" : "")}
      ${card("Errors", h.error_rate != null ? S.fmt(h.error_rate * 100) + "%" : "–")}
      ${card("Leads this run", S.fmt(h.leads_this_run, 0))}
      ${card("Eligibility skips", S.fmt(h.eligibility_skips, 0))}
      ${card("Config", `${S.esc(h.cfg_version || "–")} ${S.pill(h.frozen ? "frozen" : "draft", h.frozen ? "call" : "watch")}${h.void_day ? " " + S.pill("void day", "kill") : ""}`)}
    </div>`;
  }

  function leads(tasks) {
    if (!tasks.length) return `<p class="muted">tasks.csv not published yet.</p>`;
    const last = tasks[tasks.length - 1].run_id;
    const rows = tasks.filter(t => t.run_id === last).sort((a, b) => (a.result === "pass" ? 0 : 1) - (b.result === "pass" ? 0 : 1));
    const reasons = {};
    rows.forEach(r => { const k = r.result === "pass" ? "pass" : (r.skip_type || "skip"); reasons[k] = (reasons[k] || 0) + 1; });
    const chips = `<div class="sd-filters" data-sd-filter><button class="pill on" data-f="all">all ${rows.length}</button>${Object.entries(reasons).map(([k, n]) => `<button class="pill" data-f="${S.esc(k)}">${S.esc(k)} ${n}</button>`).join("")}</div>`;
    return `${chips}<div class="sd-sub">run ${S.esc(last)} · ${S.esc(S.when(runAt(last)))}</div><table class="tbl sd-leads"><thead><tr><th>Mint</th><th>Result</th><th>Reason</th><th class="num">Delay s</th><th>Links</th></tr></thead><tbody>${rows.map(r => {
      const k = r.result === "pass" ? "pass" : (r.skip_type || "skip");
      return `<tr data-k="${S.esc(k)}"><td class="mono" title="${S.esc(r.mint)}">${S.esc(short(r.mint))}</td><td>${S.pill(r.result === "pass" ? "pass" : k, r.result === "pass" ? "call" : (k === "data" ? "kill" : "pass"))}</td><td class="c-meta">${S.esc((r.reason || "").replace(/_/g, " "))}${r.error_src ? ` <span class="muted">(${S.esc(r.error_src.replace(/;/g, ", "))} error)</span>` : ""}</td><td class="num">${S.esc(r.check_delay_s)}</td><td>${links(r.mint)}</td></tr>`;
    }).join("")}</tbody></table>`;
  }

  function book(trades, paper) {
    const sells = trades.filter(t => /sell|exit|close/i.test(t.action));
    const pos = {};
    for (const t of trades) {
      const k = (t.book || "") + "|" + t.mint;
      const p = pos[k] = pos[k] || { book: t.book, mint: t.mint, symbol: t.symbol, frac: 0, sol: 0, px: null };
      const f = num(t.frac) ?? 1;
      if (/buy|enter|open/i.test(t.action)) { p.frac += f; p.sol += num(t.sol) || 0; p.px = num(t.quote_px); }
      else if (/sell|exit|close/i.test(t.action)) p.frac -= f;
    }
    const open = Object.values(pos).filter(p => p.frac > 1e-9);
    const pnls = sells.map(t => num(t.pnl)).filter(x => x != null);
    const total = pnls.reduce((a, b) => a + b, 0), wins = pnls.filter(x => x > 0).length;
    if (!trades.length && !(paper && ((paper.open || []).length || (paper.closed || []).length)))
      return `<p class="muted sd-empty">No paper fills yet. Config v1-v6.5 frozen Oct 26; paper fills start once the fill step ships (plan v6.5). Open positions, closed trades, P&amp;L and win rate show here once <code>trades.csv</code> has rows.</p>`;
    return `<div class="cards"><div class="card"><div class="k">Open</div><div class="v">${open.length}</div></div><div class="card"><div class="k">Closed</div><div class="v">${pnls.length}</div></div>
      <div class="card"><div class="k">P&amp;L (SOL)</div><div class="v ${S.pnlClass(total)}">${sol(total)}</div></div><div class="card"><div class="k">Win rate</div><div class="v">${pnls.length ? Math.round(wins / pnls.length * 100) + "%" : "–"}</div></div></div>
      ${open.length ? `<div class="sd-h">Open positions</div><table class="tbl"><thead><tr><th>Token</th><th>Book</th><th class="num">Size SOL</th><th class="num">Entry px</th><th>Links</th></tr></thead><tbody>${open.map(p => `<tr><td><b>${S.esc(p.symbol || short(p.mint))}</b></td><td class="c-meta">${S.esc(p.book)}</td><td class="num">${S.fmt(p.sol, 3)}</td><td class="num">${p.px == null ? "–" : S.esc(p.px)}</td><td>${links(p.mint)}</td></tr>`).join("")}</tbody></table>` : ""}
      ${sells.length ? `<div class="sd-h">Closed trades</div><table class="tbl"><thead><tr><th>Token</th><th>When</th><th>Why</th><th class="num">P&amp;L SOL</th></tr></thead><tbody>${sells.slice(-20).reverse().map(t => `<tr><td><b>${S.esc(t.symbol || short(t.mint))}</b></td><td class="c-meta">${S.esc(S.when(new Date(+t.time * 1000).toISOString()))}</td><td class="c-meta">${S.esc(t.why)}</td><td class="num ${S.pnlClass(num(t.pnl) || 0)}">${sol(num(t.pnl))}</td></tr>`).join("")}</tbody></table>` : ""}`;
  }

  function runs(tasks, h) {
    const by = {};
    for (const t of tasks) { const r = by[t.run_id] = by[t.run_id] || { leads: 0, fail: 0, n: 0 }; r.n++; if (t.result === "pass") r.leads++; if (t.skip_type === "data") r.fail++; }
    const ids = Object.keys(by).sort().slice(-48);
    if (ids.length < 2) return `<p class="muted">Run history builds up after a few runs${h && h.run_times_utc ? ` (${h.run_times_utc.length} run times today)` : ""}.</p>`;
    const max = Math.max(1, ...ids.map(i => by[i].n));
    return `<div class="sd-legend"><span class="sd-dot lead"></span>leads ${S.spark(ids.map(i => by[i].leads), { w: 120, h: 20 })} <span class="sd-dot fail"></span>data failures ${S.spark(ids.map(i => by[i].fail), { w: 120, h: 20 })}</div>
      <div class="sd-strip">${ids.map(i => `<div class="sd-run" title="${S.esc(i)} · ${by[i].leads} leads · ${by[i].fail} data failures · ${by[i].n} checks"><i class="n" style="height:${(by[i].n / max * 100).toFixed(0)}%"></i><i class="l" style="height:${(by[i].leads / max * 100).toFixed(0)}%"></i><i class="f" style="height:${(by[i].fail / max * 100).toFixed(0)}%"></i></div>`).join("")}</div>
      <div class="sd-sub">${ids.length} runs · ${S.esc(S.when(runAt(ids[0])))} → ${S.esc(S.when(runAt(ids[ids.length - 1])))}</div>`;
  }

  if (typeof document !== "undefined" && document.addEventListener && !window.__sdFilter) {
    window.__sdFilter = true;
    document.addEventListener("click", e => {
      const b = e.target.closest && e.target.closest("[data-sd-filter] [data-f]");
      if (!b) return;
      const wrap = b.closest(".sd");
      wrap.querySelectorAll("[data-sd-filter] [data-f]").forEach(x => x.classList.toggle("on", x === b));
      wrap.querySelectorAll(".sd-leads tbody tr").forEach(tr => { tr.style.display = b.dataset.f === "all" || tr.dataset.k === b.dataset.f ? "" : "none"; });
    });
  }

  S.register("solana-desk", {
    span: 12,
    async render({ data, opt }) {
      const list = Array.isArray(data) ? data : [];
      const by = re => (list.find(d => re.test(String(d.path))) || {}).json || null;
      const h = by(/health\.json$/), paper = by(/paper\.json$/);
      const base = opt.base || "solana-desk/";
      const text = opt.csv || { tasks: await S.getText(base + "tasks.csv"), trades: await S.getText(base + "trades.csv") };
      const tasks = csv(text.tasks), trades = csv(text.trades);
      const now = opt.now ? new Date(opt.now).getTime() : Date.now();
      return `<div class="sd">
        <div class="sd-sec"><div class="sd-h">Health · ${S.esc(h && h.day || "today")}</div>${health(h, now)}</div>
        <div class="sd-sec"><div class="sd-h">Latest leads</div>${leads(tasks)}</div>
        <div class="sd-row"><div class="sd-sec"><div class="sd-h">Paper book</div>${book(trades, paper)}</div><div class="sd-sec"><div class="sd-h">Recent runs</div>${runs(tasks, h)}</div></div>
        <div class="si-foot">Paper trading only · plan v6.5 · token mints only, no wallets · Research only, not advice.</div></div>`;
    }
  });
  S.solanaCsv = csv;
})();
