S.register("coming-soon", { slim: true, render({ opt }) {
  return `<div class="placeholder"><b>slot</b> · ${S.rich(opt.text || "A future component goes here.")}</div>`;
}});
