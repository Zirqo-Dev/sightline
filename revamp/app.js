/* SPD-82 public pages. Paper display only. v6.5 numbers stay on the previous site. */
(function () {
  var R = window.SightlineRaces;
  var DESK_DATA = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/";
  var state = { phase: "now", evo: "S2", dur: "all" };
  var racePromise = null;
  var stampPromise = null;

  var NOW_BOOKS = [
    { tag: "S2", name: "Survivors", desc: "Meme tokens that survive their early stretch. Only tested book (m=1, p ≤ 0.10, binding n of 150 closes, about 50 days, roughly Dec 31 if started now). Gate (item 9 prereg plus workflow isolation) and the owner's yes are still missing.", twin: "random twin, same universe", tradesDay: "~0.5 planned", status: "approved, not started", statusTone: "accent" }
  ];
  var KILLED = [
    { tag: "A", name: "Hour-drop bounce", desc: "Liquid non-meme Solana tokens after a sharp 1h drop vs SOL, exit after 24h. No retunes this cycle.", twin: "random twin, same universe", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad" },
    { tag: "B", name: "Funding extreme", desc: "Buy spot when Binance perp funding is in the bottom 5% of its 90 days, hold 72h. No retunes this cycle.", twin: "random twin, same universe", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad" },
    { tag: "C", name: "Calm-or-wild SOL", desc: "Vol-targeted SOL at 60%, 1x cap. No retunes this cycle.", twin: "buy-and-hold SOL", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad" },
    { tag: "D", name: "Best-hours SOL", desc: "Trades SOL only in historically stronger hour-of-day windows. No retunes this cycle.", twin: "random twin, same hours", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad" }
  ];
  var BLOCKED = { tag: "E", name: "Staked-SOL discount", desc: "jitoSOL discount vs SOL. Blocked because CoinGecko price glitches produced the locked-rule pass; clean data since Aug 1 has 0 episodes.", twin: "random twin on the discount", tradesDay: "—", status: "blocked: bad price data", tone: "warn" };
  var RUN_BOOKS = [
    { tag: "S2", name: "Survivors", desc: "Meme tokens that survive their early stretch. Only tested book (m=1, p ≤ 0.10, binding n of 150 closes).", twin: "random twin, same universe", tradesDay: "~0.5", n: 48, req: 150, unit: "closes", stop: "EXAMPLE stop date (~Dec 31 if started now)", lo: -4.8, mid: 1.6, hi: 8.0, insufficient: true }
  ];
  var CYCLE2 = { tag: "C2", name: "Top winners", desc: "Weekly momentum into recent winners. Killed pre-test at 11:49 AM EST Nov 11: at most 9 eligible tokens before 2025 vs the 15 needed.", twin: "—", tradesDay: "—", status: "killed pre-test (too few tokens)", tone: "bad" };
  var WALLETS = [
    { tag: "S2", name: "Survivors", group: "Cycle 1 · tested", purpose: "Meme tokens that survive their early stretch. The only tested book (m=1, p ≤ 0.10, binding n of 150 closes).", twin: "random twin, same universe", tradesDay: "~0.5 planned", status: "approved, not started", tone: "accent", note: "Gate (item 9 prereg plus workflow isolation) and the owner's yes are still missing." },
    { tag: "A", name: "Hour-drop bounce", group: "Killed in backtest (Nov 11)", purpose: "Liquid non-meme Solana tokens after a sharp 1h drop vs SOL, exit after 24h.", twin: "random twin, same universe", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad", note: "No retunes this cycle." },
    { tag: "B", name: "Funding extreme", group: "Killed in backtest (Nov 11)", purpose: "Buy spot when Binance perp funding is in the bottom 5% of its 90 days, hold 72h.", twin: "random twin, same universe", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad", note: "No retunes this cycle." },
    { tag: "C", name: "Calm-or-wild SOL", group: "Killed in backtest (Nov 11)", purpose: "Vol-targeted SOL at 60%, capped at 1x.", twin: "buy-and-hold SOL", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad", note: "No retunes this cycle." },
    { tag: "D", name: "Best-hours SOL", group: "Killed in backtest (Nov 11)", purpose: "Trades SOL only in historically stronger hour-of-day windows.", twin: "random twin, same hours", tradesDay: "—", status: "killed in backtest (Nov 11)", tone: "bad", note: "No retunes this cycle." },
    { tag: "E", name: "Staked-SOL discount", group: "Blocked", purpose: "Buy discounted jitoSOL vs SOL.", twin: "random twin on the discount", tradesDay: "—", status: "blocked: bad price data", tone: "warn", note: "CoinGecko price glitches produced the locked-rule pass; clean data since Aug 1 has 0 episodes." },
    { tag: "DW", name: "Busy S2 (exit variant)", group: "Display only", purpose: "One S2 exit rule, to be fixed in S2's prereg.", twin: "none · not tested", tradesDay: "TBD", status: "display only, untested", tone: "warn", note: "Owner pick, 11:48 AM EST Nov 11. Trades/day stays TBD until S2's prereg fixes it. Never ranked with the tested books." },
    { tag: "BH", name: "SOL buy-and-hold", group: "Display only", purpose: "Display-only benchmark, not a tested book.", twin: "—", tradesDay: "0 (hold)", status: "benchmark", tone: "accent" },
    { tag: "C2", name: "Top winners", group: "Cycle 2 · killed pre-test", purpose: "Weekly momentum into recent winners.", twin: "—", tradesDay: "—", status: "killed pre-test (too few tokens)", tone: "bad", note: "Killed pre-test at 11:49 AM EST Nov 11: at most 9 eligible tokens before 2025 vs the 15 needed." }
  ];
  var GROUPS = ["Cycle 1 · tested", "Killed in backtest (Nov 11)", "Blocked", "Display only", "Cycle 2 · killed pre-test"];
  var DAYS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11", "D12", "D13", "D14"];
  var EVO = [
    { key: "S2", label: "S2", note: "S2 vs its random twin. SOL hold is dashed and display-only.", series: [
      { name: "S2 Survivors", kind: "book", values: [5, 5.02, 4.95, 4.98, 5.06, 5.01, 4.9, 4.97, 5.08, 5.12, 5.04, 5.1, 5.15, 5.11] },
      { name: "S2 twin", kind: "twin", values: [5, 4.97, 4.93, 4.96, 4.99, 4.92, 4.86, 4.9, 4.98, 5.03, 4.97, 5.01, 5.04, 5.02] },
      { name: "SOL hold", kind: "bench", values: [5, 5.05, 5.01, 5.08, 5.12, 5.04, 4.98, 5.06, 5.14, 5.2, 5.12, 5.18, 5.25, 5.24] }
    ] },
    { key: "A", label: "A · killed", note: "Killed in backtest (Nov 11). Chart is EXAMPLE DATA only, not a live book. No retunes this cycle.", series: [
      { name: "A Hour-drop", kind: "book", values: [5, 5.04, 4.99, 5.08, 5.02, 5.11, 5.06, 5.14, 5.09, 5.18, 5.12, 5.2, 5.16, 5.22] },
      { name: "A twin", kind: "twin", values: [5, 4.98, 5.01, 4.96, 5, 4.95, 4.99, 4.93, 4.97, 5.02, 4.98, 5.04, 5, 5.03] }
    ] },
    { key: "B", label: "B · killed", note: "Killed in backtest (Nov 11). Chart is EXAMPLE DATA only, not a live book. No retunes this cycle.", series: [
      { name: "B Funding", kind: "book", values: [5, 5, 5, 5.07, 5.07, 5.07, 5.04, 5.04, 5.11, 5.11, 5.11, 5.08, 5.08, 5.14] },
      { name: "B twin", kind: "twin", values: [5, 5, 5, 4.97, 4.97, 4.97, 5.01, 5.01, 4.99, 4.99, 4.99, 5.03, 5.03, 5.01] }
    ] },
    { key: "C", label: "C · killed", note: "Killed in backtest (Nov 11). Chart is EXAMPLE DATA only, not a live book. No retunes this cycle.", series: [
      { name: "C Calm-or-wild", kind: "book", values: [5, 5.03, 5.01, 5.06, 5.08, 5.04, 4.99, 5.05, 5.1, 5.13, 5.08, 5.12, 5.16, 5.15] },
      { name: "SOL hold (twin)", kind: "bench", values: [5, 5.05, 5.01, 5.08, 5.12, 5.04, 4.98, 5.06, 5.14, 5.2, 5.12, 5.18, 5.25, 5.24] }
    ] },
    { key: "D", label: "D · killed", note: "Killed in backtest (Nov 11). Chart is EXAMPLE DATA only, not a live book. No retunes this cycle.", series: [
      { name: "D Best-hours", kind: "book", values: [5, 5.03, 5.01, 5.05, 5.04, 5.08, 5.06, 5.1, 5.09, 5.13, 5.11, 5.15, 5.14, 5.17] },
      { name: "D twin", kind: "twin", values: [5, 5.01, 4.98, 5.02, 5, 5.03, 4.99, 5.02, 5.01, 5.04, 5.02, 5.05, 5.03, 5.06] }
    ] },
    { key: "DW", label: "DW · untested", note: "Busy S2 (exit variant), display only, untested. Trades/day is TBD until S2's prereg fixes the exit rule. No live curve yet.", series: [
      { name: "Busy S2 (exit variant)", kind: "book", values: [5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5] }
    ] },
    { key: "BH", label: "SOL hold", note: "Display-only benchmark. Dashed, never ranked.", series: [
      { name: "SOL hold", kind: "bench", values: [5, 5.05, 5.01, 5.08, 5.12, 5.04, 4.98, 5.06, 5.14, 5.2, 5.12, 5.18, 5.25, 5.24] }
    ] }
  ];
  var DUR = [
    { tag: "S2 v3", name: "Survivors redesign", mode: "redesign", status: "redesign after Nov 25, dates TBD", tone: "warn", note: "S2 v2 was ruled unworkable before start. S2 v3 is a redesign after Nov 25. Start, shakedown, check-ins, verdict and hard cap will appear once dates are locked. EXAMPLE / planned — not a live book.", dayLabel: "day — of —", pctLabel: "—%" },
    { tag: "DW", name: "Busy S2 (exit variant)", mode: "waiting", status: "waits for S2 v3", tone: "warn", note: "Display only, untested. No verdict and no hard cap. It waits for S2 v3, so dates stay TBD until that redesign is locked.", dayLabel: "day — of —", pctLabel: "—%" },
    { tag: "BH", name: "SOL buy-and-hold", mode: "display", status: "display only", tone: "accent", note: "Benchmark, not a tested book. No verdict date and no hard cap. Shown for scale only.", dayLabel: "no verdict", pctLabel: "display only" },
    { tag: "v6.5", name: "Frozen F / R / S", mode: "sealed", status: "sealed review Nov 25, 2026", tone: "accent", note: "Frozen, read-only. Sealed review on Nov 25, 2026. Nothing here reads the sealed books. Today is Nov 11, 2026 (America/Toronto).", dayLabel: "14 days to review", pctLabel: "sealed" },
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
    { id: "evolution", href: "#/evolution", label: "Evolution" },
    { id: "trades", href: "#/trades", label: "Trades" },
    { id: "archive", href: "#/archive", label: "Archive" },
    { id: "fresh", href: "#/freshness", label: "Freshness" }
  ];
  var TITLES = {
    desks: "Desks",
    winning: "Who's winning · EXAMPLE DATA",
    wallets: "Wallets",
    duration: "Duration · EXAMPLE DATA",
    evolution: "Evolution · EXAMPLE DATA",
    trades: "Ongoing trades · EXAMPLE DATA",
    archive: "Archive",
    fresh: "Freshness stamps · EXAMPLE DATA"
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
    var map = { desks: "desks", winning: "winning", wallets: "wallets", duration: "duration", evolution: "evolution", trades: "trades", archive: "archive", freshness: "fresh", fresh: "fresh" };
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
      ? line("95% interval " + signed(b.lo, 1) + " to " + signed(b.hi, 1) + "% / 30d · EXAMPLE DATA", null, " small muted")
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
      line("day 18 of ~70", "26% done · EXAMPLE", " small") +
      line("EXAMPLE anatomy, not a live book and not S2 v3 dates. Kill checks land when n hits 50 and 100 closes, not on fixed calendar days. If entry pace is below the floor, the bar flags at risk during days 8–21.", null, " small muted");
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
    return '<svg width="100%" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Example equity">' + grid + days + lines + "</svg>";
  }

  function screenWinning() {
    var books = state.phase === "run" ? RUN_BOOKS : NOW_BOOKS;
    var head = state.phase === "now"
      ? "Cycle 1 · only tested book: S2 Survivors vs its twin. Not started. A–D were killed in backtest (Nov 11); E is blocked for bad price data."
      : "Cycle 1 · EXAMPLE mid-run for S2 only (m=1). Until 150 closes it shows insufficient sample. Rank never promotes or kills a book.";
    var pills = '<div class="pills"><button type="button" data-act="phase" data-v="now" class="' + (state.phase === "now" ? "on" : "") + '">Now</button>' +
      '<button type="button" data-act="phase" data-v="run" class="' + (state.phase === "run" ? "on" : "") + '">Example mid-run</button></div>';
    var killed = KILLED.map(quietCard).join("");
    var display = '<p class="kicker">Display only · never ranked with the tested books</p>' +
      '<article class="card dim"><div class="rowtop"><span>' + tag("DW") + "Busy S2 (exit variant)</span>" + tag("display only, untested", "warn") + "</div>" +
      line("Owner pick, 11:48 AM EST Nov 11: one S2 exit rule, to be fixed in S2's prereg. Not a tested book, never ranked.", null, " small muted") +
      line("Trades / day", "TBD", " small") +
      line("Trades/day stays TBD until S2's prereg fixes it.", null, " small muted") +
      "</article>" +
      '<article class="card dim"><div class="rowtop"><span>SOL buy-and-hold</span>' + tag("benchmark", "accent") + "</div>" +
      line("Display-only benchmark, not a tested book", null, " small muted") +
      line("Trades / day", "0 (hold)", " small") +
      line("Example return", state.phase === "now" ? "no results yet" : "+4.8% (example)", " small muted") +
      "</article>";
    return pills +
      '<p class="warnline">S2 figures are EXAMPLE DATA · no wallet has started. A–D killed, E blocked, and cycle-2 Top winners killed pre-test are the real Nov 11 verdicts. Rank never promotes or kills a book.</p>' +
      line(esc(head), null, " small muted") +
      books.map(bookCard).join("") +
      '<p class="kicker">Killed in backtest · Nov 11 · no retunes this cycle</p>' + killed +
      '<p class="kicker">Blocked</p>' + quietCard(BLOCKED) +
      '<p class="kicker">Cycle 2 · own group, judged separately, shown on the same board</p>' + quietCard(CYCLE2) +
      display +
      '<p class="muted">Until S2 reaches its required n it shows insufficient sample. A top-3 luck badge would apply only among tested books that have reached n. Rank never promotes or kills. Required n and every figure here are examples.</p>';
  }

  function movement(w, view) {
    var series = w.series.length ? w.series : (w.equity != null ? [{ t: w.updatedAt, equity: w.equity }] : []);
    var lead = view.lead && view.lead.ids.indexOf(w.id) !== -1 ? line(esc(view.lead.label), null, " small") : "";
    var positions = w.positions.length ? w.positions.map(function (p) {
      var pnl = p.upnl != null ? signed(p.upnl, 2) + " SOL" : "—";
      var pct = p.upnlPct != null ? " · " + signed(p.upnlPct, 1) + "%" : "";
      return '<div class="trade"><div class="rowtop"><strong>' + esc(p.symbol) + "</strong><span class=\"" + cls(p.upnl != null ? p.upnl : p.upnlPct) + "\">" + esc(pnl + pct) + "</span></div>" +
        line("Size " + (p.size == null ? "—" : p.size.toFixed(2) + " " + p.sizeUnit), "unrealized", " small muted") +
        line("Entry " + px(p.entry), "Mark " + px(p.mark), " small") +
        "</div>";
    }).join("") : '<p class="muted">No open positions.</p>';
    var trades = w.trades.length ? w.trades.map(function (t) {
      var pnl = t.pnl == null ? "—" : signed(t.pnl, 2) + " SOL";
      return '<div class="trade"><div class="rowtop"><span>' + esc(fmtEt(t.t)) + "</span><span class=\"" + (t.side === "sell" ? "dn" : "up") + "\">" + esc(t.side.toUpperCase()) + " " + esc(t.symbol) + "</span></div>" +
        line("Fill " + px(t.price), "Size " + (t.size == null ? "—" : t.size.toFixed(2) + " " + t.sizeUnit), " small") +
        line("P&amp;L", '<span class="' + cls(t.pnl) + '">' + esc(pnl) + "</span>", " small") +
        "</div>";
    }).join("") : '<p class="muted">No buys or sells yet.</p>';
    return '<article class="card"><div class="rowtop"><span><h3>' + tag(w.id, "accent") + esc(w.name) + "</h3></span>" + tag(w.status || "starting", w.status === "running" ? "good" : "warn") + "</div>" +
      lead +
      line("Last updated", esc(w.updatedAt ? fmtEt(w.updatedAt) : "—"), " small") +
      line("Run status", esc(w.status || "starting"), " small") +
      line("Equity", esc(level(w.equity, w.currency)), " small") +
      equitySvg(series) +
      '<p class="kicker">Open positions</p>' + positions +
      '<p class="kicker">Trades</p>' + trades +
      "</article>";
  }
  function raceBlock(view, focus) {
    var note = view.source === "fixtures"
      ? '<p class="warnline">EXAMPLE race file (?fixtures=1). Not a live race.</p>'
      : "";
    var lead = view.lead ? line(esc(view.lead.label), null, " small") : line(esc(view.message || R.MESSAGE), null, " small");
    var buttons = view.wallets.map(function (w) {
      var on = focus === w.id ? " on" : "";
      var right = w.equity == null ? esc(w.status || "starting") : esc(level(w.equity, w.currency));
      return '<a class="wallet' + on + '" href="#/wallets/' + esc(w.id) + '"><div class="rowtop"><span>' + tag(w.id, w.role === "benchmark" ? "accent" : "") + esc(w.name) + "</span>" + tag(w.role === "benchmark" ? "benchmark" : (w.status || "starting"), w.role === "benchmark" ? "accent" : "warn") + "</div>" +
        line(right, null, " small muted") + "</a>";
    }).join("");
    var detail = "";
    if (focus) {
      var picked = null;
      view.wallets.forEach(function (w) { if (w.id === focus) picked = w; });
      detail = picked ? movement(picked, view) : '<article class="card"><p>That wallet is not on the race list.</p></article>';
    }
    return note + '<p class="banner">' + esc(view.banner) + "</p>" + lead +
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
      : '<p class="warnline">EXAMPLE DATA · no planned wallet has started. A–D killed, E blocked, and cycle-2 Top winners killed pre-test are the real Nov 11 verdicts.</p>';
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
        line("Once a book has locked dates, its bar shows all of the marks below. This sample is EXAMPLE only — it is not S2 v3 and uses no calendar dates.", null, " small muted") +
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
        line("Shared scale: today (Nov 11, 2026, America/Toronto) to Nov 25, then an after zone for dates that are still TBD. EXAMPLE / planned, not live.", null, " small muted");
    }
    return '<p class="warnline">EXAMPLE DATA · planned dates only · no wallet has started. S2 v2 was ruled unworkable before start; the old Nov 12–Jan 8 bar is not shown.</p>' +
      pills + body +
      '<p class="muted">Killed or blocked books from cycle 1 and cycle 2 (A–E, Top winners) have no forward duration, so they are omitted here. All figures on this screen are examples or planned marks, not live results.</p>';
  }

  function screenEvolution() {
    var cur = EVO.filter(function (v) { return v.key === state.evo; })[0] || EVO[0];
    var color = function (k) { return k === "book" ? "#36d6ff" : k === "twin" ? "#B5E853" : "#848E9C"; };
    var pills = '<div class="pills">' + EVO.map(function (v) {
      return '<button type="button" data-act="evo" data-v="' + esc(v.key) + '" class="' + (state.evo === v.key ? "on" : "") + '">' + esc(v.label) + "</button>";
    }).join("") + "</div>";
    var legend = '<div class="line small secondary">' + cur.series.map(function (s) {
      return '<span><i style="display:inline-block;width:10px;height:3px;background:' + color(s.kind) + ';margin-right:4px;vertical-align:middle"></i>' + esc(s.name) + "</span>";
    }).join("") + "</div>";
    return '<p class="warnline">EXAMPLE DATA · made-up equity curves · no wallet has started</p>' +
      pills + legend + evoSvg(cur.series) +
      line("Equity in SOL, starting at 5 SOL each · EXAMPLE DATA", null, " small muted") +
      line(esc(cur.note), null, " small muted");
  }

  function screenTrades() {
    var held = 47, holdMax = 120, pct = Math.min(100, (held / holdMax) * 100);
    return '<p class="warnline">EXAMPLE DATA · no wallet has started · placeholder tokens and links</p>' +
      '<article class="card"><div class="pos"><div class="logo-dot">EX</div><div class="grow"><div class="rowtop"><strong>EXCAT</strong><span class="up">+18.4%</span></div>' +
      line("Example Cat", "unrealized", " small muted") +
      "</div></div><div style=\"margin-top:6px\">" + tag("S2", "accent") + "</div>" +
      line("Entry", "10:02 AM EST · 0.0000412 SOL", " small") +
      '<div class="bar"><i class="' + (pct > 80 ? "hot" : "") + '" style="width:' + pct + '%"></i></div>' +
      line("Held 47 of 120 min", "73 min left", " small muted") +
      line("EXAMPLE DATA. S2 has not started; this is how an open paper position would look.", null, " small muted") +
      line("+40% take-profit", "not hit", " small") +
      line("+100% take-profit", "not hit", " small") +
      line("25% trail", "peak +22.0%, exits below -8.5%", " small") +
      line("Stop", "-25% armed", " small") +
      '<p><a class="link" href="https://dexscreener.com/solana" target="_blank" rel="noreferrer">Open on DexScreener: dexscreener.com/solana/&lt;pair or mint&gt; (placeholder)</a></p>' +
      "</article>" +
      '<p class="muted">Logo circle shows the DexScreener token image when one exists; otherwise initials, as here.</p>';
  }

  function screenArchive() {
    return '<article class="card dim"><div class="rowtop"><span><h3>v6.5 F / R / S</h3></span><span>' + tag("RETIRED") + tag("STOP · final", "bad") + "</span></div>" +
      line("Day-14 verdict, Nov 11, 2026 · real figures", null, " small muted") +
      line("F filtered: closes", "118") +
      line("F total P&amp;L", "-11.64 SOL") +
      line("F expectancy per 0.5 SOL trade", "-0.197 SOL") +
      '<div style="margin-top:8px;border-top:1px solid #2B3139;padding-top:6px">' +
      line("Per trade after costs, as of Nov 4 (real, earlier read)", null, " small muted") +
      line("F filtered, 62 closes", "-30.7%", " small") +
      line("R random twin", "-28.8%", " small") +
      line("S rejected-only", "-32.5%", " small") +
      "</div>" +
      line("Frozen, read-only. This card repeats the published verdict. It does not recompute the sealed test.", null, " small muted") +
      '<p><a class="link" href="legacy/index.html#/solana">Open the frozen v6.5 view</a></p>' +
      "</article>" +
      '<article class="card dim"><div class="rowtop"><span><h3>Trend-SOL long/flat</h3></span><span>' + tag("KILLED", "bad") + tag("by Step 0") + "</span></div>" +
      line("Killed before launch by the Step 0 test; Challenger confirmed the kill", null, " small muted") +
      line("Never traded, never a display wallet", null, " small muted") +
      "</article>";
  }

  function screenFresh() {
    var rows = [
      ["Solana desk", "updated 4 min ago", "good", "fresh: under 60 min (2 × 30-min scan)"],
      ["Solana desk", "updated 74 min ago", "warn", "stale: missed 2 scans"],
      ["NFT (KING)", "updated 38 min ago", "good", "fresh: under 2 h (2 × hourly)"],
      ["Cardano (Snek)", "paused · last update 6 days ago", "", "paused desks never claim fresh"]
    ];
    return '<p class="warnline">EXAMPLE DATA · these four rows show the stamp rule. The desk screen uses the run file when one is present.</p>' +
      rows.map(function (r) {
        return '<div class="trade">' + line(esc(r[0]), tag(r[1], r[2])) + line(esc(r[3]), null, " small muted") + "</div>";
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

  function screenDesks(stamps, view) {
    var sol = stamps.sol, nft = stamps.nft, ada = stamps.ada;
    var raceLine = view.message
      ? esc(view.message)
      : (view.lead ? esc(view.lead.label) : esc(R.MESSAGE));
    return '<article class="card"><div class="rowtop"><span><h3>1. Solana desk</h3></span>' + tag("Cycle 1 setting up", "accent") + "</div>" +
      line("Only tested book: S2 vs twin, not started (gate and owner's yes still missing)", null, " small muted") +
      line("A–D killed in backtest (Nov 11); E blocked: bad price data", null, " small muted") +
      line("Display: SOL hold plus Busy S2 (exit variant), untested, trades/day TBD; cycle 2 Top winners killed pre-test", null, " small muted") +
      line("v6.5 F/R/S archived: STOP; Trend-SOL killed", null, " small muted") +
      line("Scan every 30 min", null, " small muted") +
      '<div style="margin-top:6px">' + tag(sol.text, sol.tone) + "</div></article>" +
      '<article class="card"><p class="banner">' + esc(view.banner) + "</p>" +
      line("Race wallets R1–R5, plus the SOL-hold benchmark", null, " small") +
      line(raceLine, null, " small") +
      '<p><a class="link" href="#/wallets">Open the wallet movement</a></p></article>' +
      '<article class="card"><div class="rowtop"><span><h3>2. NFT (KING)</h3></span>' + tag("Pilot running", "accent") + "</div>" +
      line("Ethereum + Base lane", null, " small muted") +
      line("Robinhood Chain focus", null, " small muted") +
      line("Snapshot hourly", null, " small muted") +
      '<div style="margin-top:6px">' + tag(nft.text, nft.tone) + "</div></article>" +
      '<article class="card dim"><div class="rowtop"><span><h3>3. Cardano (Snek)</h3></span>' + tag("Paused") + "</div>" +
      line("No runs while paused", null, " small muted") +
      line("Restart plan on SPD-121", null, " small muted") +
      '<div style="margin-top:6px">' + tag(ada.text, ada.tone) + "</div></article>" +
      '<p class="muted">Stamps replace a Live label. A desk turns amber when its last update is older than twice its normal cadence. A paused desk never claims fresh.</p>';
  }

  function draw(id, pack) {
    if (id === "desks") return screenDesks(pack.stamps, pack.race);
    if (id === "winning") return screenWinning();
    if (id === "wallets") return screenWallets(pack.race);
    if (id === "duration") return screenDuration();
    if (id === "evolution") return screenEvolution();
    if (id === "trades") return screenTrades();
    if (id === "archive") return screenArchive();
    return screenFresh();
  }

  function paintNav(id) {
    var nav = document.getElementById("nav");
    nav.innerHTML = NAV.map(function (item) {
      var on = item.id === id ? ' class="on" aria-current="page"' : "";
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

  var token = 0;
  function render() {
    var r = route();
    paintNav(r.id);
    var view = document.getElementById("view");
    var mine = ++token;
    var needRace = r.id === "desks" || r.id === "wallets";
    var needStamps = r.id === "desks";
    view.setAttribute("aria-busy", "true");
    Promise.all([
      needRace ? ensureRace() : Promise.resolve(null),
      needStamps ? ensureStamps() : Promise.resolve(null)
    ]).then(function (pair) {
      if (mine !== token) return;
      view.innerHTML = draw(r.id, { race: pair[0], stamps: pair[1] });
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
