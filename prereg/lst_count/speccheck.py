"""The runner stops unless the locked spec file matches the pinned sha256."""

import hashlib
from pathlib import Path

from lst_count import SPEC_SHA256

SPEC_NAME = "lst_count_spec_v1.md"


def spec_path():
    return Path(__file__).resolve().parent / SPEC_NAME


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_spec(path=None):
    path = Path(path) if path is not None else spec_path()
    digest = file_sha256(path)
    if digest != SPEC_SHA256:
        raise SystemExit("lst count spec hash mismatch")
    return digest
