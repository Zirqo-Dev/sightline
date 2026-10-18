S.register("coming-soon", { render({ opt }) {
  return `<div class="placeholder"><b>Plugin slot</b><br>${S.esc(opt.text || "A future component goes here.")}</div>`;
}});
