"""SPD-65 research vetoes (zero AI). No chain calls.

Reads the dev_checks and liquidity_moves files this run already wrote and emits
desk-data:solada-prefilter/vetoes.json. One row per token, each with a `vetoes` list.
Codes are standing research flags, not buy signals and not webhook triggers.

dev_sold: that file's dev_sold_ada is at least its min_sell_ada, or late_rug is true.
serial_launcher: serial_3h / serial_7d reached the existing caution floors, or prior_rugs >= 1.
lp_pulled: a liquidity move with kind lp_remove. Adds and swaps are not vetoes.

Evidence values are tags only (a w- hash or a role label). Owner wallets are never
tagged: the same _patterns / _hidden check dev_checks uses. Missing or unusable owner
patterns strip every tag (fail closed) and still keep the token-level code. A bad
document is not written. This module does not change desk rules, kill lines, or thresholds.
"""
import os, re, sys

import dev_checks as DC
import flags as F
import koios_tx as KT

NOTE = (
    "Research vetoes for the Cardano desk. Never a buy signal. They do not change desk rules, "
    "kill lines, or thresholds. Evidence values are wallet tags only."
)
CODES = ("dev_sold", "serial_launcher", "lp_pulled")
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
POLICY_RE = re.compile(r"^[0-9a-f]{8,56}$")
UNIT_RE = re.compile(r"^[0-9a-f]{56,200}$")
AT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _floor(doc, cfg, key, default):
    th = doc.get("thresholds") if isinstance(doc, dict) else None
    if isinstance(th, dict) and key in th and _num(th[key]):
        return th[key]
    dc = DC.dc_cfg(cfg)
    if key in dc and _num(dc[key]):
        return dc[key]
    return default


def keep_tag(tag, roles, loaded):
    """A public tag, or None. Unusable owner patterns, a hidden owner, or any other shape drops it."""
    if not isinstance(tag, str) or not DC.dev_tag_ok(tag, roles):
        return None
    if not loaded:
        return None
    pats, stakes = loaded
    if DC._hidden(pats, stakes, tag):
        return None
    return tag


def _policy(value):
    if not isinstance(value, str):
        return None
    raw = "".join(c for c in value if c in "0123456789abcdefABCDEF").lower()
    if raw != value.lower() or not POLICY_RE.fullmatch(raw):
        return None
    return raw


def _unit(value):
    if not isinstance(value, str):
        return None
    raw = value.lower()
    return raw if UNIT_RE.fullmatch(raw) else None


def _ticker(value):
    if not isinstance(value, str):
        return None
    raw = value.strip().upper()
    return raw if TICKER_RE.fullmatch(raw) and raw != "LP" else None


def _prefix(a, b):
    return bool(a and b and (a.startswith(b) or b.startswith(a)))


class _Tokens:
    def __init__(self):
        self.rows = []

    def find(self, ticker, policy, unit):
        for row in self.rows:
            if _prefix(policy, row.get("policy_short")):
                return row
            if ticker and row.get("ticker") == ticker:
                if policy and row.get("policy_short") and not _prefix(policy, row.get("policy_short")):
                    continue
                return row
            if unit and row.get("unit") == unit:
                return row
        return None

    def add(self, ticker, policy, unit):
        if not ticker:
            return None
        row = self.find(ticker, policy, unit)
        if row is None:
            row = {"ticker": ticker, "vetoes": []}
            if policy:
                row["policy_short"] = policy
            if unit:
                row["unit"] = unit
            self.rows.append(row)
            return row
        if row.get("ticker") in (None, "TOKEN"):
            row["ticker"] = ticker
        if policy and (not row.get("policy_short") or len(policy) > len(row["policy_short"])):
            row["policy_short"] = policy
        if unit and not row.get("unit"):
            row["unit"] = unit
        return row

    def veto(self, row, code, evidence):
        if row is None or code not in CODES:
            return
        for cur in row["vetoes"]:
            if cur["code"] == code:
                for k, v in evidence.items():
                    cur["evidence"].setdefault(k, v)
                return
        row["vetoes"].append({"code": code, "evidence": dict(evidence)})


def _evidence(field, tag):
    return {field: tag} if tag else {}


def from_dev_checks(doc, cfg, roles, loaded, tokens):
    if not isinstance(doc, dict):
        return
    min_sell = _floor(doc, cfg, "min_sell_ada", 25)
    s3 = _floor(doc, cfg, "serial_caution_3h", 2)
    s7 = _floor(doc, cfg, "serial_caution_7d", 3)
    for entry in doc.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        ticker = _ticker(entry.get("ticker"))
        policy = _policy(entry.get("policy_short"))
        unit = _unit(entry.get("unit"))
        if not ticker and not policy:
            continue
        tag = keep_tag(entry.get("dev_tag"), roles, loaded)
        ev = _evidence("dev_tag", tag)
        sold = entry.get("dev_sold_ada")
        if entry.get("late_rug") is True or (_num(sold) and sold >= min_sell):
            tokens.veto(tokens.add(ticker, policy, unit), "dev_sold", ev)
        serial = (
            (_num(entry.get("serial_3h")) and entry["serial_3h"] >= s3)
            or (_num(entry.get("serial_7d")) and entry["serial_7d"] >= s7)
            or (_num(entry.get("prior_rugs")) and entry["prior_rugs"] >= 1)
        )
        if serial:
            tokens.veto(tokens.add(ticker, policy, unit), "serial_launcher", ev)


def from_liquidity(doc, roles, loaded, tokens):
    if not isinstance(doc, dict):
        return
    for move in doc.get("moves") or []:
        if not isinstance(move, dict) or move.get("kind") != "lp_remove":
            continue
        if move.get("lp") is True:
            continue
        ticker = _ticker(move.get("ticker"))
        policy = _policy(move.get("policy_short"))
        if not ticker and not policy:
            continue
        tag = keep_tag(move.get("actor_tag"), roles, loaded)
        tokens.veto(tokens.add(ticker, policy, None), "lp_pulled", _evidence("actor_tag", tag))


def build(dev_doc, liq_doc, cfg, owner_env, at):
    loaded = DC._patterns(owner_env, who="vetoes")
    roles = DC.known_roles(cfg)
    tokens = _Tokens()
    from_dev_checks(dev_doc, cfg, roles, loaded, tokens)
    from_liquidity(liq_doc, roles, loaded, tokens)
    rows = [r for r in tokens.rows if r.get("vetoes")]
    for row in rows:
        row["vetoes"] = sorted(row["vetoes"], key=lambda v: CODES.index(v["code"]))
    rows.sort(key=lambda r: (r.get("ticker") or "", r.get("policy_short") or ""))
    doc = {
        "schema_version": 1,
        "at": at,
        "desk": "solada",
        "note": NOTE,
        "buy_signal": False,
        "sources": ["dev_checks", "liquidity_moves"],
        "tokens": rows,
    }
    validate_doc(doc)
    F.assert_public(doc)
    return doc


def validate_doc(doc):
    """Same shape as schema/solada-vetoes.schema.json. Raises SystemExit. Does not print values."""
    if not isinstance(doc, dict):
        raise SystemExit("vetoes: document is not an object")
    if doc.get("schema_version") != 1 or doc.get("desk") != "solada" or doc.get("buy_signal") is not False:
        raise SystemExit("vetoes: schema header rejected")
    if not isinstance(doc.get("note"), str) or not doc["note"]:
        raise SystemExit("vetoes: note rejected")
    if not isinstance(doc.get("at"), str) or not AT_RE.fullmatch(doc["at"]):
        raise SystemExit("vetoes: at rejected")
    if doc.get("sources") != ["dev_checks", "liquidity_moves"]:
        raise SystemExit("vetoes: sources rejected")
    allowed = {"schema_version", "at", "desk", "note", "buy_signal", "sources", "tokens"}
    if set(doc) - allowed:
        raise SystemExit("vetoes: unexpected field")
    rows = doc.get("tokens")
    if not isinstance(rows, list):
        raise SystemExit("vetoes: tokens rejected")
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"ticker", "policy_short", "unit", "vetoes"}:
            raise SystemExit("vetoes: token rejected")
        if not isinstance(row.get("ticker"), str) or not TICKER_RE.fullmatch(row["ticker"]):
            raise SystemExit("vetoes: ticker rejected")
        if "policy_short" in row and (not isinstance(row["policy_short"], str) or not POLICY_RE.fullmatch(row["policy_short"])):
            raise SystemExit("vetoes: policy rejected")
        if "unit" in row and (not isinstance(row["unit"], str) or not UNIT_RE.fullmatch(row["unit"])):
            raise SystemExit("vetoes: unit rejected")
        vetoes = row.get("vetoes")
        if not isinstance(vetoes, list) or not vetoes:
            raise SystemExit("vetoes: empty veto list")
        for veto in vetoes:
            if not isinstance(veto, dict) or set(veto) != {"code", "evidence"} or veto.get("code") not in CODES:
                raise SystemExit("vetoes: code rejected")
            ev = veto.get("evidence")
            if not isinstance(ev, dict) or set(ev) - {"dev_tag", "actor_tag"}:
                raise SystemExit("vetoes: evidence rejected")
            for value in ev.values():
                if not DC.dev_tag_ok(value, DC.known_roles()):
                    raise SystemExit("vetoes: evidence tag rejected")
    return doc


def run(data_dir, cfg, dry_run, owner_env=None, now_s=None):
    """Write vetoes.json unless dry_run. Never raises. Returns a small summary."""
    try:
        if not ((cfg or {}).get("vetoes") or {}).get("enabled", True):
            return {"enabled": False, "wrote": False}
        dev_doc = KT.read_json(os.path.join(data_dir, "dev_checks.json"), {})
        liq_doc = KT.read_json(os.path.join(data_dir, "liquidity_moves.json"), {})
        at = DC.iso(now_s if now_s is not None else __import__("time").time())
        doc = build(dev_doc, liq_doc, cfg, owner_env, at)
        path = os.path.join(data_dir, "vetoes.json")
        if not dry_run:
            KT.write_json(path, doc, indent=1)
        counts = {c: 0 for c in CODES}
        for row in doc["tokens"]:
            for veto in row["vetoes"]:
                counts[veto["code"]] += 1
        return {"wrote": not dry_run, "tokens": len(doc["tokens"]), "counts": counts, "buy_signal": False}
    except (SystemExit, Exception) as e:
        print(f"vetoes: skipped ({type(e).__name__})", file=sys.stderr)
        return {"wrote": False, "error": type(e).__name__}
