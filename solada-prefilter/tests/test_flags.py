"""Offline tests for flag-only signals. Run: python3 -m unittest discover -s solada-prefilter/tests
No network, no secrets; also runs prefilter.py --dry-run against a stubbed Http to prove flags never trigger the webhook."""
import hashlib, json, os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import flags as F  # noqa: E402

UNIT = "ab" * 28 + "4341424c"


def stake_with_prefix(prefix, seed):
    """Fake stake keyhash whose bech32 starts with stake1<prefix> (brute force, tiny prefixes)."""
    for i in range(2_000_000):
        kh = hashlib.sha256(f"{seed}{i}".encode()).digest()[:28]
        if F.b32_encode("stake", b"\xe1" + kh).startswith("stake1" + prefix):
            return kh
    raise RuntimeError(prefix)


def addr(label, n=0):
    if label.startswith("SNIPER_"):
        sk = stake_with_prefix(label.split("_", 1)[1][:4], label)  # 4 chars keeps brute force fast
    else:
        sk = hashlib.sha256(label.encode()).digest()[:28]
    pay = hashlib.sha256(f"{label}/{n}".encode()).digest()[:28]
    return F.b32_encode("addr", b"\x01" + pay + sk)


POOL = F.b32_encode("addr", b"\x71" + b"\x11" * 28)  # script payment = pool


def build(fx):
    txs, ut = [], {}
    for t in fx["txs"]:
        a = addr(t["buyer"])
        outs = [(a, 2_000_000, {UNIT: t["qty"]}), (POOL, t["ada"] * 1_000_000, {})]
        if t.get("extra_addrs"):
            outs.append((addr(t["buyer"], 1), 1_000_000, {}))
        ut[t["tx"]] = {"inputs": [(a, (t["ada"] + 3) * 1_000_000, {}), (POOL, 0, {UNIT: t["qty"]})], "outputs": outs}
        txs.append((t["tx"], t["height"], t["time"]))
    return txs, ut


class FlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = json.load(open(os.path.join(os.path.dirname(HERE), "config.json")))
        cls.fc = cls.cfg["flags"]
        cls.fx = json.load(open(os.path.join(HERE, "fixtures", "launch_cabal.json")))
        cls.txs, cls.ut = build(cls.fx)
        # tags are 4-char-matched in fixtures; watch on those short prefixes
        cls.watch = {"u9w6": "first_block_sniper", "uytg": "first_block_sniper", "uynn": "pool_preseeder"}

    def test_bech32_roundtrip_and_stake(self):
        a = addr("W1")
        s, script = F.addr_info(a)
        self.assertTrue(s.startswith("stake1u") and not script)
        self.assertEqual(F.addr_info(POOL), (None, True))

    def test_launch_flags(self):
        b = F.buyers_from_txs(UNIT, self.txs, self.ut)
        self.assertEqual(len(b), 6)
        fh = {F.addr_info(addr(k))[0]: v for k, v in self.fx["first_heights"].items()}
        out = F.analyze_launch("CABAL", UNIT, b, self.watch, self.fc, self.fx["live_at_s"], fh)
        kinds = {f["kind"] for f in out}
        for k in ("watch_tags_first_block", "identical_size_buys", "coordinated_buys", "stake_key_multi_address", "fresh_wallet_swarm"):
            self.assertIn(k, kinds)
        F.assert_public(out)  # only w- tags and short prefixes

    def test_metric_flags(self):
        prev = {"ada_side": 10000, "holders": 100, "mcap_ada": 50000}
        kinds = {f["kind"] for f in F.metric_flags("X", {"ada_side": 7000, "holders": 90, "mcap_ada": 60000, "vol_h24_ada": 30000}, prev, self.fc)}
        self.assertEqual(kinds, {"ada_side_drop", "holders_down_mcap_up", "volume_without_holders"})
        kinds = {f["kind"] for f in F.metric_flags("X", {"ada_side": 12000, "holders": 210, "mcap_ada": 60000}, prev, self.fc)}
        self.assertEqual(kinds, {"holders_doubled_ada_up"})

    def test_whale_noise_and_privacy_guard(self):
        out = F.whale_flags({"tag": "w-f3186187", "moves": [{"asset": "COCK", "delta": -5}, {"asset": "ASCEND", "delta": 9}]}, self.fc)
        self.assertEqual([f["ticker"] for f in out], ["ASCEND"])
        with self.assertRaises(SystemExit):
            F.assert_public({"x": addr("W1")})

    def test_dry_run_offline_flags_never_fire_webhook(self):
        """prefilter.py --dry-run with stubbed HTTP: flags are produced, triggers unaffected, no webhook POST."""
        import prefilter as P
        posted = []
        lt = F.b32_encode("addr", b"\x01" + b"\x22" * 28 + stake_with_prefix("ux73", "L"))
        launch = {"id": "L1", "ticker": "CABAL", "policyId": UNIT[:56], "assetName": UNIT[56:], "liveAtMs": 10**13,
                  "launcherAddress": lt, "pool": None, "curve": None}
        cabal_txs = [{"tx_hash": t, "block_height": h, "block_time": tm} for t, h, tm in self.txs]

        def fake_req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            if src == "webhook":
                posted.append(body)
            if url.endswith("/launches"):
                return [launch] if fake_req.run > 0 else []
            if url.endswith("/market"):
                return {"quotes": {}}
            if "/asset_txs" in url:
                return cabal_txs
            if url.endswith("/tx_info"):
                return [{"tx_hash": h, **{s: [{"tx_hash": h, "tx_index": i, "payment_addr": {"bech32": a}, "value": str(l),
                         "asset_list": [{"policy_id": u[:56], "asset_name": u[56:], "quantity": str(q)} for u, q in m.items()]}
                         for i, (a, l, m) in enumerate(self.ut[h][s])] for s in ("inputs", "outputs")}} for h in body["_tx_hashes"]]
            return None
        fake_req.run = 0
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P.Http, "req", fake_req), \
                mock.patch.dict(os.environ, {"BLOCKFROST_PROJECT_ID": "", "SOLADA_WEBHOOK_URL": "https://example.invalid/hook", "SOLANA_RPC_URL": "", "HELIUS_API_KEY": ""}):
            repo = os.path.join(HERE, "..", "..")
            argv = ["prefilter.py", "--repo", repo, "--data-dir", d, "--cache-dir", os.path.join(d, "c")]
            json.dump({"seen_launches": ["old"], "fired": {}, "kill_status": {}}, open(os.path.join(d, "state.json"), "w"))
            fake_req.run = 1
            with mock.patch.object(sys, "argv", argv + ["--dry-run"]):
                P.main()
            doc = json.load(open(os.path.join(d, "flags-dry-run.json")))
            kinds = {f["kind"] for f in doc["new"]}
            self.assertIn("identical_size_buys", kinds)
            run = json.load(open(os.path.join(d, "dry-run.json")))
            self.assertFalse(any(t["kind"].startswith("flag") for t in run["triggers"]))
            self.assertEqual(posted, [])
            F.assert_public(doc)


if __name__ == "__main__":
    unittest.main()
