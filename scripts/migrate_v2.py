#!/usr/bin/env python3
"""One-off migration of Solada desk files to shared format v2 (renames only, keeps all data)."""
import json,sys
def load(f):
    raw=open(f).read(); l=raw.split("\n")[1]; return json.loads(raw),len(l)-len(l.lstrip()),raw.endswith("\n")
def save(f,d,ind,nl): open(f,"w").write(json.dumps(d,indent=ind,ensure_ascii=False)+("\n" if nl else ""))
REN={"stop":"kill_line","thesis_signals":"thesis","signal_review":"review"}
def ticket(t,unit):
    out={}
    for k,v in t.items():
        if k in REN: out[REN[k]]=v
        elif k in("ticket_ada","ticket_ada_equivalent"): out["size"]=v
        elif k in("paper_pnl_ada","paper_pnl_ada_equiv","pct_change"):
            r=out.setdefault("result",{"unit":unit})
            r["pct" if k=="pct_change" else "pnl"]=v
        else: out[k]=v
    return out
def book(f,bookid,chain,unit):
    d,ind,nl=load(f)
    if d.get("schema_version")==2: return
    n=(len(d["open"]),len(d["closed"]),len(d["rules"]),len(d["summaries"]))
    o={"schema_version":2,"desk":d.get("desk"),"book":bookid,"chain":chain,"unit":unit}
    for k,v in d.items():
        if k in("schema_version","desk"): continue
        if k in("paper_ticket_ada","paper_ticket_ada_equivalent"): o["ticket_size"]=v
        elif k in("open","closed"): o[k]=[ticket(t,unit) for t in v]
        else: o[k]=v
    assert (len(o["open"]),len(o["closed"]),len(o["rules"]),len(o["summaries"]))==n
    save(f,o,ind,nl); print(f,"open/closed/rules/summaries",n,"->",n)
def log(f):
    d,ind,nl=load(f)
    if d.get("schema_version")==2: return
    n=(len(d["history"]),len(d["followups"]))
    o={"schema_version":2,"desk":"solada"}; o.update(d)
    assert (len(o["history"]),len(o["followups"]))==n
    save(f,o,ind,nl); print(f,"history/followups",n,"->",n)
book("solada/paper.json","solada","cardano","ADA")
book("solada/pumpfun/paper.json","pumpfun","solana","ADA-equivalent")
log("solada/log.json")
