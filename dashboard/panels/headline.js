S.register("headline", { render({ data }) {
  if (!data) return `<p class="muted">No run log yet.</p>`;
  const h = data.headline || (data.runs && data.runs[0] && data.runs[0].headline) || "–";
  return `<div class="muted small">As of ${S.when(data.asof)}</div><p style="font-size:15px;margin:6px 0 0">${S.esc(h)}</p>`;
}});
