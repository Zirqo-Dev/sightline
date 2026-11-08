"""SH-04: validated, unwired by design, not a writer defect.

A Solana liquidity-pull exit does not write the rugger blacklist. The README
says there is no LP-pull or dump event feed. observe_rugger is the only
Solana writer, and it records RugCheck rugged is True on a lead. It does not
see the exit, and the exit does not carry a deployer.

The Solana blacklist is log-only. The hook runs after skip classification.
Skip, fill, exit, and book code never read it, so a writer would not change
v6.5 paper entries and would not bias F against R. Leaving the write for
v6.6 is operational risk in the frozen run: new code can crash or time out
a scan, an outage over 2h voids new entries, and work in the lead loop or
manage() moves quote timing.

Production path: desk.run.scan(mode="fill") calls fills.step, which calls
exits.check. A SOL-side drop of at least liq_drop closes as liquidity_pulled.
The rugger book is saved earlier, from the lead loop.

Desk-data through 2026-11-01: 69 liquidity_pulled sells on 51 mints, and
rugger_status entries 0. 35 of those sells are before the blacklist merged
(2026-10-29 17:11Z). After that merge: 34 sells on 27 mints. 13 of the 51
mints came back as leads after the pull; RugCheck flagged none rugged, and
2 passed the safety filters again.
"""
import csv
import inspect
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("RUGGER_ID_PEPPER", "test-pepper-not-base64-and-at-least-32-chars")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desk import run  # noqa: E402
import rugger_blacklist as RB  # noqa: E402
from tests.decision_market import address_n  # noqa: E402

# Synthetic. Not a real wallet. Already the shape used by the rugger log tests.
CREATOR = "REDACTEDaddr0547"
MINT = address_n(4001)
OTHER = address_n(4002)
LATER = address_n(4003)
SOL = run.SOL
T0 = 1_800_000_000  # on a 30-minute slot boundary
TOK = 1_000_000_000
CREATED_MS = (T0 - 7200) * 1000  # 2h old: past the 15-minute floor, inside 48h


def _pair(mint, liq):
    return {
        "chainId": "solana",
        "dexId": "pumpswap",
        "pairAddress": "Pair" + mint[:8],
        "quoteToken": {"address": SOL},
        "baseToken": {"address": mint, "symbol": "PULL"},
        "pairCreatedAt": CREATED_MS,
        "txns": {"h1": {"sells": 5, "buys": 5}},
        "liquidity": {"quote": liq, "base": 1_000_000, "usd": 10000},
    }


def _rc(rugged):
    return {
        "rugged": rugged,
        "creator": CREATOR,
        "creatorBalance": 0,
        "mintAuthority": None,
        "freezeAuthority": None,
        "score_normalised": 5,
        "topHolders": [],
        "risks": [],
        "markets": [{
            "marketType": "pump_fun_amm",
            "pubkey": "Pair" + MINT[:8],
            "lp": {"lpLockedPct": 100},
        }],
        "token": {"supply": 1},
    }


def _get(mint, liq, rugged, unexpected):
    """Serve the scripted lead. Unknown URLs are recorded, then raised.

    scan catches Exception around each fetch, so a bare raise is swallowed
    and the test would still pass. The list is the guard.
    """
    def get(url, headers=None):
        if url.endswith("/token-profiles/latest/v1"):
            return [{"chainId": "solana", "tokenAddress": mint}]
        if url.endswith("/token-boosts/latest/v1"):
            return []
        if "/latest/dex/tokens/" in url:
            return {"pairs": [_pair(mint, liq)]}
        if "rugcheck.xyz" in url:
            body = _rc(rugged)
            if mint != MINT:
                body["markets"] = [{
                    "marketType": "pump_fun_amm",
                    "pubkey": "Pair" + mint[:8],
                    "lp": {"lpLockedPct": 100},
                }]
            return body
        if "jup.ag" in url and "quote" in url:
            return {"outAmount": str(TOK), "priceImpactPct": "0.01", "routePlan": [1]}
        unexpected.append(url)
        raise AssertionError("unexpected url")

    return get


class SolanaPullDoesNotBlacklist(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for key in ("RUGGER_ACCEPT_RESET", "SIGHTLINE_PRIVATE_TOKEN", "GITHUB_ACTIONS", "GITHUB_EVENT_NAME"):
            self._saved[key] = os.environ.pop(key, None)
        self.notes = []
        self.syncs = []
        self.unexpected = []
        self.phase = "setup"
        self._real_note = RB.Book.note_evidence
        self._real_sync = RB.sync_cache
        self._real_push = RB.push_private

        def note(book, wallet, chain, tag, token, at):
            # Chain, tag, and phase only. The wallet stays out of the spy log.
            self.notes.append((self.phase, chain, tag, token))
            return self._real_note(book, wallet, chain, tag, token, at)

        def sync_cache(*_args, **_kwargs):
            self.syncs.append("sync_cache")
            return False

        def push_private(*_args, **_kwargs):
            self.syncs.append("push_private")
            return False

        RB.Book.note_evidence = note
        RB.sync_cache = sync_cache
        RB.push_private = push_private

    def tearDown(self):
        RB.Book.note_evidence = self._real_note
        RB.sync_cache = self._real_sync
        RB.push_private = self._real_push
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_pull_exit_writes_no_blacklist_entry(self):
        # The hook the scan uses for a pull is fills.step. It has no blacklist call.
        # TODO(v6.6): drop these source assertions once a private-cache write exists.
        # That write would stay log-only. The wait is operational risk, not F-vs-R bias.
        self.assertIn("fills.step", inspect.getsource(run.scan))
        self.assertNotIn("note_evidence", inspect.getsource(run.fills.step))
        self.assertNotIn("note_evidence", inspect.getsource(run.fills.manage))
        self.assertNotIn("liquidity_pulled", inspect.getsource(run.observe_rugger))
        self.assertIn("rugcheck_rugged", inspect.getsource(run.observe_rugger))

        with tempfile.TemporaryDirectory() as root:
            data, cache = os.path.join(root, "data"), os.path.join(root, "cache")
            os.makedirs(data)
            os.makedirs(cache)

            def scan_at(when, mint, liq, rugged, mode):
                return run.scan(
                    data, _get(mint, liq, rugged, self.unexpected), key=None, now=when, mode=mode,
                    sleep=lambda _s: None, cache_dir=cache, v2_log=False,
                )

            self.phase = "entry"
            scan_at(T0, MINT, 100.0, False, "fill")
            with open(os.path.join(data, "positions.json")) as fh:
                positions = json.load(fh)
            held = sum(len(positions["books"][name]["open"]) for name in "FRS")
            self.assertGreater(held, 0, "entry scan did not open a position to pull")
            self.assertEqual([row for row in self.notes if row[0] == "entry"], [])

            self.phase = "pull"
            # 50 SOL is under the 40% drop from the 100 SOL entry reserve (floor 60).
            closed = scan_at(T0 + 1800, MINT, 50.0, False, "fill")
            with open(os.path.join(data, "trades.csv"), newline="") as fh:
                sells = [
                    row for row in csv.DictReader(fh)
                    if row["action"] == "sell" and row["mint"] == MINT
                ]
            self.assertGreaterEqual(len(sells), 1)
            self.assertTrue(all(row["why"] == "liquidity_pulled" for row in sells))
            self.assertEqual(closed["rugger_status"]["entries"], 0)
            self.assertEqual(closed["rugger_status"]["edges"], 0)
            self.assertEqual(closed["rugger_status"]["store"], "hit")
            self.assertEqual([row for row in self.notes if row[0] == "pull"], [])

            # Private cache: the scan rewrites an empty document. That is not an entry.
            kind, doc = RB._cache_parts(cache)
            self.assertEqual(kind, "hit")
            self.assertEqual(doc.get("entries"), {})
            self.assertEqual(doc.get("edges"), [])
            with open(os.path.join(cache, RB.PRIVATE_NAME)) as fh:
                cache_blob = fh.read()
            self.assertNotIn(CREATOR, cache_blob)
            self.assertNotIn(MINT, cache_blob)
            self.assertNotIn("lp_pulled", cache_blob)
            self.assertNotIn("rugcheck_rugged", cache_blob)

            # In memory after the pull scan: reloading the saved book is still empty,
            # and judge does not know this deployer.
            book = RB.open_book(cache, data, desk="solana")
            self.assertEqual(book.doc["entries"], {})
            self.assertEqual(book.judge(CREATOR, OTHER, T0 + 1800), [])

            # Public desk files. No blacklist document and no would-veto row.
            self.assertFalse(os.path.exists(os.path.join(data, RB.PRIVATE_NAME)))
            self.assertFalse(os.path.exists(os.path.join(data, run.WOULD_NAME)))
            public_parts = []
            for dirpath, _dirs, names in os.walk(data):
                for name in names:
                    with open(os.path.join(dirpath, name)) as fh:
                        public_parts.append(fh.read())
            public = "\n".join(public_parts)
            self.assertNotIn(CREATOR, public)
            self.assertNotIn("lp_pulled", public)
            self.assertNotIn("known_rugger", public)

            # The private-repo sync is a later workflow step. scan does not call it.
            # With nothing stored, that step would push an empty list.
            self.assertEqual(self.syncs, [])

            # Control, still fill mode: the wired writer stores RugCheck rugged.
            # The pull mint stays off that entry. A future lp_pulled tag would be
            # log-only; it would not change skips, fills, exits, or books.
            self.phase = "control"
            scan_at(T0 + 3600, OTHER, 100.0, True, "fill")
            kind, doc = RB._cache_parts(cache)
            self.assertEqual(kind, "hit")
            self.assertEqual(len(doc.get("entries") or {}), 1)
            entry = doc["entries"][CREATOR]
            self.assertEqual(entry["chain"], "solana")
            self.assertEqual(list(entry["evidence"]), ["rugcheck_rugged"])
            self.assertEqual(entry["evidence"]["rugcheck_rugged"], [OTHER])
            self.assertNotIn(MINT, entry["evidence"]["rugcheck_rugged"])
            self.assertIn(("control", "solana", "rugcheck_rugged", OTHER), self.notes)
            self.assertNotIn("lp_pulled", json.dumps(entry["evidence"]))
            reloaded = RB.open_book(cache, data, desk="solana")
            verdict = reloaded.judge(CREATOR, MINT, T0 + 3600)
            self.assertEqual(verdict[0]["code"], "known_rugger")
            self.assertEqual(verdict[0]["tags"], ["rugcheck_rugged"])
            # Same-token rugged stores the entry and does not emit a would-veto row.
            # A later lead does. The row is the RugCheck tag, and the pull mint is absent.
            self.assertFalse(os.path.exists(os.path.join(data, run.WOULD_NAME)))
            self.phase = "later"
            scan_at(T0 + 5400, LATER, 100.0, False, "scan")
            with open(os.path.join(data, run.WOULD_NAME)) as fh:
                would = json.load(fh)
            self.assertNotIn(CREATOR, json.dumps(would))
            self.assertEqual(would["rows"][0]["mint"], LATER)
            self.assertEqual(would["rows"][0]["evidence_tags"], ["rugcheck_rugged"])
            self.assertTrue(all(row["mint"] != MINT for row in would["rows"]))
            self.assertNotIn("lp_pulled", json.dumps(would))
            self.assertEqual(self.syncs, [])
            self.assertEqual(self.unexpected, [])


if __name__ == "__main__":
    unittest.main()
