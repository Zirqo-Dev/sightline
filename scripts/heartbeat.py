"""Pipeline heartbeat. Free, read-only, no AI and no paid APIs.

Reads GitHub Actions run history (time, conclusion, event) and the last
commit time on a workflow's data branch. Writes a public health document:
workflow names, times, and statuses only. The publish target is health-data.
main, desk-data, races-data, and ua-f1-data are refused.

Status, worst wins:
  red   two missed cron slots in a row (or one, when the cadence is a day
        or longer, or the workflow has never run), any consecutive failure,
        a data branch older than twice its interval plus grace, a workflow
        state of disabled_inactivity or disabled_manually, or 10 or fewer
        days until GitHub's 60-day schedule auto-disable
  amber one missed slot on a cadence under a day, a data branch older than
        its interval plus grace, or 11 to 20 days left before auto-disable
  green otherwise

The auto-disable clock is the last commit on main. Runs do not reset it,
and commits on data branches do not either. The expected interval is the
longest gap between scheduled fires. Grace is max(15 minutes, one quarter
of that interval). Slots earlier than a workflow's created_at are ignored.
ua-f1-data can skip a commit when a run has nothing new, so that branch is
judged from the later of its tip and its last success.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DISABLE_AFTER_DAYS = 60
RED_WITHIN_DAYS = 10
AMBER_WITHIN_DAYS = 20
DATA_BRANCHES = ("desk-data", "races-data", "ua-f1-data")
FORBIDDEN_TARGETS = ("main", "master", "desk-data", "races-data", "ua-f1-data")
PUBLISH_BRANCH = "health-data"
FAILURES = {"failure", "timed_out", "startup_failure"}
IGNORED_CONCLUSIONS = {"cancelled", "skipped", "neutral", "stale"}
CONCLUSIONS = FAILURES | IGNORED_CONCLUSIONS | {"success", "action_required", "in_progress"}
SLOT_SKEW = timedelta(minutes=2)
DAY_MINUTES = 24 * 60
DISABLED_STATES = {"disabled_inactivity", "disabled_manually"}
WAITING_FOR_FIRST = "waiting for first scheduled run"
_STATES = DISABLED_STATES | {"active", "disabled_fork", "deleted"}

_CRON_LINE = re.compile(
    r"""^[ \t]*(?:-\s*)?cron:\s*(?:'([^']+)'|"([^"]+)"|(\S+))\s*(?:#.*)?$"""
)
_PUSH_LITERAL = re.compile(
    r'git push origin (?:HEAD:(desk-data|races-data|ua-f1-data)\b'
    r'|"HEAD:(desk-data|races-data|ua-f1-data)")'
)
_PUSH_TARGET_VAR = re.compile(
    r'git push origin (?:"HEAD:\$\{target\}"|HEAD:\$\{target\}|HEAD:\$target\b)'
)
_TARGET_ASSIGN = re.compile(
    r"(?m)^[ \t]*target=(desk-data|races-data|ua-f1-data)[ \t]*$"
)
_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/:&+-]{0,80}$")
_FILE_OK = re.compile(r"^\.github/workflows/[A-Za-z0-9._-]+\.ya?ml$")
_ISO_OK = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_EVENT_OK = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_CRON_OK = re.compile(r"^[\d*,/-]+(?:\s+[\d*,/-]+){4}$")
_TOP_KEYS = {
    "generated_at",
    "status",
    "disable_after_days",
    "red_within_days",
    "workflows",
}
_WF_KEYS = {
    "name",
    "file",
    "status",
    "last_run_at",
    "last_conclusion",
    "last_event",
    "crons",
    "interval_minutes",
    "grace_minutes",
    "missed_slot",
    "missed_count",
    "consecutive_failures",
    "days_until_disable",
    "disable_red",
    "state",
    "detail",
    "data_branch",
}
_BRANCH_KEYS = {"name", "last_commit_at", "stale"}
_FORBIDDEN_KEYS = {
    "actor",
    "login",
    "email",
    "author",
    "committer",
    "wallet",
    "handle",
    "user",
    "sender",
    "owner",
    "token",
    "message",
    "avatar",
    "commit",
}


def assert_target(branch):
    """health-data is the only branch this job may write."""
    if branch != PUBLISH_BRANCH or branch in FORBIDDEN_TARGETS:
        raise SystemExit("refusing to write %s" % branch)


def parse_time(value):
    if value is None or value == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value):
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def workflow_name(text, fallback):
    for line in text.splitlines():
        if line.startswith("name:"):
            raw = line.split(":", 1)[1].strip().strip("'\"")
            return raw or fallback
    return fallback


def crons_in(text):
    found = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _CRON_LINE.match(line)
        if not match:
            continue
        value = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        if value:
            found.append(value)
    return found


def data_branch_of(text):
    found = []
    for match in _PUSH_LITERAL.finditer(text):
        found.append(match.group(1) or match.group(2))
    if _PUSH_TARGET_VAR.search(text):
        found.extend(_TARGET_ASSIGN.findall(text))
    unique = []
    for name in found:
        if name not in unique:
            unique.append(name)
    if not unique:
        return None
    if len(unique) > 1:
        raise ValueError("workflow writes more than one data branch")
    return unique[0]


def load_workflow_files(directory):
    specs = []
    root = str(directory)
    for name in sorted(os.listdir(root)):
        if not name.endswith((".yml", ".yaml")):
            continue
        path = os.path.join(root, name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        rel = ".github/workflows/" + name
        branch = data_branch_of(text)
        specs.append({
            "file": rel,
            "name": workflow_name(text, name),
            "crons": crons_in(text),
            "data_branch": branch,
            # ua-f1 exits 0 without a commit when the tree is unchanged.
            "noop_commit": branch == "ua-f1-data" and "nothing to commit" in text,
        })
    return specs


def _field_values(field, low, high):
    """Expand one cron field into the ints it matches, or None for all."""
    if field == "*":
        return None
    allowed = set()
    for part in field.split(","):
        step = 1
        body = part
        if "/" in part:
            body, step_text = part.split("/", 1)
            step = int(step_text)
        if step < 1:
            continue
        if body in ("", "*"):
            start, end = low, high
        elif "-" in body:
            start_text, end_text = body.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(body)
        if start > end:
            continue
        value = start
        while value <= end and value <= high:
            if value >= low:
                allowed.add(value)
            value += step
    return allowed


def _dow_ok(field, cron_dow):
    allowed = _field_values(field, 0, 7)
    if allowed is None:
        return True
    if cron_dow in allowed:
        return True
    return cron_dow == 0 and 7 in allowed


def cron_matches(expression, moment):
    fields = expression.split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    minutes = _field_values(minute, 0, 59)
    hours = _field_values(hour, 0, 23)
    months = _field_values(month, 1, 12)
    if minutes is not None and moment.minute not in minutes:
        return False
    if hours is not None and moment.hour not in hours:
        return False
    if months is not None and moment.month not in months:
        return False
    dom_ok = _field_values(dom, 1, 31)
    day_matches = dom_ok is None or moment.day in dom_ok
    cron_dow = (moment.weekday() + 1) % 7
    week_matches = _dow_ok(dow, cron_dow)
    if dom == "*" and dow == "*":
        return True
    if dom == "*":
        return week_matches
    if dow == "*":
        return day_matches
    return day_matches or week_matches


def _fires(crons, start, end):
    cursor = start.replace(second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(minutes=1)
    found = []
    while cursor < end:
        if any(cron_matches(cron, cursor) for cron in crons):
            found.append(cursor)
        cursor += timedelta(minutes=1)
    return found


def interval_minutes(crons):
    """Longest quiet stretch the schedule itself allows, in minutes."""
    if not crons:
        return None
    return _interval_minutes(tuple(crons))


@functools.lru_cache(maxsize=32)
def _interval_minutes(crons):
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fires = _fires(crons, origin, origin + timedelta(days=70))
    if len(fires) < 2:
        return None
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(fires, fires[1:])]
    return int(max(gaps))


def grace_minutes(interval):
    if not interval:
        return None
    return max(15, int(interval) // 4)


def recent_slots(crons, now, count=2):
    """Newest scheduled minute at or before now, then the one before it."""
    if not crons or count < 1:
        return []
    cursor = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if cursor > now:
        cursor -= timedelta(minutes=1)
    limit = cursor - timedelta(days=70)
    found = []
    while cursor >= limit and len(found) < count:
        if any(cron_matches(cron, cursor) for cron in crons):
            found.append(cursor)
        cursor -= timedelta(minutes=1)
    return found


def days_until_disable(last_run, now, window=DISABLE_AFTER_DAYS):
    if last_run is None:
        return None
    remaining = window - (now - last_run).total_seconds() / 86400.0
    if remaining <= 0:
        return 0
    return int(math.ceil(remaining - 1e-9))


def consecutive_failures(runs):
    streak = 0
    for run in runs:
        conclusion = run.get("conclusion")
        if conclusion in (None, ""):
            continue
        if conclusion == "success":
            break
        if conclusion in FAILURES:
            streak += 1
            continue
        if conclusion in IGNORED_CONCLUSIONS:
            continue
        break
    return streak


def _schedule_at(run):
    if run.get("event") != "schedule":
        return None
    return parse_time(run.get("at"))


def _slot_covered(slot, newer, runs):
    """A schedule run covers this slot and not the newer one."""
    start = slot - SLOT_SKEW
    end = None if newer is None else newer - SLOT_SKEW
    for run in runs:
        at = _schedule_at(run)
        if at is None or at < start:
            continue
        if end is not None and at >= end:
            continue
        return True
    return False


def _as_utc(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return parse_time(value)


def missed_count(crons, runs, now, created_at=None):
    """How many newest due slots have no schedule run.

    Slots earlier than the workflow's created_at are ignored. A file that
    merged today must not be blamed for last Monday.
    """
    if not crons:
        return 0
    interval = interval_minutes(crons)
    grace = grace_minutes(interval)
    if grace is None:
        return 0
    created = _as_utc(created_at)
    slots = recent_slots(crons, now, 6)
    grace_td = timedelta(minutes=grace)
    count = 0
    for index, slot in enumerate(slots):
        if created is not None and slot < created:
            break
        if now <= slot + grace_td:
            continue
        newer = slots[index - 1] if index else None
        if _slot_covered(slot, newer, runs):
            break
        count += 1
    return count


def miss_level(count, interval, zero_runs):
    """One short miss is amber. A long miss, a double miss, or no runs is red."""
    if count <= 0:
        return None
    if zero_runs or (interval is not None and interval >= DAY_MINUTES) or count >= 2:
        return "red"
    return "amber"


def _sort_runs(runs):
    def key(run):
        at = parse_time(run.get("at"))
        return at or datetime.min.replace(tzinfo=timezone.utc)

    return sorted(runs, key=key, reverse=True)


def _clean_conclusion(value):
    if value in (None, ""):
        return "in_progress"
    if value in CONCLUSIONS:
        return value
    return None


def _clean_event(value):
    if value is None:
        return None
    text = str(value)
    if _EVENT_OK.match(text):
        return text
    return None


def public_run(row):
    """Keep time, conclusion, and event. Drop actors, commits, and urls."""
    if not isinstance(row, dict):
        raise ValueError("run is not an object")
    return {
        "at": row.get("run_started_at") or row.get("created_at") or row.get("at"),
        "conclusion": row.get("conclusion"),
        "event": row.get("event"),
    }


def _last_success(runs):
    for run in _sort_runs(runs):
        if run.get("conclusion") == "success":
            return parse_time(run.get("at"))
    return None


def _freshness(commit_at, runs, use_success):
    """ua-f1 may skip the commit. A later success still counts as fresh."""
    if not use_success:
        return commit_at
    success = _last_success(runs)
    stamps = [stamp for stamp in (commit_at, success) if stamp is not None]
    if not stamps:
        return None
    return max(stamps)


def _branch_level(age_minutes, interval, grace):
    """ok, amber past interval+grace, red past twice the interval plus grace."""
    if interval is None:
        return "ok"
    if age_minutes is None:
        return "red"
    grace = grace or 0
    if age_minutes > (2 * interval) + grace:
        return "red"
    if age_minutes > interval + grace:
        return "amber"
    return "ok"


def _clean_state(value):
    if value in _STATES:
        return value
    return None


def _status(miss, branch_level, failures, disable_red, days, state, waiting):
    if state in DISABLED_STATES or miss == "red" or branch_level == "red" or failures >= 1 or disable_red:
        return "red"
    warn_disable = days is not None and RED_WITHIN_DAYS < days <= AMBER_WITHIN_DAYS
    if miss == "amber" or branch_level == "amber" or warn_disable or waiting:
        return "amber"
    return "green"


def build_health(workflows, runs_by_file, branch_commits, now, main_commit_at=None):
    now = now.astimezone(timezone.utc)
    # Repo activity is the tip of main. Data-branch commits do not count.
    days = days_until_disable(parse_time(main_commit_at) if not isinstance(main_commit_at, datetime) else main_commit_at, now)
    disable_red = days is not None and days <= RED_WITHIN_DAYS
    rows = []
    ordered = sorted(workflows, key=lambda item: item["file"])
    for spec in ordered:
        runs = _sort_runs(runs_by_file.get(spec["file"]) or [])
        crons = list(spec.get("crons") or [])
        interval = interval_minutes(tuple(crons))
        grace = grace_minutes(interval)
        latest = runs[0] if runs else None
        last_at = parse_time(latest.get("at")) if latest else None
        branch_name = spec.get("data_branch")
        branch = None
        branch_level = "ok"
        if branch_name:
            commit_at = parse_time((branch_commits or {}).get(branch_name))
            fresh_at = _freshness(commit_at, runs, bool(spec.get("noop_commit")))
            age = None if fresh_at is None else (now - fresh_at).total_seconds() / 60.0
            branch_level = _branch_level(age, interval, grace)
            branch = {
                "name": branch_name,
                "last_commit_at": iso(commit_at),
                "stale": branch_level == "red",
            }
        failures = consecutive_failures(runs)
        created = _as_utc(spec.get("created_at"))
        missed = missed_count(crons, runs, now, created)
        level = miss_level(missed, interval, not runs)
        # No run yet, and no slot has come due since the workflow was created.
        waiting = bool(crons) and not runs and created is not None and missed == 0
        state = _clean_state(spec.get("state"))
        name = spec.get("name") or spec["file"]
        if not _NAME_OK.match(name):
            name = os.path.splitext(os.path.basename(spec["file"]))[0]
        rows.append({
            "name": name,
            "file": spec["file"],
            "status": _status(level, branch_level, failures, disable_red, days, state, waiting),
            "last_run_at": iso(last_at),
            "last_conclusion": _clean_conclusion(latest.get("conclusion")) if latest else None,
            "last_event": _clean_event(latest.get("event")) if latest else None,
            "crons": crons,
            "interval_minutes": interval,
            "grace_minutes": grace,
            "missed_slot": missed > 0,
            "missed_count": missed,
            "consecutive_failures": failures,
            "days_until_disable": days,
            "disable_red": disable_red,
            "state": state,
            "detail": WAITING_FOR_FIRST if waiting else None,
            "data_branch": branch,
        })
    rank = {"green": 0, "amber": 1, "red": 2}
    overall = "green"
    for row in rows:
        if rank[row["status"]] > rank[overall]:
            overall = row["status"]
    return {
        "generated_at": iso(now),
        "status": overall,
        "disable_after_days": DISABLE_AFTER_DAYS,
        "red_within_days": RED_WITHIN_DAYS,
        "workflows": rows,
    }


def _reject(reason):
    raise ValueError("health.json " + reason)


def _walk_keys(value, where):
    if isinstance(value, dict):
        for key in value:
            if key.lower() in _FORBIDDEN_KEYS:
                _reject("has a forbidden field at %s" % where)
            _walk_keys(value[key], where + "." + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_keys(item, "%s[%d]" % (where, index))
    elif isinstance(value, str):
        if "@" in value or "addr1" in value or "stake1" in value:
            _reject("has an identity-shaped value at %s" % where)
        if re.search(r"0x[0-9a-fA-F]{40}", value):
            _reject("has an identity-shaped value at %s" % where)


def assert_public(doc):
    """health.json may hold workflow names, times, and statuses only."""
    if not isinstance(doc, dict):
        _reject("is not an object")
    if set(doc) != _TOP_KEYS:
        _reject("has unexpected top-level fields")
    if doc["disable_after_days"] != DISABLE_AFTER_DAYS or doc["red_within_days"] != RED_WITHIN_DAYS:
        _reject("changed the disable window")
    if doc["status"] not in ("green", "amber", "red"):
        _reject("has a bad overall status")
    if not _ISO_OK.match(doc["generated_at"] or ""):
        _reject("has a bad generated_at")
    if not isinstance(doc["workflows"], list):
        _reject("workflows is not a list")
    _walk_keys(doc, "$")
    for row in doc["workflows"]:
        if not isinstance(row, dict) or set(row) != _WF_KEYS:
            _reject("workflow row has unexpected fields")
        if not _NAME_OK.match(row["name"]) or not _FILE_OK.match(row["file"]):
            _reject("workflow name or file is not public metadata")
        if row["status"] not in ("green", "amber", "red"):
            _reject("has a bad workflow status")
        for key in ("last_run_at",):
            if row[key] is not None and not _ISO_OK.match(row[key]):
                _reject("has a bad time")
        if row["last_conclusion"] is not None and row["last_conclusion"] not in CONCLUSIONS:
            _reject("has a bad conclusion")
        if row["last_event"] is not None and not _EVENT_OK.match(row["last_event"]):
            _reject("has a bad event")
        if not isinstance(row["crons"], list) or any(not _CRON_OK.match(item) for item in row["crons"]):
            _reject("has a bad cron")
        for key in ("interval_minutes", "grace_minutes"):
            if row[key] is not None and (not isinstance(row[key], int) or isinstance(row[key], bool) or row[key] < 0):
                _reject("has a bad interval")
        if not isinstance(row["missed_slot"], bool) or not isinstance(row["disable_red"], bool):
            _reject("has a bad flag")
        if not isinstance(row["missed_count"], int) or isinstance(row["missed_count"], bool) or row["missed_count"] < 0:
            _reject("has a bad miss count")
        if row["state"] is not None and row["state"] not in _STATES:
            _reject("has a bad workflow state")
        if row["detail"] not in (None, WAITING_FOR_FIRST):
            _reject("has a bad detail")
        if not isinstance(row["consecutive_failures"], int) or isinstance(row["consecutive_failures"], bool):
            _reject("has a bad failure count")
        days = row["days_until_disable"]
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or days < 0):
            _reject("has a bad disable countdown")
        branch = row["data_branch"]
        if branch is None:
            continue
        if not isinstance(branch, dict) or set(branch) != _BRANCH_KEYS:
            _reject("data branch has unexpected fields")
        if branch["name"] not in DATA_BRANCHES:
            _reject("data branch is not a known data branch")
        if branch["last_commit_at"] is not None and not _ISO_OK.match(branch["last_commit_at"]):
            _reject("has a bad commit time")
        if not isinstance(branch["stale"], bool):
            _reject("has a bad stale flag")
    return doc


def _api_get(url, token):
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "sightline-heartbeat",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        host = url.split("?", 1)[0]
        raise SystemExit("GitHub API HTTP %s for %s" % (exc.code, host))


def _workflow_meta(api, repo, token):
    url = api.rstrip("/") + "/repos/" + repo + "/actions/workflows?per_page=100"
    payload = _api_get(url, token)
    if not isinstance(payload, dict):
        raise SystemExit("GitHub API did not return a workflow list")
    found = {}
    for row in payload.get("workflows") or []:
        path = row.get("path")
        if not path:
            continue
        found[str(path)] = {
            "id": row.get("id"),
            "state": _clean_state(row.get("state")),
            "created_at": row.get("created_at"),
        }
    return found


def _runs_for(api, repo, token, workflow_id):
    url = "%s/repos/%s/actions/workflows/%s/runs?per_page=50" % (
        api.rstrip("/"), repo, workflow_id,
    )
    payload = _api_get(url, token)
    if not isinstance(payload, dict):
        return []
    return [public_run(row) for row in payload.get("workflow_runs") or []]


def _commit_time(api, repo, token, branch):
    quoted = urllib.parse.quote(branch)
    url = "%s/repos/%s/commits?sha=%s&per_page=1" % (api.rstrip("/"), repo, quoted)
    payload = _api_get(url, token)
    if not isinstance(payload, list) or not payload:
        return None
    commit = (payload[0].get("commit") or {})
    stamp = ((commit.get("committer") or {}).get("date"))
    return iso(parse_time(stamp))


def collect(workflows, repo, token, api):
    meta = _workflow_meta(api, repo, token)
    runs = {}
    for spec in workflows:
        info = meta.get(spec["file"]) or {}
        spec["state"] = info.get("state")
        spec["created_at"] = info.get("created_at")
        ident = info.get("id")
        runs[spec["file"]] = _runs_for(api, repo, token, ident) if ident else []
    commits = {}
    for name in DATA_BRANCHES:
        if any(spec.get("data_branch") == name for spec in workflows):
            commits[name] = _commit_time(api, repo, token, name)
    # Last commit on main only. Data-branch bot commits are not activity.
    return runs, commits, _commit_time(api, repo, token, "main")


def write_health(workflows_dir, dest, now=None, repo=None, token=None, api=None):
    workflows = load_workflow_files(workflows_dir)
    if not token or not repo:
        raise SystemExit("GITHUB_TOKEN and GITHUB_REPOSITORY are required")
    runs, commits, main_at = collect(workflows, repo, token, api or "https://api.github.com")
    doc = build_health(workflows, runs, commits, now or datetime.now(timezone.utc), main_commit_at=main_at)
    assert_public(doc)
    text = json.dumps(doc, indent=2) + "\n"
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as handle:
        handle.write(text)
    return doc


def main(argv):
    parser = argparse.ArgumentParser(description="Write or check pipeline health.json")
    parser.add_argument("--workflows-dir")
    parser.add_argument("--out")
    parser.add_argument("--check", help="fail if this health.json is not public metadata")
    parser.add_argument("--publish-branch", help="must be health-data; any other name is refused")
    parser.add_argument("--now", help="UTC timestamp for tests, ISO-8601")
    args = parser.parse_args(argv)
    if args.publish_branch:
        assert_target(args.publish_branch)
    if args.check:
        with open(args.check, encoding="utf-8") as handle:
            doc = json.load(handle)
        try:
            assert_public(doc)
        except ValueError as exc:
            raise SystemExit(str(exc))
        return 0
    if not args.workflows_dir or not args.out:
        parser.error("--workflows-dir and --out are required unless --check is set")
    now = parse_time(args.now) if args.now else None
    if args.now and now is None:
        raise SystemExit("bad --now")
    write_health(
        args.workflows_dir,
        args.out,
        now=now,
        repo=os.environ.get("GITHUB_REPOSITORY"),
        token=os.environ.get("GITHUB_TOKEN"),
        api=os.environ.get("GITHUB_API_URL") or "https://api.github.com",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
