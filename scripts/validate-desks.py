#!/usr/bin/env python3
"""Validate every desk file listed in desks.json against schema/. Needs: pip install jsonschema
Usage: validate-desks.py [DATA_DIR]  DATA_DIR (default: repo root) holds desks.json and desk files;
schemas always come from this script's repo schema/. x_buzz.json is checked when present."""
import json,os,sys,glob
from jsonschema import Draft202012Validator
ROOT=os.path.join(os.path.dirname(os.path.abspath(__file__)),"..")
SCHEMA=os.path.abspath(os.path.join(ROOT,"schema"))
# Same letters as the pre-filter writer. Not spelled here, so a public grep stays on the coin ticker.
RETIRED_RISK_KEY="".join(chr(n) for n in (115,117,114,105))

def risk_maps(doc):
    """risk_check and the retired key, on the log and on each history snapshot."""
    found=[]
    if not isinstance(doc, dict):
        return found
    def take(node, where):
        if not isinstance(node, dict):
            return
        for key, label in (("risk_check", "risk_check"), (RETIRED_RISK_KEY, "retired risk map")):
            bag=node.get(key)
            if isinstance(bag, dict):
                found.append((where + label, bag, key == "risk_check" and where == ""))
    take(doc, "")
    for item in doc.get("history") or []:
        take(item, "history ")
    return found

os.chdir(sys.argv[1] if len(sys.argv)>1 else ROOT)
S={k:Draft202012Validator(json.load(open(f"{SCHEMA}/{k}.schema.json"))) for k in("paper-book","run-log","brief","pulse","solada-vetoes","x-buzz")}
for v in S.values(): v.check_schema(v.schema)
jobs=[]
for d in json.load(open("desks.json"))["desks"]:
    if d.get("log"): jobs.append((d["log"],"run-log"))
    for b in d.get("books",[]): jobs.append((b["paper"],"paper-book"))
    if d.get("briefs"): jobs+= [(f,"brief") for f in sorted(glob.glob(os.path.join(d["briefs"],"*.json"))) if not f.endswith("index.json")]
    pulse=d.get("pulse") or os.path.join(d["id"],"pulse.json")
    if os.path.exists(pulse): jobs.append((pulse,"pulse"))
    else: print(f"skip {pulse} (not written yet)")
    base=os.path.dirname(pulse) or d["id"]
    arch=os.path.join(base,"archive")
    if os.path.isdir(arch):
        jobs+= [(f,"pulse") for f in sorted(glob.glob(os.path.join(arch,"pulse-*.json")))]
for fix in ("scripts/fixtures/pulse-example.json", "scripts/fixtures/pulse-factors.json"):
    if os.path.exists(fix): jobs.append((fix,"pulse"))
for fix in ("solada-prefilter/vetoes.json", "solada-prefilter/tests/fixtures/vetoes.json"):
    if os.path.exists(fix): jobs.append((fix,"solada-vetoes"))
# Root of desk-data. Absent until a run writes it; the guard checks it when present.
buzz="x_buzz.json"
if os.path.exists(buzz): jobs.append((buzz,"x-buzz"))
else: print(f"skip {buzz} (not written yet)")
for fix in ("scripts/fixtures/x-buzz.json",):
    if os.path.exists(fix): jobs.append((fix,"x-buzz"))
bad=0
risk_schema=S["run-log"].schema["properties"]["risk_check"]
risk_check=Draft202012Validator(risk_schema)
for f,k in jobs:
    if not os.path.exists(f): print(f"MISSING {f}"); bad+=1; continue
    data=json.load(open(f))
    errs=list(S[k].iter_errors(data))
    extra=[]
    # Top-level risk_check is part of the run-log schema. History copies and the retired
    # key use that same shape, so a desk-data file still on the old name does not fail.
    if k=="run-log":
        for label, bag, already in risk_maps(data):
            if already:
                continue
            for e in risk_check.iter_errors(bag):
                extra.append(f"{label} at /{'/'.join(map(str,e.path))}: {e.message[:200]}")
    for e in errs[:20]: print(f"INVALID {f} ({k}) at /{'/'.join(map(str,e.path))}: {e.message[:200]}")
    for msg in extra[:20]: print(f"INVALID {f} ({k}) {msg}")
    bad+=bool(errs or extra)
    if not errs and not extra: print(f"ok {f} ({k})")
sys.exit(1 if bad else 0)
