/* Early runners from <desk>/pulse.json. Missing file or empty radar degrades to a quiet line. */
S.register("radar", { async render({ data, opt }) {
  const pulse = data && typeof data === "object" ? data : null;
  const rows = pulse && Array.isArray(pulse.radar) ? pulse.radar.filter(r => r && r.ticker) : [];
  if (!rows.length) return `<p class="muted">No early runners yet.</p>`;
  const now = opt && opt.now ? opt.now : Date.now();
  rows.sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0) || String(a.ticker).localeCompare(String(b.ticker)));
  const flag = pulse.example ? `<span class="pill">example</span>` : "";
  const body = rows.map((r, i) => {
    const signals = [].concat(r.signals || []).filter(Boolean);
    const seen = r.first_seen ? S.ago(r.first_seen, now) : "";
    const spark = S.spark(S.seriesOf({ ticker: r.ticker }, pulse));
    return `<div class="radar-row">
      <span class="rk">${i + 1}</span>
      <span class="who">${S.assetIcon(r.ticker)}<b>${S.esc(r.ticker)}</b>${S.chainIcon(r.chain, "sm")}</span>
      <span class="score">${S.scoreBar(r.score)}<b class="num">${typeof r.score === "number" ? S.fmt(r.score, 0) : "–"}</b></span>
      ${spark}
      <span class="seen" title="${S.esc(r.first_seen || "")}">${seen ? "seen " + S.esc(seen) : ""}</span>
      <span class="chips">${signals.map(s => S.pill(s)).join("")}</span>
      ${r.note ? `<span class="ell note">${S.rich(r.note)}</span>` : `<span></span>`}
    </div>`;
  }).join("");
  return `<div class="radar">${flag ? `<div class="radar-flag">${flag}</div>` : ""}${body}</div>`;
}});
