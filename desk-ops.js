const LIVE_KEY = "sightline_live_rows";
function stampIntel(){
  const el = document.getElementById("intelStamp");
  if(!el) return;
  const d = window.INTEL;
  if(!d){ el.textContent = ""; return; }
  const when = (d.asof || "").replace("T", " ").slice(0,16);
  el.textContent = (d.title || "intel") + (when ? " · " + when + "Z" : "");
}
function saveLiveTape(){
  try{
    if(typeof live === "undefined" || !live || !rows || !rows.length) return;
    localStorage.setItem(LIVE_KEY, JSON.stringify({ ts: Date.now(), selected: selected, rows: rows }));
  }catch(e){}
}
function restoreLiveTape(){
  try{
    const raw = JSON.parse(localStorage.getItem(LIVE_KEY) || "null");
    if(!raw || !Array.isArray(raw.rows) || !raw.rows.length) return false;
    if(Date.now() - raw.ts > 36 * 3600 * 1000) return false;
    rows = raw.rows;
    if(raw.selected) selected = raw.selected;
    if(typeof setMode === "function") setMode(true);
    if(typeof renderTable === "function") renderTable();
    if(typeof renderDetail === "function") renderDetail();
    if(typeof renderBots === "function") renderBots();
    if(typeof renderBlotter === "function") renderBlotter();
    if(window.INTEL && typeof applyIntel === "function") applyIntel(window.INTEL);
    stampIntel();
    return true;
  }catch(e){ return false; }
}
const _closePaper = typeof closePaper === "function" ? closePaper : null;
closePaper = function(slug){
  const all = typeof loadBlotter === "function" ? loadBlotter() : [];
  const rec = all.find(r => r.slug === slug && r.open);
  if(!rec){
    alert("No open paper on this name.");
    return;
  }
  const c = rows.find(x => x.slug === slug) || rec;
  const bid = Number(c.bid || 0);
  if(!bid){
    if(!confirm("No live bid on " + rec.name + ". VOID this line (not a fill, no P/L)?")) return;
    rec.open = false;
    rec.voided = true;
    rec.exit = null;
    rec.pl = null;
    if(typeof saveBlotter === "function") saveBlotter(all);
    if(typeof renderBlotter === "function") renderBlotter();
    return;
  }
  return _closePaper ? _closePaper.apply(this, arguments) : null;
};
const _connect = typeof connectLive === "function" ? connectLive : null;
connectLive = async function(){
  const r = _connect ? await _connect.apply(this, arguments) : null;
  saveLiveTape();
  stampIntel();
  return r;
};
const liveBtn = document.getElementById("btnLive");
if(liveBtn) liveBtn.onclick = connectLive;
const demoBtn = document.getElementById("btnDemo");
if(demoBtn){
  const prev = demoBtn.onclick;
  demoBtn.onclick = function(){
    try{ localStorage.removeItem(LIVE_KEY); }catch(e){}
    if(prev) prev.apply(this, arguments);
    stampIntel();
  };
}
restoreLiveTape();
stampIntel();
