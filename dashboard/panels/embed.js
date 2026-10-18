/* Link or embed another page. options.url (relative to the site root, or absolute), height, preview (image, desk-relative, shown until clicked). */
function sightlineSafeUrl(u) {
  const s = String(u || "").trim();
  if (!s || /^\s*javascript:/i.test(s)) return "";
  return s;
}
S.register("embed", { render({ desk, spec, opt }) {
  const url = sightlineSafeUrl(opt.url || S.join(desk.dir, spec.data || ""));
  const height = Number(opt.height) || 420;
  const frame = `<iframe title="${S.esc(spec.title || "Embedded page")}" src="${S.esc(url)}" style="height:${height}px" loading="lazy"></iframe>`;
  let body = frame;
  if (opt.preview && url) {
    const src = /^(https?:)?\/\//.test(opt.preview) ? opt.preview : S.join(desk.dir, opt.preview);
    body = `<div class="embed-preview" data-frame="${S.esc(frame)}"><img alt="" src="${S.esc(src)}" style="width:100%;max-height:${height}px;object-fit:cover;object-position:top;border:1px solid var(--line);border-radius:10px;display:block"><span class="badge live">Click to load live</span></div>`;
  }
  const link = url ? `<div class="small" style="margin-top:6px"><a href="${S.esc(url)}" target="_blank" rel="noopener">Open full page ↗</a></div>` : `<p class="muted">No page to embed.</p>`;
  return body + link;
}});
