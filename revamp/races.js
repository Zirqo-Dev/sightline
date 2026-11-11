/* Race wallet adapter. Display only.
   Reads the races-data branch from the browser. No secrets, no new host.
   Field names are listed once in FIELDS so a later race PR can align them.

   Canonical files on branch races-data (raw GitHub):
     summary.json
     events.jsonl
   If those 404, the same names under races/ are tried once.
   A missing branch or a missing file becomes an empty "race starting soon" view.

   summary.json
     {
       "schema": "sightline.races.summary.v1",
       "updated_at": "ISO-8601",
       "status": "starting | running | paused | stopped",
       "wallets": [
         {
           "id": "R1 | R2 | R3 | R4 | R5 | SOL",
           "name": "short label",
           "role": "race | benchmark",
           "status": "starting | running | paused | stopped",
           "updated_at": "ISO-8601",
           "equity": 5.0,
           "currency": "SOL",
           "equity_curve": [ { "t": "ISO-8601", "equity": 5.0 } ]
         }
       ]
     }
   SOL is the buy-and-hold benchmark. BH and SOL-hold map to SOL.
   An id that looks like an address is dropped and never drawn.

   events.jsonl — one JSON object per line, append-only:
     { "type": "equity", "wallet": "R1", "t": "ISO-8601", "equity": 5.02 }
     { "type": "trade", "wallet": "R1", "t": "ISO-8601", "side": "buy|sell",
       "symbol": "EXCAT", "price": 0.0000412, "size": 0.5, "size_unit": "SOL", "pnl": null }
     { "type": "position", "wallet": "R1", "t": "ISO-8601", "symbol": "EXCAT",
       "size": 0.5, "size_unit": "SOL", "entry": 0.0000412, "mark": 0.0000446,
       "upnl": 0.04, "upnl_pct": 8.2 }
   The latest position row per symbol is the open book. size 0, or closed true, drops it.
   upnl_pct is shown when present. Otherwise it is (mark - entry) / entry when both
   prices exist. A SOL unrealized figure is never invented from prices.
   pnl on a trade is realized SOL for that fill, when the file has it.
*/
(function (root) {
  var BANNER = "Display only, untested. A lead in the race is luck until tested.";
  var MESSAGE = "race starting soon";
  var RAW = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/races-data/";
  var FIELDS = {
    id: ["id", "wallet", "wallet_id", "tag", "book"],
    name: ["name", "label", "title"],
    role: ["role", "kind"],
    status: ["status", "run_status", "state"],
    updated: ["updated_at", "updated", "asof", "t", "time", "ts", "at"],
    equity: ["equity", "balance", "nav"],
    currency: ["currency", "unit", "ccy"],
    curve: ["equity_curve", "curve", "series", "equity_series"],
    type: ["type", "event"],
    side: ["side", "action"],
    symbol: ["symbol", "sym", "ticker", "token"],
    price: ["price", "fill_price", "fill", "px", "quote_px"],
    size: ["size", "qty", "quantity", "amount", "base_size"],
    sizeUnit: ["size_unit", "size_ccy", "qty_unit"],
    pnl: ["pnl", "pnl_sol", "realized", "realized_pnl"],
    upnl: ["upnl", "unrealized", "unrealized_pnl", "u_pnl"],
    upnlPct: ["upnl_pct", "unrealized_pct", "u_pnl_pct"],
    entry: ["entry", "entry_px", "entry_price"],
    mark: ["mark", "mark_px", "last", "last_mark"],
    closed: ["closed", "flat"]
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

  function blank(meta) {
    return {
      id: meta.id,
      name: meta.name,
      role: meta.role,
      status: "starting",
      updatedAt: null,
      equity: null,
      currency: "SOL",
      series: [],
      trades: [],
      positions: []
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
      wallets: DEFAULTS.map(blank)
    };
  }

  function walletList(summary) {
    if (!summary) return [];
    if (Array.isArray(summary)) return summary;
    return summary.wallets || summary.books || summary.racers || [];
  }

  function pointTime(row, fallback) {
    var raw = pick(row, FIELDS.updated);
    if (raw == null) return fallback || null;
    var t = Date.parse(String(raw));
    return isFinite(t) ? new Date(t).toISOString() : null;
  }

  function seriesFrom(value) {
    var out = [];
    if (!Array.isArray(value)) return out;
    value.forEach(function (row, i) {
      if (typeof row === "number") {
        if (isFinite(row)) out.push({ t: null, equity: row, i: i });
        return;
      }
      var eq = num(pick(row, FIELDS.equity));
      if (eq == null) return;
      out.push({ t: pointTime(row), equity: eq, i: i });
    });
    return out;
  }

  function eventKind(row) {
    var t = String(pick(row, FIELDS.type) || "").toLowerCase();
    var side = String(pick(row, FIELDS.side) || "").toLowerCase();
    if (t === "trade" || t === "fill" || t === "buy" || t === "sell" || side === "buy" || side === "sell") return "trade";
    if (t === "position" || t === "open" || t === "holding") return "position";
    if (t === "equity" || t === "nav" || t === "balance" || t === "mark") return "equity";
    if (pick(row, FIELDS.equity) != null && pick(row, FIELDS.symbol) == null && !side) return "equity";
    if (pick(row, FIELDS.symbol) != null && !side && (pick(row, FIELDS.upnl) != null || pick(row, FIELDS.entry) != null)) return "position";
    return "";
  }

  function parseEvents(text) {
    var rows = [];
    String(text || "").split(/\r?\n/).forEach(function (line) {
      var s = line.trim();
      if (!s || s.charAt(0) === "#") return;
      try {
        var row = JSON.parse(s);
        if (row && typeof row === "object") rows.push(row);
      } catch (e) { /* skip a torn line; the file is append-only */ }
    });
    return rows;
  }

  function leadOf(wallets) {
    var racers = wallets.filter(function (w) {
      return w.role === "race" && w.equity != null && isFinite(w.equity);
    });
    if (!racers.length) return null;
    var max = racers.reduce(function (m, w) { return w.equity > m ? w.equity : m; }, -Infinity);
    var top = racers.filter(function (w) { return w.equity === max; });
    var ids = top.map(function (w) { return w.id; });
    return {
      ids: ids,
      equity: max,
      label: ids.length === 1 ? ids[0] + " is leading so far" : ids.join(" and ") + " are level, leading so far"
    };
  }

  function buildView(summary, eventsText, opts) {
    var opt = opts || {};
    var byId = {};
    DEFAULTS.forEach(function (d) { byId[d.id] = blank(d); });
    var runStatus = String(pick(summary, ["status", "run_status", "state"]) || "running").toLowerCase();
    var runUpdated = pointTime(summary || {});

    walletList(summary).forEach(function (row) {
      var id = canonId(pick(row, FIELDS.id));
      if (!id) return;
      if (!byId[id]) {
        var role = String(pick(row, FIELDS.role) || "race").toLowerCase();
        byId[id] = blank({ id: id, name: id, role: role === "benchmark" ? "benchmark" : "race" });
      }
      var w = byId[id];
      var name = pick(row, FIELDS.name);
      if (name && !looksLikeAddress(name)) w.name = String(name).slice(0, 48);
      var role = String(pick(row, FIELDS.role) || "").toLowerCase();
      if (role === "benchmark" || role === "race") w.role = role;
      if (id === "SOL") w.role = "benchmark";
      var status = pick(row, FIELDS.status);
      if (status) w.status = String(status).toLowerCase();
      var updated = pointTime(row);
      if (updated) w.updatedAt = updated;
      var eq = num(pick(row, FIELDS.equity));
      if (eq != null) w.equity = eq;
      var ccy = pick(row, FIELDS.currency);
      if (ccy) w.currency = String(ccy).slice(0, 8);
      seriesFrom(pick(row, FIELDS.curve)).forEach(function (p) { w.series.push(p); });
    });

    parseEvents(eventsText).forEach(function (row, idx) {
      var id = canonId(pick(row, FIELDS.id));
      if (!id) return;
      if (!byId[id]) byId[id] = blank({ id: id, name: id, role: id === "SOL" ? "benchmark" : "race" });
      var w = byId[id];
      var kind = eventKind(row);
      var when = pointTime(row);
      if (kind === "equity") {
        var eq = num(pick(row, FIELDS.equity));
        if (eq != null) w.series.push({ t: when, equity: eq, i: idx });
        return;
      }
      if (kind === "trade") {
        var sym = cleanSymbol(pick(row, FIELDS.symbol));
        if (!sym) return;
        var side = String(pick(row, FIELDS.side) || pick(row, FIELDS.type) || "").toLowerCase();
        if (side !== "buy" && side !== "sell") side = "buy";
        w.trades.push({
          t: when,
          i: idx,
          side: side,
          symbol: sym,
          price: num(pick(row, FIELDS.price)),
          size: num(pick(row, FIELDS.size)),
          sizeUnit: String(pick(row, FIELDS.sizeUnit) || w.currency || "SOL").slice(0, 8),
          pnl: num(pick(row, FIELDS.pnl))
        });
        return;
      }
      if (kind === "position") {
        var psym = cleanSymbol(pick(row, FIELDS.symbol));
        if (!psym) return;
        var entry = num(pick(row, FIELDS.entry));
        var mark = num(pick(row, FIELDS.mark));
        var upct = num(pick(row, FIELDS.upnlPct));
        if (upct == null && entry != null && mark != null && entry !== 0) {
          upct = (mark - entry) / entry * 100;
        }
        w.positions.push({
          t: when,
          i: idx,
          symbol: psym,
          size: num(pick(row, FIELDS.size)),
          sizeUnit: String(pick(row, FIELDS.sizeUnit) || w.currency || "SOL").slice(0, 8),
          entry: entry,
          mark: mark,
          upnl: num(pick(row, FIELDS.upnl)),
          upnlPct: upct,
          closed: pick(row, FIELDS.closed) === true || num(pick(row, FIELDS.size)) === 0
        });
      }
    });

    var wallets = Object.keys(byId).map(function (id) { return byId[id]; });
    var order = { R1: 1, R2: 2, R3: 3, R4: 4, R5: 5, SOL: 6 };
    wallets.sort(function (a, b) { return (order[a.id] || 50) - (order[b.id] || 50) || (a.id < b.id ? -1 : 1); });
    wallets.forEach(function (w) {
      w.series.sort(function (a, b) {
        if (a.t && b.t && a.t !== b.t) return a.t < b.t ? -1 : 1;
        return a.i - b.i;
      });
      if (w.series.length) w.equity = w.series[w.series.length - 1].equity;
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
        w.updatedAt = marks.length ? marks[marks.length - 1] : null;
      }
    });

    var anyMark = wallets.some(function (w) {
      return w.equity != null || w.trades.length || w.positions.length;
    });
    var lead = anyMark ? leadOf(wallets) : null;
    var status = anyMark ? runStatus : "starting";
    return {
      empty: !anyMark && !summary,
      reason: anyMark ? "" : "empty",
      source: opt.source || "races-data",
      status: status,
      updatedAt: runUpdated,
      message: anyMark ? "" : MESSAGE,
      banner: BANNER,
      lead: lead,
      wallets: wallets
    };
  }

  function getText(fetchFn, url) {
    return Promise.resolve(fetchFn(url)).then(function (res) {
      if (!res || !res.ok) return null;
      return res.text();
    }).catch(function () { return null; });
  }

  function load(fetchFn, opts) {
    var opt = opts || {};
    var fetchImpl = fetchFn;
    if (opt.fixtures) {
      return Promise.all([
        getText(fetchImpl, "revamp/fixtures/summary.json"),
        getText(fetchImpl, "revamp/fixtures/events.jsonl")
      ]).then(function (pair) {
        if (pair[0] == null) return emptyView("fixtures-missing");
        try {
          return buildView(JSON.parse(pair[0]), pair[1] || "", { source: "fixtures" });
        } catch (e) {
          return emptyView("fixtures-bad");
        }
      });
    }
    var pairs = [
      ["summary.json", "events.jsonl"],
      ["races/summary.json", "races/events.jsonl"]
    ];
    var step = 0;
    function next() {
      if (step >= pairs.length) return Promise.resolve(emptyView("missing"));
      var pair = pairs[step++];
      return getText(fetchImpl, RAW + pair[0]).then(function (summaryText) {
        if (summaryText == null) return next();
        return getText(fetchImpl, RAW + pair[1]).then(function (eventsText) {
          try {
            return buildView(JSON.parse(summaryText), eventsText || "", { source: "races-data" });
          } catch (e) {
            return emptyView("bad-json");
          }
        });
      });
    }
    return next();
  }

  root.SightlineRaces = {
    BANNER: BANNER,
    MESSAGE: MESSAGE,
    FIELDS: FIELDS,
    RAW: RAW,
    canonId: canonId,
    parseEvents: parseEvents,
    buildView: buildView,
    emptyView: emptyView,
    load: load
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
