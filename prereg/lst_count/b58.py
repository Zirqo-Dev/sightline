"""Base58 for public mint and pool addresses. Stdlib only."""

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(ALPHABET)}


def b58decode(text):
    n = 0
    for ch in text:
        n = n * 58 + _INDEX[ch]
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + raw


def b58encode(raw):
    n = int.from_bytes(raw, "big")
    chars = []
    while n:
        n, rem = divmod(n, 58)
        chars.append(ALPHABET[rem])
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + "".join(reversed(chars or ["1"]))
