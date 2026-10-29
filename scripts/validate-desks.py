#!/usr/bin/env python3
"""Validate every desk file listed in desks.json against schema/. Needs: pip install jsonschema
Usage: validate-desks.py [DATA_DIR]  DATA_DIR (default: repo root) holds desks.json and desk files;
schemas always come from this script's repo schema/. x_buzz.json and x_buzz_history.jsonl
are checked when present. The history file is JSONL: each non-empty line is one record."""
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
S={k:Draft202012Validator(json.load(open(f"{SCHEMA}/{k}.schema.json"))) for k in("paper-book","run-log","brief","pulse","solada-vetoes","solana-rugger-log","x-buzz","x-buzz-history","whale-roster")}
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
for fix in ("solana-desk/rugger_would_veto.json", "solana-desk/tests/fixtures/rugger_would_veto.json"):
    if os.path.exists(fix): jobs.append((fix,"solana-rugger-log"))
roster="solada-prefilter/whale_roster.json"
if os.path.exists(roster): jobs.append((roster,"whale-roster"))
else: print(f"skip {roster} (not written yet)")
for fix in ("scripts/fixtures/whale-roster.json",):
    if os.path.exists(fix): jobs.append((fix,"whale-roster"))
# Root of desk-data. Absent until a run writes it; the guard checks it when present.
buzz="x_buzz.json"
if os.path.exists(buzz): jobs.append((buzz,"x-buzz"))
else: print(f"skip {buzz} (not written yet)")
for fix in ("scripts/fixtures/x-buzz.json",):
    if os.path.exists(fix): jobs.append((fix,"x-buzz"))
# Append-only. One JSON object per non-empty line. Absent until a run writes it.
history=[]
hist="x_buzz_history.jsonl"
if os.path.exists(hist): history.append(hist)
else: print(f"skip {hist} (not written yet)")
for fix in ("scripts/fixtures/x-buzz-history.jsonl",):
    if os.path.exists(fix): history.append(fix)
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

def history_errors(path):
    """Validate each non-empty line. Blank lines are not records."""
    found=[]
    with open(path,encoding="utf-8") as fh:
        for n,line in enumerate(fh,1):
            if not line.strip(): continue
            try: row=json.loads(line)
            except json.JSONDecodeError as e:
                found.append(f"line {n}: {e.msg}"); continue
            for e in S["x-buzz-history"].iter_errors(row):
                found.append(f"line {n} at /{'/'.join(map(str,e.path))}: {e.message[:200]}")
    return found

for f in history:
    if not os.path.exists(f): print(f"MISSING {f}"); bad+=1; continue
    errs=history_errors(f)
    for msg in errs[:20]: print(f"INVALID {f} (x-buzz-history) {msg}")
    bad+=bool(errs)
    if not errs: print(f"ok {f} (x-buzz-history)")
sys.exit(1 if bad else 0)
