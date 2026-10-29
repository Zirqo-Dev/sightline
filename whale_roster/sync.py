"""Push the private whale roster to sightline-private.

Runs after the desk-data commit. Refuses a repo that is not private before
any write. Unions with the remote file, then pull --rebase and push. Never
force-pushes. The token is the existing SIGHTLINE_PRIVATE_TOKEN.

    python3 -m whale_roster.sync --cache-dir DIR --public-dir DIR
"""
import argparse
import json
import os
import shutil
import subprocess

import rugger_blacklist as RB
import whale_roster as WR


def _load(path):
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def sync_roster(cache_dir, public_dir=None):
    """Union the cache roster into whales/roster.json. Returns True on a clean push."""
    if not cache_dir:
        return False
    token = RB._token()
    if not token:
        RB.warn_no_token()
        return False
    repo = RB._private_repo()
    if not repo:
        print("::warning::whale roster: private repo name rejected")
        return False
    if not RB._repo_is_private(repo, token):
        return False
    local = _load(os.path.join(cache_dir, WR.CACHE_ROSTER))
    if not isinstance(local, dict):
        print("::warning::whale roster: private repo not updated")
        return False
    if public_dir and RB._inside(os.path.join(cache_dir, WR.CACHE_ROSTER), public_dir):
        print("::warning::whale roster: private repo not updated")
        return False
    tmp = None
    try:
        tmp = RB._clone_private(repo, token)
        dest = os.path.join(tmp, WR.PRIVATE_REL)
        if public_dir and RB._inside(dest, public_dir):
            print("::warning::whale roster: private repo not updated")
            return False
        remote = _load(dest) if os.path.exists(dest) else None
        if os.path.exists(dest) and remote is None:
            RB.warn_not_updated()
            return False
        merged = WR.union_roster(local, remote)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as fh:
            json.dump(merged, fh, indent=2)
            fh.write("\n")
        rel = WR.PRIVATE_REL
        RB._git(["git", "add", "--", rel], tmp, token)
        quiet = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=tmp, capture_output=True)
        if quiet.returncode != 0:
            RB._git(["git", "-c", "user.email=whale-roster@users.noreply.github.com", "-c", "user.name=whale-roster",
                     "commit", "-q", "-m", "whale roster"], tmp, token)
        RB._rebase_and_push(tmp, token)
        parent = os.path.join(cache_dir, WR.CACHE_ROSTER)
        with open(parent, "w") as fh:
            json.dump(merged, fh)
            fh.write("\n")
        return True
    except Exception:
        RB.warn_not_updated()
        return False
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync the private whale roster")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--public-dir", default="")
    args = parser.parse_args(argv)
    # A missing token or a refused repo is a warning, same as the rugger sync step.
    # The desk-data commit has already happened. This step does not force-push.
    sync_roster(args.cache_dir, args.public_dir or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
