/* Race wallet adapter. Display only.
   Reads the races-data branch from the browser. No secrets, no new host.
   File names and field names live in FILES and FIELDS so the race PR can align them.
   There is no events.jsonl.

   Canonical files on branch races-data (raw GitHub), tried again under races/ if missing:
     summary.json
     equity.jsonl
     positions.json
     trades/{id}.jsonl          one append-only file per wallet

   summary.json leaderboard row (official read; the page does not rank on equity):
     id, name, role, status, net_pct, n, interval_lo, interval_hi,
     avg_deployed_pct, equity_sol, last_run_ts
   R5 is "pending lock" until summary.json lists it, and then may add
     meme_n, non_meme_n, overlap_k, overlap_n
   (overlap is "R5 holdings also in R3: k of n").

   equity.jsonl:   ts, wallet, equity_sol, net_pct
   trades/{id}.jsonl: ts, wallet, action, symbol, mint, fill_price, size_sol, cost_bps, pnl, exit_reason
   positions.json: a list, or { "positions": [ ... ] }, same wallet fields as a trade plus entry, mark, upnl, upnl_pct

   Every wallet starts at 5.0 SOL. That is the label, not a made-up current balance.
   A lead uses the official net_pct only. The benchmark is not a lead.
   At or below 0.00% the label adds "(not ahead of SOL hold)".
*/
(function (root) {
  var BANNER = "Display only, untested. A lead in the race is luck until tested.";
  var MESSAGE = "race starting soon";
  var PENDING = "pending lock";
  var START_EQUITY = 5.0;
  var RAW = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/races-data/";
  var FILES = {
    summary: "summary.json",
    equity: "equity.jsonl",
    positions: "positions.json",
    trades: "trades/{id}.jsonl"
  };
  var WALLET_IDS = ["R1", "R2", "R3", "R4", "R5", "SOL"];
  var FIELDS = {
    id: ["id", "wallet", "wallet_id", "tag", "book"],
    name: ["name", "label", "title"],
    role: ["role", "kind"],
    status: ["status", "run_status", "state"],
    updated: ["last_run_ts", "updated_at", "updated", "asof", "ts", "t", "time", "at"],
    netPct: ["net_pct", "net_percent", "official_net_pct", "net"],
    n: ["n", "sample_n", "closes"],
    lo: ["interval_lo", "ci_lo", "lo"],
    hi: ["interval_hi", "ci_hi", "hi"],
    avgDeployed: ["avg_deployed_pct", "avg_pct_deployed", "deployed_pct"],
    equity: ["equity_sol", "equity", "balance", "nav"],
    action: ["action", "side"],
    symbol: ["symbol", "sym", "ticker", "token"],
    mint: ["mint", "token_mint"],
    price: ["fill_price", "price", "fill", "px", "quote_px"],
    size: ["size_sol", "size", "qty", "quantity", "amount"],
    costBps: ["cost_bps", "cost", "fee_bps"],
    pnl: ["pnl", "pnl_sol", "realized", "realized_pnl"],
    exitReason: ["exit_reason", "reason", "exit"],
    entry: ["entry", "entry_px", "entry_price"],
    mark: ["mark", "mark_px", "last", "last_mark"],
    upnl: ["upnl", "unrealized", "unrealized_pnl", "u_pnl"],
    upnlPct: ["upnl_pct", "unrealized_pct", "u_pnl_pct"],
    closed: ["closed", "flat"],
    memeN: ["meme_n", "meme", "meme_count"],
    nonMemeN: ["non_meme_n", "non_meme", "other_n"],
    overlapK: ["overlap_k", "r3_overlap_k", "also_in_r3"],
    overlapN: ["overlap_n", "r3_overlap_n"]
  };
  var DEFAULTS = [
    { id: "R1", name: "Race wallet 1", role: "race" },
    { id: "R2", name: "Race wallet 2", role: "race" },
    { id: "R3", name: "Race wallet 3", role: "race" },
    { id: "R4", name: "Race wallet 4", role: "race" },
    { id: "R5", name: "Race wallet 5", role: "race" },
    { id: "SOL", name: "SOL buy-and-hold", role: "benchmark" }
  ];

  function pick(obj, keys) {
    if (!obj || typeof obj !== "object") return undefined;
    for (var i = 0; i < keys.length; i++) {
      var v = obj[keys[i]];
      if (v != null && v !== "") return v;
    }
    return undefined;
  }
  function num(v) {
    if (typeof v === "number") return isFinite(v) ? v : null;
    if (typeof v === "string" && v.trim() !== "" && isFinite(Number(v))) return Number(v);
    return null;
  }
  function looksLikeAddress(s) {
    return /^(?:[1-9A-HJ-NP-Za-km-z]{32,}|0x[0-9a-fA-F]{8,}|addr1|stake1)/.test(String(s || ""));
  }
  function canonId(raw) {
    var s = String(raw == null ? "" : raw).trim().toUpperCase().replace(/[\s_]+/g, "-");
    if (!s || looksLikeAddress(s)) return "";
    if (s === "BH" || s === "SOL-HOLD" || s === "SOLHOLD" || s === "SOL-BUY-AND-HOLD") return "SOL";
    if (/^R[1-5]$/.test(s) || s === "SOL") return s;
    if (s.length > 16) return "";
    return s;
  }
  function cleanSymbol(raw) {
    var s = String(raw == null ? "" : raw).trim();
    if (!s || looksLikeAddress(s) || s.length > 16) return "";
    return s.replace(/[^A-Za-z0-9._-]/g, "").slice(0, 16);
  }
  function pointTime(row) {
    var raw = pick(row, FIELDS.updated);
    if (raw == null) return null;
    var t = Date.parse(String(raw));
    return isFinite(t) ? new Date(t).toISOString() : null;
  }
  function pctText(n) {
    if (n == null || !isFinite(n)) return "—";
    return (n > 0 ? "+" : "") + n.toFixed(2) + "%";
  }
  function blank(meta) {
    var pending = meta.id === "R5";
    return {
      id: meta.id,
      name: meta.name,
      role: meta.role,
      listed: false,
      pendingLock: pending,
      status: pending ? PENDING : "starting",
      updatedAt: null,
      netPct: null,
      n: null,
      lo: null,
      hi: null,
      avgDeployed: null,
      equity: null,
      startEquity: START_EQUITY,
      currency: "SOL",
      series: [],
      trades: [],
      positions: [],
      memeN: null,
      nonMemeN: null,
      overlapK: null,
      overlapN: null
    };
  }
  function emptyView(reason) {
    return {
      empty: true,
      reason: reason || "missing",
      source: "none",
      status: "starting",
      updatedAt: null,
      message: MESSAGE,
      banner: BANNER,
      lead: null,
      startEquity: START_EQUITY,
      wallets: DEFAULTS.map(blank)
    };
  }
  function walletList(summary) {
    if (!summary) return [];
    if (Array.isArray(summary)) return summary;
    return summary.wallets || summary.books || summary.racers || [];
  }
  function parseLines(text) {
    var rows = [];
    String(text || "").split(/\r?\n/).forEach(function (line) {
      var s = line.trim();
      if (!s || s.charAt(0) === "#") return;
      try {
        var row = JSON.parse(s);
        if (row && typeof row === "object") rows.push(row);
      } catch (e) { /* a torn append is skipped */ }
    });
    return rows;
  }
  function positionRows(positions) {
    if (!positions) return [];
    if (typeof positions === "string") {
      var trimmed = positions.trim();
      if (!trimmed) return [];
      if (trimmed.charAt(0) === "{" || trimmed.charAt(0) === "[") {
        try {
          return positionRows(JSON.parse(trimmed));
        } catch (e) {
          return parseLines(positions);
        }
      }
      return parseLines(positions);
    }
    if (Array.isArray(positions)) return positions;
    return positions.positions || positions.open || [];
  }
  function leadOf(wallets) {
    var racers = wallets.filter(function (w) {
      return w.role === "race" && w.listed && !w.pendingLock && w.netPct != null && isFinite(w.netPct);
    });
    if (!racers.length) return null;
    var max = racers.reduce(function (m, w) { return w.netPct > m ? w.netPct : m; }, -Infinity);
    var top = racers.filter(function (w) { return w.netPct === max; });
    var ids = top.map(function (w) { return w.id; });
    var head = ids.length === 1 ? ids[0] + " is leading so far" : ids.join(" and ") + " are level, leading so far";
    if (max <= 0) head += " (not ahead of SOL hold)";
    var sample = top[0];
    var extra = "";
    if (sample.n != null) extra += " · n=" + sample.n;
    if (sample.lo != null && sample.hi != null) extra += " · 95% interval " + pctText(sample.lo) + " to " + pctText(sample.hi);
    return { ids: ids, netPct: max, n: sample.n, lo: sample.lo, hi: sample.hi, label: head + extra };
  }
  function applySummary(byId, summary) {
    walletList(summary).forEach(function (row) {
      var id = canonId(pick(row, FIELDS.id));
      if (!id) return;
      if (!byId[id]) {
        var role0 = String(pick(row, FIELDS.role) || "race").toLowerCase();
        byId[id] = blank({ id: id, name: id, role: role0 === "benchmark" ? "benchmark" : "race" });
      }
      var w = byId[id];
      w.listed = true;
      w.pendingLock = false;
      var name = pick(row, FIELDS.name);
      if (name && !looksLikeAddress(name)) w.name = String(name).slice(0, 48);
      var role = String(pick(row, FIELDS.role) || "").toLowerCase();
      if (role === "benchmark" || role === "race") w.role = role;
      if (id === "SOL") w.role = "benchmark";
      var status = pick(row, FIELDS.status);
      w.status = status ? String(status).toLowerCase() : "running";
      var updated = pointTime(row);
      if (updated) w.updatedAt = updated;
      var net = num(pick(row, FIELDS.netPct));
      if (net != null) w.netPct = net;
      var n = num(pick(row, FIELDS.n));
      if (n != null) w.n = n;
      var lo = num(pick(row, FIELDS.lo));
      var hi = num(pick(row, FIELDS.hi));
      if (lo != null) w.lo = lo;
      if (hi != null) w.hi = hi;
      var deployed = num(pick(row, FIELDS.avgDeployed));
      if (deployed != null) w.avgDeployed = deployed;
      var eq = num(pick(row, FIELDS.equity));
      if (eq != null) w.equity = eq;
      var meme = num(pick(row, FIELDS.memeN));
      var other = num(pick(row, FIELDS.nonMemeN));
      var k = num(pick(row, FIELDS.overlapK));
      var kn = num(pick(row, FIELDS.overlapN));
      if (meme != null) w.memeN = meme;
      if (other != null) w.nonMemeN = other;
      if (k != null) w.overlapK = k;
      if (kn != null) w.overlapN = kn;
    });
  }
  function buildView(summary, extra, opts) {
    var opt = opts || {};
    var pack = extra || {};
    var byId = {};
    DEFAULTS.forEach(function (d) { byId[d.id] = blank(d); });
    var runStatus = String(pick(summary, ["status", "run_status", "state"]) || "").toLowerCase();
    var runUpdated = pointTime(summary || {});
    if (summary) applySummary(byId, summary);

    parseLines(pack.equity || "").forEach(function (row, idx) {
      var id = canonId(pick(row, FIELDS.id));
      if (!id || !byId[id] || byId[id].pendingLock) return;
      var eq = num(pick(row, FIELDS.equity));
      if (eq == null) return;
      byId[id].series.push({ t: pointTime(row), equity: eq, netPct: num(pick(row, FIELDS.netPct)), i: idx });
    });

    function takeTrades(text) {
      parseLines(text).forEach(function (row, idx) {
        var id = canonId(pick(row, FIELDS.id));
        if (!id || !byId[id] || byId[id].pendingLock) return;
        var sym = cleanSymbol(pick(row, FIELDS.symbol));
        if (!sym) return;
        var action = String(pick(row, FIELDS.action) || "buy").toLowerCase();
        if (action !== "buy" && action !== "sell") action = "buy";
        byId[id].trades.push({
          t: pointTime(row),
          i: idx,
          side: action,
          symbol: sym,
          mint: pick(row, FIELDS.mint) && !looksLikeAddress(pick(row, FIELDS.mint)) ? String(pick(row, FIELDS.mint)).slice(0, 64) : "",
          price: num(pick(row, FIELDS.price)),
          size: num(pick(row, FIELDS.size)),
          sizeUnit: "SOL",
          costBps: num(pick(row, FIELDS.costBps)),
          pnl: num(pick(row, FIELDS.pnl)),
          exitReason: pick(row, FIELDS.exitReason) ? String(pick(row, FIELDS.exitReason)).slice(0, 48) : ""
        });
      });
    }
    if (pack.trades && typeof pack.trades === "object" && !Array.isArray(pack.trades)) {
      Object.keys(pack.trades).forEach(function (id) { takeTrades(pack.trades[id]); });
    } else {
      takeTrades(pack.trades || "");
    }

    positionRows(pack.positions).forEach(function (row, idx) {
      var id = canonId(pick(row, FIELDS.id));
      if (!id || !byId[id] || byId[id].pendingLock) return;
      var sym = cleanSymbol(pick(row, FIELDS.symbol));
      if (!sym) return;
      var entry = num(pick(row, FIELDS.entry));
      var mark = num(pick(row, FIELDS.mark));
      var upct = num(pick(row, FIELDS.upnlPct));
      if (upct == null && entry != null && mark != null && entry !== 0) upct = (mark - entry) / entry * 100;
      byId[id].positions.push({
        t: pointTime(row),
        i: idx,
        symbol: sym,
        size: num(pick(row, FIELDS.size)),
        sizeUnit: "SOL",
        entry: entry,
        mark: mark,
        upnl: num(pick(row, FIELDS.upnl)),
        upnlPct: upct,
        closed: pick(row, FIELDS.closed) === true || num(pick(row, FIELDS.size)) === 0
      });
    });

    var wallets = WALLET_IDS.map(function (id) { return byId[id]; }).filter(Boolean);
    Object.keys(byId).forEach(function (id) {
      if (WALLET_IDS.indexOf(id) === -1) wallets.push(byId[id]);
    });
    wallets.forEach(function (w) {
      w.series.sort(function (a, b) {
        if (a.t && b.t && a.t !== b.t) return a.t < b.t ? -1 : 1;
        return a.i - b.i;
      });
      if (!w.pendingLock && w.series.length) w.equity = w.series[w.series.length - 1].equity;
      w.trades.sort(function (a, b) {
        if (a.t && b.t && a.t !== b.t) return a.t < b.t ? 1 : -1;
        return b.i - a.i;
      });
      var open = {};
      w.positions.slice().sort(function (a, b) {
        if (a.t && b.t && a.t !== b.t) return a.t < b.t ? -1 : 1;
        return a.i - b.i;
      }).forEach(function (p) {
        if (p.closed) delete open[p.symbol];
        else open[p.symbol] = p;
      });
      w.positions = Object.keys(open).map(function (k) { return open[k]; });
      if (!w.updatedAt) {
        var marks = w.series.map(function (p) { return p.t; }).concat(w.trades.map(function (t) { return t.t; })).filter(Boolean).sort();
        if (marks.length) w.updatedAt = marks[marks.length - 1];
      }
    });

    var anyListed = wallets.some(function (w) { return w.listed; });
    var lead = anyListed ? leadOf(wallets) : null;
    return {
      empty: !summary,
      reason: summary ? "" : "missing",
      source: opt.source || "races-data",
      status: runStatus || (anyListed ? "running" : "starting"),
      updatedAt: runUpdated,
      message: summary ? "" : MESSAGE,
      banner: BANNER,
      lead: lead,
      startEquity: START_EQUITY,
      wallets: wallets
    };
  }

  function getText(fetchFn, url) {
    return Promise.resolve(fetchFn(url)).then(function (res) {
      if (!res || !res.ok) return null;
      return res.text();
    }).catch(function () { return null; });
  }
  function loadBundle(fetchFn, prefix) {
    return Promise.all([
      getText(fetchFn, prefix + FILES.summary),
      getText(fetchFn, prefix + FILES.equity),
      getText(fetchFn, prefix + FILES.positions),
      Promise.all(WALLET_IDS.map(function (id) {
        var path = FILES.trades.replace("{id}", id);
        return getText(fetchFn, prefix + path).then(function (text) {
          var pair = {};
          pair[id] = text || "";
          return pair;
        });
      }))
    ]).then(function (parts) {
      var trades = {};
      parts[3].forEach(function (pair) {
        Object.keys(pair).forEach(function (id) { trades[id] = pair[id]; });
      });
      return { summaryText: parts[0], equity: parts[1] || "", positions: parts[2] || "", trades: trades };
    });
  }
  function fromBundle(bundle, source) {
    if (bundle.summaryText == null) return null;
    try {
      return buildView(JSON.parse(bundle.summaryText), bundle, { source: source });
    } catch (e) {
      return emptyView("bad-json");
    }
  }
  function load(fetchFn, opts) {
    var opt = opts || {};
    if (opt.fixtures) {
      return loadBundle(fetchFn, "revamp/fixtures/").then(function (bundle) {
        return fromBundle(bundle, "fixtures") || emptyView("fixtures-missing");
      });
    }
    var prefixes = [RAW, RAW + "races/"];
    function step(i) {
      if (i >= prefixes.length) return Promise.resolve(emptyView("missing"));
      return loadBundle(fetchFn, prefixes[i]).then(function (bundle) {
        var view = fromBundle(bundle, "races-data");
        if (view && view.reason !== "bad-json" && bundle.summaryText != null) return view;
        if (view && view.reason === "bad-json") return view;
        return step(i + 1);
      });
    }
    return step(0);
  }

  root.SightlineRaces = {
    BANNER: BANNER,
    MESSAGE: MESSAGE,
    PENDING: PENDING,
    START_EQUITY: START_EQUITY,
    FILES: FILES,
    FIELDS: FIELDS,
    RAW: RAW,
    canonId: canonId,
    buildView: buildView,
    emptyView: emptyView,
    load: load
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
