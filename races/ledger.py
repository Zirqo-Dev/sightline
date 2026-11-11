"""Append-only race files. Rewritten files are the latest snapshot only."""

import json
import pathlib

from races.scrub import scrub


class Ledger:
    def __init__(self, data_dir, allow):
        self.root = pathlib.Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.allow = set(allow)

    def allow_add(self, *items):
        for item in items:
            if item:
                self.allow.add(item)

    def _clean(self, obj):
        return scrub(obj, self.allow)

    def append(self, name, obj):
        line = json.dumps(self._clean(obj), separators=(",", ":"), sort_keys=True)
        path = self.root / name
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def write_json(self, name, obj):
        path = self.root / name
        path.write_text(json.dumps(self._clean(obj), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def read_json(self, name, default):
        path = self.root / name
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def read_jsonl(self, name):
        path = self.root / name
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
