"""SPD-77 Solana log-only path. Offline. Does not change skips, entries, or exits."""
import json
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("RUGGER_ID_PEPPER", "test-pepper-not-base64-and-at-least-32-chars")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desk import config as C  # noqa: E402
from desk import run, skips  # noqa: E402
import rugger_blacklist as RB  # noqa: E402

FX = os.path.join(os.path.dirname(__file__), "fixtures")
NOW = 1793383000
CREATOR = "REDACTEDaddr0547"
MINT_A = "MintA" + "1" * 20
MINT_B = "MintB" + "2" * 20


def _pair():
    rows = json.load(open(os.path.join(FX, "dex_pairs.json")))
    return next(p for p in rows if str(p.get("baseToken", {}).get("address", "")).startswith("DUxnHr"))


def _rc(**kw):
    rc = json.load(open(os.path.join(FX, "rugcheck_full_DUxnHr.json")))
    rc.update(kw)
    return rc


class LogOnly(unittest.TestCase):
    def _get(self, rc, mint):
        pair = _pair()
        quote = json.load(open(os.path.join(FX, "jup_lite_quote.json")))

        def get(url, headers=None):
            if "token-profiles" in url or "token-boosts" in url:
                return [{"chainId": "solana", "tokenAddress": mint}]
            if "/latest/dex/tokens/" in url:
                return {"pairs": [pair]}
            if "rugcheck" in url:
                return rc
            if "jup.ag" in url:
                return quote
            raise AssertionError("unexpected url")

        return get

    def test_a_rugged_creator_is_logged_later_and_the_task_row_stays(self):
        rc = _rc(rugged=True, creator=CREATOR)
        plain = skips.classify(_pair(), rc, json.load(open(os.path.join(FX, "jup_lite_quote.json"))), NOW, set())
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            h1 = run.scan(data, self._get(rc, MINT_A), key=None, now=NOW, mode="scan",
                          sleep=lambda s: None, cache_dir=cache)
            tasks1 = open(os.path.join(data, "tasks.csv")).read()
            self.assertIn(plain[1] or "", tasks1)
            self.assertFalse(os.path.exists(os.path.join(data, run.WOULD_NAME)))
            self.assertTrue(os.path.exists(os.path.join(cache, RB.PRIVATE_NAME)))
            self.assertNotIn(CREATOR, open(os.path.join(data, "health.json")).read())
            # Same creator, a different mint, still rugged. The skip reason is unchanged.
            h2 = run.scan(data, self._get(rc, MINT_B), key=None, now=NOW + 1800, mode="scan",
                          sleep=lambda s: None, cache_dir=cache)
            would = json.load(open(os.path.join(data, run.WOULD_NAME)))
            self.assertIs(would["buy_signal"], False)
            self.assertEqual(would["mode"], "log_only")
            self.assertEqual(would["rows"][0]["code"], "known_rugger")
            self.assertEqual(would["rows"][0]["mint"], MINT_B)
            self.assertEqual(would["rows"][0]["evidence_tags"], ["rugcheck_rugged"])
            self.assertTrue(RB._ID_RE.fullmatch(would["rows"][0]["rugger_id"]))
            blob = json.dumps(would)
            self.assertNotIn(CREATOR, blob)
            lines = open(os.path.join(data, "tasks.csv")).read().strip().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertNotIn("known_rugger", lines[-1])
            self.assertEqual(lines[-1].split(",")[8], plain[1])  # reason column
            self.assertFalse(os.path.exists(os.path.join(data, "trades.csv")))
            self.assertFalse(os.path.exists(os.path.join(data, "positions.json")))
            self.assertEqual(h1["mode"], "scan")
            self.assertEqual(h2["checks"], 2)

    def test_blacklist_does_not_change_fill_inputs(self):
        rc = _rc(rugged=False, creator=CREATOR)
        pair = _pair()
        quote = json.load(open(os.path.join(FX, "jup_lite_quote.json")))
        expected = skips.classify(pair, rc, quote, NOW, set())
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(cache)
            book = RB.open_book(cache, data)
            book.note_evidence(CREATOR, "solana", "rugcheck_rugged", MINT_A, RB.iso(NOW))
            book.save()
            seen = {}

            def step(data_dir, now, run_id, seed, run_leads, st, sell, liq, sleep):
                seen["leads"] = [(row["mint"], row["stype"], row["reason"]) for row in run_leads]
                return {"void": []}

            real = run.fills.step
            run.fills.step = step
            try:
                run.scan(data, self._get(rc, MINT_B), key="k", now=NOW, mode="fill",
                         sleep=lambda s: None, cache_dir=cache)
            finally:
                run.fills.step = real
            self.assertEqual(seen["leads"], [(MINT_B, expected[0], expected[1])])
            would = json.load(open(os.path.join(data, run.WOULD_NAME)))
            self.assertEqual(would["rows"][0]["code"], "known_rugger")
            self.assertNotIn(CREATOR, json.dumps(would))
            self.assertNotIn("known_rugger", open(os.path.join(data, "tasks.csv")).read().splitlines()[-1])

    def test_cache_miss_writes_no_private_file_into_desk_data(self):
        rc = _rc(rugged=True, creator=CREATOR)
        with tempfile.TemporaryDirectory() as data:
            run.scan(data, self._get(rc, MINT_A), key=None, now=NOW, mode="scan", sleep=lambda s: None)
            self.assertFalse(os.path.exists(os.path.join(data, RB.PRIVATE_NAME)))
            self.assertFalse(os.path.exists(os.path.join(data, run.WOULD_NAME)))

    def test_public_dir_cache_is_refused(self):
        rc = _rc(rugged=True, creator=CREATOR)
        with tempfile.TemporaryDirectory() as data:
            run.scan(data, self._get(rc, MINT_A), key=None, now=NOW, mode="scan",
                     sleep=lambda s: None, cache_dir=data)
            self.assertFalse(os.path.exists(os.path.join(data, RB.PRIVATE_NAME)))
            self.assertNotIn(CREATOR, open(os.path.join(data, "health.json")).read())

    def test_quote_gap_sleep_does_not_spend_the_rugger_budget(self):
        rc = _rc(rugged=True, creator=CREATOR)
        pair = _pair()
        quote = json.load(open(os.path.join(FX, "jup_lite_quote.json")))

        def get(url, headers=None):
            if "token-profiles" in url:
                return [{"chainId": "solana", "tokenAddress": m} for m in (MINT_A, MINT_B)]
            if "token-boosts" in url:
                return []
            if "/latest/dex/tokens/" in url:
                return {"pairs": [pair]}
            if "rugcheck" in url:
                return rc
            if "jup.ag" in url:
                return quote
            raise AssertionError("unexpected url")

        old = run.RUGGER_BUDGET_S
        run.RUGGER_BUDGET_S = run.QUOTE_GAP_S / 2
        try:
            with tempfile.TemporaryDirectory() as d:
                data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
                os.makedirs(data)
                os.makedirs(cache)
                t0 = time.monotonic()
                run.scan(data, get, key=None, now=NOW, mode="scan", sleep=time.sleep, cache_dir=cache)
                elapsed = time.monotonic() - t0
                self.assertGreaterEqual(elapsed, run.QUOTE_GAP_S)
                book = RB.open_book(cache, data)
                self.assertEqual(book.judge(CREATOR, "mint-other", NOW)[0]["code"], "known_rugger")
                self.assertTrue(os.path.exists(os.path.join(cache, RB.PRIVATE_NAME)))
        finally:
            run.RUGGER_BUDGET_S = old

    def test_an_owner_base58_key_is_not_stored(self):
        sol = "N" * 40
        rc = _rc(rugged=True, creator=sol)
        old = os.environ.get("PRIVACY_GUARD_PATTERNS")
        os.environ["PRIVACY_GUARD_PATTERNS"] = sol
        try:
            with tempfile.TemporaryDirectory() as d:
                data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
                os.makedirs(data)
                os.makedirs(cache)
                run.scan(data, self._get(rc, MINT_A), key=None, now=NOW, mode="scan",
                         sleep=lambda s: None, cache_dir=cache)
                book = RB.open_book(cache, data)
                self.assertEqual(book.judge(sol, MINT_B, NOW), [])
                self.assertNotIn(sol, open(os.path.join(cache, RB.PRIVATE_NAME)).read())
        finally:
            if old is None:
                os.environ.pop("PRIVACY_GUARD_PATTERNS", None)
            else:
                os.environ["PRIVACY_GUARD_PATTERNS"] = old

    def test_health_sync_field_is_hash_free(self):
        rc = _rc(rugged=True, creator=CREATOR)
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            with open(os.path.join(cache, RB.SYNC_NAME), "w") as fh:
                json.dump({"sync": "ok"}, fh)
            run.scan(data, self._get(rc, MINT_A), key=None, now=NOW, mode="scan",
                     sleep=lambda s: None, cache_dir=cache)
            health = json.load(open(os.path.join(data, "health.json")))
            self.assertEqual(health["rugger_status"]["sync"], "ok")
            self.assertNotIn(CREATOR, json.dumps(health["rugger_status"]))
            with open(os.path.join(cache, RB.SYNC_NAME), "w") as fh:
                json.dump({"sync": "ok", "wallet": CREATOR}, fh)
            book = RB.open_book(cache, data, desk="solana")
            self.assertNotIn("sync", book.status())

    def test_exclusions_come_from_rugger_json(self):
        self.assertFalse(hasattr(C, "RUGGER_EXCLUSION_SHA256"))
        digest = "ab" * 32
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rugger.json")
            with open(path, "w") as fh:
                json.dump({"exclusion_sha256": [digest, "nope"]}, fh)
            cfg = run._rugger_cfg(path)
        self.assertEqual(cfg["rugger_blacklist"]["exclusion_sha256"], [digest])
        self.assertEqual(run._rugger_cfg(os.path.join(ROOT, "solana-desk", "rugger.json"))["rugger_blacklist"]["exclusion_sha256"], [])


if __name__ == "__main__":
    unittest.main()
