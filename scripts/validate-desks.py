#!/usr/bin/env python3
"""Validate every desk file listed in desks.json against schema/. Needs: pip install jsonschema"""
import json,os,sys,glob
from jsonschema import Draft202012Validator
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)),".."))
S={k:Draft202012Validator(json.load(open(f"schema/{k}.schema.json"))) for k in("paper-book","run-log","brief","pulse")}
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
bad=0
for f,k in jobs:
    if not os.path.exists(f): print(f"MISSING {f}"); bad+=1; continue
    errs=list(S[k].iter_errors(json.load(open(f))))
    for e in errs[:20]: print(f"INVALID {f} ({k}) at /{'/'.join(map(str,e.path))}: {e.message[:200]}")
    bad+=bool(errs)
    if not errs: print(f"ok {f} ({k})")
sys.exit(1 if bad else 0)
