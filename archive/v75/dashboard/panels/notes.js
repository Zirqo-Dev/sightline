/* A string, or an object of named sections. v2: each section is one clamped line, tap to expand. */
S.register("notes", { render({ data }) {
  if (data == null) return `<p class="muted">No notes.</p>`;
  if (typeof data !== "object") return `<div class="note clamp" data-x>${S.rich(data)}</div>`;
  return `<dl class="notes">${Object.entries(data).map(([k, v]) => `<div class="nrow" data-x><dt>${S.esc(k.replace(/_/g, " "))}</dt><dd class="clamp">${S.rich(typeof v === "string" ? v : JSON.stringify(v))}</dd></div>`).join("")}</dl>`;
}});
