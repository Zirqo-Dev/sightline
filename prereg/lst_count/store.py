"""Append-only captures.jsonl. status.json is the current snapshot.

The spec says both files are append-only. A JSON object cannot be appended
and stay valid JSON, so status.json is replaced. captures.jsonl is only
appended. Each write logs its sha256 and UTC time.
"""

import hashlib
import json
from pathlib import Path

from lst_count.logic import iso


def captures_path(data_dir):
    return Path(data_dir) / "captures.jsonl"


def status_path(data_dir):
    return Path(data_dir) / "status.json"


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_status(path):
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _seal(obj, now):
    body = dict(obj)
    body["written_at"] = iso(now)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["sha256"] = hashlib.sha256(canonical).hexdigest()
    return body


def _log(kind, record_sha, file_sha, when):
    print("write %s record_sha256=%s file_sha256=%s at=%s" % (kind, record_sha, file_sha, iso(when)), flush=True)


def append_jsonl(path, obj, now):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _seal(obj, now)
    line = json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    _log(path.name, body["sha256"], file_sha, now)
    return body


def write_status(path, obj, now):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _seal(obj, now)
    text = json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n"
    path.write_text(text, encoding="utf-8")
    file_sha = hashlib.sha256(text.encode()).hexdigest()
    _log(path.name, body["sha256"], file_sha, now)
    return body
