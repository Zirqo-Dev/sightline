/* v2: one dense row per ticket. Line 1: icon, ticker, level, book, kill meter, room %. Line 2: kill text (ellipsis). Tap to expand thesis. */
S.ticketCard = function (t, kd, extra = "") {
  const k = t.kill_line ? (typeof t.kill_line === "string" ? t.kill_line : t.kill_line.text) : "no kill line set";
  const level = t.level != null ? "L" + t.level : String(t.conviction || t.status || "open").split(" ")[0];
  const near = !kd ? "muted" : kd.pct < 10 ? "down" : kd.pct < 25 ? "warn" : "up";
  const chain = t.chain || t.side;
  const sp = S.spark(S.seriesOf(t));
  const thesis = [].concat(t.thesis || []);
  return `<div class="ticket" data-x>
    <div class="row1">${S.assetIcon(t.ticker)}<b class="t">${S.esc(t.ticker)}</b>${S.chainIcon(chain, "sm")}${S.pill(level, "lvl")}${extra}
      <span class="grow"></span>${sp}${S.killMeter(kd && kd.pct)}<b class="room ${near}">${kd ? S.fmt(kd.pct) + "%" : "n/a"}</b></div>
    <div class="row2"><span class="k">KILL</span><span class="ell">${S.rich(k || "")}</span>${kd ? `<span class="kv">${S.esc(kd.what)} ${S.fmt(kd.cur, 4)} → ${S.fmt(kd.lim, 4)}</span>` : ""}</div>
    <div class="more"><div class="when">opened ${S.when(t.opened_at)} · ends ${S.when(t.window_ends_at)}</div>${thesis.length ? `<ul>${thesis.map(x => `<li>${S.rich(x)}</li>`).join("")}</ul>` : ""}</div>
  </div>`;
};
S.register("tickets", { render({ data }) {
  const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
  const all = [];
  books.forEach(b => (b.json.open || []).forEach(t => all.push({ t, b, kd: S.killDistance(t) })));
  if (!all.length) return `<p class="muted">No open paper tickets.</p>`;
  return `<div class="tickets">${all.sort((a, b) => (a.kd?.pct ?? 999) - (b.kd?.pct ?? 999))
    .map(x => S.ticketCard(x.t, x.kd, `<span class="book">${S.esc(x.b.json.book || "")}</span>`)).join("")}</div>`;
}});
