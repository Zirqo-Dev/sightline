S.register("headline", { render({ data }) {
  if (!data) return `<p class="muted">No run log yet.</p>`;
  const h = data.headline || (data.runs && data.runs[0] && data.runs[0].headline) || "–";
  const src = data.source ? `<span class="src" title="${S.esc(data.source)}">${S.esc(data.source)}</span>` : "";
  const rate = data.rate ? Object.entries(data.rate).filter(([k, v]) => typeof v === "number").map(([k, v]) => `<span class="tick"><b>${S.esc(k.replace("_", "/"))}</b> ${S.fmt(v, 4)}</span>`).join("") : "";
  return `<div class="hl-meta"><span class="ts">▸ ${S.when(data.asof)}</span>${rate}${src}</div><p class="hl-text clamp" data-x>${S.rich(h)}</p>`;
}});
