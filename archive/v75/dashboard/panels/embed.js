/* Link or embed another page. options.url (relative to the site root, or absolute), height, preview.
   v2: collapsed to one line by default (the classic page repeats panels already on the desk page). Tap to open the preview / live frame. */
function sightlineSafeUrl(u) {
  const s = String(u || "").trim();
  if (!s || /^\s*javascript:/i.test(s)) return "";
  return s;
}
S.register("embed", { slim: true, render({ desk, spec, opt }) {
  const url = sightlineSafeUrl(opt.url || S.join(desk.dir, spec.data || ""));
  const height = Number(opt.height) || 420;
  const frame = `<iframe title="${S.esc(spec.title || "Embedded page")}" src="${S.esc(url)}" style="height:${height}px" loading="lazy"></iframe>`;
  let body = frame;
  if (opt.preview && url) {
    const src = /^(https?:)?\/\//.test(opt.preview) ? opt.preview : S.join(desk.dir, opt.preview);
    body = `<div class="embed-preview" data-frame="${S.esc(frame)}"><img alt="" src="${S.esc(src)}" style="max-height:${height}px"><span class="pill live">load live</span></div>`;
  }
  if (!url) return `<p class="muted">No page to embed.</p>`;
  return `<details class="embed"><summary><span class="ell">Legacy page · duplicates the panels above</span><a href="${S.esc(url)}" target="_blank" rel="noopener">open ↗</a></summary>${body}</details>`;
}});
