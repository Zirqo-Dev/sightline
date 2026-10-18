S.register("closed", { render({ data }) {
  const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
  const rows = [];
  books.forEach(b => (b.json.closed || []).forEach(t => rows.push({ t, b })));
  if (!rows.length) return `<p class="muted">No closed tickets yet.</p>`;
  rows.sort((a, b) => String(b.t.closed_at || "").localeCompare(String(a.t.closed_at || "")));
  return `<table class="tbl cards-m"><tr><th>Ticker</th><th>Book</th><th>Closed</th><th class="num">Result</th><th>Trace</th><th>Why</th></tr>${rows.map(({ t, b }) => {
    const r = t.result || {};
    const unit = r.unit || b.json.unit || "";
    const pnl = typeof r.pnl === "number" ? `<span class="muted">${S.pnlNum(r.pnl)}</span> ${S.unitTag(unit)}` : "";
    const why = String(t.exit_reason || "");
    return `<tr data-x><td class="c-main">${S.assetIcon(t.ticker)}<b>${S.esc(t.ticker)}</b>${S.chainIcon(t.side || b.json.chain, "sm")}</td><td class="c-meta muted">${S.esc(b.json.book || "")}</td><td class="c-meta mono small">${S.when(t.closed_at)}</td><td class="c-val num">${S.pct(r.pct)} ${S.pnlBar(r.pct)}<div class="sub-pnl">${pnl}</div></td><td class="c-meta">${S.spark(S.seriesOf(t))}</td><td class="c-long" title="${S.esc(why)}"><span class="ell">${S.rich(why)}</span></td></tr>`;
  }).join("")}</table>`;
}});
