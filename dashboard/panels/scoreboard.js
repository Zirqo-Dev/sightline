S.register("scoreboard", { render({ data }) {
  const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
  if (!books.length) return `<p class="muted">No paper books.</p>`;
  const rows = books.map(b => {
    const j = b.json;
    const s = S.score([j]);
    const unitRow = Object.entries(s.byUnit)[0];
    const pnl = unitRow
      ? `<span class="${S.pnlClass(unitRow[1].pnl)}">${S.pnlNum(unitRow[1].pnl)}</span> <span class="muted small">${S.esc(unitRow[0])}</span>`
      : "–";
    const avg = S.avgRet(j);
    return `<tr><td><b>${S.esc(j.book || b.path || "")}</b> <span class="muted small">${S.esc(j.chain || "")}</span></td><td>${s.open}</td><td>${s.closed}</td><td>${s.winRate == null ? "–" : S.fmt(s.winRate, 0) + "%"}</td><td>${S.pct(avg)}</td><td>${pnl}</td></tr>`;
  }).join("");
  const t = S.score(books.map(b => b.json));
  return `<div class="cards">${S.metricCards(t)}</div>${S.unitNote(t)}
    <div class="scroll" style="margin-top:12px"><table><tr><th>Book</th><th>Open</th><th>Closed</th><th>Win</th><th>Avg ret</th><th>P&amp;L</th></tr>${rows}</table></div>`;
}});
