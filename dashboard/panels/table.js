/* Any array of objects, or an object (shown as key/value rows). options: columns, labels, limit, badge */
S.register("table", { render({ data, opt }) {
  if (data == null) return `<p class="muted">No data at this path.</p>`;
  let rows = Array.isArray(data)
    ? data
    : Object.entries(data).map(([key, value]) => ({ key, value: typeof value === "object" && value != null ? JSON.stringify(value) : value }));
  rows = rows.slice(0, opt.limit || 50);
  if (!rows.length) return `<p class="muted">Empty.</p>`;
  const cols = opt.columns || Object.keys(rows[0]).slice(0, 6);
  const L = opt.labels || {};
  const cell = (c, v) => {
    if (v == null) return "";
    if (c === opt.badge) return `<span class="badge ${S.esc(v)}">${S.esc(v)}</span>`;
    if (/(_at|checked|asof)$/.test(c) && (typeof v === "string" || typeof v === "number")) return S.when(v);
    return S.esc(typeof v === "object" ? JSON.stringify(v) : v);
  };
  return `<div class="scroll"><table><tr>${cols.map(c => `<th>${S.esc(L[c] || c)}</th>`).join("")}</tr>${rows.map(r => `<tr>${cols.map(c => `<td>${cell(c, r[c])}</td>`).join("")}</tr>`).join("")}</table></div>`;
}});
