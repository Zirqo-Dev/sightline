S.briefHTML = (b, desk) => `<div class="brief"><div class="d">${S.esc(b.date)}${desk ? ` · ${S.esc(desk)}` : ""}</div><b>${S.esc(b.headline)}</b>${b.highlights ? `<ul class="small muted" style="margin:6px 0 0;padding-left:18px">${b.highlights.slice(0, 4).map(h => `<li>${S.esc(h)}</li>`).join("")}</ul>` : ""}${b.next_watch && b.next_watch.length ? `<div class="small" style="margin-top:6px"><span class="warn">Next:</span> ${S.esc(b.next_watch[0])}</div>` : ""}</div>`;
S.register("briefs", { async render({ desk }) {
  const list = await S.briefsOf(desk);
  if (!list.length) return `<p class="muted">No daily briefs published yet${desk.briefs ? ` (<code>${S.esc(desk.briefs)}</code> is empty)` : ""}.</p>`;
  return list.map(b => S.briefHTML(b)).join("");
}});
