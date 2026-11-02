#!/usr/bin/env python3
"""Daily status for the Sightline desks. No AI and no network.

Reads ledgers that are already in a checkout and writes
reports/status/daily_status_<YYYY-MM-DD>.json plus daily_status_latest.json.
A missing input is null with a reason. Numbers are copied or computed from
those files only.

  python3 scripts/status/daily_status.py --repo . --data /path/to/desk-data

--repo is the code checkout (config, the day-14 clock, the test log).
--data is the evidence checkout. When omitted, evidence is read from --repo.
Evidence files are taken from --data first.
"""
import argparse
import ast
import csv
import glob
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

BOOKS = ("F", "R", "S")
TRADE_NEED = ("time", "book", "action", "frac", "pnl", "mint")
SELL_WHYS = ("pool_gone", "no_route_3x", "hard_stop", "liquidity_pulled", "time_stop", "trailing_stop")
HEALTH_INT = ("runs", "expected_runs", "checks", "leads_this_run", "eligibility_skips")
HEALTH_NUM = ("error_rate", "data_failure_rate", "max_gap_min")
HEALTH_BOOL = ("loop_ran", "void_day", "ai")
FAIL_SOURCES = ("rugcheck", "jupiter", "dexscreener")
CLOSED_STATUS = ("closed", "exited", "cancelled", "canceled")
PLACES = Decimal("0.000000001")
BANNED = re.compile(
    r"@[A-Za-z0-9_]{2,}"
    r"|stake(?:_test)?1[0-9a-z]{10,}"
    r"|addr(?:_test)?1[0-9a-z]{10,}"
    r"|0x[0-9a-fA-F]{8,}"
    r"|\bfounder\b",
    re.I,
)
CODE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
MINT = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,48}")
RUN_ID = re.compile(r"\d{8}T\d{4}")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def gap(reason):
    return {"value": None, "reason": reason, "source": None}


def measured(value, source):
    return {"value": value, "reason": None, "source": source}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, type(exc).__name__


def public_code(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if CODE.fullmatch(text) and not BANNED.search(text):
        return text
    return None


def public_symbol(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if SYMBOL.fullmatch(text) and not BANNED.search(text):
        return text
    return None


def public_mint(value):
    if isinstance(value, str) and MINT.fullmatch(value):
        return value
    return None


def public_text(value, limit=400):
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit or BANNED.search(text):
        return None
    return text


def assigned(path, name):
    """Module-level assignment of a string or number. No execution."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, UnicodeError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name and isinstance(node.value, ast.Constant):
                return node.value.value
    return None


def parse_time(value):
    """UTC epoch seconds, or None when the cell is not a time we can read."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if RUN_ID.fullmatch(text):
        return _gm(text, "%Y%m%dT%H%M")
    if text.endswith("Z"):
        body = text[:-1]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            got = _gm(body, fmt)
            if got is not None:
                return got
        return None
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2}):(\d{2})", text)
    if not match:
        return None
    base = _gm(match.group(1), "%Y-%m-%dT%H:%M:%S")
    if base is None:
        return None
    hours = int(match.group(2))
    minutes = int(match.group(3))
    offset = hours * 3600 + (minutes if hours >= 0 else -minutes) * 60
    return base - offset


def _gm(text, fmt):
    try:
        return float(calendar_timegm(text, fmt))
    except (ValueError, OverflowError):
        return None


def calendar_timegm(text, fmt):
    import calendar
    return calendar.timegm(time.strptime(text, fmt))


def fmt_time(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch)))


def quant(value):
    return str(Decimal(value).quantize(PLACES, rounding=ROUND_HALF_UP))


def repo_file(repo, rel):
    path = os.path.join(repo, rel)
    return path if os.path.isfile(path) else None


class Roots:
    def __init__(self, repo, data):
        self.repo = os.path.abspath(repo)
        self.data = os.path.abspath(data) if data else self.repo
        self.used = {}
        self.missing = []

    def evidence(self, rel):
        """Data checkout first, then the code checkout."""
        order = [(self.data, "data")]
        if self.data != self.repo:
            order.append((self.repo, "repo"))
        for root, label in order:
            path = os.path.join(root, rel)
            if os.path.isfile(path):
                self._used(rel, label, path)
                return path
        self._miss(rel, "not in the data checkout or the repo")
        return None

    def code(self, rel):
        path = repo_file(self.repo, rel)
        if path:
            self._used(rel, "repo", path)
            return path
        self._miss(rel, "not in the repo")
        return None

    def _used(self, rel, label, path):
        if rel not in self.used:
            self.used[rel] = {"path": rel, "root": label, "sha256": sha256_file(path)}

    def _miss(self, rel, reason):
        if not any(item["path"] == rel for item in self.missing):
            self.missing.append({"path": rel, "reason": reason})

    def sources(self):
        rows = [dict(self.used[key], used=True) for key in sorted(self.used)]
        for item in self.missing:
            if item["path"] not in self.used:
                rows.append({"path": item["path"], "root": None, "sha256": None, "used": False, "reason": item["reason"]})
        rows.sort(key=lambda row: row["path"])
        return rows


def load_exit_program(roots):
    config_path = roots.code("solana-desk/desk/config.py")
    exits_path = roots.code("solana-desk/desk/exits.py")
    if not config_path or not exits_path:
        reason = "solana-desk/desk/config.py or solana-desk/desk/exits.py is missing"
        return None, gap(reason)
    ticket = assigned(config_path, "TICKET_SOL")
    if isinstance(ticket, bool) or not isinstance(ticket, (int, float)) or ticket <= 0:
        return None, gap("TICKET_SOL is not a positive number in solana-desk/desk/config.py")
    try:
        with open(exits_path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeError):
        return None, gap("solana-desk/desk/exits.py could not be read")
    present = [code for code in SELL_WHYS if ('"%s"' % code) in text or ("'%s'" % code) in text]
    if "take_profit_" not in text:
        present_take = False
    else:
        present_take = True
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("sightline_desk_config_status", config_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        raw = mod.EXIT
    except Exception:
        return None, gap("solana-desk/desk/config.py EXIT could not be read")
    if not isinstance(raw, dict):
        return None, gap("solana-desk/desk/config.py EXIT is not a dict")
    ladder = raw.get("ladder")
    levels = []
    if isinstance(ladder, (list, tuple)):
        for item in ladder:
            if isinstance(item, (list, tuple)) and item and isinstance(item[0], (int, float)) and not isinstance(item[0], bool):
                levels.append(item[0])
    program = {
        "source": "solana-desk/desk/config.py",
        "exits_source": "solana-desk/desk/exits.py",
        "ticket_sol": quant(str(ticket)),
        "sell_whys": present,
        "take_profit": present_take,
        "ladder": [quant(str(level)) for level in levels],
        "hard_stop": _num_field(raw, "hard_stop"),
        "trail": _num_field(raw, "trail"),
        "liq_drop": _num_field(raw, "liq_drop"),
        "time_stop_min": _num_field(raw, "time_stop_min"),
        "time_stop_keep_if_up": _num_field(raw, "time_stop_keep_if_up"),
        "no_route_kill_checks": _num_field(raw, "no_route_kill_checks"),
    }
    return program, None


def _num_field(raw, key):
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return gap("EXIT.%s is not a number" % key)
    return measured(quant(str(value)), "solana-desk/desk/config.py EXIT")


def armed_exits(steps, program):
    if program is None:
        return None, "exit program was not read"
    try:
        step = int(steps)
    except (TypeError, ValueError):
        return None, "position steps is missing, so the armed exit codes are not known"
    codes = [code for code in ("pool_gone", "no_route_3x", "hard_stop", "liquidity_pulled", "time_stop") if code in program["sell_whys"]]
    if program["take_profit"] and step < len(program["ladder"]):
        level = Decimal(program["ladder"][step])
        codes.append("take_profit_%d" % int((level * 100).to_integral_value(rounding=ROUND_HALF_UP)))
    if step >= 1 and "trailing_stop" in program["sell_whys"]:
        codes.append("trailing_stop")
    return codes, None


def read_trades(path):
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        return None, None, type(exc).__name__
    missing = [name for name in TRADE_NEED if name not in header]
    if missing:
        return None, None, "missing columns " + ",".join(missing)
    return header, rows, None


def book_closes(rows, book):
    """Fully closed lots from append-only trades. pnl is the logged net. Blank pnl is incomplete."""
    from collections import defaultdict
    lots = defaultdict(list)
    closed = []
    incomplete = 0
    for row in rows:
        if row.get("book") != book or row.get("action") not in ("buy", "sell"):
            continue
        mint = row.get("mint") or ""
        try:
            int(float(row.get("time") or ""))
        except (TypeError, ValueError):
            incomplete += 1
            continue
        if row.get("action") == "buy":
            lots[mint].append({"pnl": Decimal("0"), "frac": Decimal("0")})
            continue
        if row.get("pnl") in ("", None):
            incomplete += 1
            continue
        if not lots[mint]:
            incomplete += 1
            continue
        lot = lots[mint][0]
        try:
            lot["pnl"] += Decimal(row["pnl"])
            lot["frac"] += Decimal(row.get("frac") or "0")
        except Exception:
            incomplete += 1
            continue
        if lot["frac"] >= 1 - Decimal("0.0001"):
            closed.append(lots[mint].pop(0))
    return closed, incomplete


def solana_book(rows, trade_err, positions, pos_err, book, program, ticket_gap):
    source = "solana-desk/trades.csv"
    pos_source = "solana-desk/positions.json"
    out = {"id": book}
    if trade_err == "absent":
        out["close_count"] = gap("solana-desk/trades.csv is missing. positions.json keeps a capped closed list and is not used as the count")
        out["mean_return_after_costs"] = gap("solana-desk/trades.csv is missing")
        out["total_pnl_sol"] = gap("solana-desk/trades.csv is missing")
    elif trade_err:
        out["close_count"] = gap("solana-desk/trades.csv " + trade_err)
        out["mean_return_after_costs"] = gap("solana-desk/trades.csv " + trade_err)
        out["total_pnl_sol"] = gap("solana-desk/trades.csv " + trade_err)
    else:
        closed, incomplete = book_closes(rows, book)
        out["incomplete_rows"] = measured(incomplete, source)
        if incomplete:
            why = "%d sell or buy rows for %s could not be matched or had a blank pnl" % (incomplete, book)
            out["close_count"] = gap(why)
            out["complete_lots"] = measured(len(closed), source)
            out["mean_return_after_costs"] = gap(why + "; the mean is not taken from a partial ledger")
            out["total_pnl_sol"] = gap(why)
        elif not closed:
            out["close_count"] = measured(0, source)
            out["mean_return_after_costs"] = gap("no realized closes for %s in solana-desk/trades.csv" % book)
            out["total_pnl_sol"] = measured("0.000000000", source)
        elif ticket_gap or program is None:
            total = sum((lot["pnl"] for lot in closed), Decimal("0"))
            out["close_count"] = measured(len(closed), source)
            out["total_pnl_sol"] = measured(quant(total), source)
            out["mean_return_after_costs"] = ticket_gap or gap("ticket size was not read")
        else:
            total = sum((lot["pnl"] for lot in closed), Decimal("0"))
            ticket = Decimal(program["ticket_sol"])
            mean = total / (ticket * len(closed))
            out["close_count"] = measured(len(closed), source)
            out["total_pnl_sol"] = measured(quant(total), source)
            out["mean_return_after_costs"] = {
                "value": quant(mean),
                "percent": quant(mean * 100),
                "unit": "fraction of ticket; percent is that fraction times 100",
                "formula": "sum of sell pnl on each fully closed lot / (TICKET_SOL * close_count)",
                "reason": None,
                "source": "solana-desk/trades.csv pnl / solana-desk/desk/config.py TICKET_SOL",
            }
        out["note"] = "Every realized close in trades.csv. The day-14 void window is not applied. pnl is the logged net."
    if program:
        out["ticket_sol"] = measured(program["ticket_sol"], "solana-desk/desk/config.py TICKET_SOL")
    else:
        out["ticket_sol"] = ticket_gap or gap("solana-desk/desk/config.py TICKET_SOL was not read")
    out["open_positions"] = open_positions(positions, pos_err, book, program)
    if isinstance(out["open_positions"], list):
        out["open_count"] = measured(len(out["open_positions"]), pos_source)
    else:
        out["open_count"] = gap(out["open_positions"]["reason"])
    return out


def open_positions(positions, pos_err, book, program):
    if pos_err == "absent":
        return gap("solana-desk/positions.json is missing")
    if pos_err:
        return gap("solana-desk/positions.json " + pos_err)
    blob = ((positions.get("books") or {}).get(book) or {})
    raw = blob.get("open") if isinstance(blob, dict) else None
    if raw is None:
        return gap("solana-desk/positions.json has no books.%s.open" % book)
    if not isinstance(raw, dict):
        return gap("books.%s.open is not an object" % book)
    rows = []
    for pid in sorted(raw):
        pos = raw[pid]
        if not isinstance(pos, dict):
            continue
        ident = public_code(pid)
        if not ident:
            continue
        codes, why = armed_exits(pos.get("steps"), program)
        opened = pos.get("opened")
        opened_at = fmt_time(opened) if isinstance(opened, (int, float)) and not isinstance(opened, bool) else None
        item = {
            "id": ident,
            "symbol": public_symbol(pos.get("symbol") or ""),
            "mint": public_mint(pos.get("mint")),
            "opened_at": opened_at,
            "frac": pos.get("frac") if isinstance(pos.get("frac"), (int, float)) and not isinstance(pos.get("frac"), bool) else None,
            "steps": pos.get("steps") if isinstance(pos.get("steps"), int) and not isinstance(pos.get("steps"), bool) else None,
            "entry_code": public_code(str(pos.get("why") or "")),
            "exit_codes": codes,
            "exit_codes_source": "solana-desk/desk/exits.py why strings still armed for this steps value, thresholds from solana-desk/desk/config.py EXIT" if codes is not None else None,
        }
        if why:
            item["reason"] = why
        if item["symbol"] is None and pos.get("symbol"):
            item["symbol_reason"] = "symbol failed the public-text check"
        if item["entry_code"] is None and pos.get("why"):
            item["entry_code_reason"] = "entry code failed the public-text check"
        rows.append(item)
    return rows


def is_closed_ticket(ticket):
    if not isinstance(ticket, dict):
        return False
    if ticket.get("closed_at"):
        return True
    return str(ticket.get("status") or "").lower() in CLOSED_STATUS


def ticket_pct(ticket):
    result = ticket.get("result") if isinstance(ticket, dict) else None
    if not isinstance(result, dict) or "pct" not in result:
        return None
    value = result.get("pct")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def mean_pct(closed, source):
    if not closed:
        return (
            measured(0, source),
            gap("no closed tickets in " + source),
            gap("no closed tickets in " + source),
        )
    pcts = []
    missing = 0
    for ticket in closed:
        pct = ticket_pct(ticket)
        if pct is None:
            missing += 1
        else:
            pcts.append(pct)
    count = measured(len(closed), source)
    if missing:
        why = "%d of %d closed tickets in %s have no result.pct" % (missing, len(closed), source)
        return count, gap(why), gap(why + "; result.pct is also not labeled after costs")
    mean = sum(pcts, Decimal("0")) / len(pcts)
    stored = {
        "value": quant(mean),
        "unit": "result.pct points, as stored",
        "reason": None,
        "source": source + " closed result.pct",
    }
    after = gap("result.pct is stored but the file does not label it as after costs")
    return count, stored, after


def position_from_ticket(ticket, exit_rule, rule_source):
    ident = public_code(str(ticket.get("id") or ""))
    if not ident:
        return None
    item = {
        "id": ident,
        "ticker": public_symbol(str(ticket.get("ticker") or "")),
        "opened_at": public_text(ticket.get("opened_at") or "", 40),
        "exit_codes": None,
    }
    code = public_code(str(ticket.get("exit_reason") or ticket.get("why") or ""))
    if code:
        item["exit_codes"] = [code]
        item["exit_codes_source"] = "ticket exit_reason"
        item["reason"] = None
    else:
        rule = public_text(exit_rule or "")
        item["reason"] = "no exit code is stored on this open ticket"
        if rule:
            item["exit_rule"] = rule
            item["exit_rule_source"] = rule_source
        elif exit_rule:
            item["exit_rule"] = None
            item["exit_rule_reason"] = "exit rule failed the public-text check"
    return item


def ledger_from_paper(doc, source):
    closed = [row for row in (doc.get("closed") or []) if is_closed_ticket(row)]
    count, stored, after = mean_pct(closed, source)
    exit_rule = doc.get("exit") if isinstance(doc.get("exit"), str) else None
    if exit_rule is None:
        for rule in doc.get("rules") or []:
            if isinstance(rule, dict) and rule.get("id") == "exit" and isinstance(rule.get("rule"), str):
                exit_rule = rule["rule"]
                break
    opens = []
    withheld = 0
    for ticket in doc.get("open") or []:
        if not isinstance(ticket, dict) or is_closed_ticket(ticket):
            continue
        item = position_from_ticket(ticket, exit_rule, source + " exit rule")
        if item is None:
            withheld += 1
        else:
            opens.append(item)
    return {
        "source": source,
        "desk": public_code(str(doc.get("desk") or "")) or public_text(str(doc.get("desk") or ""), 80),
        "book": public_text(str(doc.get("book") or ""), 40),
        "unit": public_code(str(doc.get("unit") or "")),
        "close_count": count,
        "mean_stored_pct": stored,
        "mean_return_after_costs": after,
        "open_count": measured(len(opens), source + " open"),
        "open_withheld": withheld,
        "open_positions": opens,
    }


def registered_from_tests(doc, source):
    tests = doc.get("tests")
    if tests is None:
        return []
    if not isinstance(tests, list):
        return [gap(source + " tests is not a list")]
    out = []
    for test in tests:
        if not isinstance(test, dict):
            continue
        ident = public_code(str(test.get("id") or ""))
        if not ident:
            continue
        tickets = [row for row in (test.get("tickets") or []) if isinstance(row, dict)]
        closed = [row for row in tickets if is_closed_ticket(row)]
        count, stored, after = mean_pct(closed, source + " tests " + ident)
        rule = test.get("exit") if isinstance(test.get("exit"), str) else None
        if rule is None and isinstance(test.get("action"), str):
            rule = test.get("action")
        opens = []
        for ticket in tickets:
            if is_closed_ticket(ticket):
                continue
            item = position_from_ticket(ticket, rule, source + " tests " + ident)
            if item:
                opens.append(item)
        out.append({
            "id": ident,
            "title": public_text(test.get("title") or ""),
            "status": public_code(str(test.get("status") or "")),
            "registered_at": public_text(test.get("registered_at") or "", 40),
            "source": source,
            "close_count": count,
            "mean_stored_pct": stored,
            "mean_return_after_costs": after,
            "open_count": measured(len(opens), source),
            "open_positions": opens,
        })
    return out


def tests_log(path):
    source = "solana-desk/specs/tests-log.csv"
    if not path:
        return gap(source + " is missing")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        return gap(source + " " + type(exc).__name__)
    need = ["date", "id", "family", "metric", "result", "planned"]
    if header != need and any(name not in header for name in need):
        return gap(source + " header is not date,id,family,metric,result,planned")
    clean = []
    withheld = 0
    for row in rows:
        item = {key: public_text(row.get(key) or "", 120) for key in need}
        if any(row.get(key) and item[key] is None for key in need):
            withheld += 1
            continue
        clean.append(item)
    return {
        "source": source,
        "rows": clean,
        "row_count": measured(len(clean), source),
        "withheld": withheld,
    }


def checkpoints(roots, date):
    cut_path = roots.code("solana-desk/specs/day14_verdict.py")
    reg_path = roots.code("solana-desk/specs/v2-preregistration.md")
    report = _date(date)
    day14 = {"label": "day-14 verdict"}
    if not cut_path:
        day14["at"] = gap("solana-desk/specs/day14_verdict.py is missing")
        day14["days"] = gap("day-14 clock was not read")
    else:
        cutoff = assigned(cut_path, "CUTOFF_DEFAULT")
        if not isinstance(cutoff, str) or parse_time(cutoff) is None or not cutoff.endswith("Z"):
            day14["at"] = gap("CUTOFF_DEFAULT is not a Z timestamp in solana-desk/specs/day14_verdict.py")
            day14["days"] = gap("day-14 clock was not read")
        else:
            day14["at"] = measured(cutoff, "solana-desk/specs/day14_verdict.py CUTOFF_DEFAULT")
            if report is None:
                day14["days"] = gap("report date is not YYYY-MM-DD")
            else:
                target = datetime.strptime(cutoff[:10], "%Y-%m-%d").date()
                day14["days"] = measured((target - report).days, "report date to the UTC date of CUTOFF_DEFAULT")
    day28 = {"label": "day-28 checkpoint"}
    if not reg_path:
        day28["date"] = gap("solana-desk/specs/v2-preregistration.md is missing")
        day28["days"] = gap("day-28 date was not read")
    else:
        try:
            with open(reg_path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeError):
            text = ""
        if "Nov 25" not in text:
            day28["date"] = gap("solana-desk/specs/v2-preregistration.md does not name Nov 25")
            day28["days"] = gap("day-28 date was not read")
        else:
            year = None
            at = day14.get("at") or {}
            if isinstance(at, dict) and isinstance(at.get("value"), str) and DAY.match(at["value"]):
                year = int(at["value"][:4])
            if year is None:
                day28["date"] = gap("Nov 25 is named but the year is not on the day-14 clock")
                day28["days"] = gap("day-28 year was not read")
            else:
                stamped = "%04d-11-25" % year
                day28["date"] = measured(stamped, "solana-desk/specs/v2-preregistration.md names Nov 25; year from CUTOFF_DEFAULT")
                day28["at"] = gap("the pre-registration names Nov 25 and does not store a clock")
                if report is None:
                    day28["days"] = gap("report date is not YYYY-MM-DD")
                else:
                    target = datetime.strptime(stamped, "%Y-%m-%d").date()
                    day28["days"] = measured((target - report).days, "report date to " + stamped)
    return {"day14_verdict": day14, "day28_checkpoint": day28}


def _date(value):
    if not isinstance(value, str) or not DAY.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def copy_health(doc):
    source = "solana-desk/health.json"
    fields = {}
    absent = []
    if not isinstance(doc, dict):
        return gap(source + " is not an object")
    day = doc.get("day")
    if isinstance(day, str) and DAY.fullmatch(day):
        fields["day"] = day
    else:
        absent.append("day")
    for key in HEALTH_BOOL:
        if isinstance(doc.get(key), bool):
            fields[key] = doc[key]
        else:
            absent.append(key)
    for key in HEALTH_INT:
        value = doc.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            fields[key] = value
        elif key in doc:
            absent.append(key)
    for key in HEALTH_NUM:
        value = doc.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            fields[key] = value
        else:
            absent.append(key)
    last = doc.get("last_run_utc")
    if isinstance(last, str) and (RUN_ID.fullmatch(last) or parse_time(last) is not None):
        fields["last_run_utc"] = last
    else:
        absent.append("last_run_utc")
    mode = public_code(str(doc.get("mode") or ""))
    if mode:
        fields["mode"] = mode
    elif doc.get("mode"):
        absent.append("mode")
    raw = doc.get("data_fail_by_source")
    if isinstance(raw, dict):
        counts = {}
        bad = False
        for key in FAIL_SOURCES:
            if key not in raw:
                continue
            value = raw[key]
            if isinstance(value, bool) or not isinstance(value, int):
                bad = True
                break
            counts[key] = value
        if bad:
            fields["data_fail_by_source"] = None
            fields["data_fail_by_source_reason"] = "a source count was not an integer for rugcheck, jupiter, or dexscreener"
        else:
            fields["data_fail_by_source"] = counts
    else:
        absent.append("data_fail_by_source")
    return {"source": source, "fields": fields, "absent": absent}


def prefilter_health(doc):
    source = "solada-prefilter/last-run.json"
    if not isinstance(doc, dict):
        return gap(source + " is not an object")
    at = doc.get("at") if isinstance(doc.get("at"), str) and parse_time(doc.get("at")) is not None else None
    raw = doc.get("data_failures")
    failures = None
    reason = None
    if isinstance(raw, dict):
        failures = {}
        for key, value in raw.items():
            code = public_code(str(key))
            if not code or isinstance(value, bool) or not isinstance(value, int):
                failures = None
                reason = "data_failures has a key or count that is not a short code and an integer"
                break
            failures[code] = value
    elif raw is not None:
        reason = "data_failures is not an object of counts"
    out = {"source": source, "at": at, "data_failures": failures}
    if at is None:
        out["at_reason"] = "at is missing or not a timestamp"
    if reason:
        out["data_failures_reason"] = reason
    return out


def pulse_health(doc, source):
    if not isinstance(doc, dict):
        return gap(source + " is not an object")
    updated = doc.get("updated_at")
    if not isinstance(updated, str) or parse_time(updated) is None:
        return {"source": source, "updated_at": None, "reason": "updated_at is missing or not a timestamp"}
    return {"source": source, "updated_at": updated, "reason": None}


def buzz_health(doc):
    source = "x_buzz.json"
    if not isinstance(doc, dict):
        return gap(source + " is not an object")
    asof = doc.get("asof")
    if not isinstance(asof, str) or parse_time(asof) is None:
        asof_value = None
        asof_reason = "asof is missing or not a timestamp"
    else:
        asof_value = asof
        asof_reason = None
    tokens = doc.get("tokens")
    if isinstance(tokens, list):
        count = measured(len(tokens), source + " tokens")
    else:
        count = gap("tokens is not a list")
    return {"source": source, "asof": asof_value, "asof_reason": asof_reason, "token_count": count}


def random_twin(roots):
    found = []
    seen = set()
    for root, label in ((roots.data, "data"), (roots.repo, "repo")):
        pattern = os.path.join(root, "reports", "random_twin_*.json")
        for path in glob.glob(pattern):
            name = os.path.basename(path)
            if not name.startswith("random_twin_") or not name.endswith(".json"):
                continue
            key = (name, os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            found.append((name, label, path))
        if roots.data == roots.repo:
            break
    if not found:
        roots._miss("reports/random_twin_*.json", "no reports/random_twin_*.json in the data checkout or the repo")
        return gap("no reports/random_twin_*.json in the data checkout or the repo")
    # Latest filename wins. The same name on the data checkout beats the repo copy.
    found.sort(key=lambda item: (item[0], 1 if item[1] == "data" else 0))
    name, label, path = found[-1]
    rel = os.path.join("reports", name)
    roots._used(rel, label, path)
    doc, err = load_json(path)
    if err:
        return gap(rel + " " + err)
    try:
        body = json.dumps(doc, sort_keys=True)
    except TypeError:
        return gap(rel + " is not JSON-serializable")
    if BANNED.search(body):
        return gap(rel + " failed the public-text check and was not copied")
    return {"source": rel, "value": doc, "reason": None}


def evidence_as_of(roots):
    """Newest timestamp among the evidence files. The winner's field is the source."""
    candidates = []

    def add(rel, field, value):
        epoch = parse_time(value)
        if epoch is None:
            return
        candidates.append((epoch, rel, field))

    health_path = roots.evidence("solana-desk/health.json")
    if health_path:
        doc, _err = load_json(health_path)
        if isinstance(doc, dict):
            add("solana-desk/health.json", "last_run_utc", doc.get("last_run_utc"))
            times = doc.get("run_times_utc")
            if isinstance(times, list):
                for value in times:
                    add("solana-desk/health.json", "run_times_utc", value)
    pos_path = roots.evidence("solana-desk/positions.json")
    if pos_path:
        doc, _err = load_json(pos_path)
        if isinstance(doc, dict):
            add("solana-desk/positions.json", "last_run_ts", doc.get("last_run_ts"))
    paper_path = roots.evidence("solana-desk/paper.json")
    if paper_path:
        doc, _err = load_json(paper_path)
        if isinstance(doc, dict):
            add("solana-desk/paper.json", "generated_at", doc.get("generated_at"))
    last_path = roots.evidence("solada-prefilter/last-run.json")
    if last_path:
        doc, _err = load_json(last_path)
        if isinstance(doc, dict):
            add("solada-prefilter/last-run.json", "at", doc.get("at"))
    for rel in ("solada/pulse.json", "nft/pulse.json"):
        path = roots.evidence(rel)
        if not path:
            continue
        doc, _err = load_json(path)
        if isinstance(doc, dict):
            add(rel, "updated_at", doc.get("updated_at"))
    buzz_path = roots.evidence("x_buzz.json")
    if buzz_path:
        doc, _err = load_json(buzz_path)
        if isinstance(doc, dict):
            add("x_buzz.json", "asof", doc.get("asof"))
    if not candidates:
        return None, gap("no evidence file had a readable timestamp")
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    epoch, rel, field = candidates[-1]
    return fmt_time(epoch), measured(fmt_time(epoch), rel + " " + field)


def desk_papers(roots):
    path = roots.code("desks.json")
    if not path:
        return []
    doc, err = load_json(path)
    if err or not isinstance(doc, dict):
        return []
    rows = []
    for desk in doc.get("desks") or []:
        if not isinstance(desk, dict):
            continue
        for book in desk.get("books") or []:
            if isinstance(book, dict) and isinstance(book.get("paper"), str):
                rows.append((desk.get("id"), book.get("id"), book["paper"]))
    return rows


def build(repo, data, date, as_of=None):
    roots = Roots(repo, data)
    program, ticket_gap = load_exit_program(roots)
    trade_path = roots.evidence("solana-desk/trades.csv")
    if not trade_path:
        rows, trade_err = None, "absent"
    else:
        _header, rows, err = read_trades(trade_path)
        trade_err = err
        rows = rows or []
    pos_path = roots.evidence("solana-desk/positions.json")
    if not pos_path:
        positions, pos_err = None, "absent"
    else:
        positions, err = load_json(pos_path)
        pos_err = err if err or not isinstance(positions, dict) else None
        if err:
            positions = None
        elif not isinstance(positions, dict):
            pos_err = "is not an object"
            positions = None
    books = {
        book: solana_book(rows, trade_err, positions, pos_err, book, program, ticket_gap)
        for book in BOOKS
    }
    ledgers = []
    registered = []
    for _desk_id, book_id, rel in desk_papers(roots):
        path = roots.evidence(rel)
        if not path:
            ledgers.append({"id": book_id, "source": rel, "value": None, "reason": rel + " is missing"})
            continue
        doc, err = load_json(path)
        if err or not isinstance(doc, dict):
            ledgers.append({"id": book_id, "source": rel, "value": None, "reason": rel + " " + (err or "is not an object")})
            continue
        item = ledger_from_paper(doc, rel)
        item["id"] = public_code(str(book_id or "")) or book_id
        ledgers.append(item)
        registered.extend(registered_from_tests(doc, rel))
    log = tests_log(roots.code("solana-desk/specs/tests-log.csv"))
    health_path = roots.evidence("solana-desk/health.json")
    if not health_path:
        health = gap("solana-desk/health.json is missing")
    else:
        doc, err = load_json(health_path)
        health = gap("solana-desk/health.json " + err) if err else copy_health(doc)
    last_path = roots.evidence("solada-prefilter/last-run.json")
    if not last_path:
        prefilter = gap("solada-prefilter/last-run.json is missing")
    else:
        doc, err = load_json(last_path)
        prefilter = gap("solada-prefilter/last-run.json " + err) if err else prefilter_health(doc)
    pulses = []
    for rel in ("solada/pulse.json", "nft/pulse.json"):
        path = roots.evidence(rel)
        if not path:
            pulses.append(gap(rel + " is missing"))
            continue
        doc, err = load_json(path)
        pulses.append(gap(rel + " " + err) if err else pulse_health(doc, rel))
    buzz_path = roots.evidence("x_buzz.json")
    if not buzz_path:
        buzz = gap("x_buzz.json is missing")
    else:
        doc, err = load_json(buzz_path)
        buzz = gap("x_buzz.json " + err) if err else buzz_health(doc)
    derived, derived_measured = evidence_as_of(roots)
    if as_of is not None:
        if parse_time(as_of) is None:
            as_of_field = gap("--as-of is not a timestamp")
        else:
            as_of_field = measured(fmt_time(parse_time(as_of)), "cli --as-of")
    else:
        as_of_field = derived_measured
    twin = random_twin(roots)
    # evidence() above records misses. Drop duplicate misses that were later found via the other root.
    roots.missing = [item for item in roots.missing if item["path"] not in roots.used]
    doc = {
        "schema_version": 1,
        "kind": "sightline_daily_status",
        "ai": False,
        "date": date if _date(date) else None,
        "date_reason": None if _date(date) else "report date is not YYYY-MM-DD",
        "as_of": as_of_field,
        "checkpoints": checkpoints(roots, date),
        "books": books,
        "exit_program": program if program else ticket_gap,
        "ledgers": ledgers,
        "registered_tests": registered,
        "tests_log": log,
        "feed_health": {
            "solana_desk": health,
            "solada_prefilter": prefilter,
            "pulses": pulses,
            "x_buzz": buzz,
        },
        "random_twin": twin,
        "sources": roots.sources(),
    }
    if derived and as_of is None:
        doc["as_of"] = derived_measured
    return doc


def dumps(doc):
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def privacy_failure(text):
    return BANNED.search(text) is not None


def write_report(doc, out_dir, date):
    if not _date(date):
        raise SystemExit("report date must be YYYY-MM-DD")
    text = dumps(doc)
    if privacy_failure(text):
        raise SystemExit("daily status failed the public-text check and was not written")
    os.makedirs(out_dir, exist_ok=True)
    dated = os.path.join(out_dir, "daily_status_%s.json" % date)
    latest = os.path.join(out_dir, "daily_status_latest.json")
    for path in (dated, latest):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    return dated, latest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the Sightline daily status JSON from files already in the checkout.")
    parser.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    parser.add_argument("--data", default=None, help="Evidence checkout. Defaults to --repo.")
    parser.add_argument("--out", default=None, help="Output directory. Defaults to <repo>/reports/status.")
    parser.add_argument("--date", default=None, help="Report date YYYY-MM-DD. Defaults to today UTC.")
    parser.add_argument("--as-of", dest="as_of", default=None, help="Override the evidence timestamp.")
    args = parser.parse_args(argv)
    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = args.out or os.path.join(args.repo, "reports", "status")
    doc = build(args.repo, args.data, date, args.as_of)
    dated, latest = write_report(doc, out, date)
    print(dated)
    print(latest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
