function liveMark(r){
  if(!r.open) return {mark:r.exit, src:"exit", ok:!!r.exit};
  const live = rows.find(x => x.slug === r.slug);
  if(live && live.bid > 0) return {mark:live.bid, src:"bid", ok:true};
  if(live && live.floor > 0) return {mark:live.floor, src:"floor", ok:true};
  if(r.bid > 0) return {mark:r.bid, src:"last bid", ok:true};
  return {mark:0, src:"n/a", ok:false};
}
function renderBlotter(){
  const all = loadBlotter();
  const empty = document.getElementById("blotterEmpty");
  const table = document.getElementById("blotterTable");
  const body = document.getElementById("blotterRows");
  const sum = document.getElementById("blotterSummary");
  if(!all.length){
    empty.style.display = "block";
    table.style.display = "none";
    if(sum) sum.innerHTML = "";
    return;
  }
  empty.style.display = "none";
  table.style.display = "table";
  let openN = 0, openNet = 0, openGross = 0;
  body.innerHTML = all.map(r => {
    const when = (r.ts || "").slice(5,10) + " " + (r.ts || "").slice(11,16);
    if(r.side === "pass"){
      return `<tr><td class="slug">${when}</td><td>${r.name}</td><td>pass</td><td>0</td><td>${fmtEth(r.entry)}</td><td class="flat">-</td><td class="flat">-</td><td class="flat">-</td><td></td></tr>`;
    }
    const m = liveMark(r);
    const entry = Number(r.entry) || 0;
    const gross = m.ok && entry ? ((m.mark - entry) / entry) : null;
    const net = gross == null ? null : gross - 0.05;
    if(r.open && net != null){ openN++; openNet += net; openGross += gross; }
    const gtxt = gross == null ? "n/a" : ((gross > 0 ? "+" : "") + (gross * 100).toFixed(1) + "%");
    const ntxt = net == null ? "n/a" : ((net > 0 ? "+" : "") + (net * 100).toFixed(1) + "%");
    const gcls = gross == null ? "flat" : gross > 0 ? "up" : gross < 0 ? "down" : "flat";
    const ncls = net == null ? "flat" : net > 0 ? "up" : net < 0 ? "down" : "flat";
    const markCell = m.ok ? `${fmtEth(m.mark)} <span class="slug">${m.src}</span>` : "n/a";
    return `<tr><td class="slug">${when}</td><td>${r.name}</td><td>${r.side}${r.open ? " · open" : ""}</td><td>${r.qty}</td><td>${fmtEth(entry)}</td><td>${markCell}</td><td class="${gcls}">${gtxt}</td><td class="${ncls}" style="font-weight:700">${ntxt}</td><td>${r.open ? `<button onclick="closePaper('${r.slug}')">close</button>` : ""}</td></tr>`;
  }).join("");
  if(sum){
    if(!openN) sum.innerHTML = '<span class="flat">No open paper.</span>';
    else {
      const cls = openNet > 0 ? "up" : openNet < 0 ? "down" : "flat";
      const gcls = openGross > 0 ? "up" : openGross < 0 ? "down" : "flat";
      sum.innerHTML = `<strong>${openN} open</strong> · book net <span class="${cls}">${openNet > 0 ? "+" : ""}${(openNet * 100).toFixed(1)}%</span> · gross <span class="${gcls}">${openGross > 0 ? "+" : ""}${(openGross * 100).toFixed(1)}%</span> · net includes 5% fees · Refresh marks to pull OpenSea`;
    }
  }
}
const btnMarks = document.getElementById("btnRefreshMarks");
if(btnMarks){
  btnMarks.onclick = function(){
    if(document.getElementById("apiKey").value.trim()) connectLive();
    else { alert("Connect live first so marks can use OpenSea."); renderBlotter(); }
  };
}
renderBlotter();
