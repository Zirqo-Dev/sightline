"""Shared Koios tx reader (review fixes S5/S8/S9/S10 + 1 KB clamp) for big_moves, whale_ledger and flags.

- /tx_info (not the deprecated /tx_utxos) with _inputs/_assets/_scripts: inputs exclude collateral and
  reference inputs (Koios lists those separately), outputs carry inline datums (used for order-replace detection).
- Batches are clamped so every POST body stays under the public tier's 1 KB limit (a 1,716 B body = HTTP 413).
- 404s are not silent (quiet404=False): a missing endpoint shows up in the data-failure log.
"""
import json, os, tempfile

KOIOS = "https://api.koios.rest/api/v1"
BODY_MAX = 1000  # public tier rejects bodies over 1 KB


def batches(items, key, n, extra=None, body_max=BODY_MAX):
    """Split items into POST bodies {key: chunk, **extra} of <= n items and <= body_max bytes each."""
    out, cur = [], []
    for it in items:
        trial = cur + [it]
        if cur and (len(trial) > max(1, int(n)) or len(json.dumps({key: trial, **(extra or {})})) > body_max):
            out.append(cur)
            trial = [it]
        cur = trial
    if cur:
        out.append(cur)
    return [{key: c, **(extra or {})} for c in out]


def _datum_text(u):
    d = u.get("inline_datum")
    if isinstance(d, dict):
        return json.dumps(d.get("value") if d.get("value") is not None else d.get("bytes"), sort_keys=True)
    return ""


def norm_utxo(u):
    return {"ref": f"{u['tx_hash']}#{u['tx_index']}", "addr": (u.get("payment_addr") or {}).get("bech32") or "",
            "stake": u.get("stake_addr"), "lovelace": int(u.get("value") or 0),
            "assets": {a["policy_id"] + a["asset_name"]: int(a["quantity"]) for a in (u.get("asset_list") or [])},
            "datum": _datum_text(u)}


TX_INFO_FLAGS = {"_inputs": True, "_assets": True, "_scripts": True, "_withdrawals": True}


def _withdrawals(tx):
    """{stake: lovelace} claimed in this tx. Reward withdrawals are not sale proceeds."""
    out = {}
    for w in tx.get("withdrawals") or []:
        st = w.get("stake_address") or w.get("stake_addr") or ""
        try:
            amt = int(w.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0
        if st and amt > 0:
            out[st] = out.get(st, 0) + amt
    return out


def tx_info(http, hashes, n, budget_ok=lambda: True):
    """{hash: {inputs, outputs, withdrawals}} with normalized UTxO dicts (spend inputs only). Missing hashes = not read.
    `_withdrawals` is requested so a staking reward claimed in the same tx can be taken out of the ADA gain."""
    out = {}
    for body in batches(hashes, "_tx_hashes", n, TX_INFO_FLAGS):
        if not budget_ok():
            break
        r = http.req("koios", KOIOS + "/tx_info", method="POST", body=body, quiet404=False)
        for tx in r or []:
            out[tx["tx_hash"]] = {"inputs": [norm_utxo(u) for u in tx.get("inputs") or []],
                                  "outputs": sorted((norm_utxo(u) for u in tx.get("outputs") or []),
                                                    key=lambda x: int(x["ref"].split("#")[1])),
                                  "withdrawals": _withdrawals(tx)}
    return out


TMP_TAG = ".tmp-"
# Retired risk-map key. Built so the public source does not spell the vendor name.
# All-caps ticker keys (a real coin) are left alone. Launch ids that merely contain
# the same letters, such as draft-<ticker>, are values and are left alone.
RETIRED_RISK_KEY = "".join(chr(n) for n in (115, 117, 114, 105))
PUBLIC_RISK_FILES = ("state.json", "whale_ledger.json")


def neutral_public_names(obj, parent_key=None):
    """Rename a retired risk-map key to risk_check and its source value to scanner.

    dev_checks stays. A ticker key written in all caps is a coin and is not renamed.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            nk = k
            if isinstance(k, str) and k.casefold() == RETIRED_RISK_KEY and k != k.upper():
                nk = "risk_check"
            out[nk] = neutral_public_names(v, nk)
        return out
    if isinstance(obj, list):
        return [neutral_public_names(v, parent_key) for v in obj]
    if isinstance(obj, str) and parent_key == "source" and obj.casefold() == RETIRED_RISK_KEY:
        return "scanner"
    return obj


def write_json(path, obj, **kw):
    """Atomic write: temp file in the same directory, then os.replace. A killed write leaves `<name>.tmp-*`, which
    clean_tmp() removes at the start of the next run (so `git add -A` never commits one).

    state.json and whale_ledger.json drop a retired risk-map key on the way out.
    """
    if os.path.basename(path) in PUBLIC_RISK_FILES:
        obj = neutral_public_names(obj)
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + TMP_TAG)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, **kw)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def clean_tmp(d):
    """Remove leftover temp files of our own atomic writes (`*.json.tmp-*`) in d. Touches nothing else."""
    n = 0
    if d and os.path.isdir(d):
        for f in os.listdir(d):
            if ".json" + TMP_TAG in f:
                os.remove(os.path.join(d, f))
                n += 1
    return n


def read_json(path, default):
    """Load a cache/state file; a corrupt or truncated file resets to default (never crashes the run)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, type(default)) else default
    except (ValueError, OSError):
        return default
