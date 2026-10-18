/* An object of numbers, one card each. options.labels renames the keys. */
S.register("stat-cards", { render({ data, opt }) {
  if (!data || typeof data !== "object") return `<p class="muted">No data.</p>`;
  const L = opt.labels || {};
  return `<div class="cards">${Object.entries(data).map(([k, v]) => {
    let shown = "";
    let cls = "";
    if (typeof v === "number") {
      if (k === "pnl") { shown = S.pnlNum(v); cls = S.pnlClass(v); }
      else shown = S.fmt(v, Number.isInteger(v) ? 0 : 1);
    } else shown = S.esc(v);
    return `<div class="card"><div class="k">${S.esc(L[k] || k)}</div><div class="v ${cls}">${shown}</div></div>`;
  }).join("")}</div>`;
}});
