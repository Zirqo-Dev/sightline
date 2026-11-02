"""Raw forensics funding cache. Public files store w- hashes only.

The scan keeps the raw map in memory. When SIGHTLINE_PRIVATE_TOKEN is set, a
flush writes forensics/funding.json in the private repo (the repo must be
private). The token, the URL, and wallet addresses are not printed.
"""
import json
import os
import shutil
import subprocess
import sys

_BY_PATH = {}


def current(path):
    return _BY_PATH.get(str(path))


def remember(path, data):
    if isinstance(data, dict):
        _BY_PATH[str(path)] = data


def _token():
    token = os.environ.get("SIGHTLINE_PRIVATE_TOKEN", "")
    return token.strip() if isinstance(token, str) else ""


def read_repo():
    """Raw cache from the private repo, or None. No clone when the token is unset."""
    if not _token():
        return None
    return _with_repo(_read)


def flush(path):
    """Push the in-memory raw cache. Empty and already-public maps are not pushed."""
    from sol_forensics.public_names import cache_is_public
    data = current(path)
    if not isinstance(data, dict) or not data or cache_is_public(data):
        return False
    if not _token():
        return False
    return bool(_with_repo(lambda tmp: _write(tmp, data)))


def _read(tmp):
    path = os.path.join(tmp, "forensics", "funding.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    from sol_forensics.public_names import cache_is_public
    if not isinstance(doc, dict) or cache_is_public(doc):
        return None
    return doc


def _write(tmp, data):
    import rugger_blacklist as RB
    dest = os.path.join(tmp, "forensics", "funding.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as fh:
        json.dump(data, fh)
        fh.write("\n")
    token = _token()
    RB._git(["git", "add", "--", "forensics/funding.json"], tmp, token)
    quiet = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=tmp, capture_output=True)
    if quiet.returncode != 0:
        RB._git(
            ["git", "-c", "user.email=forensics@users.noreply.github.com", "-c", "user.name=forensics",
             "commit", "-q", "-m", "forensics funding cache"],
            tmp, token)
    RB._rebase_and_push(tmp, token)
    return True


def _with_repo(fn):
    import rugger_blacklist as RB
    token = _token()
    repo = RB._private_repo()
    if not repo or not RB._repo_is_private(repo, token):
        return None
    tmp = None
    try:
        tmp = RB._clone_private(repo, token)
        return fn(tmp)
    except Exception:
        print("::warning::forensics: private cache unavailable", file=sys.stderr)
        return None
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
