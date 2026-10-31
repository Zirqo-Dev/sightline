#!/usr/bin/env python3
"""Alert when the Solana paper desk's data sources fail. Logging only.

Reads tasks.csv and health.json for the run that just finished. Does not change
skips, retries, voiding, entries, exits, or the v6.5 rules.

    python3 scripts/data_health_alert.py --data-dir /path/to/solana-desk

Alert when any of these is true:
  this run's data skips are at least 25% of its checks
  the same source has a fetch failure on this run and the previous run
  today's data-failure rate was under 0.10 and is now 0.10 or higher

An open GitHub issue labeled data-health gets a comment instead of a new issue.
Two runs in a row under 5% data failures comment recovered and close it.
Issue text is counts, reason codes, and the Actions run link.
"""
import argparse, calendar, csv, json, os, re, subprocess, sys, time

SOURCES = ("rugcheck", "jupiter", "dexscreener")
LABEL = "data-health"
LABEL_DESC = "Solana paper desk data source failures"
LABEL_COLOR = "b60205"
RECOVERED = "recovered"
RUN_ID_RE = re.compile(r"\d{8}T\d{4}")
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
CODE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789")
CODE_MAX = 16


def ratio_at_least(fails, checks, numer, denom):
    """fails/checks >= numer/denom. Zero checks is not a rate."""
    return checks > 0 and fails * denom >= checks * numer


def ratio_below(fails, checks, numer, denom):
    """fails/checks < numer/denom. Zero checks is not under the line."""
    return checks > 0 and fails * denom < checks * numer


def ratio_under_or_empty(fails, checks, numer, denom):
    """No checks yet, or fails/checks < numer/denom."""
    return checks <= 0 or fails * denom < checks * numer


def tokens(cell):
    """Valid source:code pairs. Anything else is dropped, including long ids."""
    out = []
    for part in str(cell or "").split(";"):
        part = part.strip()
        src, sep, code = part.partition(":")
        if not sep or src not in SOURCES:
            continue
        if not code or len(code) > CODE_MAX or any(c not in CODE_CHARS for c in code):
            continue
        out.append((src, part))
    return out


def _empty_sources():
    return {name: 0 for name in SOURCES}


def _row_time(row):
    try:
        return int(float(row.get("time") or ""))
    except (TypeError, ValueError):
        return None


def _run_id(row):
    rid = (row.get("run_id") or "").strip()
    return rid if RUN_ID_RE.fullmatch(rid) else ""


def collect(rows):
    """Group task rows into runs. Rows with a bad run id or time are ignored."""
    runs = {}
    for row in rows:
        rid = _run_id(row)
        at = _row_time(row)
        if not rid or at is None:
            continue
        bucket = runs.get(rid)
        if bucket is None:
            bucket = {
                "run_id": rid, "time": at, "checks": 0, "data_fails": 0,
                "sources": _empty_sources(), "reasons": {},
            }
            runs[rid] = bucket
        elif at < bucket["time"]:
            bucket["time"] = at
        bucket["checks"] += 1
        if (row.get("skip_type") or "").strip() == "data":
            bucket["data_fails"] += 1
        for src, token in tokens(row.get("fail_reason")):
            bucket["sources"][src] += 1
            bucket["reasons"][token] = bucket["reasons"].get(token, 0) + 1
    return runs


def day_bounds(day):
    if not isinstance(day, str) or not DAY_RE.fullmatch(day):
        return None
    start = calendar.timegm(time.strptime(day, "%Y-%m-%d"))
    return start, start + 86400


def tally(rows, start, end, skip_run_id=""):
    """In-day checks, data skips, and fetch-failure counts. skip_run_id is left out."""
    checks = fails = 0
    sources = _empty_sources()
    reasons = {}
    for row in rows:
        rid = _run_id(row)
        at = _row_time(row)
        if not rid or at is None or at < start or at >= end:
            continue
        if skip_run_id and rid == skip_run_id:
            continue
        checks += 1
        if (row.get("skip_type") or "").strip() == "data":
            fails += 1
        for src, token in tokens(row.get("fail_reason")):
            sources[src] += 1
            reasons[token] = reasons.get(token, 0) + 1
    return fails, checks, sources, reasons


def pick_runs(runs, last_run_utc):
    """This run is the newest task run, or last_run_utc when that is the newest."""
    if not runs:
        return None, None
    ordered = sorted(runs.values(), key=lambda r: (r["time"], r["run_id"]))
    current = ordered[-1]
    if isinstance(last_run_utc, str) and last_run_utc in runs:
        chosen = runs[last_run_utc]
        if (chosen["time"], chosen["run_id"]) >= (current["time"], current["run_id"]):
            current = chosen
    idx = next(i for i, run in enumerate(ordered) if run["run_id"] == current["run_id"])
    previous = ordered[idx - 1] if idx else None
    return current, previous


def repeated_sources(current, previous):
    if not current or not previous:
        return []
    return [
        name for name in SOURCES
        if current["sources"].get(name, 0) > 0 and previous["sources"].get(name, 0) > 0
    ]


def judge(current, previous, before_fails, before_checks, after_fails, after_checks):
    """Return ('alert'|'recover'|'ok', rule codes, sources that failed on both runs)."""
    rules = []
    if current and ratio_at_least(current["data_fails"], current["checks"], 1, 4):
        rules.append("share")
    repeated = repeated_sources(current, previous)
    if repeated:
        rules.append("source")
    # Was under 0.10, now 0.10 or higher. A day with no earlier checks counts as under.
    if (ratio_under_or_empty(before_fails, before_checks, 1, 10)
            and ratio_at_least(after_fails, after_checks, 1, 10)):
        rules.append("day_rate")
    if rules:
        return "alert", rules, repeated
    if (current and previous
            and ratio_below(current["data_fails"], current["checks"], 1, 20)
            and ratio_below(previous["data_fails"], previous["checks"], 1, 20)):
        return "recover", ["recover"], []
    return "ok", [], []


def title_source(current, repeated):
    pool = list(repeated) if repeated else []
    if not pool and current:
        pool = [name for name in SOURCES if current["sources"].get(name, 0) > 0]
    if not pool or not current:
        return "checks"
    return max(pool, key=lambda name: (current["sources"].get(name, 0), -SOURCES.index(name)))


def utc_day(health, current):
    day = health.get("day") if isinstance(health, dict) else None
    if isinstance(day, str) and DAY_RE.fullmatch(day):
        return day
    if current and current.get("time", 0) > 0:
        return time.strftime("%Y-%m-%d", time.gmtime(current["time"]))
    return time.strftime("%Y-%m-%d", time.gmtime())


def run_url(server, repository, actions_run_id):
    if server != "https://github.com":
        return ""
    if not isinstance(repository, str) or len(repository) > 200 or not REPO_RE.fullmatch(repository):
        return ""
    if not isinstance(actions_run_id, str) or not re.fullmatch(r"[0-9]{1,20}", actions_run_id):
        return ""
    return f"{server}/{repository}/actions/runs/{actions_run_id}"


def _fmt_sources(counts):
    return ", ".join(f"{name} {int(counts.get(name, 0))}" for name in SOURCES)


def _fmt_reasons(counts):
    if not counts:
        return "none"
    return ", ".join(f"{key} {counts[key]}" for key in sorted(counts))


def _fmt_rate(fails, checks):
    if checks <= 0:
        return "none"
    return f"{fails / checks:.4f} ({fails}/{checks} checks)"


def render_title(source, day):
    return f"Data health: {source} failing ({day})"


def render_body(decision):
    lines = [
        f"Run {decision['run_id']}",
        f"Previous run {decision['previous_run_id'] or 'none'}",
        f"Failure share: {decision['share_text']}",
        f"Sources: {_fmt_sources(decision['sources'])}",
        f"Reasons: {_fmt_reasons(decision['reason_counts'])}",
        f"Previous sources: {_fmt_sources(decision['previous_sources'])}",
        f"Day data_failure_rate: {decision['day_rate_text']}",
        f"Day sources: {_fmt_sources(decision['day_sources'])}",
        f"Day reasons: {_fmt_reasons(decision['day_reason_counts'])}",
        f"Rules: {' '.join(decision['rules'])}",
    ]
    actions_id = decision.get("actions_run_id") or ""
    if actions_id:
        lines.append(f"Actions run {actions_id}")
    if decision.get("run_url"):
        lines.append(f"GitHub run: {decision['run_url']}")
    return "\n".join(lines) + "\n"


def _base(kind, rules):
    return {
        "kind": kind,
        "rules": list(rules),
        "source": "",
        "run_id": "",
        "previous_run_id": "",
        "checks": 0,
        "data_fails": 0,
        "share_text": "",
        "sources": _empty_sources(),
        "reason_counts": {},
        "day_rate_text": "",
        "day_checks": 0,
        "day_data_fails": 0,
        "day_sources": _empty_sources(),
        "day_reason_counts": {},
        "previous_sources": _empty_sources(),
        "title": "",
        "body": "",
        "run_url": "",
        "actions_run_id": "",
    }


def is_duplicate(health):
    """True when this workflow run wrote a duplicate stamp newer than the last scan."""
    if not isinstance(health, dict):
        return False
    skipped = health.get("last_skipped_duplicate_utc")
    if not isinstance(skipped, str) or not STAMP_RE.fullmatch(skipped):
        return False
    times = health.get("run_times_utc")
    stamps = []
    if isinstance(times, list):
        stamps = [item for item in times if isinstance(item, str) and STAMP_RE.fullmatch(item)]
    if not stamps:
        return True
    return skipped > max(stamps)


def load_health(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_rows(path):
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error, UnicodeError):
        return []


def assess(data_dir, actions_run_id="", repository="", server="https://github.com"):
    """Decision for one desk directory. No network. Issue text is built here."""
    health = load_health(os.path.join(data_dir, "health.json"))
    rows = load_rows(os.path.join(data_dir, "tasks.csv"))
    runs = collect(rows)
    last = health.get("last_run_utc") if isinstance(health, dict) else None
    current, previous = pick_runs(runs, last)
    if current is None:
        decision = _base("ok", ["duplicate"] if is_duplicate(health) else ["no-run"])
        return decision
    day = utc_day(health, current)
    bounds = day_bounds(day)
    if bounds:
        after_fails, after_checks, day_sources, day_reasons = tally(rows, bounds[0], bounds[1])
        before_fails, before_checks, _, _ = tally(rows, bounds[0], bounds[1], current["run_id"])
    else:
        after_fails = after_checks = before_fails = before_checks = 0
        day_sources, day_reasons = _empty_sources(), {}
    kind, rules, repeated = judge(
        current, previous, before_fails, before_checks, after_fails, after_checks)
    if is_duplicate(health) and kind != "recover":
        decision = _base("ok", ["duplicate"])
        decision["run_id"] = current["run_id"]
        return decision
    actions_id = actions_run_id if re.fullmatch(r"[0-9]{1,20}", actions_run_id or "") else ""
    decision = _base(kind, rules)
    decision.update(
        source=title_source(current, repeated) if kind == "alert" else "",
        run_id=current["run_id"],
        previous_run_id=previous["run_id"] if previous else "",
        checks=current["checks"],
        data_fails=current["data_fails"],
        share_text=_fmt_rate(current["data_fails"], current["checks"]),
        sources=dict(current["sources"]),
        reason_counts=dict(sorted(current["reasons"].items())),
        day_rate_text=_fmt_rate(after_fails, after_checks),
        day_checks=after_checks,
        day_data_fails=after_fails,
        day_sources=day_sources,
        day_reason_counts=dict(sorted(day_reasons.items())),
        previous_sources=dict(previous["sources"]) if previous else _empty_sources(),
        actions_run_id=actions_id,
        run_url=run_url(server, repository, actions_id),
    )
    if kind == "alert":
        decision["title"] = render_title(decision["source"], day)
        decision["body"] = render_body(decision)
    return decision


def parse_issues(payload):
    out = []
    if not isinstance(payload, list):
        return out
    for item in payload:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            continue
        out.append({"number": number})
    out.sort(key=lambda item: item["number"])
    return out


def apply(decision, hub):
    """Open, comment, or close. hub performs the GitHub calls."""
    kind = decision.get("kind")
    if kind == "alert":
        opened = hub.open_issues()
        if opened:
            hub.comment(opened[0]["number"], decision["body"])
            return {"action": "comment", "number": opened[0]["number"]}
        hub.ensure_label()
        url = hub.create_issue(decision["title"], decision["body"])
        return {"action": "create", "url": url or ""}
    if kind == "recover":
        opened = hub.open_issues()
        if not opened:
            return {"action": "none"}
        numbers = []
        for issue in opened:
            hub.close_recovered(issue["number"])
            numbers.append(issue["number"])
        return {"action": "close", "numbers": numbers}
    return {"action": "none"}


class Gh:
    """GitHub CLI calls. Issue numbers and the label name are the only ids sent."""

    def __init__(self, repo, deadline):
        self.repo = repo
        self.deadline = deadline

    def _call(self, args):
        left = self.deadline - time.monotonic()
        if left < 2:
            raise TimeoutError("budget")
        env = os.environ.copy()
        env["GH_PROMPT_DISABLED"] = "1"
        env["GH_NO_UPDATE_NOTIFIER"] = "1"
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=min(15, left),
            stdin=subprocess.DEVNULL,
            env=env,
            check=False,
        )

    def _ok(self, proc, what):
        if proc.returncode != 0:
            raise RuntimeError(what)

    def open_issues(self):
        proc = self._call([
            "gh", "issue", "list", "--repo", self.repo, "--label", LABEL,
            "--state", "open", "--limit", "30", "--json", "number",
        ])
        self._ok(proc, "issue-list")
        try:
            payload = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            raise RuntimeError("issue-list")
        return parse_issues(payload)

    def ensure_label(self):
        proc = self._call([
            "gh", "label", "create", LABEL, "--repo", self.repo,
            "--color", LABEL_COLOR, "--description", LABEL_DESC,
        ])
        if proc.returncode == 0:
            return
        err = ((proc.stderr or "") + (proc.stdout or "")).lower()
        if "already exists" in err or "already_exists" in err:
            return
        raise RuntimeError("label")

    def create_issue(self, title, body):
        proc = self._call([
            "gh", "issue", "create", "--repo", self.repo,
            "--title", title, "--label", LABEL, "--body", body,
        ])
        self._ok(proc, "issue-create")
        url = (proc.stdout or "").strip()
        prefix = f"https://github.com/{self.repo}/issues/"
        if url.startswith(prefix) and url[len(prefix):].isdigit():
            return url
        return ""

    def comment(self, number, body):
        proc = self._call([
            "gh", "issue", "comment", str(int(number)),
            "--repo", self.repo, "--body", body,
        ])
        self._ok(proc, "issue-comment")

    def close_recovered(self, number):
        proc = self._call([
            "gh", "issue", "close", str(int(number)), "--repo", self.repo,
            "--comment", RECOVERED, "--reason", "completed",
        ])
        self._ok(proc, "issue-close")


def _token_present():
    return bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))


def _valid_repo(repository):
    return bool(repository) and len(repository) <= 200 and REPO_RE.fullmatch(repository) is not None


def _main(argv):
    parser = argparse.ArgumentParser(description="Alert on Solana desk data-source failures")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    decision = assess(
        args.data_dir,
        actions_run_id=os.environ.get("GITHUB_RUN_ID", ""),
        repository=repository,
        server=os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
    )
    if args.dry_run:
        print(json.dumps(decision, sort_keys=True))
        return 0
    print(f"data health: {decision['kind']}")
    if decision["kind"] == "ok":
        return 0
    if not _valid_repo(repository) or not _token_present():
        print("data health: not sent")
        return 0
    result = apply(decision, Gh(repository, time.monotonic() + 45))
    print(f"data health: {result['action']}")
    if result.get("url"):
        print(result["url"])
    return 0


def main(argv=None):
    try:
        return _main(argv)
    except SystemExit as exc:
        if exc.code not in (0, None):
            print("data health alert: skipped (args)", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"data health alert: skipped ({type(exc).__name__})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
