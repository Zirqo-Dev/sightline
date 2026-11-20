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
from test_dev_checks import FX, addr, dc, stake, unit, utxo  # noqa: E402

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


class _Book:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def koios_open(self):
        return True

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
        return {h: {"inputs": by[h]["inputs"], "outputs": by[h]["outputs"]} for h in hashes if h in by}

    def attach_withdrawals(self, ordered, stake_key):
        return None


def _sell_tx(token, height):
    return {
        "hash": "sell", "height": height, "time": FX["launch_s"] + FX["late_after_s"],
        "inputs": [utxo("dev", 5_000_000, {token: FX["mint_qty"]}, "mint#0")],
        "outputs": [
            utxo("dev", 5_000_000 + FX["sell_lovelace"], {token: FX["mint_qty"] - FX["sell_qty"]}, "sell#0"),
            utxo("buyer", 2_000_000, {token: FX["sell_qty"]}, "sell#1"),
        ],
    }


def _sell_book(middle, with_sell):
    token = unit("SELL")
    rows = [{
        "hash": "mint", "height": 1, "time": FX["launch_s"],
        "inputs": [],
        "outputs": [
            utxo("dev", 2_000_000, {token: FX["mint_qty"]}, "mint#0"),
            utxo("buyer", 2_000_000, {token: FX["buyer_qty"]}, "mint#1"),
        ],
    }]
    for i in range(middle):
        rows.append({
            "hash": "f%04d" % i, "height": i + 2, "time": FX["launch_s"] + i + 1,
            "inputs": [], "outputs": [],
        })
    if with_sell:
        rows.append(_sell_tx(token, rows[-1]["height"] + 1))
    return token, rows


def _sell_item(token):
    return {
        "ticker": "SELL", "unit": token,
        "launch": {
            "launcherAddress": addr("dev"), "liveAtMs": FX["launch_s"] * 1000,
            "policyId": token[:56], "assetName": token[56:], "ticker": "SELL",
        },
        "watch": {},
    }


def _main_ordered(rows):
    """The two pages a scan without a cursor reads: oldest, then newest."""
    merged = {}
    for row in list(rows[:LIM]) + list(reversed(rows))[:LIM]:
        merged[row["hash"]] = row
    return sorted(merged.values(), key=lambda row: (row["height"], row["hash"]))


def _main_scan(token, rows):
    ordered = _main_ordered(rows)
    stats = DC.fold(token, ordered, stake("dev"), set(), FX["launch_s"], dc())
    return {
        "stake": stake("dev"), "complete": len(rows) <= LIM, "saw_newest": True,
        "last_tx_s": ordered[-1]["time"], "stats": stats, "skipped": None,
        "unread_txs": 0, "launch_s": FX["launch_s"],
    }


def _trigger(scan, item):
    stats = scan.get("stats") or {}
    hashes = list(stats.get("sell_hashes") or [])
    ada = round(sum(float((stats.get("sell_ada") or {}).get(h) or 0) for h in hashes), 2)
    prev = {"unit": item["unit"], "dev_sold_ada": 0, "late_rug": False, "dev_tag": "w-abcdef01"}
    row = DC._entry(
        item, scan, prev, {"units": {}, "sells": {}}, [item["launch"]], dc(),
        FX["now_s"], FX["now_s"] * 1000, False, hashes, ada,
    )
    return DC.trigger_for(row)


def _kind(trigger):
    return None if trigger is None else trigger.get("kind")


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
        created = cache["cursors"][_item()["unit"]]["created_at"]
        second = _read(chain, cache, now + 7200)
        # Newest page every resume, then the next oldest page. Two calls, same budget.
        self.assertEqual(chain.calls[2:], [(True, 0), (False, LIM)])
        self.assertTrue(second["complete"])
        self.assertEqual(second["txs_read"], 60)
        self.assertNotIn("asc_offset", second)
        self.assertEqual(cache["cursors"][_item()["unit"]]["created_at"], created)
        third = _read(chain, cache, now + 14400)
        self.assertTrue(third["complete"])
        self.assertEqual(third["txs_read"], 60)
        self.assertEqual(chain.calls[-1], (True, 0))

    def test_age_is_from_creation_and_a_refresh_does_not_keep_a_stale_cursor(self):
        chain = _Chain(60)
        cache = {}
        now = FX["now_s"]
        _read(chain, cache, now)
        token = _item()["unit"]
        created = cache["cursors"][token]["created_at"]
        cache["cursors"][token]["updated_at"] = int(now - DC.CURSOR_MAX_AGE_S - 10)
        chain.calls.clear()
        _read(chain, cache, now + 10)
        self.assertEqual(chain.calls[0], (True, 0))
        self.assertEqual(cache["cursors"][token]["created_at"], created)
        cache["cursors"][token]["created_at"] = int(now - DC.CURSOR_MAX_AGE_S - 10)
        cache["cursors"][token]["updated_at"] = int(now)
        cache["cursors"][token]["asc_offset"] = 999
        chain.calls.clear()
        again = _read(chain, cache, now + 20)
        self.assertEqual(chain.calls[0], (False, 0))
        self.assertNotIn(999, [offset for _desc, offset in chain.calls])
        self.assertFalse(again["complete"])
        self.assertEqual(cache["cursors"][token]["asc_offset"], LIM)

    def test_known_roles_still_come_from_the_config_labels(self):
        roles = DC.known_roles()
        self.assertIn("flip_pair", roles)
        self.assertIn("first_block_sniper", roles)
        self.assertTrue(all(isinstance(role, str) for role in roles))

    def test_the_cap_keeps_the_cursor(self):
        old = DC._CURSOR_TX_CAP
        DC._CURSOR_TX_CAP = 30
        try:
            chain = _Chain(60)
            cache = {}
            now = FX["now_s"]
            first = _read(chain, cache, now)
            token = _item()["unit"]
            stored = cache["cursors"][token]
            self.assertTrue(stored["capped"])
            self.assertLessEqual(len(stored["txs"]), 30)
            self.assertFalse(first["complete"])
            self.assertIsNotNone(DC.cursor_usable(stored, now))
            chain.calls.clear()
            second = _read(chain, cache, now + 7200)
            self.assertEqual(chain.calls[0], (True, 0))
            self.assertFalse(second["complete"])
            self.assertLessEqual(len(cache["cursors"][token]["txs"]), 30)
            self.assertEqual(cache["cursors"][token]["created_at"], stored["created_at"])
        finally:
            DC._CURSOR_TX_CAP = old

    def test_a_sell_that_arrives_mid_walk_is_on_the_next_newest_page(self):
        token, rows = _sell_book(40, False)
        chain = _Book(rows)
        cache = {}
        item = _sell_item(token)
        first = DC._scan(chain, item, set(), dc(), cache, FX["now_s"])
        DC._store_cursor(cache, token, first)
        self.assertNotIn("sell", (first.get("stats") or {}).get("sell_hashes") or [])
        rows.append(_sell_tx(token, rows[-1]["height"] + 1))
        chain.calls.clear()
        second = DC._scan(chain, item, set(), dc(), cache, FX["now_s"] + 7200)
        DC._store_cursor(cache, token, second)
        self.assertEqual(chain.calls[0], (True, 0))
        main = _main_scan(token, rows)
        self.assertIn("sell", second["stats"]["sell_hashes"])
        self.assertIn("sell", main["stats"]["sell_hashes"])
        self.assertEqual(second["stats"]["sell_hashes"], main["stats"]["sell_hashes"])
        self.assertEqual(_kind(_trigger(second, item)), "dev_sell")
        self.assertEqual(_kind(_trigger(main, item)), "dev_sell")

    def test_run_1_sees_the_same_dev_sell_as_a_scan_without_a_cursor(self):
        token, rows = _sell_book(40, True)
        pr = DC._scan(_Book(rows), _sell_item(token), set(), dc(), {}, FX["now_s"])
        main = _main_scan(token, rows)
        self.assertIn("sell", pr["stats"]["sell_hashes"])
        self.assertEqual(pr["stats"]["sell_hashes"], main["stats"]["sell_hashes"])
        self.assertEqual(pr["stats"]["sold_ada"], main["stats"]["sold_ada"])
        self.assertEqual(pr["stats"]["late_rug"], main["stats"]["late_rug"])
        self.assertEqual(_kind(_trigger(pr, _sell_item(token))), "dev_sell")
        self.assertEqual(_kind(_trigger(main, _sell_item(token))), "dev_sell")

    def test_a_full_new_page_after_done_stays_incomplete(self):
        chain = _Chain(60)
        cache = {}
        now = FX["now_s"]
        _read(chain, cache, now)
        done = _read(chain, cache, now + 7200)
        self.assertTrue(done["complete"])
        start = chain.rows[-1]["height"]
        for i in range(60):
            chain.rows.append({
                "hash": "n%04d" % i, "height": start + 1 + i, "time": 1_800_000_000 + i,
                "inputs": [], "outputs": [],
            })
        gap = _read(chain, cache, now + 14400)
        fresh = _Chain(0)
        fresh.rows = list(chain.rows)
        main = DC._scan(fresh, _item(), set(), dc(), {}, now + 14400)
        self.assertFalse(gap["complete"])
        self.assertFalse(main["complete"])
        self.assertFalse(gap["stats"]["late_rug"])
        self.assertFalse(main["stats"]["late_rug"])

    def test_a_cursor_for_a_token_that_left_the_list_is_dropped(self):
        cache = {"cursors": {"stay": {"v": 1}, "left": {"v": 1}}}
        DC._prune_cursors(cache, ["stay"])
        self.assertEqual(set(cache["cursors"]), {"stay"})
        import json
        import tempfile
        from test_dev_checks import run_dc, world
        _, _, books, launches, tracked = world()
        with tempfile.TemporaryDirectory() as directory:
            os.makedirs(os.path.join(directory, "cache"))
            run_dc(True, books, launches, tracked, directory)
            path = os.path.join(directory, "cache", "dev-checks-cache.json")
            stored = json.load(open(path))
            stored.setdefault("cursors", {})["not-on-the-list"] = {"v": 1, "txs": []}
            json.dump(stored, open(path, "w"))
            run_dc(True, books, launches, tracked, directory)
            stored = json.load(open(path))
        self.assertNotIn("not-on-the-list", stored.get("cursors") or {})


if __name__ == "__main__":
    unittest.main()
