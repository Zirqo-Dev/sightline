"""SPD-65 research vetoes (zero AI). No chain calls.

Reads the dev_checks and liquidity_moves files this run already wrote and emits
desk-data:solada-prefilter/vetoes.json. One row per token, each with a `vetoes` list.
Codes are standing research flags, not buy signals and not webhook triggers.

dev_sold: that file's dev_sold_ada is at least its min_sell_ada, or late_rug is true.
serial_launcher: serial_3h / serial_7d reached the existing caution floors, or prior_rugs >= 1.
lp_pulled: a liquidity lp_remove whose actor_tag is a role label or that token's dev tag.
A third-party pull is not an lp_pulled veto. LP-token rows are not vetoes. Adds and swaps
are not vetoes.
ahead_of_dump: its own code, not lp_pulled. A liquidity lp_remove whose ahead_of_dump meets
the Balanced floors (AHEAD_OF_DUMP: pull at least 20% of the pool reserve and 2,000 ADA,
and same-party sells inside 6h total at least 25% of that pull). A loose flag does not
count. Each such token carries a reason with those amounts only (no wallet). counts on
the document is the per-code total.

A source whose `at` is older than vetoes.max_age_h (default 5, about two 2h runs) is
skipped and listed under `stale`. A corrupt source keeps the previous vetoes.json.

Evidence values are tags only (a w- hash or a role label). Owner wallets are dropped
upstream, before these files are written; keep_tag is the backstop. A bad document is
not written. This module does not change desk rules, kill lines, or thresholds.
"""
import json, os, re, sys
from datetime import datetime, timezone

import dev_checks as DC
import flags as F
import koios_tx as KT
import liquidity_moves as LM

NOTE = (
    "Research vetoes for the Cardano desk. Never a buy signal. They do not change desk rules, "
    "kill lines, or thresholds. Evidence values are wallet tags only."
)
CODES = ("dev_sold", "serial_launcher", "lp_pulled", "ahead_of_dump")
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
POLICY_RE = re.compile(r"^[0-9a-f]{8,56}$")
UNIT_RE = re.compile(r"^[0-9a-f]{56,200}$")
AT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
REASON_RE = re.compile(r"^[A-Za-z0-9 .,;%:()=<>+-]+$")
# Amounts only. A wallet tag, address, or stake key in a reason is rejected.
REASON_BANNED = re.compile(r"stake|addr|w-", re.I)


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
    """A public tag, or None.

    Owner wallets are dropped upstream (dev_checks._quiet_owner, liquidity_moves._safe_tag)
    before those files are written, so a public dev_tag or actor_tag is already not an owner.
    This is the backstop: unusable patterns, a hidden owner, or any other shape still drop it.
    """
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
        """Same asset only. Two full units never merge on a shared policy prefix.

        Policy prefix (or ticker) is used only when at least one side has no unit.
        """
        for row in self.rows:
            if unit and row.get("unit"):
                if row["unit"] == unit:
                    return row
                continue
            if _prefix(policy, row.get("policy_short")):
                return row
            if ticker and row.get("ticker") == ticker:
                if policy and row.get("policy_short") and not _prefix(policy, row.get("policy_short")):
                    continue
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

    def veto(self, row, code, evidence, reason=None, rank=0):
        if row is None or code not in CODES:
            return
        for cur in row["vetoes"]:
            if cur["code"] == code:
                for k, v in evidence.items():
                    cur["evidence"].setdefault(k, v)
                if reason and rank >= cur.get("_rank", 0):
                    cur["reason"] = reason
                    cur["_rank"] = rank
                return
        item = {"code": code, "evidence": dict(evidence)}
        if reason:
            item["reason"] = reason
            item["_rank"] = rank
        row["vetoes"].append(item)


def _evidence(field, tag):
    return {field: tag} if tag else {}


def from_dev_checks(doc, cfg, roles, loaded, tokens, dev_index):
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
        if not ticker and not policy and not unit:
            continue
        tag = keep_tag(entry.get("dev_tag"), roles, loaded)
        if tag:
            dev_index.append((ticker, policy, unit, tag))
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


def _actor_is_dev(actor, ticker, policy, dev_index):
    """True when actor_tag is the dev tag of this token. A unit-less liquidity row may use the policy prefix."""
    if not actor:
        return False
    for tk, pol, _unit, tag in dev_index:
        if tag != actor:
            continue
        if policy and pol and _prefix(policy, pol):
            return True
        if ticker and tk == ticker and not (policy and pol and not _prefix(policy, pol)):
            return True
    return False


def _num_txt(value):
    value = float(value)
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def dump_reason(move, floors):
    """Amounts and the Balanced floors. No wallet, address, stake key, or tx hash."""
    floors = LM.floors_of(floors)
    ada, dump = LM._ada(move.get("ada")), LM._ada(move.get("dump_ada"))
    after = LM._ada(LM._reserve_after(move))
    if ada is None or dump is None or after is None or ada <= 0:
        return (
            f"pull meets the Balanced floors (>= {_num_txt(floors['pull_reserve_pct'])} percent "
            f"and >= {_num_txt(floors['pull_min_ada'])} ADA, same-party sell "
            f">= {_num_txt(floors['sell_of_pull_pct'])} percent inside {_num_txt(floors['window_h'])}h)"
        )
    before = after + ada
    pct = int(round(100.0 * ada / before)) if before > 0 else 0
    sell_pct = int(round(100.0 * dump / ada))
    return (
        f"pull {_num_txt(ada)} ADA is {pct} percent of the reserve "
        f"(>= {_num_txt(floors['pull_reserve_pct'])} percent and >= {_num_txt(floors['pull_min_ada'])} ADA); "
        f"same-party sell {_num_txt(dump)} ADA is {sell_pct} percent of the pull "
        f"inside {_num_txt(floors['window_h'])}h"
    )


def from_liquidity(doc, roles, loaded, tokens, dev_index, floors):
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
        ev = _evidence("actor_tag", tag)
        row = None
        # ahead_of_dump is its own veto. lp_pulled stays the dev or role pull.
        if LM.tight_ahead(move, floors):
            row = tokens.add(ticker, policy, None)
            ada = LM._ada(move.get("ada")) or 0
            tokens.veto(row, "ahead_of_dump", ev, dump_reason(move, floors), rank=ada)
        role = bool(tag) and tag in roles
        if role or _actor_is_dev(tag, ticker, policy, dev_index):
            tokens.veto(row or tokens.add(ticker, policy, None), "lp_pulled", ev)


def build(dev_doc, liq_doc, cfg, owner_env, at, stale=None):
    loaded = DC._patterns(owner_env, who="vetoes")
    roles = DC.known_roles(cfg)
    tokens = _Tokens()
    dev_index = []
    from_dev_checks(dev_doc, cfg, roles, loaded, tokens, dev_index)
    from_liquidity(liq_doc, roles, loaded, tokens, dev_index, LM.resolve_floors(liq_doc, cfg))
    rows = [r for r in tokens.rows if r.get("vetoes")]
    for row in rows:
        for veto in row["vetoes"]:
            veto.pop("_rank", None)
        row["vetoes"] = sorted(row["vetoes"], key=lambda v: CODES.index(v["code"]))
    rows.sort(key=lambda r: (r.get("ticker") or "", r.get("policy_short") or ""))
    doc = {
        "schema_version": 1,
        "at": at,
        "desk": "solada",
        "note": NOTE,
        "buy_signal": False,
        "sources": ["dev_checks", "liquidity_moves"],
        "stale": [s for s in (stale or []) if s in ("dev_checks", "liquidity_moves")],
        "counts": code_counts(rows),
        "tokens": rows,
    }
    validate_doc(doc)
    F.assert_public(doc)
    return doc


def code_counts(rows):
    counts = {c: 0 for c in CODES}
    for row in rows or []:
        for veto in row.get("vetoes") or []:
            code = veto.get("code")
            if code in counts:
                counts[code] += 1
    return counts


def reason_ok(reason):
    """True for an amounts-only reason. No wallet tag, address, or stake key."""
    if not isinstance(reason, str) or not reason or len(reason) > 240:
        return False
    if not REASON_RE.fullmatch(reason):
        return False
    if REASON_BANNED.search(reason):
        return False
    return True


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
    stale = doc.get("stale", [])
    if not isinstance(stale, list) or any(s not in ("dev_checks", "liquidity_moves") for s in stale):
        raise SystemExit("vetoes: stale rejected")
    allowed = {"schema_version", "at", "desk", "note", "buy_signal", "sources", "stale", "counts", "tokens"}
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
            if not isinstance(veto, dict) or veto.get("code") not in CODES:
                raise SystemExit("vetoes: code rejected")
            keys = set(veto)
            if veto.get("code") == "ahead_of_dump":
                if keys != {"code", "evidence", "reason"}:
                    raise SystemExit("vetoes: code rejected")
            elif keys != {"code", "evidence"}:
                raise SystemExit("vetoes: code rejected")
            ev = veto.get("evidence")
            if not isinstance(ev, dict) or set(ev) - {"dev_tag", "actor_tag"}:
                raise SystemExit("vetoes: evidence rejected")
            for value in ev.values():
                if not DC.dev_tag_ok(value, DC.known_roles()):
                    raise SystemExit("vetoes: evidence tag rejected")
            if veto.get("code") == "ahead_of_dump" and not reason_ok(veto.get("reason")):
                raise SystemExit("vetoes: reason rejected")
    counts = doc.get("counts")
    if counts != code_counts(rows):
        raise SystemExit("vetoes: counts rejected")
    return doc


def veto_cfg(cfg):
    block = (cfg or {}).get("vetoes") or {}
    age = block.get("max_age_h", 5)
    try:
        age = float(age)
    except (TypeError, ValueError):
        age = 5.0
    return {"enabled": block.get("enabled", True) is not False, "max_age_h": age}


def _load_named(path):
    """(status, doc). status is missing, corrupt, or ok. Corrupt is not turned into an empty object."""
    if not os.path.exists(path):
        return "missing", None
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return "corrupt", None
    if not isinstance(doc, dict):
        return "corrupt", None
    return "ok", doc


def _source_time(doc):
    at = doc.get("at") if isinstance(doc, dict) else None
    if not isinstance(at, str) or not AT_RE.fullmatch(at):
        return None
    try:
        return datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _fresh(doc, now_s, max_age_s):
    ts = _source_time(doc)
    if ts is None:
        return False
    return (float(now_s) - ts) <= float(max_age_s)


def run(data_dir, cfg, dry_run, owner_env=None, now_s=None):
    """Write vetoes.json unless dry_run. Never raises. Returns a small summary.

    A corrupt dev_checks or liquidity_moves file keeps the previous vetoes.json.
    A source older than max_age_h is skipped and named in stale.
    """
    try:
        vc = veto_cfg(cfg)
        if not vc["enabled"]:
            return {"enabled": False, "wrote": False}
        dev_status, dev_doc = _load_named(os.path.join(data_dir, "dev_checks.json"))
        liq_status, liq_doc = _load_named(os.path.join(data_dir, "liquidity_moves.json"))
        if dev_status == "corrupt" or liq_status == "corrupt":
            return {"wrote": False, "kept": "previous", "reason": "corrupt"}
        now = float(now_s if now_s is not None else __import__("time").time())
        max_age = vc["max_age_h"] * 3600.0
        stale = []
        if dev_status != "ok":
            dev_doc = {}
        elif not _fresh(dev_doc, now, max_age):
            stale.append("dev_checks")
            dev_doc = {}
        if liq_status != "ok":
            liq_doc = {}
        elif not _fresh(liq_doc, now, max_age):
            stale.append("liquidity_moves")
            liq_doc = {}
        doc = build(dev_doc, liq_doc, cfg, owner_env, DC.iso(now), stale=stale)
        path = os.path.join(data_dir, "vetoes.json")
        if not dry_run:
            KT.write_json(path, doc, indent=1)
        counts = {c: 0 for c in CODES}
        for row in doc["tokens"]:
            for veto in row["vetoes"]:
                counts[veto["code"]] += 1
        return {"wrote": not dry_run, "tokens": len(doc["tokens"]), "counts": counts,
                "buy_signal": False, "stale": stale}
    except (SystemExit, Exception) as e:
        print(f"vetoes: skipped ({type(e).__name__})", file=sys.stderr)
        return {"wrote": False, "error": type(e).__name__}
