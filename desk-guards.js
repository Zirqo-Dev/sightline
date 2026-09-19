function watchRow(slug){
  const list = (window.WATCHLIST && window.WATCHLIST.collections) || [];
  return list.find(c => c.slug === slug) || (typeof WATCH !== "undefined" ? WATCH.find(c => c.slug === slug) : null) || {};
}
const _paper = typeof paper === "function" ? paper : null;
paper = function(slug, side){
  const c = (typeof rows !== "undefined" && rows.find(x => x.slug === slug)) || {};
  const w = watchRow(slug);
  const mode = w.mode || (typeof modeOf === "function" ? modeOf(c) : "");
  const floor = Number(c.floor || 0);
  if(side !== "pass"){
    if(mode === "observe" || w.scan === false){
      alert(c.name + " is observe-only. Log a pass, do not paper a fill.");
      return _paper ? _paper(slug, "pass") : null;
    }
    if(!floor){
      alert("No live floor on " + (c.name || slug) + ". Will not paper at 0.");
      return;
    }
  }
  return _paper ? _paper.apply(this, arguments) : null;
};
function voidPaper(slug){
  const all = loadBlotter();
  const rec = all.find(r => r.slug === slug && r.open);
  if(!rec){ alert("No open paper on this name."); return; }
  rec.open = false;
  rec.voided = true;
  rec.exit = null;
  rec.pl = null;
  saveBlotter(all);
  renderBlotter();
}
closePaper = function(slug){
  const all = loadBlotter();
  const rec = all.find(r => r.slug === slug && r.open);
  if(!rec){ alert("No open paper on this name."); return; }
  const c = (rows && rows.find(x => x.slug === slug)) || rec;
  const bid = Number(c.bid || 0);
  if(!bid){
    if(!confirm("No live bid on " + rec.name + ". VOID this line (not a fill)?")) return;
    return voidPaper(slug);
  }
  rec.open = false;
  rec.exit = bid;
  rec.pl = +(((bid - rec.entry) / rec.entry) - 0.05).toFixed(4);
  saveBlotter(all);
  renderBlotter();
};
