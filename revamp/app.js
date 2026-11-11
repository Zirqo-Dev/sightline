/* SPD-82 public pages. Paper display only. v6.5 numbers stay on the previous site. */
(function () {
  var R = window.SightlineRaces;
  var DESK_DATA = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/";
  var state = { phase: "now", evo: "S2", dur: "all" };
  var racePromise = null;
  var stampPromise = null;
  var healthPromise = null;

  var S2_STATUS = "unworkable before start (owner, Nov 11); v3 redesign after Nov 25";
  var F_STATUS = "F stopped early on Nov 11, 2026 under its pre-set rule (Day-14 check). Sealed review Nov 25, 2026.";
  var NOW_BOOKS = [
    { tag: "S2", name: "Survivors", desc: "Meme tokens that survive their early stretch. S2 v2 is not a running test. v3 is a redesign after Nov 25, dates TBD.", twin: "random twin, same universe", tradesDay: "—", status: S2_STATUS, statusTone: "warn" }
  ];
  var KILLED = [
    { tag: "A", name: "Hour-drop bounce", desc: "Liquid non-meme Solana tokens after a sharp 1h drop vs SOL, exit after 24h. No retunes this cycle.", twin: "random twin, same universe", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad" },
    { tag: "B", name: "Funding extreme", desc: "Buy spot when Binance perp funding is in the bottom 5% of its 90 days, hold 72h. No retunes this cycle.", twin: "random twin, same universe", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad" },
    { tag: "C", name: "Calm-or-wild SOL", desc: "Vol-targeted SOL at 60%, 1x cap. No retunes this cycle.", twin: "buy-and-hold SOL", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad" },
    { tag: "D", name: "Best-hours SOL", desc: "Trades SOL only in historically stronger hour-of-day windows. No retunes this cycle.", twin: "random twin, same hours", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad" }
  ];
  var BLOCKED = { tag: "E", name: "Staked-SOL discount", desc: "jitoSOL discount vs SOL. Blocked because CoinGecko price glitches produced the locked-rule pass; clean data since Aug 1 has 0 episodes.", twin: "random twin on the discount", tradesDay: "—", status: "blocked: bad price data", tone: "warn" };
  var CYCLE2 = { tag: "C2", name: "Top winners", desc: "Weekly momentum into recent winners. Killed pre-test at 11:49 AM EST Nov 11: at most 9 eligible tokens before 2025 vs the 15 needed.", twin: "—", tradesDay: "—", status: "killed pre-test (too few tokens)", tone: "bad" };
  var WALLETS = [
    { tag: "S2", name: "Survivors", group: "Cycle 1 · tested", purpose: "Meme tokens that survive their early stretch. No tested book is running.", twin: "random twin, same universe", tradesDay: "—", status: S2_STATUS, tone: "warn", note: S2_STATUS },
    { tag: "A", name: "Hour-drop bounce", group: "Killed in backtest (Nov 11)", purpose: "Liquid non-meme Solana tokens after a sharp 1h drop vs SOL, exit after 24h.", twin: "random twin, same universe", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad", note: "No retunes this cycle." },
    { tag: "B", name: "Funding extreme", group: "Killed in backtest (Nov 11)", purpose: "Buy spot when Binance perp funding is in the bottom 5% of its 90 days, hold 72h.", twin: "random twin, same universe", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad", note: "No retunes this cycle." },
    { tag: "C", name: "Calm-or-wild SOL", group: "Killed in backtest (Nov 11)", purpose: "Vol-targeted SOL at 60%, capped at 1x.", twin: "buy-and-hold SOL", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad", note: "No retunes this cycle." },
    { tag: "D", name: "Best-hours SOL", group: "Killed in backtest (Nov 11)", purpose: "Trades SOL only in historically stronger hour-of-day windows.", twin: "random twin, same hours", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad", note: "No retunes this cycle." },
    { tag: "E", name: "Staked-SOL discount", group: "Blocked", purpose: "Buy discounted jitoSOL vs SOL.", twin: "random twin on the discount", tradesDay: "—", status: "blocked: bad price data", tone: "warn", note: "CoinGecko price glitches produced the locked-rule pass; clean data since Aug 1 has 0 episodes." },
    { tag: "DW", name: "Busy S2 (exit variant)", group: "Display only", purpose: "One S2 exit rule. It waits for S2 v3.", twin: "none · not tested", tradesDay: "TBD", status: "waits for S2 v3", tone: "warn", note: "Display only, untested. It waits for S2 v3. Never ranked with the tested books." },
    { tag: "BH", name: "SOL buy-and-hold", group: "Display only", purpose: "Display-only benchmark, not a tested book.", twin: "—", tradesDay: "0 (hold)", status: "benchmark", tone: "accent" },
    { tag: "C2", name: "Top winners", group: "Cycle 2 · killed pre-test", purpose: "Weekly momentum into recent winners.", twin: "—", tradesDay: "—", status: "killed pre-test (too few tokens)", tone: "bad", note: "Killed pre-test at 11:49 AM EST Nov 11: at most 9 eligible tokens before 2025 vs the 15 needed." }
  ];
  var GROUPS = ["Cycle 1 · tested", "Killed in backtest (Nov 11)", "Blocked", "Display only", "Cycle 2 · killed pre-test"];
  var DAYS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11", "D12", "D13", "D14"];
  var DUR = [
    { tag: "S2 v3", name: "Survivors redesign", mode: "redesign", status: S2_STATUS, tone: "warn", note: S2_STATUS + ". Start, shakedown, check-ins, verdict and hard cap will appear once dates are locked. Dates are not locked, so there is no day count.", dayLabel: "day — of —", pctLabel: "—%" },
    { tag: "DW", name: "Busy S2 (exit variant)", mode: "waiting", status: "waits for S2 v3", tone: "warn", note: "Display only, untested. No verdict and no hard cap. It waits for S2 v3, so dates stay TBD until that redesign is locked.", dayLabel: "day — of —", pctLabel: "—%" },
    { tag: "BH", name: "SOL buy-and-hold", mode: "display", status: "display only", tone: "accent", note: "Benchmark, not a tested book. No verdict date and no hard cap. Shown for scale only.", dayLabel: "no verdict", pctLabel: "display only" },
    { tag: "v6.5", name: "Frozen F / R / S", mode: "sealed", status: "sealed review Nov 25, 2026", tone: "accent", note: F_STATUS + " Nothing here reads the sealed books.", dayLabel: "review Nov 25", pctLabel: "sealed" },
    { tag: "L", name: "Listing bounce", mode: "counting", status: "counting history now", tone: "accent", note: "Round 3 idea. Free event count is running. Dates TBD until the count finishes and a prereg is locked.", dayLabel: "day — of —", pctLabel: "—%" },
    { tag: "M", name: "Meme basket", mode: "counting", status: "counting history now", tone: "accent", note: "Round 3 idea. Free history count is running. Dates TBD until the count finishes and a prereg is locked.", dayLabel: "day — of —", pctLabel: "—%" },
    { tag: "U", name: "Unlock → SOL overlay", mode: "counting", status: "counting history now", tone: "accent", note: "Switch-to-SOL safety overlay, not a long-only book. Free history count is running. Dates TBD.", dayLabel: "day — of —", pctLabel: "—%" },
    { tag: "RWA", name: "Real-world assets", mode: "rejected", status: "rejected as a test book", tone: "bad", note: "Not a tested book. Shown so the board is honest about what will not get a duration bar.", dayLabel: "no test", pctLabel: "—" },
    { tag: "NR", name: "Narrative rotation", mode: "rejected", status: "rejected as a test book", tone: "bad", note: "Not a tested book. Shown so the board is honest about what will not get a duration bar.", dayLabel: "no test", pctLabel: "—" }
  ];
  var NAV = [
    { id: "desks", href: "#/", label: "Desks" },
    { id: "winning", href: "#/winning", label: "Winning" },
    { id: "wallets", href: "#/wallets", label: "Wallets" },
    { id: "duration", href: "#/duration", label: "Duration" },
    { id: "evolution", href: "#/evolution", label: "Evolve" },
    { id: "trades", href: "#/trades", label: "Trades" },
    { id: "archive", href: "#/archive", label: "Archive" },
    { id: "fresh", href: "#/freshness", label: "Fresh" }
  ];
  var TITLES = {
    desks: "Desks",
    winning: "Who's winning",
    wallets: "Wallets",
    duration: "Duration",
    evolution: "Evolution",
    trades: "Ongoing trades",
    archive: "Archive",
    fresh: "Freshness",
    king2: "KING desk (2)"
  };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function tag(text, tone) {
    return '<span class="tag ' + esc(tone || "") + '">' + esc(text) + "</span>";
  }
  function line(left, right, extra) {
    var cls = "line" + (extra || "");
    var r = right == null ? "" : "<span>" + right + "</span>";
    return '<div class="' + cls + '"><span>' + left + "</span>" + r + "</div>";
  }
  function signed(n, d) {
    if (n == null || !isFinite(Number(n))) return "—";
    var v = Number(n);
    return (v > 0 ? "+" : "") + v.toFixed(d);
  }
  function level(n, unit) {
    if (n == null || !isFinite(Number(n))) return "—";
    return Number(n).toFixed(2) + " " + (unit || "SOL");
  }
  function px(n) {
    if (n == null || !isFinite(Number(n))) return "—";
    var v = Number(n);
    return Math.abs(v) >= 1 ? v.toFixed(4) : v.toPrecision(3);
  }
  function cls(n) {
    if (n == null || !isFinite(Number(n)) || Number(n) === 0) return "";
    return Number(n) > 0 ? "up" : "dn";
  }
  function fmtEt(iso) {
    var t = Date.parse(iso);
    if (!isFinite(t)) return "—";
    return new Date(t).toLocaleString("en-US", {
      timeZone: "America/Toronto", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
    }) + " ET";
  }
  function route() {
    var h = String(location.hash || "#/").replace(/^#/, "") || "/";
    var parts = h.split("/").filter(Boolean);
    var name = parts[0] || "desks";
    var map = { desks: "desks", winning: "winning", wallets: "wallets", duration: "duration", evolution: "evolution", trades: "trades", archive: "archive", freshness: "fresh", fresh: "fresh", king2: "king2" };
    return { id: map[name] || "desks", arg: parts[1] ? decodeURIComponent(parts[1]) : "" };
  }
  function fixturesOn() {
    return new URLSearchParams(location.search).get("fixtures") === "1";
  }

  function intervalSvg(lo, mid, hi) {
    var W = 320, H = 34, pad = 14, min = -10, max = 10;
    var x = function (v) { return pad + ((v - min) / (max - min)) * (W - 2 * pad); };
    var has = lo != null && hi != null && mid != null;
    var mark = has
      ? '<line x1="' + x(lo) + '" x2="' + x(hi) + '" y1="14" y2="14" stroke="#36d6ff" stroke-width="5" stroke-linecap="round" />' +
        '<circle cx="' + x(mid) + '" cy="14" r="5" fill="#36d6ff" stroke="#161A1E" stroke-width="2" />'
      : '<line x1="' + x(min / 2) + '" x2="' + x(max / 2) + '" y1="14" y2="14" stroke="#2B3139" stroke-width="5" stroke-dasharray="3 5" stroke-linecap="round" />';
    return '<svg width="100%" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Excess versus twin">' +
      '<line x1="' + pad + '" x2="' + (W - pad) + '" y1="14" y2="14" stroke="#2B3139" stroke-width="1" />' +
      '<line x1="' + x(0) + '" x2="' + x(0) + '" y1="4" y2="24" stroke="#848E9C" stroke-width="1" />' +
      mark +
      '<text x="' + pad + '" y="32" fill="#848E9C" font-size="10">' + min + "%</text>" +
      '<text x="' + x(0) + '" y="32" text-anchor="middle" fill="#848E9C" font-size="10">0</text>' +
      '<text x="' + (W - pad) + '" y="32" text-anchor="end" fill="#848E9C" font-size="10">+' + max + "% / 30d</text>" +
      "</svg>";
  }
  function progress(n, req) {
    var pct = Math.min(100, (n / req) * 100);
    return '<div class="bar"><i style="width:' + pct.toFixed(1) + '%"></i></div>' +
      line(esc(n + " of " + req + " closes"), esc(Math.round(pct) + "%"), " small muted");
  }
  function bookCard(b) {
    var excess = b.mid != null ? signed(b.mid, 1) + "% / 30d" : "no results yet";
    var band = b.lo != null && b.hi != null
      ? line("95% interval " + signed(b.lo, 1) + " to " + signed(b.hi, 1) + "% / 30d", null, " small muted")
      : "";
    var sample = b.n != null && b.req != null ? progress(b.n, b.req) : line("n / required n", "not started", " small muted");
    var flags = (b.insufficient ? tag("Insufficient sample", "warn") : "") + (b.status ? tag(b.status, b.statusTone) : "");
    return '<article class="card"><div class="rowtop"><span><h3>' + tag(b.tag, "accent") + esc(b.name) + "</h3></span><span>" + flags + "</span></div>" +
      line(esc(b.desc), null, " small muted") +
      line("vs " + esc(b.twin), null, " small muted") +
      '<div style="margin-top:8px">' + line("Net excess vs twin", esc(excess), " small") + intervalSvg(b.lo, b.mid, b.hi) + band + "</div>" +
      sample +
      line("Trades / day", esc(b.tradesDay), " small") +
      (b.stop ? line("Stop date", esc(b.stop), " small muted") : "") +
      "</article>";
  }
  function quietCard(b) {
    return '<article class="card dim"><div class="rowtop"><span>' + tag(b.tag) + esc(b.name) + "</span>" + tag(b.status, b.tone) + "</div>" +
      line(esc(b.desc), null, " small muted") +
      line("Trades / day", esc(b.tradesDay), " small") +
      "</article>";
  }

  function axisLabels() {
    return '<div class="line small"><span>Today, Nov 11</span><span class="secondary">Nov 25</span><span class="muted">after</span></div>';
  }
  function durationTrack(mode) {
    var W = 320, H = 36, pad = 8, y = 13, h = 10;
    var x0 = pad, x1 = W - pad, n25 = x0 + (x1 - x0) * 0.5, mid = y + h / 2;
    var body = '<line x1="' + x0 + '" x2="' + x1 + '" y1="' + mid + '" y2="' + mid + '" stroke="#2B3139" stroke-width="8" stroke-linecap="round" />';
    if (mode === "sealed") body += '<line x1="' + x0 + '" x2="' + n25 + '" y1="' + mid + '" y2="' + mid + '" stroke="#36d6ff" stroke-width="8" stroke-linecap="round" />';
    if (mode === "display") body += '<line x1="' + x0 + '" x2="' + x1 + '" y1="' + mid + '" y2="' + mid + '" stroke="#848E9C" stroke-width="8" stroke-linecap="round" opacity="0.7" />';
    if (mode === "redesign" || mode === "waiting" || mode === "counting") {
      body += '<line x1="' + n25 + '" x2="' + x1 + '" y1="' + mid + '" y2="' + mid + '" stroke="#B5E853" stroke-width="8" stroke-linecap="round" stroke-dasharray="5 5" />';
    }
    if (mode === "rejected") body += '<line x1="' + x0 + '" x2="' + x1 + '" y1="' + mid + '" y2="' + mid + '" stroke="#2B3139" stroke-width="6" stroke-linecap="round" stroke-dasharray="3 5" />';
    body += '<line x1="' + x0 + '" x2="' + x0 + '" y1="' + (y - 6) + '" y2="' + (y + h + 6) + '" stroke="#EAECEF" stroke-width="2" />';
    body += '<line x1="' + n25 + '" x2="' + n25 + '" y1="' + (y - 5) + '" y2="' + (y + h + 5) + '" stroke="#848E9C" stroke-width="1.5" stroke-dasharray="2 2" />';
    if (mode === "sealed") body += '<circle cx="' + n25 + '" cy="' + mid + '" r="5" fill="#36d6ff" stroke="#161A1E" stroke-width="2" />';
    if (mode === "counting") body += '<circle cx="' + (x0 + 6) + '" cy="' + mid + '" r="4" fill="#36d6ff" stroke="#161A1E" stroke-width="2" />';
    return '<svg width="100%" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Experiment length">' + body + "</svg>";
  }
  function afterNote(mode) {
    if (mode === "redesign") return "Dashed after Nov 25: redesign, dates TBD.";
    if (mode === "waiting") return "Dashed after Nov 25: waits for S2 v3.";
    if (mode === "counting") return "Dot at today: counting history now. Dashed after Nov 25: dates TBD.";
    if (mode === "sealed") return "Solid bar to the Nov 25 review marker.";
    if (mode === "display") return "Open-ended display bar. No verdict, no hard cap.";
    return "No experiment bar. Rejected as a test book.";
  }
  function anatomy() {
    var W = 320, H = 86, pad = 10, y = 28, h = 12;
    var x0 = pad, x1 = W - pad, span = x1 - x0;
    var xAt = function (pct) { return x0 + span * pct; };
    var mid = y + h / 2;
    var shake = xAt(7 / 84), floor0 = xAt(8 / 84), floor1 = xAt(21 / 84), today = xAt(18 / 84);
    var k50 = xAt(33 / 84), k100 = xAt(55 / 84), verdict = xAt(70 / 84);
    return '<svg width="100%" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Anatomy of a test bar">' +
      '<line x1="' + x0 + '" x2="' + x1 + '" y1="' + mid + '" y2="' + mid + '" stroke="#2B3139" stroke-width="12" stroke-linecap="round" />' +
      '<line x1="' + x0 + '" x2="' + shake + '" y1="' + mid + '" y2="' + mid + '" stroke="#B5E853" stroke-width="12" stroke-linecap="round" />' +
      '<line x1="' + floor0 + '" x2="' + floor1 + '" y1="' + mid + '" y2="' + mid + '" stroke="#F0B90B" stroke-width="12" stroke-linecap="round" opacity="0.85" />' +
      '<line x1="' + floor1 + '" x2="' + verdict + '" y1="' + mid + '" y2="' + mid + '" stroke="#36d6ff" stroke-width="12" />' +
      '<line x1="' + today + '" x2="' + today + '" y1="' + (y - 14) + '" y2="' + (y + h + 10) + '" stroke="#EAECEF" stroke-width="2" />' +
      '<line x1="' + k50 + '" x2="' + k50 + '" y1="' + (y - 4) + '" y2="' + (y + h + 4) + '" stroke="#848E9C" stroke-width="1.5" />' +
      '<line x1="' + k100 + '" x2="' + k100 + '" y1="' + (y - 4) + '" y2="' + (y + h + 4) + '" stroke="#848E9C" stroke-width="1.5" />' +
      '<circle cx="' + verdict + '" cy="' + mid + '" r="5" fill="#0ECB81" stroke="#161A1E" stroke-width="2" />' +
      '<circle cx="' + x1 + '" cy="' + mid + '" r="5" fill="#F6465D" stroke="#161A1E" stroke-width="2" />' +
      '<circle cx="' + x0 + '" cy="' + mid + '" r="4" fill="#EAECEF" stroke="#161A1E" stroke-width="2" />' +
      '<text x="' + x0 + '" y="12" fill="#848E9C" font-size="10">Start</text>' +
      '<text x="' + today + '" y="12" text-anchor="middle" fill="#EAECEF" font-size="10">Today</text>' +
      '<text x="' + x1 + '" y="12" text-anchor="end" fill="#848E9C" font-size="10">Hard cap</text>' +
      '<text x="' + k50 + '" y="' + (H - 8) + '" text-anchor="middle" fill="#848E9C" font-size="10">50</text>' +
      '<text x="' + k100 + '" y="' + (H - 8) + '" text-anchor="middle" fill="#848E9C" font-size="10">100</text>' +
      '<text x="' + verdict + '" y="' + (H - 8) + '" text-anchor="middle" fill="#848E9C" font-size="10">Verdict</text>' +
      "</svg>" +
      line("No day count yet. Marks appear after a book locks its dates.", null, " small muted");
  }
  function equitySvg(points) {
    var W = 340, H = 168, l = 36, r = 10, top = 14, btm = 22;
    if (!points.length) {
      return '<p class="muted small">No equity marks yet.</p>';
    }
    var vals = points.map(function (p) { return p.equity; });
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    if (min === max) { min -= 0.05; max += 0.05; }
    else { var pad = (max - min) * 0.15; min -= pad; max += pad; }
    var x = function (i) { return l + (points.length === 1 ? (W - l - r) / 2 : (i / (points.length - 1)) * (W - l - r)); };
    var y = function (v) { return top + (1 - (v - min) / (max - min)) * (H - top - btm); };
    var ticks = [min, (min + max) / 2, max];
    var grid = ticks.map(function (g) {
      return '<line x1="' + l + '" x2="' + (W - r) + '" y1="' + y(g) + '" y2="' + y(g) + '" stroke="#2B3139" stroke-width="1" />' +
        '<text x="' + (l - 4) + '" y="' + (y(g) + 3) + '" text-anchor="end" fill="#848E9C" font-size="10">' + g.toFixed(2) + "</text>";
    }).join("");
    var coords = points.map(function (p, i) { return x(i) + "," + y(p.equity); }).join(" ");
    var dots = points.map(function (p, i) {
      return '<circle cx="' + x(i) + '" cy="' + y(p.equity) + '" r="3" fill="#36d6ff" />';
    }).join("");
    var labels = [0, points.length - 1].filter(function (i, idx, arr) { return arr.indexOf(i) === idx; }).map(function (i) {
      var text = points[i].t ? fmtEt(points[i].t).replace(/ ET$/, "") : "";
      return '<text x="' + x(i) + '" y="' + (H - 6) + '" text-anchor="' + (i === 0 ? "start" : "end") + '" fill="#848E9C" font-size="10">' + esc(text) + "</text>";
    }).join("");
    return '<svg width="100%" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Equity over time">' + grid + labels +
      '<polyline fill="none" stroke="#36d6ff" stroke-width="2.5" points="' + coords + '" />' + dots + "</svg>";
  }
  function evoSvg(series) {
    var W = 340, H = 190, l = 34, r = 10, top = 14, btm = 26;
    var all = [];
    series.forEach(function (s) { all = all.concat(s.values); });
    var min = Math.min.apply(null, all) - 0.1;
    var max = Math.max.apply(null, all) + 0.1;
    var x = function (i) { return l + (i / (DAYS.length - 1)) * (W - l - r); };
    var y = function (v) { return top + (1 - (v - min) / (max - min)) * (H - top - btm); };
    var color = function (k) { return k === "book" ? "#36d6ff" : k === "twin" ? "#B5E853" : "#848E9C"; };
    var ticks = [min, (min + max) / 2, max];
    var grid = ticks.map(function (g) {
      return '<line x1="' + l + '" x2="' + (W - r) + '" y1="' + y(g) + '" y2="' + y(g) + '" stroke="#2B3139" />' +
        '<text x="' + (l - 4) + '" y="' + (y(g) + 4) + '" text-anchor="end" fill="#848E9C" font-size="10">' + g.toFixed(1) + "</text>";
    }).join("");
    var days = [0, 6, 13].map(function (i) {
      return '<text x="' + x(i) + '" y="' + (H - 8) + '" text-anchor="' + (i === 0 ? "start" : i === 13 ? "end" : "middle") + '" fill="#848E9C" font-size="10">' + DAYS[i] + "</text>";
    }).join("");
    var lines = series.map(function (s) {
      var pts = s.values.map(function (v, i) { return x(i) + "," + y(v); }).join(" ");
      var dash = s.kind === "bench" ? ' stroke-dasharray="5 4"' : "";
      return '<polyline fill="none" stroke="' + color(s.kind) + '" stroke-width="' + (s.kind === "book" ? 2.5 : 2) + '"' + dash + ' points="' + pts + '" />';
    }).join("");
    return '<svg width="100%" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Equity">' + grid + days + lines + "</svg>";
  }

  function screenWinning() {
    var anatomy = state.phase === "anatomy";
    var pills = '<div class="pills"><button type="button" data-act="phase" data-v="now" class="' + (state.phase === "now" ? "on" : "") + '">Now</button>' +
      '<button type="button" data-act="phase" data-v="anatomy" class="' + (anatomy ? "on" : "") + '">Book anatomy</button></div>';
    var killed = KILLED.map(quietCard).join("");
    var display = '<p class="kicker">Display only · never ranked with the tested books</p>' +
      '<article class="card dim"><div class="rowtop"><span>' + tag("DW") + "Busy S2 (exit variant)</span>" + tag("waits for S2 v3", "warn") + "</div>" +
      line("Display only, untested. It waits for S2 v3. Never ranked.", null, " small muted") +
      line("Trades / day", "TBD", " small") +
      "</article>" +
      '<article class="card dim"><div class="rowtop"><span>SOL buy-and-hold</span>' + tag("benchmark", "accent") + "</div>" +
      line("Display-only benchmark, not a tested book", null, " small muted") +
      line("Trades / day", "0 (hold)", " small") +
      line("Return", "no results yet", " small muted") +
      "</article>";
    var body = anatomy
      ? '<article class="card"><h3>Waiting for first run</h3><p class="muted">A tested book would show net excess, a 95% interval, and n against its twin. No tested book has those figures yet.</p></article>'
      : '<p class="warnline">No tested book is running.</p>' + NOW_BOOKS.map(bookCard).join("");
    return pills + body +
      line("A–D were killed in backtest (Nov 11). E is blocked. Top winners was killed pre-test. Rank never promotes or kills a book.", null, " small muted") +
      '<p class="kicker">Killed in backtest · Nov 11 · no retunes this cycle</p>' + killed +
      '<p class="kicker">Blocked</p>' + quietCard(BLOCKED) +
      '<p class="kicker">Cycle 2 · own group, judged separately, shown on the same board</p>' + quietCard(CYCLE2) +
      display +
      '<p class="muted">No tested book is running. ' + esc(S2_STATUS) + ". Rank never promotes or kills a book.</p>";
  }

  function movement(w, view) {
    var series = w.series.length ? w.series : (w.equity != null ? [{ t: w.updatedAt, equity: w.equity }] : []);
    var lead = view.lead && view.lead.ids.indexOf(w.id) !== -1 ? line(esc(view.lead.label), null, " small") : "";
    var positions = w.positions.length ? w.positions.map(function (p) {
      var pnl = p.upnl != null ? signed(p.upnl, 2) + " SOL" : "—";
      var pct = p.upnlPct != null ? " · " + signed(p.upnlPct, 1) + "%" : "";
      return '<div class="trade"><div class="rowtop"><strong>' + esc(p.symbol) + "</strong><span class=\"" + cls(p.upnl != null ? p.upnl : p.upnlPct) + "\">" + esc(pnl + pct) + "</span></div>" +
        line("Size " + (p.size == null ? "—" : p.size.toFixed(2) + " SOL"), "unrealized", " small muted") +
        line("Entry " + px(p.entry), "Mark " + px(p.mark), " small") +
        "</div>";
    }).join("") : '<p class="muted">No open positions.</p>';
    var trades = w.trades.length ? w.trades.map(function (t) {
      var pnl = t.pnl == null ? "—" : signed(t.pnl, 2) + " SOL";
      var extra = (t.costBps != null ? "Cost " + t.costBps + " bps" : "") + (t.exitReason ? " · " + t.exitReason : "");
      return '<div class="trade"><div class="rowtop"><span>' + esc(fmtEt(t.t)) + "</span><span class=\"" + (t.side === "sell" ? "dn" : "up") + "\">" + esc(t.side.toUpperCase()) + " " + esc(t.symbol) + "</span></div>" +
        line("Fill " + px(t.price), "Size " + (t.size == null ? "—" : t.size.toFixed(2) + " SOL"), " small") +
        line("P&amp;L", '<span class="' + cls(t.pnl) + '">' + esc(pnl) + "</span>", " small") +
        (extra ? line(esc(extra), null, " small muted") : "") +
        "</div>";
    }).join("") : '<p class="muted">No buys or sells yet.</p>';
    var split = "";
    if (w.id === "R5" && view.split) {
      ["meme", "non_meme", "untagged"].forEach(function (key) {
        var side = view.split[key] || {};
        var net = side.net_pct == null ? "—" : signed(side.net_pct, 2) + "%";
        split += line(key.replace("_", "-") + " n=" + (side.n == null ? "—" : side.n), esc(net), " small");
      });
    }
    if (w.id === "R5" && view.overlap && view.overlap.line) {
      split += line(esc(view.overlap.line), null, " small");
    }
    var official = w.pendingLock ? "" :
      line("Official net", esc(w.netPct == null ? "—" : signed(w.netPct, 2) + "%"), " small") +
      line("n", esc(w.n == null ? "—" : String(w.n)), " small") +
      line("95% interval", esc(w.lo == null || w.hi == null ? "—" : signed(w.lo, 2) + "% to " + signed(w.hi, 2) + "%"), " small") +
      line("Avg % deployed", esc(w.avgDeployed == null ? "—" : w.avgDeployed.toFixed(0) + "%"), " small");
    return '<article class="card"><div class="rowtop"><span><h3>' + tag(w.id, "accent") + esc(w.name) + "</h3></span>" + tag(w.status || "starting", w.status === "running" ? "good" : "warn") + "</div>" +
      lead +
      (w.pendingLock ? line(esc(R.PENDING) + ". No equity until summary.json lists R5.", null, " small muted") : "") +
      line("Last updated", esc(w.updatedAt ? fmtEt(w.updatedAt) : "—"), " small") +
      line("Run status", esc(w.status || "starting"), " small") +
      line("Started at", esc(level(w.startEquity, "SOL")), " small") +
      line("Equity", esc(w.pendingLock || w.equity == null ? "—" : level(w.equity, w.currency)), " small") +
      official + split +
      (w.pendingLock ? "" : equitySvg(series)) +
      '<p class="kicker">Open positions</p>' + (w.pendingLock ? '<p class="muted">None yet.</p>' : positions) +
      '<p class="kicker">Trades</p>' + (w.pendingLock ? '<p class="muted">None yet.</p>' : trades) +
      "</article>";
  }
  function raceBlock(view, focus) {
    var note = view.source === "fixtures"
      ? '<p class="warnline">Fixture file (?fixtures=1). Not a live race.</p>'
      : "";
    var lead = view.lead ? line(esc(view.lead.label), null, " small") : line(esc(view.message || R.MESSAGE), null, " small");
    var buttons = view.wallets.map(function (w) {
      var on = focus === w.id ? " on" : "";
      var right = w.pendingLock ? R.PENDING : (w.netPct == null ? (w.status || "starting") : signed(w.netPct, 2) + "%");
      var deployed = !w.pendingLock && w.avgDeployed != null ? line("Avg % deployed " + w.avgDeployed.toFixed(0) + "%", null, " small muted") : "";
      return '<a class="wallet' + on + '" href="#/wallets/' + esc(w.id) + '"><div class="rowtop"><span>' + tag(w.id, w.role === "benchmark" ? "accent" : "") + esc(w.name) + "</span>" + tag(w.role === "benchmark" ? "benchmark" : (w.status || "starting"), w.role === "benchmark" ? "accent" : "warn") + "</div>" +
        line(esc(right), null, " small muted") + deployed + "</a>";
    }).join("");
    var detail = "";
    if (focus) {
      var picked = null;
      view.wallets.forEach(function (w) { if (w.id === focus) picked = w; });
      detail = picked ? movement(picked, view) : '<article class="card"><p>That wallet is not on the race list.</p></article>';
    }
    var overlap = view.overlap && view.overlap.line ? line(esc(view.overlap.line), null, " small") : "";
    var note2 = view.overlap && view.overlap.note ? line(esc(view.overlap.note), null, " small muted") : "";
    return note + '<p class="banner">' + esc(view.banner) + "</p>" + lead + overlap + note2 +
      (view.updatedAt ? line("File updated", esc(fmtEt(view.updatedAt)), " small muted") : "") +
      detail + buttons;
  }
  function plannedCard(w) {
    return '<article class="card"><div class="rowtop"><span><h3>' + tag(w.tag, "accent") + esc(w.name) + "</h3></span>" + tag(w.status, w.tone) + "</div>" +
      line(esc(w.purpose), null, " small muted") +
      line("Twin", esc(w.twin), " small") +
      line("Trades / day", esc(w.tradesDay), " small") +
      (w.note ? line(esc(w.note), null, " small muted") : "") +
      line("No equity, fills or ranks yet. This is the planned wallet, not a live one.", null, " small muted") +
      "</article>";
  }
  function screenWallets(view) {
    var arg = (route().arg || "").toUpperCase();
    var raceIds = {};
    view.wallets.forEach(function (w) { raceIds[w.id] = true; });
    var focusRace = raceIds[arg] ? arg : "";
    var planned = WALLETS.filter(function (w) { return w.tag === arg; })[0];
    var groups = GROUPS.map(function (g) {
      var buttons = WALLETS.filter(function (w) { return w.group === g; }).map(function (w) {
        var on = arg === w.tag ? " on" : "";
        var tone = g.indexOf("Cycle 1") === 0 ? "accent" : w.tone;
        return '<a class="wallet' + on + '" href="#/wallets/' + esc(w.tag) + '"><div class="rowtop"><span>' + tag(w.tag, tone) + esc(w.name) + "</span>" + tag(w.status, w.tone) + "</div>" +
          line("Trades / day  " + esc(w.tradesDay), null, " small muted") + "</a>";
      }).join("");
      return '<p class="kicker">' + esc(g) + "</p>" + buttons;
    }).join("");
    var head = focusRace
      ? ""
      : '<p class="warnline">No planned wallet has started. A–D killed, E blocked, and cycle-2 Top winners killed pre-test are the real Nov 11 verdicts.</p>';
    return head +
      '<p class="kicker">Race wallets · follow the balance</p>' +
      raceBlock(view, focusRace) +
      (planned ? plannedCard(planned) : "") +
      groups +
      (planned || focusRace ? "" : plannedCard(WALLETS[0]));
  }

  function screenDuration() {
    var pills = '<div class="pills">' +
      ["all", "each", "how"].map(function (k) {
        var label = k === "all" ? "All books" : k === "each" ? "Each book" : "How to read";
        return '<button type="button" data-act="dur" data-v="' + k + '" class="' + (state.dur === k ? "on" : "") + '">' + label + "</button>";
      }).join("") + "</div>";
    var body = "";
    if (state.dur === "how") {
      body = '<article class="card"><h3>Anatomy of a test bar</h3>' +
        line("Once a book has locked dates, its bar shows the marks below. This sample has no dates and no percents. It is not S2 v3.", null, " small muted") +
        anatomy() +
        line("Start date", "left end", " small") +
        line("Today", "bold vertical line", " small") +
        line("Shakedown (7 days)", "first band", " small") +
        line("Entry-floor check (days 8–21)", "second band", " small") +
        line("Kill checks at 50 and 100 closes", "ticks 50 / 100", " small") +
        line("Expected verdict (n=150)", "green dot", " small") +
        line("Hard cap date", "red dot, right end", " small") +
        line("day X of ~Y, and % done", "under the bar", " small") +
        "</article>";
    } else if (state.dur === "each") {
      body = DUR.map(function (b) {
        var dim = b.mode === "rejected" || b.mode === "waiting" || b.mode === "display" ? " dim" : "";
        return '<article class="card' + dim + '"><div class="rowtop"><span>' + tag(b.tag, b.mode === "rejected" ? "bad" : "accent") + esc(b.name) + "</span>" + tag(b.status, b.tone) + "</div>" +
          durationTrack(b.mode) + axisLabels() +
          line(esc(b.dayLabel), esc(b.pctLabel), " small") +
          line(esc(afterNote(b.mode)), null, " small muted") +
          line(esc(b.note), null, " small muted") +
          "</article>";
      }).join("");
    } else {
      body = axisLabels() + DUR.map(function (b) {
        return '<div style="margin-bottom:10px"><div class="rowtop"><span>' + tag(b.tag, b.mode === "rejected" ? "bad" : "accent") + esc(b.name) + "</span>" + tag(b.status, b.tone) + "</div>" +
          durationTrack(b.mode) +
          line(esc(b.dayLabel + " · " + b.pctLabel), null, " small muted") +
          "</div>";
      }).join("") +
        line("Shared scale: today (Nov 11, 2026, America/Toronto) to Nov 25, then an after zone for dates that are still TBD. Planned marks, not a live result.", null, " small muted");
    }
    return '<p class="warnline">No tested book is running. S2 v2 was ruled unworkable before start; the old Nov 12–Jan 8 bar is not shown.</p>' +
      pills + body +
      '<p class="muted">Killed or blocked books from cycle 1 and cycle 2 (A–E, Top winners) have no forward duration, so they are omitted here. Bars are planned marks, not live results.</p>';
  }

  function screenEvolution(view) {
    var rows = (view && view.wallets ? view.wallets : []).filter(function (w) { return w.series && w.series.length; });
    if (!view || view.message || !rows.length) {
      return '<article class="card"><h3>Waiting for first run</h3><p class="muted">No live equity curve is on races-data yet. Killed books A–D have no curve. S2 has not started. No start date is published.</p></article>';
    }
    return rows.map(function (w) {
      return '<article class="card"><div class="rowtop"><span><h3>' + tag(w.id, "accent") + esc(w.name) + "</h3></span>" + tag(w.role === "benchmark" ? "benchmark" : (w.status || ""), w.role === "benchmark" ? "accent" : "") + "</div>" +
        line("Started at", esc(level(w.startEquity, "SOL")), " small") +
        equitySvg(w.series) +
        "</article>";
    }).join("") + '<p class="muted">Curves are the race equity file. They are display only.</p>';
  }

  function screenTrades(view) {
    var open = [];
    (view && view.wallets ? view.wallets : []).forEach(function (w) {
      (w.positions || []).forEach(function (p) { open.push({ w: w, p: p }); });
    });
    if (!open.length) {
      return '<article class="card"><h3>Waiting for first run</h3><p class="muted">S2 has not started, and the race has no open positions yet. No start date is published.</p></article>';
    }
    return '<p class="banner">' + esc(view.banner) + "</p>" + open.map(function (row) {
      var p = row.p;
      var pnl = p.upnl != null ? signed(p.upnl, 2) + " SOL" : "—";
      var pct = p.upnlPct != null ? " · " + signed(p.upnlPct, 1) + "%" : "";
      return '<article class="card"><div class="rowtop"><strong>' + esc(p.symbol) + "</strong><span class=\"" + cls(p.upnl != null ? p.upnl : p.upnlPct) + "\">" + esc(pnl + pct) + "</span></div>" +
        line(esc(row.w.id + " · " + row.w.name), "unrealized", " small muted") +
        line("Size " + (p.size == null ? "—" : p.size.toFixed(2) + " SOL"), null, " small") +
        line("Entry " + px(p.entry), "Mark " + px(p.mark), " small") +
        "</article>";
    }).join("");
  }

  function screenArchive() {
    return '<article class="card dim"><div class="rowtop"><span><h3>v6.5 F / R / S</h3></span>' + tag("sealed review Nov 25", "warn") + "</div>" +
      line(esc(F_STATUS), null, " small") +
      line("This card does not print the sealed book's figures. The previous site still shows that view.", null, " small muted") +
      '<p><a class="link" href="legacy/index.html#/solana">Open the frozen v6.5 view</a></p>' +
      "</article>" +
      '<article class="card dim"><div class="rowtop"><span><h3>Trend-SOL long/flat</h3></span><span>' + tag("KILLED", "bad") + tag("by Step 0") + "</span></div>" +
      line("Killed before launch by the Step 0 test; Challenger confirmed the kill", null, " small muted") +
      line("Never traded, never a display wallet", null, " small muted") +
      "</article>";
  }

  function screenFresh(stamps) {
    var rows = [
      ["Solana desk", stamps.sol, "Fresh under 60 min (2 × 30-min scan). Older than that is amber."],
      ["KING desk", stamps.nft, "Fresh under 2 h (2 × hourly). Older than that is amber."],
      ["Cardano (Snek)", stamps.ada, "Paused desks never claim fresh."]
    ];
    return rows.map(function (r) {
      return '<div class="trade">' + line(esc(r[0]), tag(r[1].text, r[1].tone)) + line(esc(r[2]), null, " small muted") + "</div>";
    }).join("") +
      '<p class="muted">Stamps replace a Live label. A desk turns amber when its last update is older than twice its normal cadence.</p>';
  }

  function agePhrase(ms, now) {
    if (!isFinite(ms)) return "";
    var min = Math.max(0, Math.round((now - ms) / 60000));
    if (min < 90) return "updated " + min + " min ago";
    var h = Math.round(min / 60);
    if (h < 48) return "updated " + h + " h ago";
    return "updated " + Math.round(h / 24) + " days ago";
  }
  function parseStamp(v) {
    if (v == null) return NaN;
    var s = String(v).trim();
    var m = s.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?Z?$/);
    if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
    var t = Date.parse(s);
    return isFinite(t) ? t : NaN;
  }
  function stampFrom(doc) {
    if (!doc || typeof doc !== "object") return NaN;
    var keys = ["asof", "updated_at", "generated_at", "last_run_utc", "last_run"];
    var best = NaN;
    keys.forEach(function (k) {
      var t = parseStamp(doc[k]);
      if (isFinite(t) && (!isFinite(best) || t > best)) best = t;
    });
    return best;
  }
  function getJson(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }
  function newer(a, b) {
    var sa = stampFrom(a), sb = stampFrom(b);
    if (!isFinite(sa)) return b;
    if (!isFinite(sb)) return a;
    return sb > sa ? b : a;
  }
  function loadStamps() {
    var files = ["solana-desk/health.json", "nft/log.json", "nft/pulse.json", "solada/log.json"];
    return Promise.all(files.map(function (rel) {
      return Promise.all([getJson(rel), getJson(DESK_DATA + rel)]).then(function (pair) { return newer(pair[0], pair[1]); });
    })).then(function (docs) {
      var now = Date.now();
      var sol = stampFrom(docs[0]);
      var nft = Math.max(stampFrom(docs[1]), stampFrom(docs[2]));
      var ada = stampFrom(docs[3]);
      function one(ms, limitMin, paused) {
        if (!isFinite(ms)) return { text: "no run file yet", tone: "" };
        var min = Math.max(0, (now - ms) / 60000);
        var text = agePhrase(ms, now);
        if (paused) return { text: "paused · last update " + text.replace(/^updated /, ""), tone: "" };
        return { text: text, tone: min <= limitMin ? "good" : "warn" };
      }
      return { sol: one(sol, 60, false), nft: one(nft, 120, false), ada: one(ada, 0, true) };
    }).catch(function () {
      var missing = { text: "no run file yet", tone: "" };
      return { sol: missing, nft: missing, ada: missing };
    });
  }

  function initials(name) {
    var letters = String(name || "").replace(/[^A-Za-z0-9]/g, "");
    return (letters.slice(0, 2) || "?").toUpperCase();
  }
  function logoHtml(row) {
    if (!row.image) return '<span class="logo-dot">' + esc(initials(row.name)) + "</span>";
    return '<img class="logo-dot" alt="" src="' + esc(row.image) + '" data-fallback="' + esc(initials(row.name)) + '">';
  }
  function volTxt(row) {
    if (row.volume == null) return "—";
    var n = row.volume >= 1 ? row.volume.toFixed(2) : row.volume.toFixed(3);
    return n + " " + row.symbol;
  }
  function collRow(row) {
    var marks = row.rwa ? tag("RWA", "warn") : "";
    if (row.flagNew && !row.rwa) marks += tag("new", "accent");
    var inner = logoHtml(row) + '<div class="grow"><div class="rowtop"><strong>' + esc(row.name) + "</strong>" + marks + "</div>" +
      line("24h volume " + esc(volTxt(row)), "sales " + esc(row.sales == null ? "—" : String(row.sales)), " small muted") + "</div>";
    if (!row.url) return '<div class="coll">' + inner + "</div>";
    return '<a class="coll" href="' + esc(row.url) + '" target="_blank" rel="noreferrer">' + inner + "</a>";
  }
  function kingCard(hood) {
    if (!hood || hood.empty) {
      return '<article class="card"><div class="rowtop"><span><h3>KING desk (2)</h3></span>' + tag("Robinhood Chain", "accent") + "</div>" +
        '<p class="muted">Couldn\'t load Hoodscout data/nft_discover.json. No volumes are shown.</p></article>';
    }
    var age = SightlineHood.ageStamp(hood.generatedAt);
    var top = hood.top ? esc(hood.top.name) + " is " + esc(hood.shareLabel) : "No ETH volume yet";
    return '<article class="card"><div class="rowtop"><span><h3>KING desk (2)</h3></span>' + tag("Robinhood Chain", "accent") + "</div>" +
      line("Display only. Not a trading signal.", null, " small muted") +
      line(top, null, " small") +
      (hood.totalLabel ? line(esc(hood.totalLabel), null, " small muted") : "") +
      (hood.volumeCheck ? line(esc(hood.volumeCheck) + ". " + esc(hood.volumeCheckNote), null, " small") : "") +
      '<div style="margin-top:6px">' + tag("updated " + age.text, age.tone) + "</div>" +
      '<p><a class="link" href="#/king2">Open KING desk (2)</a></p></article>';
  }
  function uaCard(doc) {
    if (!doc || !doc.counts) {
      return '<article class="card dim"><div class="rowtop"><span><h3>UA-F1</h3></span>' + tag("paper", "") + "</div>" +
        '<p class="muted">Waiting for first run. S1 is due 1 December 2026. The weekly pass is Mondays at 01:00 UTC.</p></article>';
    }
    var c = doc.counts;
    return '<article class="card"><div class="rowtop"><span><h3>UA-F1</h3></span>' + tag(String(doc.status || "paper"), "accent") + "</div>" +
      line("Registered episodes", esc(c.registered == null ? "—" : String(c.registered)), " small") +
      line("Usable", esc(c.usable == null ? "—" : String(c.usable)), " small") +
      line("As of", esc(doc.as_of ? fmtEt(doc.as_of) : "—"), " small muted") +
      '<p class="muted">Counts only. No mean, t, or win rate.</p></article>';
  }
  function screenKing(hood) {
    var back = '<p><a class="back" href="#/">Back to Desks</a></p>';
    if (!hood || hood.empty) {
      return back + '<article class="card"><h3>Couldn\'t load</h3><p class="muted">Couldn\'t load the Hoodscout file. No collections and no volumes are shown.</p></article>';
    }
    var news = hood.newcomers.length
      ? '<p class="kicker">New on the top list</p>' + hood.newcomers.map(collRow).join("")
      : '<p class="muted">No collection is flagged as newly entered.</p>';
    var jump = hood.jumpSupported && hood.hot
      ? line(esc(hood.hot.name) + " has the largest 24h floor change", esc(signed(hood.hot.floorChg, 1) + "%"), " small")
      : '<p class="muted">No 24h jump is in this file. Floor change is empty, so nothing is marked hot for a jump.</p>';
    var head = hood.top
      ? '<article class="card"><div class="rowtop"><span><h3>' + esc(hood.top.name) + "</h3></span>" + tag("top 24h", "accent") + "</div>" +
        line(esc(hood.shareLabel), esc(hood.totalLabel), " small") +
        (hood.volumeCheck ? '<p class="warnline">' + esc(hood.volumeCheck) + ". " + esc(hood.volumeCheckNote) + "</p>" : "") +
        '<p class="muted">RWA rows are left out of this ETH total.</p></article>'
      : "";
    var rwa = hood.rwa.length
      ? '<p class="kicker">RWA · left out of the headline total</p>' + hood.rwa.map(collRow).join("")
      : "";
    var age = SightlineHood.ageStamp(hood.generatedAt);
    return back + '<p class="muted">KING desk (2) · Robinhood Chain · display only, not a trading signal. Logos come from the image URL already in the Hoodscout file.</p>' +
      '<div style="margin:8px 0">' + tag("data age " + age.text, age.tone) + "</div>" +
      head + news + jump +
      '<p class="kicker">Top collections by 24h volume</p>' +
      hood.rows.filter(function (row) { return !row.rwa; }).map(collRow).join("") +
      rwa;
  }
  function bindLogos(root) {
    root.querySelectorAll("img[data-fallback]").forEach(function (img) {
      img.addEventListener("error", function () {
        var span = document.createElement("span");
        span.className = "logo-dot";
        span.textContent = img.getAttribute("data-fallback") || "?";
        img.replaceWith(span);
      });
    });
  }

  function screenDesks(stamps, view, hood, ua) {
    var sol = stamps.sol, nft = stamps.nft, ada = stamps.ada;
    var raceLine = view.message
      ? esc(view.message)
      : (view.lead ? esc(view.lead.label) : esc(R.MESSAGE));
    return '<article class="card"><div class="rowtop"><span><h3>1. Solana desk</h3></span>' + tag("Cycle 1 setting up", "accent") + "</div>" +
      line("No tested book is running. " + esc(S2_STATUS) + ".", null, " small muted") +
      line("A–D killed in backtest (Nov 11); E blocked: bad price data", null, " small muted") +
      line("Display: SOL hold plus Busy S2 (exit variant), which waits for S2 v3; cycle 2 Top winners killed pre-test", null, " small muted") +
      line(esc(F_STATUS), null, " small muted") +
      line("Trend-SOL was killed by Step 0 before launch.", null, " small muted") +
      line("Scan every 30 min", null, " small muted") +
      '<div style="margin-top:6px">' + tag(sol.text, sol.tone) + "</div></article>" +
      '<article class="card"><p class="banner">' + esc(view.banner) + "</p>" +
      line("Race wallets R1–R5, plus the SOL-hold benchmark", null, " small") +
      line(raceLine, null, " small") +
      (view.overlap && view.overlap.line ? line(esc(view.overlap.line), null, " small") : "") +
      '<p><a class="link" href="#/wallets">Open the wallet movement</a></p></article>' +
      '<article class="card"><div class="rowtop"><span><h3>KING desk</h3></span>' + tag("Pilot running", "accent") + "</div>" +
      line("Ethereum + Base lane. Display only.", null, " small muted") +
      line("Snapshot hourly", null, " small muted") +
      '<div style="margin-top:6px">' + tag(nft.text, nft.tone) + "</div>" +
      '<p><a class="link" href="legacy/index.html#/nft">Open KING desk</a></p></article>' +
      kingCard(hood) +
      uaCard(ua) +
      '<article class="card dim"><div class="rowtop"><span><h3>Cardano (Snek)</h3></span>' + tag("Paused") + "</div>" +
      line("No runs while paused", null, " small muted") +
      line("Restart plan on SPD-121", null, " small muted") +
      '<div style="margin-top:6px">' + tag(ada.text, ada.tone) + "</div></article>" +
      '<p class="muted">Stamps replace a Live label. A desk turns amber when its last update is older than twice its normal cadence. A paused desk never claims fresh.</p>';
  }

  function draw(id, pack) {
    if (id === "desks") return screenDesks(pack.stamps, pack.race, pack.hood, pack.ua);
    if (id === "winning") return screenWinning();
    if (id === "wallets") return screenWallets(pack.race);
    if (id === "duration") return screenDuration();
    if (id === "evolution") return screenEvolution(pack.race);
    if (id === "trades") return screenTrades(pack.race);
    if (id === "archive") return screenArchive();
    if (id === "king2") return screenKing(pack.hood);
    return screenFresh(pack.stamps);
  }

  function paintNav(id) {
    var nav = document.getElementById("nav");
    var active = id === "king2" ? "desks" : id;
    nav.innerHTML = NAV.map(function (item) {
      var on = item.id === active ? ' class="on" aria-current="page"' : "";
      return '<a href="' + item.href + '"' + on + ">" + item.label + "</a>";
    }).join("");
    var r = route();
    var title = TITLES[id] || "Sightline";
    if (id === "wallets" && r.arg) title = "Wallet " + r.arg.toUpperCase();
    document.getElementById("screen").textContent = title;
  }

  function ensureRace() {
    if (!racePromise) racePromise = R.load(fetch, { fixtures: fixturesOn() });
    return racePromise;
  }
  function ensureStamps() {
    if (!stampPromise) stampPromise = loadStamps();
    return stampPromise;
  }
  function loadHealth() {
    var H = window.SightlineHealth;
    if (!H) return Promise.resolve(null);
    var q = new URLSearchParams(location.search).get("health");
    if (q === "empty" || q === "missing") return Promise.resolve(null);
    if (q === "green" || q === "red" || q === "amber") return Promise.resolve(H.sample(q, Date.now()));
    return getJson(H.URL);
  }
  function ensureHealth() {
    if (!healthPromise) healthPromise = loadHealth();
    return healthPromise;
  }
  function healthHtml(doc) {
    if (!window.SightlineHealth) return "";
    return window.SightlineHealth.render(doc, Date.now());
  }

  var hoodPromise = null;
  var uaPromise = null;
  function ensureHood() {
    if (new URLSearchParams(location.search).get("hood") === "missing") return Promise.resolve(SightlineHood.parse(null));
    if (!hoodPromise) hoodPromise = getJson(SightlineHood.SOURCE).then(function (doc) { return SightlineHood.parse(doc); });
    return hoodPromise;
  }
  function ensureUa() {
    if (!uaPromise) uaPromise = getJson("https://raw.githubusercontent.com/Zirqo-Dev/sightline/ua-f1-data/status.json");
    return uaPromise;
  }

  var token = 0;
  function render() {
    var r = route();
    paintNav(r.id);
    var view = document.getElementById("view");
    var mine = ++token;
    var needRace = r.id === "desks" || r.id === "wallets" || r.id === "evolution" || r.id === "trades";
    var needStamps = r.id === "desks" || r.id === "fresh";
    var needHood = r.id === "desks" || r.id === "king2";
    var needUa = r.id === "desks";
    view.setAttribute("aria-busy", "true");
    Promise.all([
      needRace ? ensureRace() : Promise.resolve(null),
      needStamps ? ensureStamps() : Promise.resolve(null),
      needHood ? ensureHood() : Promise.resolve(null),
      needUa ? ensureUa() : Promise.resolve(null),
      ensureHealth()
    ]).then(function (pair) {
      if (mine !== token) return;
      view.innerHTML = healthHtml(pair[4]) + draw(r.id, { race: pair[0], stamps: pair[1], hood: pair[2], ua: pair[3] });
      bindLogos(view);
      view.removeAttribute("aria-busy");
    }).catch(function () {
      if (mine !== token) return;
      view.innerHTML = '<article class="card"><h2>The page could not be drawn</h2><p class="muted">Reload to try again.</p></article>';
      view.removeAttribute("aria-busy");
    });
  }

  document.getElementById("view").addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-act]");
    if (!b) return;
    var act = b.getAttribute("data-act");
    var v = b.getAttribute("data-v");
    if (act === "phase") state.phase = v;
    if (act === "evo") state.evo = v;
    if (act === "dur") state.dur = v;
    render();
  });
  window.addEventListener("hashchange", render);
  render();
})();
