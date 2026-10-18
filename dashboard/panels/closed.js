S.register("closed", { render({ data }) {
  const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
  const rows = [];
  books.forEach(b => (b.json.closed || []).forEach(t => rows.push({ t, b })));
  if (!rows.length) return `<p class="muted">No closed tickets yet.</p>`;
  rows.sort((a, b) => String(b.t.closed_at || "").localeCompare(String(a.t.closed_at || "")));
  return `<div class="scroll"><table><tr><th>Ticker</th><th>Book</th><th>Closed</th><th>Result</th><th>Why</th></tr>${rows.map(({ t, b }) => {
    const r = t.result || {};
    const unit = r.unit || b.json.unit || "";
    const pnl = typeof r.pnl === "number" ? `<div class="muted small">${S.pnlNum(r.pnl)} ${S.esc(unit)}</div>` : "";
    return `<tr><td><b>${S.esc(t.ticker)}</b></td><td class="muted">${S.esc(b.json.book || "")}</td><td class="small">${S.when(t.closed_at)}</td><td>${S.pct(r.pct)}${pnl}</td><td class="small muted">${S.esc(String(t.exit_reason || "").slice(0, 160))}</td></tr>`;
  }).join("")}</table></div>`;
}});
