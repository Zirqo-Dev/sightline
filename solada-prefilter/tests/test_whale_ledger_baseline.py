"""Baseline tip/balance reads: one retry, and the other provider still runs when a breaker or the
shared research deadline has closed the first. Offline mocks only (no network, no secrets)."""
import contextlib, io, json, os, sys, tempfile, time, unittest, urllib.error, urllib.parse, urllib.request
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import flags as F  # noqa: E402
import valuation as V  # noqa: E402
import whale_ledger as WL  # noqa: E402
import prefilter as P  # noqa: E402
from test_whale_ledger import STAKE, UNITS, addr, full  # noqa: E402
from test_whale_ledger_fallback import KEY, bf_shape, koios_shape  # noqa: E402

TOK = UNITS["ASCEND"]
TIP = full("7777")
LOV, QTY, HEIGHT = 50_000_000, 100, 99
CFG = {"whale": {"stake": STAKE, "stake_prefix": STAKE[6:14]}, "whale_ledger": {}}


def tip_tx():
    """The tip tx is already inside the balance snapshot. Applying it again would double the position."""
    friend = addr("friend")
    return {"hash": TIP, "inputs": [{"ref": full("abcd") + "#0", "addr": friend, "stake": F.addr_info(friend)[0],
                                     "lovelace": LOV + 200_000, "assets": {TOK: QTY}}],
            "outputs": [{"ref": TIP + "#0", "addr": addr("whale"), "stake": STAKE, "lovelace": LOV, "assets": {TOK: QTY}}]}


def resp(payload):
    class R:
        status = 200

        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return R()


def bf_payload(url):
    if "/transactions" in url and "order=desc" in url:
        return [{"tx_hash": TIP, "block_height": HEIGHT, "block_time": 1793040000}]
    if "/transactions" in url:
        return [{"tx_hash": TIP, "block_height": HEIGHT, "block_time": 1793040000}]
    if "/addresses/assets" in url:
        return [{"unit": TOK, "quantity": str(QTY)}]
    if "/txs/" in url:
        return bf_shape(tip_tx())
    if "/accounts/" in url:
        return {"controlled_amount": str(LOV), "withdrawable_amount": "0"}
    raise AssertionError("unexpected blockfrost path")


def http():
    h = P.Http({"koios": 0, "blockfrost": 0})
    h.sleep = lambda _s: None
    return h


def run(h, d, bf_key):
    return WL.run(h, CFG, d, False, 1793050000, V.lp_policies(), bf_key=bf_key, cache_dir=os.path.join(d, "c"),
                  owner_env="")


def load(path):
    with open(path) as f:
        return json.load(f)


class BaselineFallback(unittest.TestCase):
    def test_tripped_koios_and_past_deadline_still_falls_back(self):
        """The failure mode of runs 40424298746 and 40436557846: both providers skipped, baseline never retried."""
        hosts = []

        def urlopen(req, timeout=30):
            hosts.append(urllib.parse.urlparse(req.full_url).netloc)
            if urllib.parse.urlparse(req.full_url).netloc != "cardano-mainnet.blockfrost.io":
                raise AssertionError("fallback called the closed provider")
            if req.get_header("Project_id") != KEY:
                raise AssertionError("blockfrost key was not sent as a header")
            return resp(bf_payload(req.full_url))

        h = http()
        h.deadline = time.time() - 1
        h.last_fail["koios"] = {"status": 429, "why": "http 429"}
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", urlopen), tempfile.TemporaryDirectory() as d, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            h.trip("koios", "429 (public tier blocks the IP ~60 s)")
            summary = run(h, d, KEY)
            doc = load(os.path.join(d, "whale_ledger.json"))
            state = load(os.path.join(d, "c", "whale-ledger-state.json"))
        self.assertNotIn("error", summary)
        self.assertEqual(summary["basis"], "blockfrost")
        self.assertGreater(summary["blockfrost_fallbacks"], 0)
        self.assertEqual(doc["positions"], {"ASCEND": QTY})
        self.assertEqual(doc["ada_balance"], LOV / 1e6)
        self.assertEqual(state["positions_raw"][TOK], QTY)
        self.assertIn(TIP, state["seen"])  # recorded so the next run does not apply it; not added on top
        self.assertFalse(h.calls.get("blockfrost_skipped"))
        self.assertEqual(set(hosts), {"cardano-mainnet.blockfrost.io"})
        blob = out.getvalue() + json.dumps(doc) + json.dumps(summary)
        self.assertNotIn(STAKE, blob)
        self.assertNotIn(KEY, blob)
        self.assertNotIn(addr("whale"), blob)
        F.assert_public(doc)
        F.assert_public(state)

    def test_both_providers_name_the_failed_read(self):
        def urlopen(req, timeout=30):
            raise urllib.error.HTTPError(req.full_url, 429, "busy", {"Retry-After": "0"}, None)

        h = http()
        h.deadline = time.time() + 60
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep"), \
                tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            err = run(h, d, KEY)
            wrote = os.path.exists(os.path.join(d, "whale_ledger.json"))
            wrote_state = os.path.exists(os.path.join(d, "c", "whale-ledger-state.json"))
        self.assertEqual(err, {"error": "baseline failed", "failed_read": "tip", "provider": "blockfrost", "status": 429})
        self.assertIsInstance(err["status"], int)
        self.assertFalse(wrote or wrote_state)  # a failed pass must not leave balances for the retry to add again
        blob = out.getvalue() + json.dumps(err)
        self.assertNotIn(STAKE, blob)
        self.assertNotIn(KEY, blob)

    def test_koios_429_with_no_key_names_koios(self):
        calls = {"n": 0}

        def urlopen(req, timeout=30):
            calls["n"] += 1
            raise urllib.error.HTTPError("https://api.koios.rest/api/v1/tip", 429, "busy", {}, None)

        h = http()
        h.deadline = time.time() + 60
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep"), tempfile.TemporaryDirectory() as d, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            err = run(h, d, "")
        self.assertNotIn(STAKE, out.getvalue())
        self.assertEqual(err, {"error": "baseline failed", "failed_read": "tip", "provider": "koios", "status": 429})
        self.assertEqual(calls["n"], 1)  # the retry hits the tripped breaker; it does not hammer Koios

    def test_retry_does_not_double_positions(self):
        desc, info = {"n": 0}, {"n": 0}

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            if "order=block_height.desc" in url:
                desc["n"] += 1
                if desc["n"] == 2:  # confirm-tip fails once; the baseline retries the whole snapshot
                    self_.last_fail[src] = {"status": 503, "why": "http 503"}
                    return None
                return [{"tx_hash": TIP, "block_height": HEIGHT, "block_time": 1793040000}]
            if url.endswith("/account_info"):
                info["n"] += 1
                return [{"total_balance": str(LOV), "rewards_available": "0"}]
            if "/account_assets" in url:
                return [{"policy_id": TOK[:56], "asset_name": TOK[56:], "quantity": str(QTY)}]
            if "/account_txs" in url:
                return [{"tx_hash": TIP, "block_height": HEIGHT, "block_time": 1793040000}]
            if url.endswith("/tx_info"):
                return [koios_shape(tip_tx())]
            if url.endswith("/utxo_info"):
                return []
            return None

        with mock.patch.object(P.Http, "req", req), tempfile.TemporaryDirectory() as d:
            h = http()
            summary = run(h, d, "")
            doc = load(os.path.join(d, "whale_ledger.json"))
            state = load(os.path.join(d, "c", "whale-ledger-state.json"))
            self.assertNotIn("error", summary)
            self.assertEqual(desc["n"], 4)  # tip, failed confirm, retry tip, retry confirm
            self.assertEqual(info["n"], 2)  # balance read was retried, not reused by adding it twice
            self.assertEqual(state["positions_raw"], {TOK: QTY})
            self.assertEqual(state["lovelace"], LOV)
            self.assertEqual(doc["positions"], {"ASCEND": QTY})
            self.assertIn(TIP, state["seen"])
            h2 = http()
            run(h2, d, "")  # next run sees the tip tx again; it is already in the baseline
            doc2 = load(os.path.join(d, "whale_ledger.json"))
            state2 = load(os.path.join(d, "c", "whale-ledger-state.json"))
        self.assertEqual(doc2["positions"], {"ASCEND": QTY})
        self.assertEqual(state2["positions_raw"], {TOK: QTY})
        self.assertEqual(state2["lovelace"], LOV)
        self.assertEqual(doc2["rows"], [])


if __name__ == "__main__":
    unittest.main()
