/* Sightline Radar preview. Reads radar.json. Research only. */
(function (root) {
  var FILTERS = ["All", "Coins", "NFTs", "SOL", "BASE", "RH", "ADA", "ETH"];
  var SORTS = [["heat", "heat"], ["move", "move"], ["vol", "vol"], ["buyers", "buyers"]];
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
    ["pool", "New pool", "Under an hour old, or a HoodScout NFT marked new."],
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
    if (row && row.chain === "SOL") return CHAIN_LOGO.SOL;
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
  function clock(iso) {
    var t = Date.parse(iso);
    if (!isFinite(t)) return "no time";
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Toronto",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hourCycle: "h12"
      }).format(new Date(t)).replace(/\u202f/g, " ").replace(/\u00a0/g, " ") + " ET";
    } catch (err) {
      return new Date(t).toISOString().replace(".000Z", "Z").replace("T", " ");
    }
  }
  function xStale(doc, now) {
    var asof = doc.x && Date.parse(doc.x.asof);
    if (!isFinite(asof)) return true;
    return (now - asof) > 48 * 60 * 60 * 1000;
  }
  function snapStale(doc, now) {
    var built = Date.parse(doc && doc.built_at);
    if (!isFinite(built)) return "missing";
    if (now - built > 2 * 60 * 60 * 1000) return "old";
    if (built - now > 10 * 60 * 1000) return "future";
    return "";
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
    if (!filter || filter === "All") return true;
    if (filter === "Coins") return row.kind === "coin";
    if (filter === "NFTs") return row.kind === "nft";
    return row.chain === filter;
  }
  function ordered(rows, sort) {
    var copy = rows.slice();
    var key = sort === "move" ? "change" : sort === "vol" ? "size" : sort === "buyers" ? "buyers" : "heat";
    copy.sort(function (a, b) {
      var av = finite(a[key]);
      var bv = finite(b[key]);
      if (av == null && bv == null) return String(a.name).localeCompare(String(b.name));
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av;
    });
    return copy;
  }
  function scanLine(doc, now) {
    var hollow = xStale(doc, now);
    var xClass = hollow ? "xmark" : "xmark filled";
    var xAge = doc.x && doc.x.asof ? ageLabel(doc.x.asof, now) : "";
    var xText = hollow ? "X buzz stale" + (xAge ? " · " + xAge : "") : "X buzz" + (xAge ? " · " + xAge : "");
    var scanAge = ageLabel(doc.last_scan, now) || "no time";
    return '<span class="buddy">[•_•]</span> Built ' + esc(clock(doc.built_at))
      + " · last scan " + esc(scanAge)
      + " · " + esc(doc.lead_count == null ? 0 : doc.lead_count) + " leads<br>"
      + '<span class="' + xClass + '">' + icon("buzz") + "</span> " + esc(xText);
  }
  function rowHtml(row, rank, open, now) {
    var seen = ageLabel(row.seen_at, now);
    var chips = (row.chips || []).map(function (chip) {
      var label = chip.label;
      if (chip.id === "seen" && seen) label = "seen " + seen;
      return '<span class="sig sig-' + esc(chip.id) + '">' + icon(chip.id) + esc(label) + "</span>";
    }).join("");
    var hint = row.hint ? '<span class="hint">' + esc(row.hint) + "</span>" : (row.show_age && seen ? '<span class="hint">' + esc(seen) + "</span>" : "");
    var heat = finite(row.heat);
    var heatChip = heat == null ? "" : '<span class="sig sig-heat">' + icon("heat") + "heat " + esc(heat) + "</span>";
    var letter = esc(String(row.name || "?").trim().charAt(0) || "?");
    var src = markSrc(row);
    var img = src ? '<img alt="" src="' + esc(src) + '" referrerpolicy="no-referrer" loading="lazy" onerror="this.remove()">' : "";
    var mark = '<span class="mark">' + letter + img + "</span>";
    var bar = finite(row.buy_pct);
    var barHtml = bar == null ? "" : '<span class="bar" aria-hidden="true"><i style="width:' + Math.round(bar * 100) + '%"></i></span>';
    var detail = "";
    if (open) {
      var facts = (row.facts || []).map(function (fact) { return "<li>" + esc(fact) + "</li>"; }).join("");
      detail = '<div class="detail"><p class="why">' + esc(row.why || row.name) + "</p>"
        + (facts ? '<ul class="facts">' + facts + "</ul>" : "")
        + '<p class="src">' + esc(row.source || "") + "</p></div>";
    }
    return '<article class="entry">'
      + '<button type="button" class="row-btn" data-id="' + esc(row.id) + '" aria-expanded="' + (open ? "true" : "false") + '">'
      + '<span class="rank">' + rank + "</span>" + mark
      + '<span class="main"><span class="name-line"><span class="name">' + esc(row.name) + "</span>"
      + hint + '<span class="tag">' + esc(row.chain) + "</span>" + chips + heatChip + "</span>"
      + '<span class="stat-line"><span class="stat">' + esc(row.stat || "") + "</span>" + barHtml + spark(row.spark) + "</span></span>"
      + '<span class="metrics"><span class="price">' + priceHtml(finite(row.price), row.price_unit) + "</span>"
      + pill(row.change) + '<span class="size">' + esc(sizeText(row)) + "</span></span></button>"
      + detail + "</article>";
  }
  function renderHtml(doc, state, now) {
    state = state || {};
    now = now == null ? Date.now() : now;
    if (!doc || !Array.isArray(doc.rows)) {
      return '<p class="empty">Radar snapshot is not published yet.</p>';
    }
    var filter = state.filter || "All";
    var feeds = (doc.feeds || []).map(function (feed) {
      var status = feed.id === "X" ? (xStale(doc, now) ? "hollow" : "green") : (feed.status || "unknown");
      return '<button type="button" class="feed is-' + esc(status) + '" data-feed="' + esc(feed.id) + '">'
        + "<i></i><span>" + esc(feed.label) + "</span></button>";
    }).join("");
    var staleKind = snapStale(doc, now);
    var banner = "";
    if (staleKind === "old") {
      banner = '<p class="banner">This snapshot is more than 2 hours old. Built ' + esc(clock(doc.built_at)) + ".</p>";
    } else if (staleKind) {
      banner = '<p class="banner">This snapshot is stale.</p>';
    }
    var chips = FILTERS.map(function (name) {
      var on = name === filter ? " on" : "";
      return '<button type="button" class="chip' + on + '" data-filter="' + esc(name) + '" aria-pressed="' + (on ? "true" : "false") + '">' + esc(name) + "</button>";
    }).join("");
    var sort = state.sort || "heat";
    var sorts = SORTS.map(function (pair) {
      var on = pair[0] === sort ? " on" : "";
      return '<button type="button" class="sort' + on + '" data-sort="' + pair[0] + '" aria-pressed="' + (on ? "true" : "false") + '">' + esc(pair[1]) + "</button>";
    }).join("");
    var rows = ordered((doc.rows || []).filter(function (row) { return accept(row, filter); }), sort);
    var list;
    if (filter === "BASE" && !rows.length) {
      list = '<p class="empty">' + esc(BASE_EMPTY) + "</p>";
    } else if (!rows.length) {
      list = '<p class="empty">Nothing in this cut.</p>';
    } else {
      list = rows.map(function (row, index) {
        return rowHtml(row, index + 1, state.open === row.id, now);
      }).join("");
    }
    var note = state.feedNote ? '<p class="note" id="feed-note">' + esc(state.feedNote) + "</p>" : '<p class="note" id="feed-note"></p>';
    var gaps = (doc.gaps || []).map(function (gap) { return "<li>" + esc(gap) + "</li>"; }).join("");
    return banner + '<div class="feeds" role="group" aria-label="Feed health">' + feeds + "</div>" + note
      + '<div class="filters" role="group" aria-label="Filter">' + chips + "</div>"
      + '<div class="sorts" role="group" aria-label="Sort">' + sorts + "</div>"
      + '<p class="caption">Heat is a ranking, not a buy signal.</p>'
      + (doc.hidden_note ? '<p class="hidden-note" id="hidden-note">' + esc(doc.hidden_note) + "</p>" : "")
      + '<div class="list">' + list + "</div>"
      + (gaps ? '<details class="gaps"><summary>Data notes</summary><ul>' + gaps + "</ul></details>" : "");
  }
  function legendHtml() {
    function items(list) {
      return list.map(function (item) {
        return '<li class="leg-' + esc(item[0]) + '">' + icon(item[0]) + "<p><strong>" + esc(item[1]) + "</strong> " + esc(item[2]) + "</p></li>";
      }).join("");
    }
    return '<ul class="legend">' + items(LEGEND) + '</ul><p class="not-live">Not on live rows</p><ul class="legend">' + items(NOT_LIVE) + "</ul>";
  }

  var state = { filter: "All", sort: "heat", open: null, feedNote: "" };
  var doc = null;

  function paint() {
    var app = document.getElementById("app");
    var scan = document.getElementById("scan");
    if (!app) return;
    if (!doc) {
      if (scan) scan.textContent = "Snapshot is unavailable.";
      app.innerHTML = renderHtml(null, state);
      return;
    }
    if (scan) scan.innerHTML = scanLine(doc, Date.now());
    app.innerHTML = renderHtml(doc, state, Date.now());
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
    if (legendBody) legendBody.innerHTML = legendHtml();
    if (openLegend && legend) {
      openLegend.addEventListener("click", function () {
        if (typeof legend.showModal === "function") legend.showModal();
      });
    }
    var app = document.getElementById("app");
    if (!app) return;
    app.addEventListener("click", function (event) {
      var filter = event.target.closest("[data-filter]");
      if (filter) {
        state.filter = filter.getAttribute("data-filter");
        paint();
        return;
      }
      var sortBtn = event.target.closest("[data-sort]");
      if (sortBtn) {
        state.sort = sortBtn.getAttribute("data-sort");
        paint();
        return;
      }
      var feed = event.target.closest("[data-feed]");
      if (feed && doc) {
        var id = feed.getAttribute("data-feed");
        var row = (doc.feeds || []).filter(function (item) { return item.id === id; })[0];
        var age = row && row.at ? ageLabel(row.at, Date.now()) : "";
        state.feedNote = row ? row.label + " · " + (row.detail || row.status) + (age ? " · " + age : "") : "";
        paint();
        return;
      }
      var hit = event.target.closest("[data-id]");
      if (!hit) return;
      var id = hit.getAttribute("data-id");
      state.open = state.open === id ? null : id;
      paint();
    });
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
    scanLine: scanLine,
    legendHtml: legendHtml,
    paint: paint,
    BASE_EMPTY: BASE_EMPTY,
    LEGEND: LEGEND,
    NOT_LIVE: NOT_LIVE
  };
  if (root.document && root.document.getElementById && root.document.getElementById("app")) boot();
})(typeof globalThis !== "undefined" ? globalThis : this);
