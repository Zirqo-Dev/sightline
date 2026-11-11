"""Drop wallet-like strings before anything is written.

Token mints and pool ids that the caller marks as public may stay.
"""

import re

_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# fullmatch, not `$`: `$` also matches before a trailing newline.
_DROP_KEYS = {
    "dev", "twitter", "telegram", "instagram", "owner", "holder", "holders",
    "creator", "deployer", "authority", "user", "wallet", "address", "pubkey",
    "signature", "secret", "seed",
}


def is_b58(value):
    return isinstance(value, str) and bool(_B58.fullmatch(value))


def scrub(value, allow):
    allow = set(allow or ())

    def walk(node, key=None):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "wallet" and isinstance(v, str) and re.fullmatch(r"(R[1-5]|SOL)", v):
                    out[k] = v
                    continue
                if k in _DROP_KEYS and k not in ("mint", "pool_id"):
                    continue
                out[k] = walk(v, k)
            return out
        if isinstance(node, list):
            return [walk(v, key) for v in node]
        if isinstance(node, str):
            if key in ("mint", "pool_id", "input_mint", "output_mint") and node in allow:
                return node
            if is_b58(node) and node not in allow:
                return "[redacted]"
            return node
        return node

    return walk(value)
