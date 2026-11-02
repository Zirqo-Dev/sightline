/* Any array of objects, or an object (shown as key/value rows). options: columns, labels, limit, badge.
   v2: no mid-word breaks (nowrap + ellipsis, tap a row for full text), icons for asset-like keys/names,
   chain icons for a "chain" column, level/status pills, and two-line rows on phones. */
S.register("table", { render({ data, opt }) {
  if (data == null) return `<p class="muted">No data at this path.</p>`;
  let rows = Array.isArray(data)
    ? data
    : Object.entries(data).map(([key, value]) => ({ key, value: typeof value === "object" && value != null ? JSON.stringify(value) : value }));
  const total = rows.length;
  rows = rows.slice(0, opt.limit || 50);
  if (!rows.length) return `<p class="muted">Empty.</p>`;
  const cols = opt.columns || Object.keys(rows[0]).slice(0, 6);
  const L = opt.labels || {};
  const main = cols[0];
  const isLong = c => rows.some(r => typeof r[c] === "string" && r[c].length > 48);
  const role = c => c === main ? "c-main" : c === opt.badge ? "c-badge" : c === "chain" ? "c-chain" : isLong(c) ? "c-long" : (/(_at|checked|asof)$/.test(c) ? "c-meta mono" : "c-meta");
  const cell = (c, v, r) => {
    if (v == null) return "";
    if (c === opt.badge) return S.pill(v);
    if (c === "chain") return `${S.chainIcon(v)}<span class="chain-name">${S.esc(v)}</span>`;
    if ((/(_at|_seen|checked|asof)$/.test(c)) && (typeof v === "string" || typeof v === "number")) return S.when(v);
    const txt = typeof v === "object" ? JSON.stringify(v) : String(v);
    if (c === main) return `${S.assetIcon(r.slug || txt)}<b class="ell">${S.rich(txt)}</b>`;
    // Leading level / kill-hit tokens become pills: "L2, kill hit (…)" -> [L2][KILL HIT] …
    const m = txt.match(/^(L\d)\b[,:]?\s*(kill hit\b)?[,:]?\s*/i) || txt.match(/^(hit)\b\s*/i);
    if (m) {
      const pills = (m[1].toLowerCase() === "hit" ? S.pill("kill hit", "kill") : S.pill(m[1].toUpperCase(), "lvl") + (m[2] ? S.pill("kill hit", "kill") : ""));
      return `${pills}<span class="ell">${S.rich(txt.slice(m[0].length))}</span>`;
    }
    return `<span class="ell">${S.rich(txt)}</span>`;
  };
  // Fields not shown as columns (e.g. watchlist "kill" / "today") open under the row on tap.
  const extra = r => {
    const ks = Object.keys(r).filter(k => !cols.includes(k) && k !== "slug" && typeof r[k] === "string" && r[k].length > 12);
    return ks.length ? `<td class="c-x">${ks.map(k => `<div><span class="k">${S.esc(k)}</span>${S.rich(r[k])}</div>`).join("")}</td>` : "";
  };
  const more = total > rows.length ? `<div class="tmore">+${total - rows.length} more rows (limit ${rows.length})</div>` : "";
  return `<table class="tbl cards-m"><tr>${cols.map(c => `<th class="${role(c)}">${S.esc(L[c] || c)}</th>`).join("")}</tr>${rows.map(r => `<tr data-x>${cols.map(c => {
    const v = r[c]; const t = v == null ? "" : (typeof v === "object" ? JSON.stringify(v) : String(v));
    return `<td class="${role(c)}" title="${S.esc(t)}">${cell(c, v, r)}</td>`;
  }).join("")}${extra(r)}</tr>`).join("")}</table>${more}`;
}});
