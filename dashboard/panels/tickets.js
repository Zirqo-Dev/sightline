S.ticketCard = function (t, kd, extra = "") {
  const bar = kd ? Math.max(3, Math.min(100, kd.pct * 2)) : null;
  const k = t.kill_line ? (typeof t.kill_line === "string" ? t.kill_line : t.kill_line.text) : "no kill line set";
  const level = t.level != null ? "L" + t.level : String(t.conviction || t.status || "open").split(" ")[0];
  const near = !kd ? "" : kd.pct < 10 ? "down" : kd.pct < 25 ? "warn" : "up";
  return `<div class="ticket"><div class="row"><span><span class="t">${S.esc(t.ticker)}</span> <span class="badge open">${S.esc(level)}</span> ${extra}</span><span class="muted small">opened ${S.when(t.opened_at)} · ends ${S.when(t.window_ends_at)}</span></div>
    ${kd ? `<div class="bar"><i style="width:${bar}%"></i></div><div class="small"><b class="${near}">${S.fmt(kd.pct)}% above kill</b> <span class="muted">· ${S.esc(kd.what)} ${S.fmt(kd.cur, 4)} vs kill ${S.fmt(kd.lim, 4)}</span></div>` : `<div class="small muted" style="margin:6px 0">Distance to kill: not machine-readable</div>`}
    <div class="kill"><b>Kill:</b> ${S.esc(k || "")}</div>${t.thesis ? `<div class="small muted" style="margin-top:4px">${S.esc([].concat(t.thesis)[0])}</div>` : ""}</div>`;
};
S.register("tickets", { render({ data }) {
  const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
  const all = [];
  books.forEach(b => (b.json.open || []).forEach(t => all.push({ t, b, kd: S.killDistance(t) })));
  if (!all.length) return `<p class="muted">No open paper tickets.</p>`;
  return all.sort((a, b) => (a.kd?.pct ?? 999) - (b.kd?.pct ?? 999))
    .map(x => S.ticketCard(x.t, x.kd, `<span class="muted small">${S.esc(x.b.json.book || "")}</span>`)).join("");
}});
