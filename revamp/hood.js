/* KING desk (2). Display only. Reads Hoodscout's public nft_discover.json.
   Logos use image_url already in that file (OpenSea CDN). No key, no paid host.
   Volume totals skip Courtyard-style real-world collections. */
(function (root) {
  var SOURCE = "https://raw.githubusercontent.com/Zirqo-Dev/hoodscout-data/main/data/nft_discover.json";
  var LOGO_SOURCE = "image_url in hoodscout-data data/nft_discover.json (OpenSea CDN). No extra fetch.";
  /* nft.yml cron "17 13,21 * * *": 13:17 and 21:17 UTC. The gaps are 8h and 16h.
     The longer gap is the update cadence. Amber after twice that gap. */
  var CADENCE_MS = 16 * 60 * 60 * 1000;
  var AMBER_MS = 2 * CADENCE_MS;

  function isRwa(row) {
    var blob = String((row && row.name) || "") + " " + String((row && row.slug) || "");
    if (/courtyard|yield[-\s]?field|real[-\s]?world|\brwa\b/i.test(blob)) return true;
    var sym = String((row && row.floor_symbol) || "").toUpperCase();
    return sym === "USDG" || sym === "USDC" || sym === "USD";
  }

  function num(v) {
    if (v == null || v === "") return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function httpsUrl(value) {
    var s = String(value || "").trim();
    if (!s) return "";
    try {
      return new URL(s).protocol === "https:" ? s : "";
    } catch (e) {
      return "";
    }
  }

  function orderLabel(orderBy) {
    if (orderBy === "seven_day_volume") return "7-day volume";
    if (orderBy === "one_day_volume") return "1-day volume";
    return orderBy ? String(orderBy).replace(/_/g, " ") : "7-day volume";
  }

  function ageStamp(iso, now) {
    var t = Date.parse(iso);
    if (!isFinite(t)) return { text: "no timestamp", tone: "" };
    var age = (now == null ? Date.now() : now) - t;
    var h = Math.round(age / 3600000);
    var text = h < 1 ? Math.max(0, Math.round(age / 60000)) + " min ago" : (h < 48 ? h + " h ago" : Math.round(h / 24) + " days ago");
    return { text: text, tone: age > AMBER_MS ? "warn" : "good" };
  }

  function parse(doc) {
    if (!doc || !Array.isArray(doc.collections)) {
      return {
        empty: true,
        generatedAt: null,
        rows: [],
        rwa: [],
        totalEth: null,
        top: null,
        share: null,
        volumeCheck: "",
        newcomers: [],
        hot: null,
        jumpSupported: false,
        logoSource: LOGO_SOURCE
      };
    }
    var rows = doc.collections.map(function (row) {
      var vol = num(row.volume_24h);
      var sales = num(row.sales_24h);
      var chg = num(row.floor_chg_pct);
      return {
        slug: String(row.slug || ""),
        name: String(row.name || row.slug || "Collection"),
        image: httpsUrl(row.image_url),
        volume: vol,
        sales: sales,
        symbol: String(row.floor_symbol || "ETH").toUpperCase(),
        url: httpsUrl(row.opensea_url) || (row.slug ? "https://opensea.io/collection/" + encodeURIComponent(row.slug) : ""),
        flagNew: row.flag_new === true,
        floorChg: chg,
        rwa: isRwa(row)
      };
    }).filter(function (row) { return row.slug || row.name; });
    rows.sort(function (a, b) { return (b.volume || 0) - (a.volume || 0); });
    var tradable = rows.filter(function (row) { return !row.rwa && row.symbol === "ETH" && row.volume != null; });
    var total = tradable.reduce(function (sum, row) { return sum + row.volume; }, 0);
    var top = tradable[0] || null;
    var share = top && total > 0 ? (top.volume / total) * 100 : null;
    var listed = rows.length;
    var by = orderLabel(doc.order_by);
    var shareLabel = share == null ? "" : share.toFixed(1) + "% of 24h volume across the " + listed + " listed collections (top " + listed + " by " + by + ")";
    var totalLabel = total.toFixed(2) + " ETH across those listed collections";
    var jumps = rows.filter(function (row) { return row.floorChg != null; });
    var hot = null;
    if (jumps.length) {
      hot = jumps.slice().sort(function (a, b) { return b.floorChg - a.floorChg; })[0];
      if (!(hot.floorChg > 0)) hot = null;
    }
    return {
      empty: rows.length === 0,
      generatedAt: doc.generated_at || null,
      chain: doc.chain || "",
      rows: rows,
      listedCount: listed,
      orderLabel: by,
      rwa: rows.filter(function (row) { return row.rwa; }),
      totalEth: total,
      top: top,
      share: share,
      shareLabel: shareLabel,
      totalLabel: totalLabel,
      volumeCheck: share != null && share > 40 ? "volume check: not confirmed" : "",
      volumeCheckNote: "The 40% check uses this same listed set, not the whole chain.",
      newcomers: rows.filter(function (row) { return row.flagNew && !row.rwa; }),
      hot: hot,
      jumpSupported: jumps.length > 0,
      logoSource: LOGO_SOURCE
    };
  }

  root.SightlineHood = {
    SOURCE: SOURCE,
    LOGO_SOURCE: LOGO_SOURCE,
    CADENCE_MS: CADENCE_MS,
    AMBER_MS: AMBER_MS,
    isRwa: isRwa,
    httpsUrl: httpsUrl,
    ageStamp: ageStamp,
    parse: parse
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
