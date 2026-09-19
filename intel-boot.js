function applyIntel(data){
  window.INTEL = data;
  if(!data) return;
  const tape = document.getElementById("tape");
  if(tape){
    const lines = (data.cio || []).map(t => `<div class="event"><time>${(data.asof || "").slice(0,16)}</time><b>CIO</b><p>${t}</p></div>`).join("");
    const head = `<div class="event social"><time>${data.source || "intel.json"}</time><b>${data.title || "Morning tape"}</b><p>Watchlist SOCIAL from last Grok run. Not a live firehose.</p></div>`;
    tape.innerHTML = head + lines;
  }
  const bySlug = {};
  (data.names || []).forEach(n => { if(n.slug) bySlug[n.slug] = n; });
  if(typeof rows !== "undefined" && Array.isArray(rows)){
    rows.forEach(c => {
      const n = bySlug[c.slug];
      if(!n) return;
      c.social = Object.assign({}, c.social || {}, {
        human: n.human,
        farm: n.farm,
        note: n.note,
        tone: n.tone
      });
      const flags = new Set(c.flags || []);
      flags.add("intel");
      if(n.tone) flags.add(n.tone);
      c.flags = Array.from(flags);
    });
  }
  if(typeof renderTable === "function") renderTable();
  if(typeof renderDetail === "function") renderDetail();
  if(typeof renderBots === "function") renderBots();
  honestZeros();
}
function honestZeros(){
  document.querySelectorAll("#rows tr").forEach(tr => {
    const tds = tr.querySelectorAll("td");
    if(tds[2] && /^[+−-]?0\.0%$/.test(tds[2].textContent.trim())){
      tds[2].textContent = "n/a";
      tds[2].className = "flat";
    }
    if(tds[6] && tds[6].textContent.trim() === "0%"){
      tds[6].textContent = "n/a";
      tds[6].className = "flat";
    }
  });
}
async function bootIntel(){
  try{
    const res = await fetch("intel.json?ts=" + Date.now());
    if(!res.ok) throw new Error("intel HTTP " + res.status);
    applyIntel(await res.json());
  }catch(e){
    const tape = document.getElementById("tape");
    if(tape) tape.innerHTML = '<div class="event social"><time>intel</time><b>CIO</b><p>intel.json missing. Read the 07:00 run in Grok Automations.</p></div>';
  }
}
const _renderTable = typeof renderTable === "function" ? renderTable : null;
if(_renderTable){
  renderTable = function(){
    const r = _renderTable.apply(this, arguments);
    honestZeros();
    return r;
  };
}
const _connectLive = typeof connectLive === "function" ? connectLive : null;
if(_connectLive){
  connectLive = async function(){
    const r = await _connectLive.apply(this, arguments);
    if(window.INTEL) applyIntel(window.INTEL);
    return r;
  };
}
bootIntel();
