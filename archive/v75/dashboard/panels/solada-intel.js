/* Solada intel: flag strip, Cardano top 10, whale card, per-ticker drill-down,
   cluster heat grid, pattern scoreboard. Zero AI, no dependencies.
   data: [log.json, pulse.json, paper.json, /solada-prefilter/flags.json] (any may be missing).
   Wallets render only as short tags (w-xxxxxxxx hashes or Snek stake-prefix tags <= 12 chars). */
(function () {
  const SEV = {
    high: ["identical_size_buys", "watch_tags_first_block", "sniper_full_exit", "ada_side_drop", "founder_whale_move", "whale_move", "koth_pile_in", "known_launcher_deploy"],
    med: ["coordinated_buys", "fresh_wallet_swarm", "repeat_early_buyer", "holders_down_mcap_up", "volume_without_holders", "holder_concentration", "concentration_jump", "user_created_pool", "pool_preseeder_early"],
    pos: ["holders_doubled_ada_up"]
  };
  const EARLY = ["watch_tags_first_block", "watch_tags_early_buyers", "coordinated_buys", "identical_size_buys", "fresh_wallet_swarm", "pool_preseeder_early", "repeat_early_buyer"];
  const DAY = 864e5;
  const sevOf = f => f.severity || Object.keys(SEV).find(k => SEV[k].includes(f.kind)) || "low";
  const label = k => (k === "founder_whale_move" || k === "whale_move") ? "Whale move" : String(k || "").replace(/_/g, " ");
  /* Never let a full address or key through: tags only. */
  const safeTag = t => {
    const s = String(t || "").trim();
    if (!s || s.length > 12 || /^(addr|stake)(_test)?1/i.test(s)) return null;
    return s;
  };
  const tagsOf = f => [...(f.tags || []), ...(f.wallets || []), f.wallet, f.tag, f.top_cluster].map(safeTag).filter(Boolean);
  const chips = (list, cls) => [...new Set(list)].map(t => `<span class="si-chip ${cls || ""}">${S.walletTag(t)}</span>`).join("");
  const ms = s => { const t = new Date(s).getTime(); return isFinite(t) ? t : NaN; };
  const arrow = d => d == null || isNaN(d) ? "" : d > 0 ? `<span class="up">▲${S.fmt(d, 0)}</span>` : d < 0 ? `<span class="down">▼${S.fmt(-d, 0)}</span>` : `<span class="muted">=0</span>`;

  /* Asset names decoded from hex can be binary (DEX LP tokens are 28-32 byte hashes).
     Unprintable names become a short hex label: LP·4b52… (hash-length, or marked lp) or asset·4b52…. */
  const PRINTABLE = /^[\x20-\x7E]{1,32}$/;
  function hexOf(s) {
    let bytes;
    try { bytes = unescape(encodeURIComponent(String(s))); } catch (e) { bytes = String(s); }
    return { hex: [...bytes].map(c => c.charCodeAt(0).toString(16).padStart(2, "0")).join(""), n: bytes.length };
  }
  function cleanName(name, f) {
    const s = String(name == null ? "" : name);
    if (PRINTABLE.test(s) && !/[\uFFFD]/.test(s)) return { name: s, lp: !!(f && f.lp) };
    const { hex, n } = hexOf(s);
    const lp = !!(f && (f.lp || f.dex)) || n >= 16;
    return { name: (lp ? "LP·" : "asset·") + hex.slice(0, 4) + "…", lp, binary: true };
  }
  /* Whale moves before the valuation fix (#18) carry ada_equiv from raw units with no value_basis. */
  function normalize(f) {
    const g = Object.assign({}, f);
    if (g.ticker != null) { const c = cleanName(g.ticker, g); g.ticker = c.name; if (c.lp) g.lp = true; }
    if (g.kind === "founder_whale_move" || g.kind === "whale_move") {
      if (g.ada_equiv != null && !g.value_basis) { delete g.ada_equiv; g.raw_units = true; }
      if (g.lp || g.raw_units) g.severity = "low";
    }
    return g;
  }
  function allFlags(fj) {
    if (!fj || typeof fj !== "object") return [];
    const seen = new Set(), out = [];
    for (const f of [...(fj.new || []), ...(fj.recent || [])]) {
      if (!f || !f.kind) continue;
      const k = JSON.stringify(f);
      if (seen.has(k)) continue;
      seen.add(k); out.push(normalize(f));
    }
    return out.sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
  }

  function strip(flags, now) {
    const today = flags.filter(f => now - ms(f.at) < DAY);
    if (!today.length) return `<p class="muted si-empty">No flags in the last 24h${flags.length ? ` · ${flags.length} older in flags.json` : " · flags.json not published yet"}.</p>`;
    return `<div class="si-strip">${today.slice(0, 40).map(f => `<div class="si-flag sev-${sevOf(f)}">
      <b class="ell">${S.esc(f.ticker || (f.launches || []).join("/") || "–")}</b>${S.pill(sevOf(f) === "pos" ? "positive" : sevOf(f), "sev-" + sevOf(f))}
      <span class="si-kind ell">${S.esc(label(f.kind))}</span><span class="ts">${S.esc(S.ago(f.at, now))}</span>
      <div class="si-tags">${chips(tagsOf(f).slice(0, 6))}</div></div>`).join("")}</div>`;
  }

  function top10(rows) {
    if (!Array.isArray(rows) || !rows.length) return `<p class="muted si-empty">Top 10 not published yet (Snek's <code>cardano_top10</code> lands with the 7 PM run).</p>`;
    const r = rows.slice().sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99)).slice(0, 10);
    return `<table class="tbl si-top"><thead><tr><th>#</th><th>Ticker</th><th>Lvl</th><th class="num">ADA side</th><th class="num">Holders</th><th>Whales</th><th class="num si-dim">Mcap</th></tr></thead><tbody>${r.map(x => `<tr data-x>
      <td class="num">${S.esc(x.rank ?? "")}</td><td class="c-main"><b>${S.esc(x.ticker || "–")}</b>${x.policy_short && safeTag(x.policy_short) ? ` <span class="si-dim mono">${S.esc(x.policy_short)}</span>` : ""}</td>
      <td>${x.level != null && x.level !== "" ? S.pill(String(x.level), "lvl") : ""}</td>
      <td class="num">${S.fmt(x.ada_side_depth, 0)}</td><td class="num">${S.fmt(x.holders, 0)} ${arrow(x.holders_delta)}</td>
      <td>${(x.whale_tags || []).length ? `<span class="si-count" title="${S.esc((x.whale_tags || []).map(safeTag).filter(Boolean).join(" "))}">${(x.whale_tags || []).length}</span>` : ""}</td>
      <td class="num si-dim">${S.fmt(x.mcap_ada, 0)}</td></tr>${x.note ? `<tr class="si-note"><td></td><td colspan="6" class="c-long"><span class="ell">${S.rich(x.note)}</span></td></tr>` : ""}`).join("")}</tbody></table>`;
  }

  function whaleRecord(log) {
    if (!log) return null;
    if (Array.isArray(log.whales) && log.whales.length) return log.whales[0];
    return log.founder_whale || null;
  }
  function whale(log, flags) {
    const w = whaleRecord(log);
    const moves = flags.filter(f => f.kind === "founder_whale_move" || f.kind === "whale_move");
    if ((!w || typeof w !== "object") && !moves.length) return "";
    const first = t => /ASCEND|STRIKE/i.test(String(t)) ? 0 : 1;
    const lines = w && w.line ? String(w.line).split(/(?<=\.)\s+(?=[A-Z$])/).sort((a, b) => first(a) - first(b)) : [];
    const mv = moves.slice().sort((a, b) => ((a.lp || a.raw_units) - (b.lp || b.raw_units)) || first(a.ticker) - first(b.ticker)).slice(0, 8);
    const net = w && typeof w.net_flow_ada === "number" ? w.net_flow_ada : null;
    const tagged = safeTag(w && w.stake_prefix) || safeTag(moves[0] && moves[0].tag);
    const who = (w && typeof w.codename === "string" && w.codename && !/founder|\$|@/i.test(w.codename)) ? w.codename : "Whale";
    return `<div class="si-whale"><div class="si-whead"><b>${S.esc(who)}</b> ${tagged ? chips([tagged]) : ""}${net != null ? ` <span class="${net >= 0 ? "up" : "down"} mono">net ${net > 0 ? "+" : ""}${S.fmt(net, 0)} ADA</span>` : ""}</div>
      ${mv.length ? `<div class="si-moves">${mv.map(m => `<span class="si-move ${/ASCEND|STRIKE/i.test(m.ticker) ? "key" : ""}${m.lp || m.raw_units ? " dim" : ""}"><b>${S.esc(m.ticker)}</b> ${m.lp ? S.pill("LP", "pass") + " " : ""}<span class="${m.delta >= 0 ? "up" : "down"}">${m.delta > 0 ? "+" : ""}${S.fmt(m.delta, 0)}</span>${m.raw_units ? ` <span class="si-dim">raw units · unvalued</span>` : m.ada_equiv ? ` ≈${S.fmt(m.ada_equiv, 0)}₳` : ""}</span>`).join("")}</div>` : ""}
      ${lines.map(l => `<div class="si-wline ${first(l) ? "" : "key"}">${S.rich(l)}</div>`).join("")}</div>`;
  }

  function seriesFor(pulse, log, tk) {
    const pts = S.pointsFor(pulse, tk);
    let h = pts.map(p => (p.metrics || {}).holders_onchain ?? (p.metrics || {}).holders);
    let a = pts.map(p => (p.metrics || {}).ada_side);
    if (h.filter(x => typeof x === "number").length < 2 && log && Array.isArray(log.history)) {
      const rows = log.history.slice().reverse().map(x => (x.cardano_top10 || []).find(r => r.ticker === tk)).filter(Boolean);
      h = rows.map(r => r.holders); a = rows.map(r => r.ada_side_depth);
    }
    return { h, a };
  }

  function drill(tk, flags, pulse, log, paper) {
    const fl = flags.filter(f => f.ticker === tk || String(f.ticker || "").toUpperCase() === tk);
    const { h, a } = seriesFor(pulse, log, tk);
    const ticket = ((paper && paper.open) || []).find(t => String(t.ticker).toUpperCase() === tk) ||
      (log && log.kill_lines && log.kill_lines[tk] ? { ticker: tk, kill_line: { text: String(log.kill_lines[tk]) } } : null);
    const kd = ticket ? S.killDistance(ticket, pulse) : null;
    const exited = new Set(fl.filter(f => f.kind === "sniper_full_exit").flatMap(tagsOf));
    const fb = [...new Set(fl.filter(f => f.kind === "watch_tags_first_block" || f.kind === "pool_preseeder_early").flatMap(tagsOf)), ...exited];
    const buyers = [...new Set(fb)].map(t => `<span class="si-chip ${exited.has(t) ? "exited" : "held"}">${S.walletTag(t)}<i>${exited.has(t) ? "exited" : "held"}</i></span>`).join("");
    const tl = fl.slice().sort((x, y) => String(x.at || "").localeCompare(String(y.at || "")));
    return `<details class="si-drill"><summary><b>${S.esc(tk)}</b><span class="si-dim">${fl.length} flag${fl.length === 1 ? "" : "s"}</span>${S.spark(h)}</summary>
      <div class="si-dgrid">
        <div><div class="k">Holders</div>${S.spark(h, { w: 120, h: 26 }) || `<span class="muted">no history</span>`}</div>
        <div><div class="k">ADA side</div>${S.spark(a, { w: 120, h: 26 }) || `<span class="muted">no history</span>`}</div>
        <div><div class="k">Kill line</div>${kd ? `${S.killMeter(kd.pct)} <span class="mono">${S.fmt(kd.pct)}% room · ${S.esc(kd.what || "")}</span>` : `<span class="muted">no machine-readable kill line</span>`}</div>
      </div>
      <div class="k">First-block buyers</div>${buyers || `<span class="muted">none flagged</span>`}
      <div class="k">Cluster timeline</div>${tl.length ? `<ol class="si-tl">${tl.map(f => `<li class="${f.kind === "identical_size_buys" ? "hit" : ""}"><span class="ts">${S.esc(S.when(f.at))}</span> ${S.esc(label(f.kind))}${f.ada_each ? ` · ${S.fmt(f.ada_each)}₳ each` : ""} ${chips(tagsOf(f).slice(0, 8))}</li>`).join("")}</ol>` : `<span class="muted">no cluster events</span>`}
    </details>`;
  }

  function heat(flags) {
    const cell = {}, cols = new Set();
    for (const f of flags) {
      if (!EARLY.includes(f.kind)) continue;
      const launches = f.kind === "repeat_early_buyer" ? (f.launches || []) : [f.ticker];
      for (const tk of launches) {
        if (!tk) continue;
        cols.add(tk);
        for (const t of tagsOf(f)) { cell[t] = cell[t] || {}; cell[t][tk] = (cell[t][tk] || 0) + 1; }
      }
    }
    const rows = Object.keys(cell).sort((a, b) => Object.keys(cell[b]).length - Object.keys(cell[a]).length).slice(0, 24);
    const cs = [...cols].slice(0, 14);
    if (!rows.length) return `<p class="muted si-empty">No early-buy clusters yet.</p>`;
    return `<div class="si-heatwrap"><table class="si-heat"><thead><tr><th></th>${cs.map(c => `<th><span>${S.esc(c)}</span></th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr><th>${S.walletTag(r)}</th>${cs.map(c => { const n = cell[r][c] || 0; return `<td class="h${Math.min(n, 3)}" title="${S.esc(r + " · " + c + ": " + n)}">${n || ""}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function board(flags, fj, log) {
    const kinds = {};
    for (const f of flags) { const k = kinds[f.kind] = kinds[f.kind] || { n: 0, pump: 0, rug: 0 }; k.n++; if (f.outcome === "pump") k.pump++; if (f.outcome === "rug") k.rug++; }
    const ext = (fj && fj.outcomes) || (log && log.flag_outcomes) || {};
    for (const [k, v] of Object.entries(ext)) { const r = kinds[k] = kinds[k] || { n: 0, pump: 0, rug: 0 }; r.pump += +v.pump || 0; r.rug += +v.rug || 0; }
    const list = Object.entries(kinds).sort((a, b) => b[1].n - a[1].n);
    if (!list.length) return `<p class="muted si-empty">No flags fired yet.</p>`;
    const anyOut = list.some(([, v]) => v.pump || v.rug);
    return `<table class="tbl si-board"><thead><tr><th>Flag</th><th class="num">Fired</th><th class="num">→Pump</th><th class="num">→Rug</th></tr></thead><tbody>${list.map(([k, v]) => `<tr><td>${S.pill(sevOf({ kind: k }), "sev-" + sevOf({ kind: k }))} ${S.esc(label(k))}</td><td class="num">${v.n}</td><td class="num up">${anyOut ? v.pump : "–"}</td><td class="num down">${anyOut ? v.rug : "–"}</td></tr>`).join("")}</tbody></table>${anyOut ? "" : `<p class="muted si-empty">Outcomes not tracked yet: pump/rug counts appear once flags are scored.</p>`}`;
  }

  S.register("solada-intel", {
    span: 12,
    render({ data, opt }) {
      const list = Array.isArray(data) ? data : [{ path: "", json: data }];
      const by = re => (list.find(d => re.test(String(d.path))) || {}).json || null;
      const log = by(/log\.json$/), pulse = by(/pulse\.json$/), paper = by(/paper\.json$/), fj = by(/flags\.json$/);
      const now = opt.now ? new Date(opt.now).getTime() : Date.now();
      const flags = allFlags(fj);
      const top = (log && Array.isArray(log.cardano_top10)) ? log.cardano_top10 : [];
      const tks = [...new Set([...top.map(r => r.ticker), ...flags.map(f => f.ticker)].filter(Boolean).map(t => /·/.test(t) ? String(t) : String(t).toUpperCase()))].slice(0, 20);
      const w = whale(log, flags);
      return `<div class="si">
        <div class="si-sec"><div class="si-h">Flags · 24h</div>${strip(flags, now)}</div>
        <div class="si-row"><div class="si-sec si-main"><div class="si-h">Cardano top 10</div>${top10(top)}</div>${w ? `<div class="si-sec si-side">${w}</div>` : ""}</div>
        <div class="si-sec"><div class="si-h">Drill-down</div>${tks.length ? tks.map(t => drill(t, flags, pulse, log, paper)).join("") : `<p class="muted si-empty">Nothing to drill into yet.</p>`}</div>
        <div class="si-row"><div class="si-sec si-main"><div class="si-h">Cluster heat grid</div>${heat(flags)}</div><div class="si-sec si-side"><div class="si-h">Pattern scoreboard</div>${board(flags, fj, log)}</div></div>
        <div class="si-foot">Flags are heuristics, not triggers · wallets shown as tags only · Research only, not advice.</div>
      </div>`;
    }
  });
})();
