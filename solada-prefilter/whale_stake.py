"""Resolve the lead whale stake key at runtime. Never print it.

Order: WHALE_STAKE, then an in-memory config stake (unit tests and a local
override; the committed config must not contain one), then the private repo.
The private read uses SIGHTLINE_PRIVATE_TOKEN and refuses a repo that is not
private, same as the identity-hash fetch. It tries whales/lead.stake (one line)
and then the lead row of whales/roster.json. Nothing here is written to a
public file. Callers publish the w- tag only.
"""
import json
import os
import re
import urllib.request

_STAKE = re.compile(r"stake1[02-9ac-hj-np-z]{20,}")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_LEAD_FILE = "whales/lead.stake"
_ROSTER_FILE = "whales/roster.json"


def _shape(value):
    return isinstance(value, str) and bool(_STAKE.fullmatch(value.strip()))


def _prefix(cfg):
    if not isinstance(cfg, dict):
        return ""
    raw = ((cfg.get("whale") or {}).get("stake_prefix") or "")
    return raw.strip() if isinstance(raw, str) else ""


def _matches_prefix(stake, cfg):
    prefix = _prefix(cfg)
    if not prefix:
        return True
    return stake.startswith("stake1" + prefix)


def _memory(cfg):
    if not isinstance(cfg, dict):
        return ""
    raw = (cfg.get("whale") or {}).get("stake")
    if not isinstance(raw, str):
        return ""
    raw = raw.strip()
    return raw if _shape(raw) else ""


def _private_repo():
    repo = os.environ.get("SIGHTLINE_PRIVATE_REPO", "Zirqo-Dev/sightline-private")
    if not isinstance(repo, str):
        return ""
    repo = repo.strip()
    return repo if _REPO.fullmatch(repo) else ""


def _github(url, token, accept):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": accept,
            "User-Agent": "sightline-whale-stake",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read(1_000_000)


def _repo_is_private(repo, token):
    try:
        body = _github("https://api.github.com/repos/" + repo, token, "application/vnd.github+json")
        meta = json.loads(body.decode())
    except Exception:
        meta = None
    if not isinstance(meta, dict) or meta.get("private") is not True:
        print("::warning::whale stake: private repo refused", flush=True)
        return False
    return True


def _raw_file(repo, token, path):
    url = "https://api.github.com/repos/%s/contents/%s" % (repo, path)
    try:
        body = _github(url, token, "application/vnd.github.raw")
    except Exception:
        return None
    try:
        return body.decode()
    except Exception:
        return None


def _from_text(text, cfg):
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        line = line.strip()
        if _shape(line) and _matches_prefix(line, cfg):
            return line
    return ""


def _from_roster(text, cfg):
    if not isinstance(text, str) or not text.strip():
        return ""
    try:
        doc = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(doc, dict):
        return ""
    for whale in doc.get("whales") or []:
        if not isinstance(whale, dict):
            continue
        if whale.get("role") != "lead" and whale.get("codename") != "Grumpy Walrus":
            continue
        for wallet in whale.get("wallets") or []:
            if isinstance(wallet, str) and _shape(wallet.strip()) and _matches_prefix(wallet.strip(), cfg):
                return wallet.strip()
    return ""


def _from_private(cfg):
    token = os.environ.get("SIGHTLINE_PRIVATE_TOKEN", "")
    token = token.strip() if isinstance(token, str) else ""
    if not token:
        return ""
    repo = _private_repo()
    if not repo or not _repo_is_private(repo, token):
        return ""
    stake = _from_text(_raw_file(repo, token, _LEAD_FILE), cfg)
    if stake:
        return stake
    stake = _from_roster(_raw_file(repo, token, _ROSTER_FILE), cfg)
    if not stake:
        print("::warning::whale stake: private repo has no lead stake", flush=True)
    return stake


def resolve(cfg=None):
    """Return the stake key, or "" when none of the runtime sources have one."""
    env = os.environ.get("WHALE_STAKE", "")
    env = env.strip() if isinstance(env, str) else ""
    if _shape(env):
        return env
    mem = _memory(cfg)
    if mem:
        return mem
    try:
        return _from_private(cfg) or ""
    except Exception:
        print("::warning::whale stake: private repo unread", flush=True)
        return ""
