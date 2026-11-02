/* Early runners. Same S.register("radar") type and pulse object.
   Read order: r.factors / r.flags, then regex on signals[], then the raw prose.
   A missing factor is hidden. Zero is a real reading and stays. */
(function () {
  const num = s => (s == null || s === "" ? null : parseFloat(String(s).replace(/[,~+]/g, "")));
  const sgn = s => (s == null || s === "" ? null : parseFloat(String(s).replace(/[,~]/g, "")));
  const hasNum = v => typeof v === "number" && isFinite(v);

  function soladaFactors(r) {
    const f = {};
    for (const s of [].concat(r.signals || [])) {
      let m;
      if ((m = s.match(/(on-chain|native) holders ([\d,]+)\s*->\s*([\d,]+) since [\d:]+ \(([+-]?[\d.]+)\/h\)/i))) {
        f.holders_basis = m[1].toLowerCase() === "native" ? "native" : "onchain";
        f.holders_prev = num(m[2]); f.holders = num(m[3]); f.holders_per_h = sgn(m[4]);
      }
      if ((m = s.match(/acceleration ([+-]?[\d.]+)\/h/i))) f.accel_per_h = sgn(m[1]);
      if ((m = s.match(/graduated to ([A-Za-z0-9-]+)/i))) f.graduated = m[1].toLowerCase();
      if ((m = s.match(/\bcurve ([\d.]+)%\s*->\s*([\d.]+)% \((?:~)?([+-]?[\d.]+) pts\/h\)/i))) {
        f.curve_pct = num(m[2]); f.curve_pts_per_h = sgn(m[3]);
      } else if ((m = s.match(/\bcurve ([\d.]+)%\s*->\s*complete/i))) {
        f.curve_pct = 100;
      } else if ((m = s.match(/\bcurve ([\d.]+)% flat/i))) {
        f.curve_pct = num(m[1]); f.curve_pts_per_h = 0;
      }
      if ((m = s.match(/\(x([\d.]+)\)/i))) f.vol_x = num(m[1]);
      else if ((m = s.match(/vol h1 ~?([\d,.]+)k?\s+[A-Za-z-]+\s+(?:vs|of)\s+~?([\d,.]+)k?\s+.*h6/i))) {
        const k1 = /vol h1 ~?[\d,.]+k/i.test(s), k2 = /(?:vs|of)\s+~?[\d,.]+k/i.test(s);
        const a = num(m[1]) * (k1 ? 1000 : 1), b = num(m[2]) * (k2 ? 1000 : 1);
        if (a != null && b) f.vol_x = +((a / (b / 6)).toFixed(2));
      }
      if ((m = s.match(/h1 (\d+)\s*(?:buys?)?\s*\/\s*(\d+)\b/i)) || (m = s.match(/\((\d+) buys? \/ (\d+) sells?\)/i)) || (m = s.match(/(\d+) buys? \/ (\d+) sells?/i))) {
        f.buys_h1 = +m[1]; f.sells_h1 = +m[2];
      }
      if ((m = s.match(/price h1 ([+-]?[\d.]+)%/i))) f.price_h1_pct = sgn(m[1]);
      if ((m = s.match(/price h6 ([+-]?[\d.]+)%/i)) || (m = s.match(/\bh6 ([+-][\d.]+)%/i))) f.price_h6_pct = sgn(m[1]);
      if (/swarm keys?/i.test(s) && !/holding now:\s*none/i.test(s)) {
        let keys = [];
        const after = s.match(/holding now:\s*([^;]+)/i);
        if (after && !/^none\b/i.test(after[1].trim())) keys = after[1].match(/\bu[0-9a-z]{4,}/gi) || [];
        if (!keys.length) {
          const before = s.match(/((?:\bu[0-9a-z]{4,}\s*,\s*)+\bu[0-9a-z]{4,})\s+holding now/i);
          if (before) keys = before[1].match(/\bu[0-9a-z]{4,}/gi) || [];
        }
        if (keys.length) { f.swarm = keys.length; f.swarm_keys = keys; }
      }
      if ((m = s.match(/(\d+) of (\d+) first buyers are on the log's repeat/i))) f.repeat_first_buyers = [+m[1], +m[2]];
      if ((m = s.match(/flags (\d+) sniper\/bundler/i)) || (m = s.match(/flagged wallets in top holders (?:[\d,]+->)?(\d+)/i))) f.snipers = +m[1];
      if (/one-sided/i.test(s)) f.one_sided = true;
      if (/airdrop/i.test(s)) f.airdrop = true;
      if ((m = s.match(/native (\d+) of (\d+) Solada-count holders \((\d+)%\)/i))) f.native_share_pct = +m[3];
      if ((m = s.match(/top-10(?: ex-pool)? ([\d.]+)%/i))) f.top10_pct = num(m[1]);
      if ((m = s.match(/top-1 ([\d.]+)%/i))) f.top1_pct = num(m[1]);
      if ((m = s.match(/pool ADA side ([\d,]+)\s*->\s*([\d,]+) \(([+-][\d.]+)%\)/i))) { f.ada_side = num(m[2]); f.ada_side_pct = sgn(m[3]); }
      else if ((m = s.match(/pool ADA side ([\d,]+)/i))) f.ada_side = num(m[1]);
    }
    return f;
  }
  function nftFactors(r) {
    const f = {};
    for (const s of [].concat(r.signals || [])) {
      let m;
      if ((m = s.match(/^floor ([+-][\d.]+)%\/h/i))) f.floor_pct_h = sgn(m[1]);
      if ((m = s.match(/floor accelerating \(([+-]?[\d.]+) -> ([+-]?[\d.]+)%\/h\)/i))) f.floor_pct_h_prev = sgn(m[1]);
      if ((m = s.match(/sales spike ([\d.]+)\/h vs ([\d.]+)\/h base/i))) {
        f.sales_h = num(m[1]); f.sales_base_h = num(m[2]);
        if (f.sales_base_h) f.sales_x = f.sales_h / f.sales_base_h;
      }
      if ((m = s.match(/^buyers ([\d.]+)\/h vs ([\d.]+)\/h/i))) {
        f.buyers_h = num(m[1]); f.buyers_base_h = num(m[2]);
        if (f.buyers_base_h) f.buyers_x = f.buyers_h / f.buyers_base_h;
      }
      if ((m = s.match(/near-floor listings ([+-]?\d+)% in (\d+)h/i))) { f.listings_chg_pct = sgn(m[1]); f.listings_window_h = +m[2]; }
      if ((m = s.match(/sell-through (\d+)%\/h/i))) f.sell_through_pct_h = +m[1];
      if ((m = s.match(/(\d+) repeat buyer wallets/i))) f.repeat_wallets_24h = +m[1];
      if ((m = s.match(/concentrated: (\d+) wallet\(s\) took (\d+) sales/i))) f.concentrated = { wallets: +m[1], sales: +m[2] };
      if ((m = s.match(/broad buying (\d+) wallets\/(\d+) sales/i))) f.broad = { wallets: +m[1], sales: +m[2] };
      if (/^fresh:/i.test(s)) f.fresh = true;
    }
    const n = String(r.note || "").match(/floor ([\d.]+) ([A-Z]+)/);
    if (n) { f.floor = num(n[1]); f.floor_unit = n[2]; }
    const offer = String(r.note || "").match(/top offer ([\d.]+)/i);
    if (offer) f.top_offer = num(offer[1]);
    return f;
  }
  function mergeFactors(r, nft) {
    const parsed = nft ? nftFactors(r) : soladaFactors(r);
    const structured = r.factors && typeof r.factors === "object" ? r.factors : null;
    const f = Object.assign({}, parsed);
    if (structured) {
      for (const [k, v] of Object.entries(structured)) {
        if (v != null) f[k] = v;
      }
    }
    if (f.holders_prev == null && hasNum(f.holders_from)) f.holders_prev = f.holders_from;
    if (!Array.isArray(f.floor_accel) && hasNum(f.floor_pct_h_prev) && hasNum(f.floor_pct_h)) f.floor_accel = [f.floor_pct_h_prev, f.floor_pct_h];
    if (!hasNum(f.sales_x) && hasNum(f.sales_h) && hasNum(f.sales_base_h) && f.sales_base_h) f.sales_x = f.sales_h / f.sales_base_h;
    if (!hasNum(f.buyers_x) && hasNum(f.buyers_h) && hasNum(f.buyers_base_h) && f.buyers_base_h) f.buyers_x = f.buyers_h / f.buyers_base_h;
    return f;
  }

  const FLAG_LABEL = {
    first_sighting: "FIRST",
    thin_floor: "THIN FLOOR",
    no_offer: "NO OFFER",
    concentrated_buyers: "CONCENTRATED",
    floor_falling: "FLOOR DOWN",
    dust: "DUST",
    sales_spike: "SALES SPIKE",
    broad_buyers: "BROAD",
    fresh: "FRESH",
    "swarm-holding": "SWARM",
    "volume-faded": "VOL FADED",
    "holders-flat": "FLAT",
    "above-kill-lines": "ABOVE KILL",
    "holders-rising": "RISING",
    "volume-spike": "VOL SPIKE",
    "sniper-flags": "SNIPERS",
    "low-native-share": "LOW NATIVE",
    "swarm-rotation": "ROTATION",
    "pre-seeded-pool": "PRE-SEEDED",
    "holders-falling": "FALLING",
    "team-airdrop": "AIRDROP",
    "below-rule-k-depth": "THIN DEPTH",
    "new-launch": "NEW",
    "buyers-lead-h1": "BUYERS LEAD",
    "thin-pool": "THIN POOL",
    "sellers-lead-h1": "SELLERS H1",
    "curve-reversing": "CURVE DOWN",
    "swarm-first-buyers": "REPEAT EARLY",
    "native-majority": "NATIVE",
    "sellers-lead-h6": "SELLERS H6",
    "one-sided": "ONE-SIDED"
  };
  function toneOf(t) { return t === "good" || t === "warn" || t === "bad" ? t : "warn"; }
  function flagBadges(flags) {
    return [].concat(flags || []).filter(fl => fl && fl.code).map(fl => {
      const label = Object.prototype.hasOwnProperty.call(FLAG_LABEL, fl.code) ? FLAG_LABEL[fl.code] : String(fl.code);
      return [label, toneOf(fl.tone), String(fl.code)];
    });
  }
  function repeatLabel(v) {
    if (Array.isArray(v) && v.length >= 2 && v[0] != null && v[1] != null) return v[0] + "/" + v[1];
    return v ? String(v) : "";
  }
  function derivedBadges(f, nft) {
    const b = [];
    if (nft) {
      if (f.concentrated && f.concentrated.wallets) b.push([`CONCENTRATED ${f.concentrated.wallets}w/${f.concentrated.sales}`, "bad", "few wallets took most sales"]);
      if (f.broad && f.broad.wallets) b.push([`BROAD ${f.broad.wallets}w/${f.broad.sales}`, "good", "sales spread across wallets"]);
      if (f.fresh === true) b.push(["FRESH", "good", "most lifetime sales are recent"]);
    } else {
      if (f.graduated) b.push(["GRADUATED", "good", "graduated to " + f.graduated]);
      if (hasNum(f.swarm) && f.swarm > 0) b.push([`SWARM ×${f.swarm}`, "warn", "swarm keys still holding"]);
      if (hasNum(f.snipers) && f.snipers > 0) b.push([`SNIPERS ${f.snipers}`, "bad", "sniper or bundler flags"]);
      if (f.one_sided === true) b.push(["ONE-SIDED", "bad", "one-sided order flow"]);
      const rep = repeatLabel(f.repeat_first_buyers);
      if (rep) b.push([`REPEAT EARLY ${rep}`, "warn", "first buyers on the repeat lists"]);
      if (f.airdrop === true) b.push(["AIRDROP", "warn", "team airdrop inflates the holder base"]);
      if (hasNum(f.native_share_pct)) b.push([`NATIVE ${S.fmt(f.native_share_pct, 0)}%`, f.native_share_pct < 50 ? "warn" : "good", "native share of the holder count"]);
    }
    return b;
  }

  const STOPS = [[0, [59, 91, 122]], [35, [54, 214, 255]], [60, [255, 210, 63]], [80, [255, 138, 61]], [100, [255, 61, 110]]];
  function heat(n) {
    n = Math.max(0, Math.min(100, n));
    for (let i = 1; i < STOPS.length; i++) {
      const [b, cb] = STOPS[i], [a, ca] = STOPS[i - 1];
      if (n <= b) { const t = (n - a) / (b - a); return `rgb(${ca.map((c, k) => Math.round(c + (cb[k] - c) * t)).join(",")})`; }
    }
    return "rgb(255,61,110)";
  }
  function ring(norm, raw) {
    const R = 19, C = 2 * Math.PI * R, col = heat(norm);
    const shown = Math.round(Math.max(0, Math.min(100, norm)));
    const rawTxt = hasNum(raw) ? S.fmt(raw, Math.abs(raw) < 10 && raw % 1 ? 1 : 0) : "–";
    return `<span class="ring" style="--heat:${col}" title="score ${S.esc(rawTxt)} (desk-normalized ${shown}/100)">
      <svg viewBox="0 0 48 48" width="48" height="48"><circle cx="24" cy="24" r="${R}" class="trk"/><circle cx="24" cy="24" r="${R}" class="val" stroke-dasharray="${(C * Math.max(0, Math.min(100, norm)) / 100).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 24 24)"/></svg>
      <b>${shown}</b><i>raw ${S.esc(rawTxt)}</i></span>`;
  }
  const IC = {
    holders: '<path d="M6 7a2.5 2.5 0 1 0 0-.01M2 14c0-2.5 2-4 4-4s4 1.5 4 4M11.5 6.5a2 2 0 1 0 0-.01M10.5 10.2c2.2-.6 4 .8 4 3.3"/>',
    accel: '<path d="M2 13c3 0 4-2 5.5-5S11 3 14 3M10.5 3H14v3.5"/>',
    curve: '<path d="M2 13a6 6 0 0 1 12 0M8 13l3-4"/>',
    vol: '<path d="M3 13V9M6.5 13V5M10 13V8M13.5 13V2"/>',
    flow: '<path d="M2 5h10l-2.5-2.5M14 11H4l2.5 2.5"/>',
    price: '<path d="M2 12l4-4 3 3 5-6M10 5h4v4"/>',
    floor: '<path d="M2 12h12M4 9l3-3 2 2 4-4"/>',
    sales: '<path d="M3 4h10l-1 6H4zM5 13a.8.8 0 1 0 0-.01M11 13a.8.8 0 1 0 0-.01"/>',
    list: '<path d="M5 4h9M5 8h6M5 12h3M2 4h.01M2 8h.01M2 12h.01"/>',
    repeat: '<path d="M3 7a5 5 0 0 1 9-2M13 9a5 5 0 0 1-9 2M12 2v3h-3M4 14v-3h3"/>',
    depth: '<path d="M2 5c2 0 2-1.5 4-1.5S8 5 10 5s2-1.5 4-1.5M2 9c2 0 2-1.5 4-1.5S8 9 10 9s2-1.5 4-1.5M2 13c2 0 2-1.5 4-1.5S8 13 10 13s2-1.5 4-1.5"/>',
    sell: '<path d="M8 2v9M4.5 7.5 8 11l3.5-3.5M3 14h10"/>'
  };
  const icon = k => `<svg class="ti" viewBox="0 0 16 16" width="13" height="13">${IC[k] || ""}</svg>`;
  const arrow = (v, goodUp = true) => !hasNum(v) || v === 0 ? `<span class="ar flat">■</span>` : `<span class="ar ${(v > 0) === goodUp ? "up" : "down"}">${v > 0 ? "▲" : "▼"}</span>`;
  const sign = (v, d = 1, suf = "") => !hasNum(v) ? "–" : (v > 0 ? "+" : "") + S.fmt(v, d) + suf;
  function tile(k, label, val, extra = "", cls = "", tip = "") {
    const mx = (extra.match(/<span class="mx[^"]*">.*?<\/span>/) || [""])[0], bar = extra.replace(mx, "");
    return `<span class="mt ${cls}" title="${S.esc(tip || label)}">${icon(k)}<span class="mv">${val}</span><span class="ml">${S.esc(label)}${mx}</span>${bar}</span>`;
  }
  function holdersTile(f) {
    if (!hasNum(f.holders_per_h)) return "";
    const basis = String(f.holders_basis || "").toLowerCase();
    const from = hasNum(f.holders_prev) && hasNum(f.holders) ? `<span class="mx">${S.fmt(f.holders_prev, 0)}→${S.fmt(f.holders, 0)}</span>` : "";
    return tile("holders", (basis === "native" ? "native" : "holders") + "/h", `${arrow(f.holders_per_h)}${sign(f.holders_per_h)}`, from, "", "holder change per hour");
  }
  function soladaTiles(f) {
    const t = [holdersTile(f)];
    if (hasNum(f.accel_per_h)) t.push(tile("accel", "accel", `${arrow(f.accel_per_h)}${sign(f.accel_per_h)}`, "", "", "change in holders/h vs the prior pulse"));
    if (hasNum(f.curve_pct) || f.graduated) {
      const pct = hasNum(f.curve_pct) ? f.curve_pct : 100;
      const label = f.graduated ? "curve · done" : (hasNum(f.curve_pts_per_h) ? `curve ${sign(f.curve_pts_per_h)}/h` : "curve");
      t.push(tile("curve", label, `${S.fmt(pct, 1)}%`, `<span class="pg"><i style="width:${Math.min(100, Math.max(0, pct))}%"></i></span>`, "wide", f.graduated ? "graduated to " + f.graduated : "bonding-curve progress"));
    }
    if (hasNum(f.vol_x)) t.push(tile("vol", "vol ×h-avg", `${arrow(f.vol_x - 1)}×${S.fmt(f.vol_x, 1)}`, "", f.vol_x >= 2 ? "hot" : "", "last hour volume vs the prior-hours average"));
    if (hasNum(f.buys_h1) && hasNum(f.sells_h1)) {
      const tot = f.buys_h1 + f.sells_h1, b = tot ? f.buys_h1 / tot * 100 : 50;
      t.push(tile("flow", "buys / sells h1", `<span class="up">${f.buys_h1}</span><span class="sl">/</span><span class="down">${f.sells_h1}</span>`, `<span class="split"><i style="width:${b.toFixed(0)}%"></i></span>`, "wide", "observed trade counts, last hour"));
    }
    if (hasNum(f.price_h1_pct)) t.push(tile("price", "price h1", `${arrow(f.price_h1_pct)}${sign(f.price_h1_pct, 1, "%")}`));
    if (hasNum(f.ada_side)) {
      const thin = f.ada_side < 11000;
      const mx = hasNum(f.ada_side_pct) ? `<span class="mx ${f.ada_side_pct >= 0 ? "up" : "down"}">${sign(f.ada_side_pct, 1, "%")}</span>` : "";
      t.push(tile("depth", "ADA side", `${S.fmt(f.ada_side / 1000, 1)}k`, mx, thin ? "thin" : "", "pool ADA-side depth"));
    }
    return t.join("");
  }
  function nftTiles(f) {
    const t = [];
    if (hasNum(f.floor_pct_h)) {
      const tip = f.floor_pct_h_basis ? "floor change per hour (" + f.floor_pct_h_basis + ")" : "floor change per hour";
      const from = Array.isArray(f.floor_accel) ? `<span class="mx">from ${sign(f.floor_accel[0], 1, "%")}</span>` : "";
      t.push(tile("floor", "floor /h", `${arrow(f.floor_pct_h)}${sign(f.floor_pct_h, 1, "%")}`, from, "", tip));
    }
    if (hasNum(f.sales_x)) {
      const tip = f.sales_base_basis ? "sales vs " + f.sales_base_basis : "sales vs baseline";
      t.push(tile("sales", "sales ×base", `${arrow(f.sales_x - 1)}×${S.fmt(f.sales_x, 1)}`, `<span class="mx">${S.fmt(f.sales_h, 0)}/h vs ${S.fmt(f.sales_base_h, 1)}</span>`, f.sales_x >= 3 ? "hot" : "", tip));
    }
    if (hasNum(f.buyers_x)) t.push(tile("holders", "buyers ×base", `${arrow(f.buyers_x - 1)}×${S.fmt(f.buyers_x, 1)}`, `<span class="mx">${S.fmt(f.buyers_h, 0)}/h vs ${S.fmt(f.buyers_base_h, 1)}</span>`));
    if (hasNum(f.listings_chg_pct)) {
      const tip = f.listings_basis ? "near-floor listings (" + f.listings_basis + ")" : "near-floor listings change";
      const win = hasNum(f.listings_window_h) ? S.fmt(f.listings_window_h, 0) + "h" : "";
      t.push(tile("list", `listings ${win}`.trim(), `${arrow(-f.listings_chg_pct)}${sign(f.listings_chg_pct, 0, "%")}`, `<span class="pg thin"><i style="width:${Math.min(100, Math.abs(f.listings_chg_pct))}%"></i></span>`, "wide", tip));
    }
    if (hasNum(f.sell_through_pct_h)) t.push(tile("sell", "sell-through", `${S.fmt(f.sell_through_pct_h, 0)}%/h`, "", "", f.sell_through_basis || "share of listed supply selling per hour"));
    if (hasNum(f.repeat_wallets_24h)) t.push(tile("repeat", "repeat 24h", `${S.fmt(f.repeat_wallets_24h, 0)}`, "", "", "repeat buyer wallets, 24h"));
    if (hasNum(f.bid_units_10pct)) t.push(tile("depth", "bids 10%", `${S.fmt(f.bid_units_10pct, 0)}`, hasNum(f.bidders_10pct) ? `<span class="mx">${S.fmt(f.bidders_10pct, 0)} wallets</span>` : "", "", "bids within 10% of floor"));
    return t.join("");
  }
  function sparkOf(pulse, r) {
    const pts = S.pointsFor(pulse, r.ticker).map(p => p.metrics || {});
    const key = ["holders_native", "holders_onchain", "holders", "floor"].find(k => pts.filter(m => typeof m[k] === "number").length >= 2);
    if (key) return { key: key === "floor" ? "floor" : "holders", vals: pts.map(m => m[key]).filter(v => typeof v === "number") };
    const vals = S.seriesOf({ ticker: r.ticker }, pulse);
    return { key: vals.length >= 2 ? "points" : null, vals };
  }
  function pointsX(pulse, r, key) {
    const v = S.pointsFor(pulse, r.ticker).map(p => (p.metrics || {})[key] ?? (p.extra || {})[key]).filter(x => typeof x === "number");
    if (v.length < 2) return null;
    const prior = v.slice(Math.max(0, v.length - 6), -1);
    const avg = prior.reduce((a, b) => a + b, 0) / (prior.length || 1);
    return avg > 0 ? v[v.length - 1] / avg : null;
  }
  function ageChip(iso, now) {
    const h = (now - new Date(iso).getTime()) / 36e5;
    if (!isFinite(h) || h < 0) return "";
    const txt = h < 1 ? Math.max(1, Math.round(h * 60)) + "m old" : h < 48 ? Math.floor(h) + "h old" : Math.floor(h / 24) + "d old";
    return `<span class="age-chip ${h < 6 ? "new" : h < 48 ? "mid" : "old"}" title="first seen ${S.esc(S.when(iso))}">${txt}</span>`;
  }
  function heatScore(r) {
    const n = hasNum(r.score) ? r.score : r.score_raw;
    return hasNum(n) ? n : 0;
  }
  function rawScore(r) { return hasNum(r.score_raw) ? r.score_raw : (hasNum(r.score) ? r.score : null); }
  function deskScale(rows) {
    const listed = rows.map(r => Number(r.score_max)).filter(n => isFinite(n) && n > 0);
    if (listed.length) return Math.max.apply(null, listed);
    return Math.max.apply(null, rows.map(heatScore).concat([0])) || 1;
  }

  function momentumMap(items, nft) {
    const W = 340, H = 210, L = 30, Rr = 10, T = 12, B = 26;
    const xs = items.map(d => d.mx).filter(hasNum), ys = items.map(d => d.my).filter(hasNum);
    let xlo, xhi;
    if (nft) { xlo = 0.25; xhi = Math.max(4, ...(xs.length ? xs : [1])) * 1.15; }
    else { xlo = Math.min(-5, ...(xs.length ? xs : [0])) - 2; xhi = Math.max(10, ...(xs.length ? xs : [0])) + 3; }
    const ylo = 0.1, yhi = Math.max(8, ...(ys.length ? ys : [1])) * 1.4;
    const lx = v => nft ? Math.log(Math.max(v, 0.05)) : v;
    const X = v => L + (lx(v) - lx(xlo)) / ((lx(xhi) - lx(xlo)) || 1) * (W - L - Rr);
    const Y = v => T + (1 - (Math.log(Math.max(v, ylo)) - Math.log(ylo)) / ((Math.log(yhi) - Math.log(ylo)) || 1)) * (H - T - B);
    const x0 = X(nft ? 1 : 0), y1 = Y(1);
    const maxS = Math.max(1, ...items.map(d => d.norm));
    const geo = items.slice().sort((a, b) => b.norm - a.norm).map(d => ({ d, known: hasNum(d.mx) && hasNum(d.my),
      cx: X(hasNum(d.mx) ? d.mx : (nft ? 1 : 0)), cy: Y(Math.max(ylo * 1.2, hasNum(d.my) ? d.my : 1)), r: 4 + 10 * Math.sqrt(d.norm / maxS) }));
    const placed = geo.map(g => [g.cx - g.r, g.cy - g.r, g.cx + g.r, g.cy + g.r]);
    const boxOf = (x, y, a, lw) => {
      const x0 = a === "middle" ? x - lw / 2 : a === "end" ? x - lw : x;
      return [x0, y - 8, x0 + lw, y + 1];
    };
    const hits = bx => bx[0] < 2 || bx[2] > W - 2 || bx[1] < 1 || bx[3] > H - 4 || placed.some(p => bx[0] < p[2] + 1 && bx[2] > p[0] - 1 && bx[1] < p[3] + 1 && bx[3] > p[1] - 1);
    let spill = 0;
    const labels = geo.map(g => {
      const lw = Math.min(86, g.d.label.length * 5.5 + 6);
      const dirs = [[0, -1, "middle"], [1, 0.15, "start"], [-1, 0.15, "end"], [0, 1, "middle"], [0.85, -0.85, "start"], [-0.85, -0.85, "end"], [1, 0.85, "start"], [-1, 0.85, "end"]];
      const cands = [];
      for (const rad of [g.r + 5, g.r + 14, g.r + 26]) for (const [dx, dy, a] of dirs) cands.push([g.cx + dx * rad, g.cy + dy * rad, a]);
      let pick = cands.find(c => !hits(boxOf(c[0], c[1], c[2], lw)));
      let lead = false;
      if (!pick) {
        const y = 14 + spill * 11;
        spill += 1;
        const side = g.cx < W / 2 ? W - 6 : 8;
        pick = [side, y, g.cx < W / 2 ? "end" : "start"];
        lead = true;
      } else lead = Math.hypot(pick[0] - g.cx, pick[1] - g.cy) > g.r + 15;
      placed.push(boxOf(pick[0], pick[1], pick[2], lw));
      const line = lead ? `<line class="lead" x1="${g.cx.toFixed(1)}" y1="${g.cy.toFixed(1)}" x2="${pick[0].toFixed(1)}" y2="${pick[1].toFixed(1)}"/>` : "";
      return `${line}<g class="dot ${g.known ? "" : "hollow"}" style="--heat:${heat(g.d.norm)}"><circle cx="${g.cx.toFixed(1)}" cy="${g.cy.toFixed(1)}" r="${g.r.toFixed(1)}"/><text x="${pick[0].toFixed(1)}" y="${Math.max(9, pick[1]).toFixed(1)}" text-anchor="${pick[2]}">${S.esc(g.d.label)}</text></g>`;
    }).join("");
    const yt = [0.25, 1, 4].filter(v => v > ylo && v < yhi).map(v => `<text class="ax" x="${L - 4}" y="${(Y(v) + 3).toFixed(1)}" text-anchor="end">×${v}</text><line class="g" x1="${L}" x2="${W - Rr}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>`).join("");
    const xt = (nft ? [0.5, 1, 2, 4] : [-5, 0, 5, 10, 20]).filter(v => v > xlo && v < xhi).map(v => `<text class="ax" x="${X(v).toFixed(1)}" y="${H - B + 11}" text-anchor="middle">${nft ? "×" + v : (v > 0 ? "+" : "") + v}</text>`).join("");
    return `<figure class="mmap"><figcaption><b>Momentum map</b><span>x: ${nft ? "buyers × base" : "holders Δ/h"} · y: ${nft ? "sales × base" : "volume × h-avg"} (log) · size: score</span></figcaption>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Momentum map">
        <rect class="hotq" x="${Math.min(x0, W - Rr)}" y="${T}" width="${Math.max(0, W - Rr - x0)}" height="${Math.max(0, y1 - T)}"/>
        <text class="q" x="${W - Rr - 4}" y="${T + 10}" text-anchor="end">${nft ? "more buyers + sales" : "holders + volume up"}</text>
        <text class="q" x="${L + 4}" y="${H - B - 4}">${nft ? "fading" : "quiet"}</text>
        ${yt}${xt}
        <line class="z" x1="${x0.toFixed(1)}" x2="${x0.toFixed(1)}" y1="${T}" y2="${H - B}"/><line class="z" x1="${L}" x2="${W - Rr}" y1="${y1.toFixed(1)}" y2="${y1.toFixed(1)}"/>
        ${labels}
      </svg><span class="mleg"><i class="sw solid"></i>reading <i class="sw hollow"></i>no reading (parked on axis)</span></figure>`;
  }

  S.radarFactors = (r, nft) => mergeFactors(r || {}, nft);

  S.register("radar", { span: 12, async render({ desk, data, opt }) {
    const pulse = data && typeof data === "object" && !Array.isArray(data) ? data : null;
    const rows = pulse && Array.isArray(pulse.radar) ? pulse.radar.filter(r => r && r.ticker) : [];
    if (!rows.length) return `<p class="muted">No early runners yet.</p>`;
    const nft = (pulse.desk || (desk && desk.id)) === "nft";
    const now = opt && opt.now ? new Date(opt.now).getTime() : Date.now();
    rows.sort((a, b) => heatScore(b) - heatScore(a) || String(a.ticker).localeCompare(String(b.ticker)));
    const scale = deskScale(rows);
    const items = rows.map(r => {
      const f = mergeFactors(r, nft);
      const norm = heatScore(r) / scale * 100;
      let mx = nft ? f.buyers_x : f.holders_per_h, my = nft ? f.sales_x : f.vol_x, derived = false;
      if (nft && !hasNum(mx)) { const d = pointsX(pulse, r, "buyers_1h"); if (d != null) { mx = d; derived = true; } }
      if (!hasNum(my)) { const d = pointsX(pulse, r, nft ? "sales_1h" : "volume_h1_ada"); if (d != null) { my = d; derived = true; } }
      const badges = Array.isArray(r.flags) ? flagBadges(r.flags) : derivedBadges(f, nft);
      if (nft && r.venue && !Array.isArray(r.flags)) badges.push([String(r.venue).toUpperCase(), "warn", "venue"]);
      return { r, f, norm, mx: hasNum(mx) ? mx : null, my: hasNum(my) ? my : null, derived, badges, label: r.ticker };
    });
    if (nft) {
      const w = items.map(d => String(d.r.ticker).split(/[-_]+/));
      items.forEach((d, i) => {
        const dup = w.some((o, j) => j !== i && o[0] === w[i][0]);
        d.label = (dup && w[i][1] ? w[i][0].slice(0, 4) + "-" + w[i][1].slice(0, 5) : w[i][0]).slice(0, 10);
      });
    }
    const cards = items.map(({ r, f, norm, derived, badges }, i) => {
      const hs = sparkOf(pulse, r);
      const sp = hs.vals.length >= 2 ? `<span class="hspark" title="${S.esc(hs.key)} over ${hs.vals.length} pulses">${S.spark(hs.vals, { w: 74, h: 22 })}<em>${S.esc(hs.key)}</em></span>` : `<span class="hspark na"><em>1 pulse</em></span>`;
      const bdg = badges.map(([t, c, tip]) => `<span class="bdg ${c}" title="${S.esc(tip)}">${c === "bad" ? "⚠ " : ""}${S.esc(t)}</span>`).join("");
      const tiles = nft ? nftTiles(f) : soladaTiles(f);
      const sig = [].concat(r.signals || []).filter(Boolean);
      const rawFace = !tiles && !bdg && sig.length ? `<div class="rawsig">${sig.map(s => `<p>${S.rich(s)}</p>`).join("")}</div>` : "";
      const name = nft ? String(r.ticker).replace(/[-_]+/g, " ") : r.ticker;
      const floor = nft && hasNum(f.floor) ? `<span class="fl">${S.fmt(f.floor, 4)} ${S.esc(f.floor_unit || "")}</span>` : "";
      const offer = nft && hasNum(f.top_offer) ? `<span class="fl">offer ${S.fmt(f.top_offer, 4)}</span>` : "";
      return `<article class="rc" data-x style="--heat:${heat(norm)}">
        <div class="rc-top">${ring(norm, rawScore(r))}
          <div class="rc-id">${S.assetIcon(r.ticker, "lg")}<div class="rc-n"><b class="ell" title="${S.esc(r.ticker)}">${S.esc(name)}</b>
            <span class="rc-sub">${S.chainIcon(r.chain, "xs")}<span>${S.esc(r.chain || "")}</span>${r.first_seen ? ageChip(r.first_seen, now) : ""}${floor}${offer}</span></div></div>
          ${sp}<span class="rk">#${i + 1}</span></div>
        ${tiles ? `<div class="tiles">${tiles}</div>` : ""}
        ${bdg ? `<div class="bdgs">${bdg}</div>` : ""}
        ${rawFace}
        <div class="rc-more"><span class="tap">▾ ${sig.length} signal${sig.length === 1 ? "" : "s"}${derived ? " · map uses points[] fallback" : ""}</span>
          <ul class="prose">${sig.map(s => `<li>${S.rich(s)}</li>`).join("")}</ul>${r.note ? `<p class="rnote">${S.rich(r.note)}</p>` : ""}</div>
      </article>`;
    }).join("");
    const legend = `<div class="heatleg"><span>score heat</span><i style="background:linear-gradient(90deg,${[0, 35, 60, 80, 100].map(heat).join(",")})"></i><span>0 → 100 (top of desk = 100)</span></div>`;
    return `<div class="rv">${pulse.example ? `<span class="pill">example</span>` : ""}
      <div class="rv-side">${momentumMap(items, nft)}${legend}</div>
      <div class="rv-cards">${cards}</div></div>`;
  }});
})();
