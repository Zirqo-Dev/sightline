S.briefHTML = (b, desk) => `<article class="brief" data-x><div class="bh"><span class="d">${S.esc(b.date)}</span>${desk ? `<span class="desk">${S.esc(desk)}</span>` : ""}</div><div class="bt clamp">${S.rich(b.headline)}</div>${b.highlights ? `<ul class="hi">${b.highlights.slice(0, 6).map(h => {
  const m = String(h).match(/^(.*?)\s*\(([^)]+)\):\s*(\w+)\s*$/);
  return m ? `<li>${S.assetIcon(m[1], "sm")}<span class="ell">${S.rich(m[1])}</span>${S.chainIcon(m[2], "xs")}${S.pill(m[3])}</li>` : `<li><span class="ell">${S.rich(h)}</span></li>`;
}).join("")}</ul>` : ""}${b.next_watch && b.next_watch.length ? `<div class="next"><span class="k">NEXT</span><span class="ell">${S.rich(b.next_watch[0])}</span></div>` : ""}</article>`;
S.register("briefs", { async render({ desk }) {
  const list = await S.briefsOf(desk);
  if (!list.length) return `<p class="muted">No daily briefs published yet${desk.briefs ? ` (<code>${S.esc(desk.briefs)}</code> is empty)` : ""}.</p>`;
  return `<div class="briefs">${list.map(b => S.briefHTML(b)).join("")}</div>`;
}});
