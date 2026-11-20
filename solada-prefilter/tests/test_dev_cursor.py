"""Dev-check history resumes from a private cursor. Offline, no secrets.

The first read of a token still uses the same two pages. Later runs continue,
and a cursor older than the bound starts over.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import dev_checks as DC  # noqa: E402
from test_dev_checks import FX, addr, dc, unit  # noqa: E402

LIM = 25


class _Chain:
    def __init__(self, n, koios=True):
        self.rows = []
        for i in range(n):
            self.rows.append({
                "hash": "h%04d" % i,
                "height": i + 1,
                "time": 1_700_000_000 + i,
                "inputs": [],
                "outputs": [],
            })
        self.calls = []
        self.koios = koios

    def koios_open(self):
        return self.koios

    def stop_reason(self):
        return None

    def late(self):
        return False

    def asset_txs(self, unit, desc=False, offset=0):
        self.calls.append((bool(desc), int(offset)))
        rows = list(reversed(self.rows)) if desc else list(self.rows)
        chunk = rows[int(offset):int(offset) + LIM]
        return [(row["hash"], row["height"], row["time"]) for row in chunk]

    def utxos(self, hashes, dev=None):
        by = {row["hash"]: row for row in self.rows}
        return {h: {"inputs": [], "outputs": []} for h in hashes if h in by}

    def attach_withdrawals(self, ordered, stake):
        return None


def _item():
    token = unit("CUR")
    return {
        "ticker": "CUR", "unit": token,
        "launch": {"launcherAddress": addr("dev"), "liveAtMs": FX["now_s"] * 1000},
        "watch": {},
    }


def _read(chain, cache, now_s):
    scan = DC._scan(chain, _item(), set(), dc(), cache, now_s)
    DC._store_cursor(cache, _item()["unit"], scan)
    return scan


class Resume(unittest.TestCase):
    def test_a_long_history_continues_and_then_completes(self):
        chain = _Chain(60)
        cache = {}
        now = FX["now_s"]
        first = _read(chain, cache, now)
        self.assertFalse(first["complete"])
        self.assertEqual(chain.calls, [(False, 0), (True, 0)])
        self.assertNotIn("cursor", first)
        stored = cache["cursors"][_item()["unit"]]
        self.assertEqual(stored["asc_offset"], LIM)
        self.assertEqual(stored["desc_offset"], LIM)
        self.assertFalse(stored["done"])
        second = _read(chain, cache, now + 7200)
        self.assertEqual(chain.calls[2:], [(False, LIM), (True, LIM)])
        self.assertTrue(second["complete"])
        self.assertEqual(second["txs_read"], 60)
        self.assertNotIn("asc_offset", second)
        third = _read(chain, cache, now + 14400)
        self.assertTrue(third["complete"])
        self.assertEqual(third["txs_read"], 60)
        self.assertEqual(chain.calls[-1], (True, 0))

    def test_a_stale_cursor_starts_at_the_first_page(self):
        chain = _Chain(60)
        cache = {}
        now = FX["now_s"]
        _read(chain, cache, now)
        token = _item()["unit"]
        cache["cursors"][token]["updated_at"] = int(now - DC.CURSOR_MAX_AGE_S - 10)
        cache["cursors"][token]["asc_offset"] = 999
        cache["cursors"][token]["desc_offset"] = 999
        chain.calls.clear()
        again = _read(chain, cache, now)
        self.assertEqual(chain.calls[0], (False, 0))
        self.assertNotIn(999, [offset for _desc, offset in chain.calls])
        self.assertFalse(again["complete"])
        self.assertEqual(cache["cursors"][token]["asc_offset"], LIM)
        self.assertNotEqual(cache["cursors"][token]["updated_at"], int(now - DC.CURSOR_MAX_AGE_S - 10))

    def test_known_roles_still_come_from_the_config_labels(self):
        roles = DC.known_roles()
        self.assertIn("flip_pair", roles)
        self.assertIn("first_block_sniper", roles)
        self.assertTrue(all(isinstance(role, str) for role in roles))


if __name__ == "__main__":
    unittest.main()
