/* Time series: array of objects. options.x (default "t" or "at"), options.y. */
S.register("chart", { render({ data, opt }) {
  const yKey = opt.y;
  const pts = (Array.isArray(data) ? data : []).map(p => {
    const x = new Date(p[opt.x || "t"] || p.at || p.t).getTime();
    const y = yKey ? +p[yKey] : NaN;
    return [x, y];
  }).filter(p => !isNaN(p[0]) && !isNaN(p[1]));
  if (pts.length < 2) return `<p class="muted">Not enough points to chart yet (${pts.length}).</p>`;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${((p[0] - x0) / (x1 - x0 || 1) * 600).toFixed(1)},${(150 - (p[1] - y0) / (y1 - y0 || 1) * 140).toFixed(1)}`).join("");
  return `<svg viewBox="0 0 600 155" style="width:100%;height:160px" role="img" aria-label="chart"><path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"/></svg>`;
}});
