/* A string, or an object of named sections. */
S.register("notes", { render({ data }) {
  if (data == null) return `<p class="muted">No notes.</p>`;
  if (typeof data === "string") return `<div class="note">${S.esc(data)}</div>`;
  if (typeof data !== "object") return `<div class="note">${S.esc(data)}</div>`;
  return `<dl class="note">${Object.entries(data).map(([k, v]) => `<dt>${S.esc(k.replace(/_/g, " "))}</dt><dd>${S.esc(typeof v === "string" ? v : JSON.stringify(v))}</dd>`).join("")}</dl>`;
}});
