async function applyWatchlist(data){
  if(!data || !Array.isArray(data.collections)) return;
  window.WATCHLIST = data;
  const list = data.collections.map(c => ({
    slug: c.slug,
    name: c.name,
    chain: c.chain || "ethereum",
    role: c.role || "tape",
    mode: c.mode || "watch",
    scan: c.scan !== false,
    priority: !!c.priority,
    tags: c.tags || []
  }));
  if(typeof WATCH !== "undefined" && Array.isArray(WATCH)){
    WATCH.length = 0;
    list.forEach(w => WATCH.push(w));
  }
  if(typeof WATCH_SLUGS !== "undefined" && Array.isArray(WATCH_SLUGS)){
    WATCH_SLUGS.length = 0;
    list.forEach(w => WATCH_SLUGS.push(w.slug));
  }
  const tab = document.getElementById("tabRadar");
  if(tab) tab.textContent = "Radar \u00b7 " + list.length;
  if(typeof filter !== "undefined") filter = "watch";
  if(typeof renderFilters === "function") renderFilters();
  if(typeof renderTable === "function") renderTable();
  if(typeof renderBots === "function") renderBots();
  if(typeof renderDetail === "function") renderDetail();
}
async function bootWatchlist(){
  try{
    const res = await fetch("watchlist.json?ts=" + Date.now());
    if(!res.ok) throw new Error("watchlist HTTP " + res.status);
    await applyWatchlist(await res.json());
  }catch(e){
    console.warn("watchlist.json missing, staying on baked WATCH", e);
  }
}
const _connectLive = typeof connectLive === "function" ? connectLive : null;
connectLive = async function(){
  const data = window.WATCHLIST;
  if(data && Array.isArray(data.collections) && typeof WATCH !== "undefined"){
    const scanned = data.collections.filter(c => c.scan !== false).map(c => ({
      slug: c.slug, role: c.role, mode: c.mode
    }));
    if(scanned.length){
      WATCH.length = 0;
      scanned.forEach(w => WATCH.push(w));
    }
  }
  if(_connectLive) return _connectLive.apply(this, arguments);
};
bootWatchlist();
