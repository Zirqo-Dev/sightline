/* KING desk (2). Display only. Reads Hoodscout's public nft_discover.json.
   Logos use image_url already in that file (OpenSea CDN). No key, no paid host.
   Volume totals skip Courtyard-style real-world collections. */
(function (root) {
  var SOURCE = "https://raw.githubusercontent.com/Zirqo-Dev/hoodscout-data/main/data/nft_discover.json";
  var LOGO_SOURCE = "image_url in hoodscout-data data/nft_discover.json (OpenSea CDN). No extra fetch.";

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
        image: row.image_url ? String(row.image_url) : "",
        volume: vol,
        sales: sales,
        symbol: String(row.floor_symbol || "ETH").toUpperCase(),
        url: row.opensea_url ? String(row.opensea_url) : (row.slug ? "https://opensea.io/collection/" + row.slug : ""),
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
      rwa: rows.filter(function (row) { return row.rwa; }),
      totalEth: total,
      top: top,
      share: share,
      volumeCheck: share != null && share > 40 ? "volume check: not confirmed" : "",
      newcomers: rows.filter(function (row) { return row.flagNew && !row.rwa; }),
      hot: hot,
      jumpSupported: jumps.length > 0,
      logoSource: LOGO_SOURCE
    };
  }

  root.SightlineHood = { SOURCE: SOURCE, LOGO_SOURCE: LOGO_SOURCE, isRwa: isRwa, parse: parse };
})(typeof globalThis !== "undefined" ? globalThis : this);
