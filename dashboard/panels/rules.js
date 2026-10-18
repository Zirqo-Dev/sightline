/* Rules learned. Reads title / category / evidence / mirrors when present.
   Otherwise a fixed table, then a keyword reading of the rule text. */
(function () {
  const CATS = {
    kill:   { label: "Kill / Exit",     color: "#ff4d6a", d: '<path d="M4 4l8 8M12 4l-8 8"/><circle cx="8" cy="8" r="6.5"/>' },
    entry:  { label: "Entry filter",    color: "#36d6ff", d: '<path d="M2 3h12l-4.5 5.5V13l-3 1.5V8.5z"/>' },
    sybil:  { label: "Sybil / Wash",    color: "#c084fc", d: '<circle cx="5" cy="6" r="2"/><circle cx="11" cy="6" r="2"/><path d="M1.5 13c.5-2.5 2-3.5 3.5-3.5S8 10.5 8 13M8 13c0-2.5 1.5-3.5 3-3.5s3 1 3.5 3.5"/>' },
    swarm:  { label: "Wallets / Swarm", color: "#ffb020", d: '<rect x="2" y="4" width="12" height="9" rx="1.5"/><path d="M2 7h12M10.5 10h1.5"/>' },
    liq:    { label: "Liquidity",       color: "#21e6a1", d: '<path d="M8 2s4.5 5 4.5 8a4.5 4.5 0 0 1-9 0C3.5 7 8 2 8 2z"/>' },
    review: { label: "Day review",      color: "#9fb3c8", d: '<rect x="2.5" y="3" width="11" height="11" rx="1.5"/><path d="M2.5 6.5h11M5.5 1.5v3M10.5 1.5v3M5 9.5h3"/>' }
  };
  const BOOKS = { solada: ["SOLADA", "solada"], pumpfun: ["PUMP.FUN", "pumpfun"], nft: ["KING · NFT", "nft"] };
  const RULE_META = {
    "solada:Watch (L3) needs early": ["Watch tier needs holders that survive the fade", "entry", []],
    "solada:KOTH loss + curve": ["KOTH lost + curve reversing + bleed = Pass", "entry", []],
    "solada:Copycats appearing often": ["Copycats mark peak attention", "entry", []],
    "solada:Solada headline 24h": ["Post-grad 24h volume can be stale: check pool flow", "liq", []],
    "solada:Holders > trades": ["More holders than trades = distribution", "sybil", []],
    "solada:Graduation alone isn't": ["Graduation alone isn't a signal", "entry", ["JUICE", "FEED", "HERO", "BEAST"]],
    "solada:Pools with an ADA": ["Thin pools (<11k ADA): trust holders, not price", "liq", []],
    "solada:Price rising with": ["Price up, holders flat = lottery at best", "entry", []],
    "solada:Linked flip pair": ["Flip pair + first-block snipers = rotation risk", "swarm", []],
    "solada:j": ["Sniper exit = kill", "kill", ["IAGONAUT"]],
    "solada:k": ["Holding tier: 120+ holders, 25k depth, positive 6h flow", "liq", ["HERO", "JUICE", "ROBIN"]],
    "solada:l": ["Promo pops: wait for re-hold", "entry", ["BOLT"]],
    "solada:m": ["Team airdrop holders don't count", "sybil", ["CLOVE"]],
    "solada:n": ["Solana side: count native holders only", "sybil", ["CORE"]],
    "solada:o": ["Trade counts aren't flow; 2 holder drops = early close", "kill", ["FTR", "CLOVE", "BOLT"]],
    "solada:p": ["Swarm launchers stay L1 until 60 outside holders", "swarm", ["PPC", "DINO"]],
    "solada:q": ["Queen-of-the-hill pushes ≠ native holders", "entry", ["MISS"]],
    "solada:r": ["Reward claims aren't buys", "swarm", []],
    "solada:daily-2026-10-17": ["Oct 17: both losses came after run-ups", "review", ["BOLT", "CLOVE"]],
    "pumpfun:a": ["Watch tier: 1,500 holders after a 30% pullback", "entry", []],
    "pumpfun:f": ["Score the PumpSwap pool, not graduation", "entry", []],
    "pumpfun:h": ["Price up, holders flat = lottery at best", "entry", []],
    "pumpfun:i": ["Create-slot snipers cap a name at L2", "swarm", []],
    "pumpfun:j": ["Sniper exit = kill (no ticket if already out)", "kill", ["FTR"]],
    "pumpfun:k": ["Ticket floor: 500/1,500 holders + 85/200 SOL depth", "liq", []],
    "pumpfun:pf1": ["Wash-farm shapes = Pass", "sybil", ["RST", "TRUTH", "SARP"]],
    "pumpfun:pf2": ["Don't ticket copycat tickers", "entry", ["FTR"]],
    "pumpfun:pf-l": ["Reactivation spikes: wait one full candle", "entry", ["CYBERAXE"]],
    "pumpfun:pf-m": ["Trade counts aren't flow; 2 holder drops = early close", "kill", ["FTR"]],
    "pumpfun:pf-n": ["Tight top-12 holder band = sybil", "sybil", ["MINTER"]],
    "pumpfun:pf-o": ["Old coins need fresh holders (+2%)", "entry", ["SWORDCAT"]],
    "pumpfun:pf-daily-2026-10-17": ["Oct 17: 3 spike entries lost, CATE held", "review", ["CYBERAXE", "FTR", "SWORDCAT", "CATE"]],
    "nft:R001": ["Never open after a pump", "entry", []]
  };
  function guess(x) {
    const t = String(x.rule || ""), id = String(x.id || "");
    let cat = "entry";
    if (/daily|day review/i.test(id + " " + t.slice(0, 24))) cat = "review";
    else if (/kill|exit|close .* early/i.test(t)) cat = "kill";
    else if (/sybil|wash|airdrop|bundle|identical/i.test(t)) cat = "sybil";
    else if (/swarm|wallet|sniper|flip pair/i.test(t)) cat = "swarm";
    else if (/ADA side|pool|depth|liq/i.test(t)) cat = "liq";
    let title = t.split(/[:;.]| = |\(/)[0].replace(/^\s*buy\b[:\s-]*/i, "").trim().slice(0, 72);
    if (!title) title = CATS[cat].label;
    const ev = (t.match(/\b[A-Z][A-Z0-9]{2,9}\b/g) || []).filter(w => !/^(ADA|SOL|KOTH|ETH|USD|OS|PM|AM|ET)$/.test(w) && !/^L\d$/.test(w));
    return [title, cat, [...new Set(ev)].slice(0, 4)];
  }
  function meta(x, book) {
    const ev = Array.isArray(x.evidence) ? x.evidence.filter(Boolean) : [];
    if (x.title) {
      const cat = CATS[x.category] ? x.category : guess(x)[1];
      return [String(x.title), cat, ev];
    }
    const key = x.id ? `${book}:${x.id}` : Object.keys(RULE_META).find(k => k.startsWith(book + ":") && String(x.rule || "").startsWith(k.slice(book.length + 1)));
    return (key && RULE_META[key]) || guess(x);
  }
  const catIcon = c => `<svg class="ci" viewBox="0 0 16 16" width="14" height="14">${(CATS[c] || CATS.entry).d}</svg>`;

  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("click", e => {
      const chip = e.target.closest && e.target.closest(".rchip");
      if (!chip) return;
      const box = chip.closest(".rv2");
      if (!box) return;
      e.preventDefault();
      box.querySelectorAll(".rchip").forEach(c => c.classList.toggle("on", c === chip));
      box.dataset.cat = chip.dataset.cat;
    });
  }

  function mirrorHTML(mirrors, byKey) {
    if (mirrors == null || mirrors === "") return "";
    const key = String(mirrors);
    const hit = byKey[key];
    const label = hit ? `${key} · ${hit}` : key;
    return `<span class="rmirror" title="${S.esc(hit ? "mirrors " + hit : "mirror not in this list")}">${S.esc(label)}</span>`;
  }

  S.register("rules", { span: 12, render({ data, opt }) {
    const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
    let r = [];
    books.forEach(b => (b.json.rules || []).forEach(x => r.push(Object.assign({}, x, { book: b.json.book }))));
    r.sort((a, b) => String(b.added_at || "").localeCompare(String(a.added_at || "")));
    if (!r.length) return `<p class="muted">No rules yet.</p>`;
    const rows = r.map(x => { const [title, cat, ev] = meta(x, x.book); return { x, title, cat, ev }; });
    const byKey = {};
    rows.forEach(o => { if (o.x.id) byKey[`${o.x.book}:${o.x.id}`] = o.title; });
    const counts = {};
    rows.forEach(o => counts[o.cat] = (counts[o.cat] || 0) + 1);
    const chips = `<div class="rchips"><button type="button" class="rchip on" data-cat="all">All <b>${rows.length}</b></button>${Object.keys(CATS).filter(c => counts[c]).map(c => `<button type="button" class="rchip" data-cat="${c}" style="--c:${CATS[c].color}">${catIcon(c)}${S.esc(CATS[c].label)} <b>${counts[c]}</b></button>`).join("")}</div>`;
    const limit = opt.limit || 0;
    const items = rows.map(({ x, title, cat, ev }, i) => {
      const pair = BOOKS[x.book] || [String(x.book || "").toUpperCase(), ""];
      return `<li class="rr${limit && i >= limit ? " extra" : ""}" data-x data-cat="${cat}" style="--c:${CATS[cat].color}">
        <span class="rico">${catIcon(cat)}</span>
        <div class="rbody"><div class="rt"><b>${S.esc(title)}</b></div>
          <div class="rmeta"><span class="rbook" data-desk="${S.esc(pair[1])}">${S.esc(pair[0])}</span><span class="rcat">${S.esc(CATS[cat].label)}</span><span class="rdate">${S.when(String(x.added_at || "").slice(0, 10))}</span>
            ${ev.map(c => `<span class="ev" title="evidence">${S.assetIcon(c, "sm")}${S.esc(c)}</span>`).join("")}
            ${mirrorHTML(x.mirrors, byKey)}
            <span class="rid2" title="original id">${S.esc(x.id || "no id")}</span></div>
          <div class="rfull"><p>${S.rich(x.rule)}</p><span class="rsrc">source: ${S.rich(x.source || "–")} · added ${S.when(x.added_at)}</span></div>
        </div></li>`;
    }).join("");
    const more = limit && rows.length > limit ? `<li class="rmore" onclick="this.closest('.rv2').classList.add('all')">Show all ${rows.length} rules ▾</li>` : "";
    return `<div class="rv2" data-cat="all">${chips}<ul class="rlist">${items}${more}</ul></div>`;
  }});
  S.ruleRow = (x, ctx) => {
    const book = String(ctx || "").split("·").pop() || x.book;
    const [title, cat] = meta(x, book);
    return `<li data-x><span class="rid" style="color:${CATS[cat].color}">${catIcon(cat)}</span><span class="ell rtext"><b>${S.esc(title)}</b> · ${S.rich(x.rule)}</span><span class="meta">${S.esc(ctx || "")} · ${S.when(x.added_at)}</span></li>`;
  };
})();
