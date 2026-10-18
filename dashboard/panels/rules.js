S.register("rules", { render({ data, opt }) {
  const books = (Array.isArray(data) ? data : [{ json: data }]).filter(b => b && b.json);
  let r = [];
  books.forEach(b => (b.json.rules || []).forEach(x => r.push({ ...x, book: b.json.book })));
  r.sort((a, b) => String(b.added_at || "").localeCompare(String(a.added_at || "")));
  r = r.slice(0, opt.limit || 12);
  if (!r.length) return `<p class="muted">No rules yet.</p>`;
  return `<ul class="list">${r.map(x => `<li>${x.id ? `<b class="warn">${S.esc(x.id)}</b> ` : ""}${S.esc(x.rule)}<div class="meta">${S.esc(x.book || "")} · ${S.when(x.added_at)} · ${S.esc(x.source || "")}</div></li>`).join("")}</ul>`;
}});
