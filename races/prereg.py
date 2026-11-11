"""The runner refuses to start unless prereg_races_v2.md matches the pinned sha."""

import hashlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
PINNED = "5f52f68845e93c7338c08b1a898e29d6a96560c5f108916000e53e708f578e66"


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prereg_path(cfg=None):
    name = "prereg_races_v2.md"
    if cfg:
        name = ((cfg.get("prereg") or {}).get("file")) or name
    return ROOT / name


def check_prereg(cfg, log=None):
    """Stop the run before any quote if the v2 file does not match the pin."""
    path = prereg_path(cfg)
    digest = file_sha256(path)
    expected = str((cfg.get("prereg") or {}).get("sha256_expected") or "")
    if log:
        log(f"prereg sha256 {digest}")
    if digest != expected or digest != PINNED:
        raise SystemExit("prereg hash mismatch")
    return digest
