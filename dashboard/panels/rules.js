S.register("rules", { render({ data, opt }) {
  const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
  let r = [];
  books.forEach(b => (b.json.rules || []).forEach(x => r.push({ ...x, book: b.json.book })));
  r.sort((a, b) => String(b.added_at || "").localeCompare(String(a.added_at || "")));
  r = r.slice(0, opt.limit || 12);
  if (!r.length) return `<p class="muted">No rules yet.</p>`;
  return `<ul class="rules">${r.map(x => S.ruleRow(x, x.book)).join("")}</ul>`;
}});
S.ruleRow = (x, ctx) => `<li data-x>${x.id ? `<span class="rid">${S.esc(x.id)}</span>` : `<span class="rid">·</span>`}<span class="ell rtext">${S.rich(x.rule)}</span><span class="meta">${S.esc(ctx || "")} · ${S.when(x.added_at)}</span></li>`;
