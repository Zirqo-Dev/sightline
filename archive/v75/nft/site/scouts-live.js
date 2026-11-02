function renderBots(){
  const el = document.getElementById("bots");
  if(!el) return;
  const c = (typeof rows !== "undefined" && rows.find(x => x.slug === selected)) || (rows && rows[0]) || {};
  const on = typeof live !== "undefined" && live && !!c.floor;
  const gap = (c.floor && c.bid) ? (((c.floor - c.bid) / c.floor) * 100).toFixed(1) + "%" : "n/a";
  const tiles = [
    {id:"floor", name:"FLOOR", task: on ? (c.name + " floor " + fmtEth(c.floor) + " ETH · 24h " + (Number(c.chg||0)>0?"+":"") + Number(c.chg||0).toFixed(1) + "%") : "Connect live. Floor comes from OpenSea stats.", last: on ? "OpenSea" : "idle"},
    {id:"tape", name:"TAPE", task: on ? (Number(c.vol||0).toFixed(1) + " ETH vol · " + (c.sales||0) + " sales (24h)") : "Connect live for volume and sales.", last: on ? "OpenSea" : "idle"},
    {id:"book", name:"BOOK", task: on ? (c.bid ? ("Bid " + fmtEth(c.bid) + " · gap " + gap) : "Bid n/a — check OpenSea offers. Flag bid-miss.") : "Connect live for bid vs floor.", last: on ? (c.bid ? "OpenSea bid" : "bid-miss") : "idle"},
    {id:"hold", name:"HOLD", task: on ? ((Number(c.holders||0).toLocaleString()) + " holders · listed " + (c.listed||0) + "%") : "Connect live for holders.", last: on ? "OpenSea" : "idle"},
    {id:"wash", name:"WASH", task: "Heuristic only. Wash " + (c.wash||"-") + " — not an on-chain detector.", last: "heuristic"},
    {id:"mint", name:"MINT", task: "Observe only. No mint sniper.", last: "observe"},
    {id:"social", name:"SOCIAL", task: "Canned until 07:00 Grok tape. Human " + ((c.social&&c.social.human)||"-") + " / farm " + ((c.social&&c.social.farm)||"-"), last: "Grok 07:00"},
    {id:"risk", name:"RISK", task: (c.risk || "-") + (c.flags && c.flags.includes("live-miss") ? " · live-miss" : ""), last: on ? "flags" : "idle"}
  ];
  el.innerHTML = tiles.map(b => {
    const pill = (b.id === "social") ? "X" : (on && b.last !== "heuristic" && b.last !== "observe" ? "LIVE" : (b.last === "idle" ? "OFF" : "NOTE"));
    const cls = pill === "LIVE" ? "live" : "";
    return `<article class="bot"><div class="top"><div class="name">${b.name}</div><span class="pill ${cls}">${pill}</span></div><p>${b.task}</p><div class="meta">${b.last}</div></article>`;
  }).join("");
}
(function(){
  const wrap = fn => {
    if(typeof fn !== "function") return fn;
    return function(){ const r = fn.apply(this, arguments); try{ renderBots(); }catch(e){} return r; };
  };
  if(typeof renderTable === "function") renderTable = wrap(renderTable);
  if(typeof renderDetail === "function") renderDetail = wrap(renderDetail);
  renderBots();
})();
