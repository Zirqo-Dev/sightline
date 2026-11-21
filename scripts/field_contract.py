"""Field names the site or a report reads, taken from the reader source.

Writers are not described here. A test runs the writer, or reads the file the
writer already left on main, and checks those names are present.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

_FIELDS = re.compile(
    r"(\w+)\s*:\s*\[([^\]]*)\]",
)
_STRING = re.compile(r'"([^"]+)"|\'([^\']+)\'')


def js_fields(text):
    """The FIELDS object: group name to the alias list, first alias first."""
    start = text.find("var FIELDS = {")
    if start < 0:
        raise ValueError("reader has no FIELDS object")
    body = _brace(text, text.find("{", start))
    found = {}
    for match in _FIELDS.finditer(body):
        names = [a or b for a, b in _STRING.findall(match.group(2))]
        if names:
            found[match.group(1)] = names
    if "id" not in found or "netPct" not in found:
        raise ValueError("FIELDS is missing id or netPct")
    return found


def _brace(text, open_at):
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at:i + 1]
    raise ValueError("unbalanced braces")


def function_body(text, name):
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\(", text)
    if not match:
        raise ValueError("no function " + name)
    open_at = text.find("{", match.end())
    return _brace(text, open_at)


def between(text, start, end):
    i = text.find(start)
    if i < 0:
        raise ValueError("missing " + start)
    j = text.find(end, i + len(start))
    if j < 0:
        raise ValueError("missing " + end)
    return text[i:j]


def _assigned(body, group_re):
    return re.findall(group_re, body)


def required_any_alias(body):
    """Groups the reader uses to keep a row. Any alias may satisfy them.

    A row is dropped when the picked id or symbol is empty, or when a numeric
    pick is null and the function returns.
    """
    required = set()
    for var, group in _assigned(
        body,
        r"var\s+(\w+)\s*=\s*canonId\(\s*pick\(\s*\w+\s*,\s*FIELDS\.(\w+)\s*\)\s*\)",
    ):
        if re.search(r"if\s*\(\s*!" + var + r"\b", body):
            required.add(group)
    for var, group in _assigned(
        body,
        r"var\s+(\w+)\s*=\s*(?:cleanSymbol|num)\(\s*pick(?:Rank)?\(\s*\w+\s*,\s*FIELDS\.(\w+)\s*\)\s*\)",
    ):
        if re.search(r"if\s*\(\s*!" + var + r"\b", body):
            required.add(group)
        if re.search(r"if\s*\(\s*" + var + r"\s*==\s*null\s*\)\s*return", body):
            required.add(group)
    return required


def required_first_alias(body):
    """Groups where the first alias is the value, even when it is null.

    pickRank returns the first key when that key is present, including null.
    hasOwnProperty on FIELDS.x[0] does the same for the interval.
    """
    groups = set(re.findall(r"pickRank\(\s*\w+\s*,\s*FIELDS\.(\w+)\s*\)", body))
    groups.update(re.findall(r"hasOwnProperty\.call\(\s*\w+\s*,\s*FIELDS\.(\w+)\[0\]", body))
    return groups


def dotted(text, name):
    return re.findall(r"\b" + re.escape(name) + r"\.([A-Za-z_][A-Za-z0-9_]*)", text)


def string_list(body, var):
    match = re.search(r"var\s+" + var + r"\s*=\s*\[([^\]]*)\]", body)
    if not match:
        raise ValueError("no list " + var)
    return [a or b for a, b in _STRING.findall(match.group(1))]


def read_race_view_in(repo, directory, summary_text, equity, positions, trades):
    """Run revamp/races.js on writer output. Returns the view the page builds."""
    pack_path = os.path.join(directory, "pack.json")
    with open(pack_path, "w", encoding="utf-8") as fh:
        json.dump({
            "summary": summary_text,
            "equity": equity,
            "positions": positions,
            "trades": trades,
        }, fh)
    script = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = { console: console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox, { filename: "races.js" });
const R = sandbox.SightlineRaces;
if (!R || !R.buildView) {
  process.stderr.write("reader did not load\n");
  process.exit(2);
}
const pack = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const summary = JSON.parse(pack.summary);
const view = R.buildView(summary, {
  equity: pack.equity,
  positions: pack.positions,
  trades: pack.trades
}, { source: "writer" });
let solCanon = 0;
String(pack.equity || "").split(/\n/).forEach(function (line) {
  if (!line.trim()) return;
  let row = null;
  try { row = JSON.parse(line); } catch (e) { return; }
  const id = R.canonId(row.wallet || row.id || row.wallet_id || "");
  if (id === "SOL") solCanon += 1;
});
process.stdout.write(JSON.stringify({
  wallets: view.wallets.map(function (w) {
    return {
      id: w.id, role: w.role, listed: w.listed, netPct: w.netPct, n: w.n,
      lo: w.lo, hi: w.hi, avgDeployed: w.avgDeployed, series: w.series.length,
      trades: w.trades.length, positions: w.positions.length
    };
  }),
  overlap: view.overlap,
  split: view.split,
  solCanon: solCanon
}));
"""
    proc = subprocess.run(
        ["node", "-e", script, os.path.join(repo, "revamp", "races.js"), pack_path],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "node could not run the race reader")
    return json.loads(proc.stdout)
