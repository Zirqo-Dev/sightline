#!/usr/bin/env python3
"""Read latest GitHub Actions runs for the evidence packet. No writes to the repo.

The packet builder does not call this. A manual run saves a snapshot, and the
builder treats that file as an input. Same snapshot bytes, same packet bytes.
Actor logins, commit messages, and token-bearing URLs are not copied.
"""
import argparse
import json
import os
import re
import subprocess
import sys

SCHEMA_VERSION = 1
KIND = "sightline_actions_snapshot"
SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA_RE = re.compile(r"[0-9a-f]{40}")
EVENT_RE = re.compile(r"[a-z_]{1,40}")
# Filename is the Actions workflow id. The path is what the packet records.
WORKFLOWS = (
    ("solana-desk-scan.yml", ".github/workflows/solana-desk-scan.yml"),
    ("solada-prefilter.yml", ".github/workflows/solada-prefilter.yml"),
    ("daily-status.yml", ".github/workflows/daily-status.yml"),
    ("desk-data-guard.yml", ".github/workflows/desk-data-guard.yml"),
    ("privacy-guard.yml", ".github/workflows/privacy-guard.yml"),
    ("schema-check.yml", ".github/workflows/schema-check.yml"),
    ("run-forensics.yml", ".github/workflows/run-forensics.yml"),
)
CONCLUSIONS = {
    "success", "failure", "cancelled", "skipped", "timed_out",
    "startup_failure", "neutral", "action_required", "stale",
}
STATUSES = {"completed", "in_progress", "queued", "requested", "waiting", "pending"}
FAILED = {"failure", "timed_out", "startup_failure"}
# A skipped pull request is not the health of the workflow. These events are
# the runs that actually execute the job. The list is newest-first, as the
# Actions API returns it. Twenty is the page, not a full history.
RELEVANT_EVENTS = {"schedule", "push", "workflow_dispatch"}
RUN_PAGE = 20


def dumps(doc):
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def github_slug(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(".git"):
        text = text[:-4]
    if SLUG_RE.fullmatch(text):
        return text
    return None


def _int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 10**15:
        return None
    return value


def safe_run(raw, slug):
    """Keep conclusion, ids, and timing. Drop actor and any URL the API sent."""
    if not isinstance(raw, dict):
        return None
    run_id = _int(raw.get("id"))
    if run_id is None:
        return None
    conclusion = raw.get("conclusion")
    status = raw.get("status")
    head = raw.get("head_sha")
    if isinstance(head, str):
        head = head.strip().lower()
    event = raw.get("event")
    out = {
        "id": run_id,
        "conclusion": conclusion if conclusion in CONCLUSIONS else None,
        "status": status if status in STATUSES else None,
        "head_sha": head if isinstance(head, str) and SHA_RE.fullmatch(head) else None,
        "event": event if isinstance(event, str) and EVENT_RE.fullmatch(event) else None,
        "html_url": "https://github.com/%s/actions/runs/%s" % (slug, run_id),
        "created_at": _stamp(raw.get("created_at")),
        "updated_at": _stamp(raw.get("updated_at")),
        "run_started_at": _stamp(raw.get("run_started_at")),
        "run_duration_ms": _int(raw.get("run_duration_ms")),
        "billable_ubuntu_total_ms": _int(raw.get("billable_ubuntu_total_ms")),
        "failed": conclusion in FAILED if conclusion in CONCLUSIONS else None,
    }
    return out


def _stamp(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text):
        return text
    return None


def apply_timing(run, timing):
    if not isinstance(run, dict) or not isinstance(timing, dict):
        return run
    duration = _int(timing.get("run_duration_ms"))
    ubuntu = timing.get("billable")
    total = None
    if isinstance(ubuntu, dict):
        block = ubuntu.get("UBUNTU")
        if isinstance(block, dict):
            total = _int(block.get("total_ms"))
    run["run_duration_ms"] = duration
    run["billable_ubuntu_total_ms"] = total
    return run


def blank_billing():
    return {
        "http_status": None,
        "endpoint": None,
        "total_minutes_used": None,
        "total_paid_minutes_used": None,
        "included_minutes": None,
        "note": "billing account lookup is deferred. Remaining quota is unknown. This is not a claim that usage is zero.",
    }


def pick_runs(raw_runs, slug):
    """Newest run, plus the newest schedule, push, or workflow_dispatch run.

    raw_runs is newest first. A skipped pull request stays on `latest` and
    does not become the health run. If the page has no relevant run, health
    is None and resolved is false.
    """
    safe = []
    if isinstance(raw_runs, list):
        for raw in raw_runs:
            item = safe_run(raw, slug)
            if item:
                safe.append(item)
    latest = safe[0] if safe else None
    health = None
    for item in safe:
        if item.get("event") in RELEVANT_EVENTS:
            health = item
            break
    return latest, health, health is not None


def build_snapshot(slug, workflow_rows, billing, fetched, error):
    workflows = {}
    for path, row in sorted(workflow_rows.items()):
        if path not in {item[1] for item in WORKFLOWS}:
            continue
        run = None
        latest = None
        resolved = False
        err = None
        if isinstance(row, dict):
            err = row.get("error") if isinstance(row.get("error"), str) else None
            if err is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", err):
                err = "unreadable_error"
            run = safe_run(row.get("run"), slug) if row.get("run") else None
            latest = safe_run(row.get("latest"), slug) if row.get("latest") else None
            resolved = row.get("health_resolved") is True and run is not None
        workflows[path] = {
            "error": err,
            "run": run,
            "latest": latest,
            "health_resolved": resolved,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "fetched": bool(fetched) and error is None,
        "error": error,
        "repo": slug,
        "workflows": workflows,
        "billing": billing if isinstance(billing, dict) else blank_billing(),
    }


def _gh_json(path):
    proc = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if proc.returncode != 0:
        return None, "gh_exit_%s" % proc.returncode
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        return None, "JSONDecodeError"


def fetch(slug):
    rows = {}
    for filename, path in WORKFLOWS:
        payload, err = _gh_json(
            "repos/%s/actions/workflows/%s/runs?per_page=%s" % (slug, filename, RUN_PAGE)
        )
        raw_runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        latest, health, resolved = pick_runs(raw_runs if isinstance(raw_runs, list) else [], slug)
        if health and health.get("id"):
            timing, _timing_err = _gh_json(
                "repos/%s/actions/runs/%s/timing" % (slug, health.get("id"))
            )
            if isinstance(timing, dict):
                apply_timing(health, timing)
        rows[path] = {
            "error": err,
            "run": health,
            "latest": latest,
            "health_resolved": resolved,
        }
    return build_snapshot(slug, rows, blank_billing(), True, None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write a public-safe Actions snapshot. Does not push.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    slug = github_slug(args.repo)
    if not slug:
        doc = build_snapshot(None, {}, blank_billing(), False, "repo_slug")
        # build_snapshot drops workflows and needs a slug for runs. Rewrite repo null.
        doc["repo"] = None
        doc["fetched"] = False
    else:
        try:
            doc = fetch(slug)
        except (OSError, subprocess.TimeoutExpired) as exc:
            doc = build_snapshot(slug, {}, blank_billing(), False, type(exc).__name__)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(dumps(doc))
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
