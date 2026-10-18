S.register("scoreboard", { render({ data }) {
  const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
  if (!books.length) return `<p class="muted">No paper books.</p>`;
  const rows = books.map(b => {
    const j = b.json;
    const s = S.score([j]);
    const unitRow = Object.entries(s.byUnit)[0];
    const pnl = unitRow
      ? `<span class="${S.pnlClass(unitRow[1].pnl)}">${S.pnlNum(unitRow[1].pnl)}</span> ${S.unitTag(unitRow[0])}`
      : "–";
    const avg = S.avgRet(j);
    return `<tr><td class="c-main">${S.chainIcon(j.chain === "multi" ? (j.chains || ["multi"])[0] : j.chain, "sm")}<b>${S.esc(j.book || b.path || "")}</b></td><td class="num">${s.open}</td><td class="num">${s.closed}</td><td class="num">${s.winRate == null ? "–" : S.fmt(s.winRate, 0) + "%"}</td><td class="num">${S.pct(avg)} ${S.pnlBar(avg)}</td><td class="num">${pnl}</td></tr>`;
  }).join("");
  const t = S.score(books.map(b => b.json));
  return `<div class="cards">${S.metricCards(t)}</div>${S.unitNote(t)}
    <table class="tbl books"><tr><th>Book</th><th class="num">Open</th><th class="num">Closed</th><th class="num">Win</th><th class="num">Avg ret</th><th class="num">P&amp;L</th></tr>${rows}</table>`;
}});
