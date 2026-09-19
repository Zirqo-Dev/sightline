function liveMark(r){
  if(r.voided) return {mark:0, src:"void", ok:false};
  if(!r.open) return {mark:r.exit, src:"exit", ok:!!r.exit};
  const live = rows.find(x => x.slug === r.slug);
  if(live && live.bid > 0) return {mark:live.bid, src:"bid", ok:true};
  if(live && live.floor > 0) return {mark:live.floor, src:"floor", ok:true};
  if(r.bid > 0) return {mark:r.bid, src:"last bid", ok:true};
  return {mark:0, src:"n/a", ok:false};
}
function signedEth(n){
  if(n == null || !Number.isFinite(n)) return "n/a";
  const sign = n > 0 ? "+" : "";
  return sign + fmtEth(n);
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
  let openN = 0, notion = 0, ethGross = 0, ethNet = 0;
  body.innerHTML = all.map(r => {
    const when = (r.ts || "").slice(5,10) + " " + (r.ts || "").slice(11,16);
    const qty = Number(r.qty) || 0;
    if(r.side === "pass"){
      return `<tr><td class="slug">${when}</td><td>${r.name}</td><td>pass</td><td>0</td><td>${fmtEth(r.entry)}</td><td class="flat">-</td><td class="flat">-</td><td class="flat">-</td><td></td></tr>`;
    }
    if(r.voided){
      return `<tr><td class="slug">${when}</td><td>${r.name}</td><td>void</td><td>${qty}</td><td>${fmtEth(r.entry)}</td><td class="flat">void</td><td class="flat">-</td><td class="flat">-</td><td></td></tr>`;
    }
    const m = liveMark(r);
    const entry = Number(r.entry) || 0;
    const gEth = m.ok ? (m.mark - entry) * (qty || 1) : null;
    const nEth = gEth == null ? null : gEth - 0.05 * entry * (qty || 1);
    const gPct = m.ok && entry ? (m.mark - entry) / entry : null;
    const nPct = gPct == null ? null : gPct - 0.05;
    if(r.open && nEth != null){
      openN++;
      notion += entry * (qty || 1);
      ethGross += gEth;
      ethNet += nEth;
    }
    const gcls = gEth == null ? "flat" : gEth > 0 ? "up" : gEth < 0 ? "down" : "flat";
    const ncls = nEth == null ? "flat" : nEth > 0 ? "up" : nEth < 0 ? "down" : "flat";
    const gtxt = gEth == null ? "n/a" : (signedEth(gEth) + " <span class=\"slug\">" + ((gPct>0?"+":"") + (gPct*100).toFixed(1) + "%") + "</span>");
    const ntxt = nEth == null ? "n/a" : (signedEth(nEth) + " <span class=\"slug\">" + ((nPct>0?"+":"") + (nPct*100).toFixed(1) + "%") + "</span>");
    const markCell = m.ok ? `${fmtEth(m.mark)} <span class="slug">${m.src}</span>` : "n/a";
    return `<tr><td class="slug">${when}</td><td>${r.name}</td><td>${r.side}${r.open ? " · open" : ""}</td><td>${qty}</td><td>${fmtEth(entry)}</td><td>${markCell}</td><td class="${gcls}">${gtxt}</td><td class="${ncls}" style="font-weight:700">${ntxt}</td><td>${r.open ? `<button onclick="closePaper('${r.slug}')">close</button>` : ""}</td></tr>`;
  }).join("");
  if(sum){
    if(!openN) sum.innerHTML = '<span class="flat">No open paper.</span>';
    else {
      const cls = ethNet > 0 ? "up" : ethNet < 0 ? "down" : "flat";
      const gcls = ethGross > 0 ? "up" : ethGross < 0 ? "down" : "flat";
      sum.innerHTML = `<strong>${openN} open</strong> · ${fmtEth(notion)} ETH in · net <span class="${cls}">${signedEth(ethNet)} ETH</span> · gross <span class="${gcls}">${signedEth(ethGross)} ETH</span> · net = mark minus 5% of entry · Refresh marks`;
    }
  }
}
renderBlotter();
