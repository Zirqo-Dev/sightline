/* Sightline Radar preview. Reads radar.json. Research only. */
(function (root) {
  var BASE_EMPTY = "No Base coin feed is published. This cut stays empty.";

  function svg(body) {
    return '<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + body + "</svg>";
  }
  var ICONS = {
    seen: svg('<circle cx="8" cy="8" r="5.2"/><path d="M8 5.2V8l2.2 1.3"/>'),
    pool: svg('<path d="M8 2.2C8 2.2 3.4 7.2 3.4 10a4.6 4.6 0 0 0 9.2 0C12.6 7.2 8 2.2 8 2.2z"/>'),
    desk: svg('<path d="M4 2.8v10.4M4 3.2h7.2L9.2 5.6 11.2 8H4"/>'),
    whale: svg('<path d="M2.2 9.2c1.6-2.6 4.2-2.4 5.4 0 1.6-2.8 5.8-2.2 6.2.2-1.8 2.6-4.6 2.2-6 .2-1.6 2.2-4.4 2.4-5.6-.4z"/>'),
    buzz: svg('<path d="M4 4l8 8M12 4L4 12"/>'),
    snipers: svg('<circle cx="8" cy="8" r="4.2"/><path d="M8 1.8v2.2M8 12v2.2M1.8 8h2.2M12 8h2.2"/>'),
    coord: svg('<circle cx="4" cy="11" r="1.3"/><circle cx="8" cy="4.2" r="1.3"/><circle cx="12" cy="11" r="1.3"/><path d="M5 10.2 7.2 5.4M8.8 5.4 11 10.2M5.2 11h5.6"/>'),
    heavy: svg('<path d="M3 13V8M8 13V4M13 13V10"/>'),
    thin: svg('<path d="M8 2.6v10.8"/>'),
    skull: svg('<circle cx="8" cy="7" r="3.4"/><path d="M6.2 6.4h.1M9.8 6.4h.1M6.4 11.2h3.2"/>'),
    heat: svg('<path d="M3.2 11.5a4.8 4.8 0 0 1 9.6 0"/><path d="M8 11.5V7.2"/>'),
    streak: svg('<path d="M2.6 12.2 6 8.6l2.4 2L13.2 4.4"/>'),
    leg: svg('<path d="M2 12.2 5.2 6.2 8 11l3.2-6.2L14 9"/>'),
    lock: svg('<rect x="4" y="7" width="8" height="6" rx="1"/><path d="M6 7V5.2a2 2 0 0 1 4 0V7"/>'),
    dev: svg('<path d="M2.8 8h7.2M8.2 5.2 11.2 8 8.2 10.8M12.4 3.2v9.6"/>'),
    holders: svg('<circle cx="6" cy="5.2" r="1.8"/><path d="M3.2 12.2c.4-2 1.6-3 2.8-3s2.4 1 2.8 3M11 6.2v4M9.2 8.2H12.8"/>'),
    sweep: svg('<path d="M3 12.2h10M5.2 12.2 8 5.2l2.8 7"/>'),
    whales: svg('<path d="M2 10.5c.8-1.4 2-1.3 2.6 0M6.6 10.5c.8-1.4 2-1.3 2.6 0M11.2 10.5c.8-1.4 2-1.3 2.6 0"/>')
  };
  var LEGEND = [
    ["seen", "Seen at", "How old the name was when the desk first logged it."],
    ["pool", "New pool", "Under an hour old."],
    ["desk", "Desk flag", "The Cardano screen passed this name. Not a Hood tier and not a Solana pick."],
    ["whale", "Whale", "A tracked whale moved. The detail uses the codename."],
    ["buzz", "X buzz", "Hollow while the feed is stale. Fresh buzz stays off the rows."],
    ["snipers", "Snipers out", "A sniper exit is on the flag file."],
    ["coord", "Coordinated", "Coordinated buys are on the flag file."],
    ["heavy", "Top-heavy", "Top holders are crowded, or the flag file says so."],
    ["thin", "Thin start", "The pool opened under 50 SOL, or HoodScout marked thin depth."],
    ["skull", "Kill line", "A Cardano kill line is crossed, or the Solana follow labelled the price dead."],
    ["heat", "Heat", "Heat is a ranking, not a buy signal."],
    ["streak", "Buy streak", "Buys beat sells on every recent 30-minute snap."],
    ["leg", "Second leg", "Price spiked, dipped, then pushed again."]
  ];
  var NOT_LIVE = [
    ["lock", "LP locked", "Not live. No lock field is published yet."],
    ["dev", "Dev sold", "Not live. Nothing to show today."],
    ["holders", "Holders up", "Not live. No 1-hour holder series."],
    ["sweep", "Floor sweep", "Not live. Sales totals are not a sweep."],
    ["whales", "Whale count", "Not live. No per-name whale count is published."]
  ];

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function icon(id) {
    return ICONS[id] || "";
  }
  function finite(value) {
    var n = Number(value);
    return value == null || value === "" || !isFinite(n) ? null : n;
  }
  var CHAIN_LOGO = {
    SOL: "dashboard/icons/chains/solana.svg",
    ADA: "dashboard/icons/chains/cardano.svg",
    ETH: "dashboard/icons/chains/ethereum.svg",
    BASE: "dashboard/icons/chains/base.svg",
    RH: "dashboard/icons/chains/multi.svg"
  };
  var SUB_DIGITS = "₀₁₂₃₄₅₆₇₈₉";
  var subscriptOk = true;

  function markSrc(row) {
    if (row && row.image) return row.image;
    return (row && CHAIN_LOGO[row.chain]) || "";
  }
  function compact(n) {
    var abs = Math.abs(n);
    if (abs >= 1e6) return (n / 1e6).toFixed(1) + "m";
    if (abs >= 1e3) return (n / 1e3).toFixed(1) + "k";
    if (abs >= 1) return String(Math.round(n));
    if (abs === 0) return "0";
    var exp = Math.floor(Math.log10(abs));
    var places = Math.min(8, Math.max(0, -exp + 1));
    var text = abs.toFixed(places).replace(/0+$/, "").replace(/\.$/, "");
    return (n < 0 ? "-" : "") + text;
  }
  function plainDecimal(abs) {
    if (!(abs > 0) || !isFinite(abs)) return "0";
    var exp = Math.floor(Math.log10(abs));
    if (!isFinite(exp)) return "0";
    var places = Math.max(12, -exp + 2);
    if (places > 100) places = 100;
    var text = abs.toFixed(places).replace(/0+$/, "").replace(/\.$/, "");
    if (text && text !== "0") return text;
    var zeros = -exp - 1;
    var digits = (abs / Math.pow(10, exp)).toFixed(2).replace(".", "").replace(/0+$/, "");
    return "0." + new Array(zeros + 1).join("0") + (digits || "0");
  }
  function subscriptDigits(value) {
    return String(value).replace(/[0-9]/g, function (digit) {
      return SUB_DIGITS.charAt(Number(digit));
    });
  }
  function tinyParts(abs) {
    if (!(abs > 0) || abs >= 0.0001 || !subscriptOk) return null;
    var exp = Math.floor(Math.log10(abs));
    if (!isFinite(exp)) return null;
    var zeros = -exp - 1;
    var rounded = Number((abs / Math.pow(10, exp)).toFixed(1));
    if (rounded >= 10) {
      rounded = 1;
      zeros -= 1;
    }
    if (zeros < 4 || zeros > 18) return null;
    return { zeros: zeros, digits: rounded.toFixed(1).replace(".", "") };
  }
  function withUnit(text, unit) {
    if (unit === "USD") return "$" + text;
    return text + (unit ? " " + unit : "");
  }
  function priceHtml(n, unit) {
    if (n == null) return esc("—");
    var abs = Math.abs(n);
    var sign = n < 0 ? "-" : "";
    if (abs === 0) return esc(withUnit("0", unit));
    if (abs >= 1) return esc(withUnit(n.toFixed(2), unit));
    if (abs >= 0.0001) {
      var mid = sign + abs.toFixed(abs >= 0.01 ? 4 : 6).replace(/0+$/, "").replace(/\.$/, "");
      return esc(withUnit(mid, unit));
    }
    var tiny = tinyParts(abs);
    var plain = withUnit(sign + plainDecimal(abs), unit);
    if (!tiny) return esc(plain);
    var visual = esc(sign + "0.0") + '<span class="subz">' + esc(subscriptDigits(tiny.zeros)) + "</span>" + esc(tiny.digits);
    if (unit === "USD") visual = "$" + visual;
    else if (unit) visual += " " + esc(unit);
    return '<span aria-label="' + esc(plain) + '"><span aria-hidden="true">' + visual + "</span></span>";
  }
  function sizeText(row) {
    var n = finite(row.size);
    if (n == null) return "—";
    if (row.size_kind === "liq" && row.size_unit === "SOL") return compact(n) + " SOL reserve";
    var body = row.size_unit === "USD" ? "$" + compact(n) : compact(n) + (row.size_unit ? " " + row.size_unit : "");
    return body;
  }
  function ageLabel(iso, now) {
    var t = Date.parse(iso);
    if (!isFinite(t)) return "";
    var mins = Math.max(0, Math.round((now - t) / 60000));
    if (mins < 90) return mins + "m";
    var hours = Math.round(mins / 60);
    if (hours < 48) return hours + "h";
    return Math.round(hours / 24) + "d";
  }
  function xStale(doc, now) {
    var asof = doc.x && Date.parse(doc.x.asof);
    if (!isFinite(asof)) return true;
    return (now - asof) > 48 * 60 * 60 * 1000;
  }
  function pill(change) {
    var n = finite(change);
    if (n == null) return '<span class="pill flat">—</span>';
    var cls = n > 0 ? "up" : n < 0 ? "down" : "flat";
    var text;
    if (n > 1000) {
      // Price multiple, not the gain divided by 100. +1000% is 11×, so the
      // first value past the cap is +11× and does not read as a smaller move.
      text = "+" + Math.round(1 + n / 100) + "\u00d7";
    } else {
      var rounded = Math.round(n * 10) / 10;
      if (n > -100 && rounded <= -100) rounded = -99.9;
      text = (rounded > 0 ? "+" : "") + rounded + "%";
    }
    return '<span class="pill ' + cls + '">' + text + "</span>";
  }
  function spark(values) {
    if (!values || values.length < 2) return "";
    var w = 64;
    var h = 22;
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var span = max - min || 1;
    var pts = values.map(function (v, i) {
      var x = 1 + (w - 2) * (i / (values.length - 1));
      var y = 1 + (h - 2) * (1 - (v - min) / span);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var tone = values[values.length - 1] >= values[0] ? "var(--up)" : "var(--down)";
    return '<svg class="spark" viewBox="0 0 64 22" aria-hidden="true"><polyline points="' + pts + '" fill="none" stroke="' + tone + '" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>';
  }
  function accept(row, filter) {
    if (!row) return false;
    if (filter === "NFT") return row.kind === "nft";
    if (row.kind === "nft") return false;
    if (!filter || filter === "All") return row.kind === "coin";
    return row.chain === filter && row.kind === "coin";
  }
  var FILTER_IDS = { All: 1, SOL: 1, RH: 1, ADA: 1, NFT: 1 };
  var SORTS = [
    ["hotter", "\u25b2 HOTTER"],
    ["heat", "HEAT"],
    ["fresh", "FRESH"],
    ["degen", "DEGEN"]
  ];
  var SORT_IDS = { hotter: 1, heat: 1, fresh: 1, degen: 1 };
  var FEED_NAMES = {
    SOL: "Solana desk scan",
    WAL: "Wallet races",
    FOR: "Solana forensics",
    NFT: "NFT snapshot",
    ADA: "Solada prefilter",
    HS: "HoodScout",
    LST: "LST count",
    X: "X buzz"
  };
  var FEED_FOR = { SOL: "SOL", RH: "HS", ADA: "ADA", NFT: "NFT" };
  var TIERS = [
    ["scorching", "Scorching", "heat 75+"],
    ["hot", "Hot", "heat 55\u201375"],
    ["warm", "Warm", "heat 40\u201355"]
  ];
  // HOTTER score, higher first. Same knobs as scripts/radar_snapshot.py.
  // score = heat + 8 * freshness + marker - 25 * red_flags
  // freshness = 0 when seen_at is missing, else max(0, (48 - age_hours) / 48)
  // marker = 20 for ▲▲ hotter, 10 for ▲ heating, else 0
  // A missing heat sorts last and does not use the score.
  // Ties break toward the younger seen_at, then the ticker.
  var FRESH_BONUS = 8;
  var FLAG_PENALTY = 25;

  function feedName(feed) {
    return FEED_NAMES[feed && feed.id] || (feed && (feed.name || feed.label)) || "Collector";
  }
  function ageHours(iso, now) {
    var t = Date.parse(iso);
    if (!isFinite(t)) return null;
    return (now - t) / 3600000;
  }
  function nameCmp(a, b) {
    return String(a && a.name || "").localeCompare(String(b && b.name || ""));
  }
  function hasChip(row, id) {
    var chips = (row && row.chips) || [];
    for (var i = 0; i < chips.length; i++) {
      if (chips[i] && chips[i].id === id) return true;
    }
    return false;
  }
  function fading(row, now) {
    if (row && row.buys_drop === true) return true;
    var age = ageHours(row && row.seen_at, now);
    if (age == null) return null;
    if (age > 48) return true;
    if (row && row.buys_drop === false) return false;
    return null;
  }
  function redFlags(row, now) {
    var flags = [];
    if (!row) return flags;
    if (row.thin_book === true) flags.push({ id: "thin_book", label: "thin book" });
    if (row.crowd_flag === true) flags.push({ id: "crowd", label: "crowded" });
    if (row.line_state === "crossed") flags.push({ id: "line", label: "kill line crossed" });
    else if (row.line_state === "near") flags.push({ id: "line", label: "kill line near" });
    if (fading(row, now) === true) flags.push({ id: "fade", label: "fading" });
    return flags;
  }
  function isSmart(row, now) {
    var heat = finite(row && row.heat);
    if (heat == null) return false;
    var mark = row.heat_mark;
    if (!(heat >= 55 || mark === "heating" || mark === "hotter")) return false;
    if (row.thin_book == null || row.crowd_flag == null) return false;
    if (row.line_state !== "clear" && row.line_state !== "near" && row.line_state !== "crossed") return false;
    if (fading(row, now) == null) return false;
    return redFlags(row, now).length === 0;
  }
  function hotterScore(row, now) {
    var heat = finite(row && row.heat);
    if (heat == null) return null;
    var age = ageHours(row.seen_at, now);
    var fresh = age == null ? 0 : Math.max(0, (48 - age) / 48);
    var mark = row.heat_mark === "hotter" ? 20 : row.heat_mark === "heating" ? 10 : 0;
    return heat + FRESH_BONUS * fresh + mark - FLAG_PENALTY * redFlags(row, now).length;
  }
  function place(row, now) {
    if (row.line_state === "crossed" || hasChip(row, "skull")) return "graveyard";
    var age = ageHours(row.seen_at, now);
    if (age != null && age > 48) return "old";
    var heat = finite(row.heat);
    if (heat == null || heat < 40) return "cool";
    if (heat >= 75) return "scorching";
    if (heat >= 55) return "hot";
    return "warm";
  }
  function bySort(sort, now) {
    return function (a, b) {
      if (sort === "fresh") {
        var ah = ageHours(a.seen_at, now);
        var bh = ageHours(b.seen_at, now);
        if (ah == null && bh == null) return nameCmp(a, b);
        if (ah == null) return 1;
        if (bh == null) return -1;
        if (ah !== bh) return ah - bh;
        return nameCmp(a, b);
      }
      if (sort === "degen") {
        var ad = finite(a.degen);
        var bd = finite(b.degen);
        if (ad == null && bd == null) return nameCmp(a, b);
        if (ad == null) return 1;
        if (bd == null) return -1;
        if (ad !== bd) return bd - ad;
        return nameCmp(a, b);
      }
      if (sort === "heat") {
        var aheat = finite(a.heat);
        var bheat = finite(b.heat);
        if (aheat == null && bheat == null) return nameCmp(a, b);
        if (aheat == null) return 1;
        if (bheat == null) return -1;
        if (aheat !== bheat) return bheat - aheat;
        return nameCmp(a, b);
      }
      var ascore = hotterScore(a, now);
      var bscore = hotterScore(b, now);
      if (ascore == null && bscore == null) return nameCmp(a, b);
      if (ascore == null) return 1;
      if (bscore == null) return -1;
      if (ascore !== bscore) return bscore - ascore;
      var aAge = ageHours(a.seen_at, now);
      var bAge = ageHours(b.seen_at, now);
      if (aAge == null && bAge == null) return nameCmp(a, b);
      if (aAge == null) return 1;
      if (bAge == null) return -1;
      if (aAge !== bAge) return aAge - bAge;
      return nameCmp(a, b);
    };
  }
  function pillAge(ms) {
    var mins = Math.max(0, Math.floor(ms / 60000));
    if (mins < 90) return mins + "m";
    var hours = Math.round(mins / 60);
    if (hours < 48) return hours + "h";
    return Math.round(hours / 24) + "d";
  }
  function freshness(doc, now) {
    var built = Date.parse(doc && doc.built_at);
    if (!isFinite(built)) return { kind: "stale", text: "STALE" };
    var delta = now - built;
    if (delta < -10 * 60 * 1000) return { kind: "stale", text: "STALE" };
    var label = pillAge(Math.abs(delta));
    if (delta < 45 * 60 * 1000) return { kind: "live", text: "LIVE \u00b7 " + label };
    return { kind: "stale", text: "STALE \u00b7 " + label };
  }
  function xText(doc, now) {
    var hollow = xStale(doc, now);
    var xAge = doc.x && doc.x.asof ? ageLabel(doc.x.asof, now) : "";
    return (hollow ? "X buzz stale" : "X buzz") + (xAge ? " \u00b7 " + xAge : "");
  }
  function feedLine(feed, now) {
    var name = feedName(feed);
    var age = feed && feed.at ? ageLabel(feed.at, now) : "";
    var word = feed && feed.status === "green" ? "last recorded"
      : feed && feed.status === "red" ? "last recorded run failed"
      : feed && feed.status === "amber" ? "last recorded run degraded"
      : "last recorded status unknown";
    return name + " \u00b7 " + word + (age ? " \u00b7 " + age : "");
  }
  function dexLink(row) {
    var url = row && row.dex_url;
    if (typeof url !== "string") return "";
    if (!/^https:\/\/dexscreener\.com\/solana\/[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(url)) return "";
    return '<a class="dex" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">DexScreener</a>';
  }
  function degenHtml(row) {
    var n = finite(row && row.degen);
    if (n == null) return '<span class="deg"><span class="deg-label">\u2014</span></span>';
    var level = Math.max(1, Math.min(5, Math.round(n)));
    var dots = "";
    for (var i = 1; i <= 5; i++) {
      dots += '<i class="pip' + (i <= level ? " on d" + level : "") + '"></i>';
    }
    var label = row.degen_label || "";
    return '<span class="deg d' + level + '">' + dots + '<span class="deg-label">' + esc(label) + "</span></span>";
  }
  function markerHtml(row) {
    if (row.heat_mark === "hotter") return '<span class="heat-mark">\u25b2\u25b2 hotter</span>';
    if (row.heat_mark === "heating") return '<span class="heat-mark">\u25b2 heating</span>';
    return "";
  }
  function rowHtml(row, rank, open, now) {
    var seen = ageLabel(row.seen_at, now);
    var ageH = ageHours(row.seen_at, now);
    var chips = (row.chips || []).filter(function (chip) {
      if (!chip || chip.id !== "pool") return true;
      return ageH != null && ageH < 1;
    }).map(function (chip) {
      var label = chip.label;
      if (chip.id === "seen" && seen) label = "seen " + seen;
      return '<span class="sig sig-' + esc(chip.id) + '">' + icon(chip.id) + esc(label) + "</span>";
    }).join("");
    var flags = redFlags(row, now).map(function (flag) {
      return '<span class="sig sig-flag">' + esc(flag.label) + "</span>";
    }).join("");
    var smart = isSmart(row, now) ? '<span class="smart">SMART</span>' : "";
    var hintText = row.token_name && row.token_name !== row.name ? row.token_name : (row.hint || "");
    var hint = hintText ? '<span class="hint">' + esc(hintText) + "</span>" : "";
    var heat = finite(row.heat);
    var heatText = heat == null ? "\u2014" : String(heat);
    var letter = esc(String(row.name || "?").trim().charAt(0) || "?");
    var src = markSrc(row);
    var img = src ? '<img alt="" class="logo" src="' + esc(src) + '" referrerpolicy="no-referrer" loading="lazy" decoding="async">' : "";
    var mark = '<span class="mark"><span class="initial">' + letter + "</span>" + img + "</span>";
    var detail = "";
    if (open) {
      var facts = (row.facts || []).map(function (fact) { return "<li>" + esc(fact) + "</li>"; }).join("");
      var whyBits = (row.degen_why || []).join(", ");
      detail = '<div class="detail"><p class="why">' + esc(row.why || row.name) + "</p>"
        + (whyBits ? '<p class="why">degen ' + esc(whyBits) + "</p>" : "")
        + (facts ? '<ul class="facts">' + facts + "</ul>" : "")
        + '<p class="src">' + esc(row.source || "") + "</p></div>";
    }
    var ageBit = seen ? '<span class="age">' + esc(seen) + "</span>" : "";
    return '<article class="entry" data-chain="' + esc(row.chain) + '">'
      + '<button type="button" class="row-btn" data-id="' + esc(row.id) + '" aria-expanded="' + (open ? "true" : "false") + '">'
      + '<span class="rank">' + (rank == null ? "" : rank) + "</span>" + mark
      + '<span class="main"><span class="name-line"><span class="name">' + esc(row.name) + "</span>"
      + hint + '<span class="tag">' + esc(row.chain) + "</span>" + ageBit + markerHtml(row) + smart + "</span>"
      + '<span class="meter-line">' + degenHtml(row) + '<span class="heat-n">heat ' + esc(heatText) + "</span></span>"
      + '<span class="stat-line">' + chips + flags + spark(row.spark) + "</span></span>"
      + '<span class="metrics"><span class="price">' + priceHtml(finite(row.price), row.price_unit) + "</span>"
      + pill(row.change) + '<span class="size">' + esc(sizeText(row)) + "</span></span></button>"
      + dexLink(row)
      + detail + "</article>";
  }
  function summaryHtml(doc, now) {
    if (!doc || !Array.isArray(doc.rows)) {
      return '<p class="scan" id="scan">Snapshot is unavailable.</p>';
    }
    var fresh = freshness(doc, now);
    return '<p class="fresh fresh-' + fresh.kind + '" id="scan">' + esc(fresh.text) + "</p>";
  }
  function scanLine(doc, now) {
    if (!doc || !Array.isArray(doc.rows)) return "Snapshot is unavailable.";
    return freshness(doc, now).text;
  }
  function blankState() {
    return { filter: "All", sort: "hotter", open: null };
  }
  function reduce(state, action, doc, now) {
    var next = blankState();
    state = state || {};
    next.filter = FILTER_IDS[state.filter] ? state.filter : "All";
    next.sort = SORT_IDS[state.sort] ? state.sort : "hotter";
    next.open = state.open || null;
    action = action || {};
    if (action.type === "filter") {
      if (FILTER_IDS[action.id]) next.filter = action.id;
    } else if (action.type === "sort") {
      if (SORT_IDS[action.id]) next.sort = action.id;
    } else if (action.type === "toggle-row") {
      next.open = next.open === action.id ? null : action.id;
    }
    return next;
  }
  function bucket(rows, now) {
    var out = { scorching: [], hot: [], warm: [], cool: [], graveyard: [], old: [] };
    rows.forEach(function (row) {
      var key = place(row, now);
      (out[key] || out.cool).push(row);
    });
    return out;
  }
  function renderHtml(doc, state, now) {
    state = state || {};
    now = now == null ? Date.now() : now;
    if (!doc || !Array.isArray(doc.rows)) {
      return '<p class="empty">Radar snapshot is not published yet.</p>';
    }
    var filter = FILTER_IDS[state.filter] ? state.filter : "All";
    var sort = SORT_IDS[state.sort] ? state.sort : "hotter";
    var filtered = (doc.rows || []).filter(function (row) { return accept(row, filter); });
    var ordered = filtered.slice().sort(bySort(sort, now));
    var groups = bucket(ordered, now);
    var chips = [["All", "ALL"], ["SOL", "SOL"], ["RH", "RH"], ["ADA", "ADA"], ["NFT", "NFT"]].map(function (item) {
      var on = item[0] === filter;
      var count = (doc.rows || []).filter(function (row) { return accept(row, item[0]); }).length;
      var feedId = FEED_FOR[item[0]];
      var feed = feedId && (doc.feeds || []).filter(function (entry) { return entry.id === feedId; })[0];
      var dot = feed && feed.status === "red" ? '<i class="dot"></i>' : "";
      return '<button type="button" class="chip' + (on ? " on" : "") + '" data-filter="' + esc(item[0]) + '" aria-pressed="' + (on ? "true" : "false") + '">'
        + dot + esc(item[1]) + " " + count + "</button>";
    }).join("");
    var sorts = SORTS.map(function (item) {
      var on = item[0] === sort;
      return '<button type="button" class="sort' + (on ? " on" : "") + '" data-sort="' + esc(item[0]) + '" aria-pressed="' + (on ? "true" : "false") + '">' + esc(item[1]) + "</button>";
    }).join("");
    var ladder = [];
    TIERS.forEach(function (tier) { ladder = ladder.concat(groups[tier[0]]); });
    var scorching = groups.scorching.length;
    var heating = ladder.filter(function (row) { return row.heat_mark === "heating" || row.heat_mark === "hotter"; }).length;
    var casino = ladder.filter(function (row) { return finite(row.degen) === 5; }).length;
    var smartN = ladder.filter(function (row) { return isSmart(row, now); }).length;
    var tiles = '<div class="tiles">'
      + '<p class="tile"><b>' + scorching + '</b><span>scorching</span></p>'
      + '<p class="tile"><b>' + heating + '</b><span>heating</span></p>'
      + '<p class="tile"><b>' + casino + '</b><span>casino-tier</span></p>'
      + '<p class="tile"><b>' + smartN + '</b><span>smart picks</span></p>'
      + "</div>";
    var key = '<p class="degen-key"><span class="d1">1 MID</span><span class="d2">2 SPICY</span><span class="d3">3 DEGEN</span><span class="d4">4 FULL SEND</span><span class="d5">5 CASINO</span></p>';
    var rank = 0;
    var sections = TIERS.map(function (tier) {
      var rows = groups[tier[0]];
      if (!rows.length) return "";
      var body = rows.map(function (row) {
        rank += 1;
        return rowHtml(row, rank, state.open === row.id, now);
      }).join("");
      return '<section class="tier tier-' + tier[0] + '"><h2>' + icon("heat") + esc(tier[1]) + ' <span>' + esc(tier[2]) + '</span> <b>' + rows.length + "</b></h2>" + body + "</section>";
    }).join("");
    if (!sections && !groups.cool.length && !groups.graveyard.length && !groups.old.length) {
      sections = '<p class="empty">Nothing in this cut.</p>';
    }
    function fold(id, title, detail, rows) {
      if (!rows.length) return "";
      var body = rows.map(function (row) { return rowHtml(row, null, state.open === row.id, now); }).join("");
      return '<details class="fold"><summary>' + esc(title) + ' <span class="fold-d">' + esc(detail) + '</span> <b>' + rows.length + "</b></summary>" + body + "</details>";
    }
    var folds = fold("graveyard", "Graveyard", "kill line crossed", groups.graveyard)
      + fold("old", "Old guard", "seen over 48h", groups.old)
      + fold("cool", "Cool", "heat under 40", groups.cool);
    var nftCount = (doc.rows || []).filter(function (row) { return row.kind === "nft"; }).length;
    var nftLink = "";
    if (filter !== "NFT" && nftCount) {
      nftLink = '<button type="button" class="fold-link" data-filter="NFT">NFTs <span class="fold-d">own ladder</span> <b>' + nftCount + "</b></button>";
    }
    return '<div class="filters" role="group" aria-label="Filter" data-active="' + esc(filter) + '">' + chips + "</div>"
      + '<div class="sorts" role="group" aria-label="Sort" data-active="' + esc(sort) + '">' + sorts + "</div>"
      + tiles + key
      + '<p class="caption">Heat is a ranking, not a buy signal.</p>'
      + '<div class="list">' + sections + "</div>"
      + folds + nftLink;
  }
  function legendHtml(doc, now) {
    now = now == null ? Date.now() : now;
    function items(list) {
      return list.map(function (item) {
        return '<li class="leg-' + esc(item[0]) + '">' + icon(item[0]) + "<p><strong>" + esc(item[1]) + "</strong> " + esc(item[2]) + "</p></li>";
      }).join("");
    }
    var feeds = "";
    if (doc && Array.isArray(doc.feeds)) {
      feeds = (doc.feeds || []).map(function (feed) {
        var text = feed && feed.id === "X" ? xText(doc, now) : feedLine(feed, now);
        return "<li>" + esc(text) + "</li>";
      }).join("");
    }
    var unnamed = "";
    if (doc && doc.unnamed_hidden) {
      unnamed = '<p class="hidden-note" id="unnamed-note">' + esc(doc.unnamed_hidden) + " unnamed rows left off the ladder.</p>";
    }
    var hidden = "";
    if (doc && doc.hidden_note) {
      hidden = '<p class="hidden-note" id="hidden-note">' + esc(doc.hidden_note) + "</p>";
    }
    var rules = '<p class="not-live">Smart</p><ul class="legend"><li><p><strong>SMART</strong> Hot or heating, and no red flag. A missing input gives no badge.</p></li>'
      + "<li><p><strong>Thin book</strong> Liquidity under 10% of volume, when both numbers are on the row.</p></li>"
      + "<li><p><strong>Crowded</strong> Top-heavy, snipers, or coordinated.</p></li>"
      + "<li><p><strong>Kill line</strong> Crossed, or within 25% of the line. A Solana snap uses an 80% price drop, and 60% is near.</p></li>"
      + "<li><p><strong>Fading</strong> Buys falling, or seen over 48h. Prior heat is not stored.</p></li></ul>";
    return '<ul class="legend">' + items(LEGEND) + '</ul><p class="not-live">Not on live rows</p><ul class="legend">' + items(NOT_LIVE) + "</ul>"
      + rules
      + '<p class="not-live">Feed health</p>'
      + (feeds ? '<ul class="healthy">' + feeds + "</ul>" : "<p>Feed health appears when a snapshot is loaded.</p>")
      + unnamed + hidden
      + '<p class="hidden-note">' + esc(BASE_EMPTY) + "</p>";
  }

  var state = blankState();
  var doc = null;

  function focusedKey() {
    try {
      var el = document.activeElement;
      if (!el || !el.getAttribute) return null;
      if (el.hasAttribute("data-filter")) return "filter";
      if (el.hasAttribute("data-sort")) return "sort:" + el.getAttribute("data-sort");
      if (el.hasAttribute("data-id")) return "row:" + el.getAttribute("data-id");
      if (el.id) return "id:" + el.id;
    } catch (err) {
      return null;
    }
    return null;
  }
  function restoreFocus(key) {
    if (!key || !document.querySelector) return;
    var el = null;
    if (key === "filter") el = document.querySelector('[data-filter="' + state.filter + '"]');
    else if (key.indexOf("sort:") === 0) el = document.querySelector('[data-sort="' + key.slice(5) + '"]');
    else if (key.indexOf("row:") === 0) el = document.querySelector('[data-id="' + key.slice(4) + '"]');
    else if (key.indexOf("id:") === 0) el = document.getElementById(key.slice(3));
    if (!el && key.indexOf("sort:") === 0) el = document.querySelector('[data-sort="' + state.sort + '"]');
    if (el && el.focus) {
      try { el.focus({ preventScroll: true }); } catch (err) { el.focus(); }
    }
  }
  function paint() {
    var app = document.getElementById("app");
    var summary = document.getElementById("summary");
    var scan = document.getElementById("scan");
    var legendBody = document.getElementById("legend-body");
    var key = focusedKey();
    var y = 0;
    try { y = root.scrollY || 0; } catch (err) { y = 0; }
    if (!app && !summary) return;
    var now = Date.now();
    if (summary) summary.innerHTML = summaryHtml(doc, now);
    else if (!doc && scan) scan.textContent = "Snapshot is unavailable.";
    else if (scan) scan.textContent = scanLine(doc, now);
    if (legendBody) legendBody.innerHTML = legendHtml(doc, now);
    if (app) app.innerHTML = renderHtml(doc, state, now);
    restoreFocus(key);
    try {
      if (root.scrollTo) root.scrollTo(0, y);
    } catch (err) { /* the smoke document has no scroller */ }
  }
  function glyphsOk() {
    try {
      if (!root.document || !root.document.createElement) return true;
      var canvas = root.document.createElement("canvas");
      if (!canvas || !canvas.getContext) return true;
      var ctx = canvas.getContext("2d");
      if (!ctx || !ctx.fillText || !ctx.getImageData) return true;
      canvas.width = 32;
      canvas.height = 32;
      ctx.font = "20px sans-serif";
      ctx.fillStyle = "#000";
      ctx.fillText("\u2084", 0, 24);
      var data = ctx.getImageData(0, 0, 32, 32).data;
      for (var i = 3; i < data.length; i += 4) {
        if (data[i] !== 0) return true;
      }
      return false;
    } catch (err) {
      return false;
    }
  }
  function boot() {
    subscriptOk = glyphsOk();
    var openLegend = document.getElementById("legend-open");
    var legend = document.getElementById("legend");
    var legendBody = document.getElementById("legend-body");
    if (legendBody) legendBody.innerHTML = legendHtml(null, Date.now());
    if (openLegend && legend) {
      openLegend.addEventListener("click", function () {
        if (typeof legend.showModal === "function") legend.showModal();
      });
    }
    document.addEventListener("keydown", function (event) {
      if (event.key !== " " && event.code !== "Space") return;
      var btn = event.target && event.target.closest && event.target.closest("button");
      if (!btn || btn.disabled) return;
      var box = document.getElementById("app");
      var summaryBox = document.getElementById("summary");
      if ((!box || !box.contains(btn)) && (!summaryBox || !summaryBox.contains(btn))) return;
      event.preventDefault();
      btn.click();
    });
    var app = document.getElementById("app");
    if (!app) return;
    app.addEventListener("click", function (event) {
      var filter = event.target.closest("[data-filter]");
      if (filter) {
        state = reduce(state, { type: "filter", id: filter.getAttribute("data-filter") }, doc, Date.now());
        paint();
        return;
      }
      var sortBtn = event.target.closest("[data-sort]");
      if (sortBtn) {
        state = reduce(state, { type: "sort", id: sortBtn.getAttribute("data-sort") }, doc, Date.now());
        paint();
        return;
      }
      var hit = event.target.closest("[data-id]");
      if (!hit) return;
      state = reduce(state, { type: "toggle-row", id: hit.getAttribute("data-id") }, doc, Date.now());
      paint();
    });
    document.addEventListener("load", function (event) {
      var img = event.target;
      if (!img || img.tagName !== "IMG" || !img.classList || !img.classList.contains("logo")) return;
      if (img.naturalWidth > 0) img.classList.add("is-ready");
      else img.remove();
    }, true);
    document.addEventListener("error", function (event) {
      var img = event.target;
      if (!img || img.tagName !== "IMG" || !img.classList || !img.classList.contains("logo")) return;
      img.remove();
    }, true);
    var params = new URLSearchParams(location.search);
    fetch("radar.json", { cache: "no-store" }).then(function (res) {
      if (!res.ok) throw new Error("missing");
      return res.json();
    }).then(function (payload) {
      doc = payload;
      var hint = params.get("open");
      if (hint === "1" && doc.rows && doc.rows[0]) state.open = doc.rows[0].id;
      else if (hint) state.open = hint;
      paint();
    }).catch(function () {
      doc = null;
      paint();
    });
  }

  root.SightlineRadar = {
    renderHtml: renderHtml,
    summaryHtml: summaryHtml,
    scanLine: scanLine,
    legendHtml: legendHtml,
    paint: paint,
    reduce: reduce,
    hotterScore: hotterScore,
    redFlags: redFlags,
    isSmart: isSmart,
    place: place,
    BASE_EMPTY: BASE_EMPTY,
    LEGEND: LEGEND,
    NOT_LIVE: NOT_LIVE
  };
  if (root.document && root.document.getElementById && root.document.getElementById("app")) boot();
})(typeof globalThis !== "undefined" ? globalThis : this);
