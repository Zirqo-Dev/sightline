"""Claude re-review of #29: should-fixes, partials, nice-to-haves, and the 5 reverts the #29 tests missed.
Offline, no secrets, no network. Fake ids from labels."""
import json, os, re, shutil, subprocess, sys, tempfile, time, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)
import flags as F  # noqa: E402
import valuation as V  # noqa: E402
import big_moves as BM  # noqa: E402
import whale_ledger as WL  # noqa: E402
import koios_tx as KT  # noqa: E402
import prefilter as P  # noqa: E402
from test_regressions import run_main, quiet_req, fresh_state  # noqa: E402
from test_kill_status import tracked_with_units, patches, kills  # noqa: E402
from test_whale_ledger import STAKE, UNITS, addr, full, fresh, kh  # noqa: E402
from test_review_fixes import u, own_script_addr, ser_run, FakeKL, TIP_TX, NEW_TX  # noqa: E402

LP = V.lp_policies()
WC = dict(WL.wl_cfg(), _owner_pats=[re.compile("^zzOWNERzz$")])
TOK = "bb" * 28 + b"TOK".hex()
NOW = "2026-10-26T20:07:00Z"
HOOK = {"SOLADA_WEBHOOK_URL": "https://example.invalid/h"}
COCK = UNITS["COCK"]


def ok_hook(log):
    return mock.patch.object(P.Http, "post_webhook", lambda self, url, body, h=None: log.append("webhook") or True)


# ======================================================== the 5 reverts #29's tests did not catch
class Revert1ResearchAfterWebhook(unittest.TestCase):
    def test_research_starts_only_after_webhook_and_state(self):
        log = []

        def bm(*a, **k):
            log.append("research")
            log.append(("state_written", os.path.exists(os.path.join(a[4], "state.json")),
                        os.path.exists(os.path.join(a[4], "kill_status.json"))))
            return {}, None
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            os.remove(os.path.join(d, "state.json")) if os.path.exists(os.path.join(d, "state.json")) else None
            fresh_state(d)
            st0 = os.path.getmtime(os.path.join(d, "state.json"))
            os.utime(os.path.join(d, "state.json"), (st0 - 100, st0 - 100))
            run_main(d, quiet_req, ser_run(4000) + [ok_hook(log), mock.patch.object(BM, "run", bm)], dry=False, env=HOOK)
        self.assertEqual(log[:2], ["webhook", "research"])
        self.assertEqual(log[2], ("state_written", True, True))


class Revert2WholeTokenMask(unittest.TestCase):
    def test_key_inside_a_longer_token_in_config_is_not_masked(self):
        cfg = json.load(open(os.path.join(SRC, "config.json")))
        key = cfg["whale"]["stake"]
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(d, "scripts"))
            os.makedirs(os.path.join(d, "solada-prefilter"))
            for f in os.listdir(SRC):
                if f.endswith(".py"):
                    shutil.copy(os.path.join(SRC, f), os.path.join(d, "solada-prefilter"))
            cfg["_probe"] = "zz" + key  # a different (longer) id that merely contains the public key
            json.dump(cfg, open(os.path.join(d, "solada-prefilter", "config.json"), "w"))
            env = dict(os.environ, PRIVACY_GUARD_PATTERNS="^zzOWNERzz$")
            r = subprocess.run(["bash", "scripts/privacy-guard.sh", "solada-prefilter/config.json"], cwd=d, env=env, capture_output=True)
            self.assertEqual(r.returncode, 1, r.stderr)
            del cfg["_probe"]
            json.dump(cfg, open(os.path.join(d, "solada-prefilter", "config.json"), "w"))
            r = subprocess.run(["bash", "scripts/privacy-guard.sh", "solada-prefilter/config.json"], cwd=d, env=env, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)


def bm_fake(calls, txs, mover="mover"):
    def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
        self_.calls[src] = self_.calls.get(src, 0) + 1
        calls.append((url, body, quiet404))
        if url.endswith("/tip"):
            return [{"block_height": 10_000, "block_time": 100_000}]
        if "/asset_txs" in url:
            off = int(url.split("offset=")[1].split("&")[0]) if "offset=" in url else 0
            lim = int(url.split("limit=")[1].split("&")[0])
            rows = [{"tx_hash": h, "block_height": hg, "block_time": 100_000 - (10_000 - hg)} for h, hg in txs]
            rows = rows[::-1] if "desc" in url else rows
            return rows[off:off + lim]
        if url.endswith("/tx_info"):
            w = addr(mover)
            return [{"tx_hash": h, "inputs": [{"tx_hash": "e" * 64, "tx_index": 0, "payment_addr": {"bech32": w}, "stake_addr": None,
                                                "value": "9000000000", "asset_list": []}],
                     "outputs": [{"tx_hash": h, "tx_index": 0, "payment_addr": {"bech32": w}, "stake_addr": None, "value": "1000000",
                                  "asset_list": [{"policy_id": TOK[:56], "asset_name": TOK[56:], "quantity": "5"}]}]} for h in body["_tx_hashes"]]
        return None
    return req


META = {TOK: {"ticker": "T", "ada_side": 10, "decimals": 0}}


class Revert3BigMovesExcludeTags(unittest.TestCase):
    def test_excluded_tag_never_becomes_a_move_row(self):
        txs = [(f"{i:x}" * 64, 9_990 + i)[:2] for i in range(1, 3)]
        txs = [(h[:64], hg) for h, hg in txs]
        mover_tag = F.tag(F.addr_info(addr("mover"))[0])
        with mock.patch.object(P.Http, "req", bm_fake([], txs)), tempfile.TemporaryDirectory() as d:
            _, doc = BM.run(P.Http({}), {"big_moves": {"min_ada": 1}}, META, d, d, False, 100_000, LP, [])
            self.assertEqual(len(doc["moves"]), 2)
        with mock.patch.object(P.Http, "req", bm_fake([], txs)), tempfile.TemporaryDirectory() as d:
            _, doc = BM.run(P.Http({}), {"big_moves": {"min_ada": 1}, "flags": {"exclude_tags": {mover_tag: "reserve"}}},
                            META, d, d, False, 100_000, LP, [])
            self.assertEqual(doc["moves"], [])


class Revert4BigMovesDeadline(unittest.TestCase):
    def test_past_deadline_reads_no_token(self):
        calls = []
        with mock.patch.object(P.Http, "req", bm_fake(calls, [("a" * 64, 9_999)])), tempfile.TemporaryDirectory() as d:
            s, doc = BM.run(P.Http({}), {}, META, d, d, False, 100_000, LP, [], deadline=time.time() - 1)
        self.assertFalse([c for c in calls if "/asset_txs" in c[0] or "/tx_info" in c[0]])
        self.assertEqual(s["capped"], ["T"])
        self.assertEqual(doc["coverage"]["T"]["skipped"], "budget/deadline")


class Revert5Quiet404(unittest.TestCase):
    def test_every_research_koios_call_logs_404s(self):
        calls = []
        with mock.patch.object(P.Http, "req", bm_fake(calls, [("a" * 64, 9_999)])), tempfile.TemporaryDirectory() as d:
            BM.run(P.Http({}), {}, META, d, d, False, 100_000, LP, [])
            kl = WL.KoiosLedger(P.Http({}), WC)
            kl.txs_after(STAKE, 1)
            kl.utxos(["b" * 64])
            kl.spent(["b" * 64 + "#0"])
            F.FlagChain(P.Http({}), "").tx_utxos(["c" * 64])
        self.assertTrue(calls)
        self.assertEqual({q for _, _, q in calls}, {False})


# ======================================================== ranked should-fixes
class SF1BreakerScope(unittest.TestCase):
    def test_429_before_research_does_not_trip(self):
        import urllib.error
        http = P.Http({"koios": 0}, backoff={"koios": [0]})
        http.sleep = lambda s: None
        err = urllib.error.HTTPError("u", 429, "busy", {}, None)
        with mock.patch("time.sleep"), mock.patch("urllib.request.urlopen", side_effect=err):
            http.req("koios", "https://api.koios.rest/api/v1/tip")
            for _ in range(5):
                http._fail("koios", "x")
        self.assertEqual(http.tripped, {})
        self.assertFalse(http.blocked("koios"))
        http.deadline = time.time() + 60
        for _ in range(3):
            http._fail("koios", "x")
        self.assertTrue(http.blocked("koios"))


class SF2KillReFire(unittest.TestCase):
    L = {"ada_side": 5000.0, "holders": 200.0}

    def e(self, prev, vals):
        cm = {k: (v, self.L[k]) for k, v in vals.items() if v is not None and v < self.L[k]}
        return V.kill_status_entry(prev, "", vals, self.L, cm, all(v is not None for v in vals.values()), NOW)

    def test_unread_metric_stays_crossed_so_nothing_refires(self):
        r1 = self.e(None, {"ada_side": 4000, "holders": 150})
        r2 = self.e(r1, {"ada_side": 4000, "holders": None})  # holders unread this run
        r3 = self.e(r2, {"ada_side": 4000, "holders": 150})
        self.assertEqual(r2["crossed"], ["ada_side", "holders"])
        self.assertFalse(r2["changed_since_last"])
        self.assertFalse(r3["changed_since_last"])


def guard_tree(d, allow_lines, patterns):
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(d, "scripts"))
    shutil.copytree(SRC, os.path.join(d, "solada-prefilter"), ignore=shutil.ignore_patterns("tests", "__pycache__"))
    open(os.path.join(d, "scripts", "privacy-public-keys.txt"), "w").write("\n".join(allow_lines) + "\n")
    env = dict(os.environ, PRIVACY_GUARD_PATTERNS=patterns)
    return subprocess.run(["bash", "scripts/privacy-guard.sh", "solada-prefilter/config.json"], cwd=d, env=env, capture_output=True)


class SF3OwnerKeyDerivation(unittest.TestCase):
    def test_owner_payment_address_pattern_protects_its_stake_key(self):
        key = json.load(open(os.path.join(SRC, "config.json")))["whale"]["stake"]
        owner_addr = F.b32_encode("addr", b"\x01" + b"\x11" * 28 + F.b32_decode(key)[1][1:])  # same stake part
        with tempfile.TemporaryDirectory() as d:
            r = guard_tree(d, [key], owner_addr)
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"stake key of an owner address pattern", r.stderr)
        self.assertNotIn(key.encode(), r.stderr + r.stdout)

    def test_entries_are_pinned_to_whale_stake(self):
        other = F.b32_encode("stake", b"\xe1" + kh("someone-else"))
        key = json.load(open(os.path.join(SRC, "config.json")))["whale"]["stake"]
        with tempfile.TemporaryDirectory() as d:
            r = guard_tree(d, [key, other], "^zzOWNERzz$")
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"not config.json whale.stake", r.stderr)


class RootModePublicKeys(unittest.TestCase):
    def test_root_mode_loads_public_keys_after_cd(self):
        # CI runs `bash main/scripts/privacy-guard.sh --root desk-data` from the parent
        # of both checkouts. The script directory has to be resolved before that cd,
        # or privacy-public-keys.txt is missed and the public whale key is not masked.
        key = F.b32_encode("stake", b"\xe1" + kh("public-whale"))
        with tempfile.TemporaryDirectory() as d:
            main = os.path.join(d, "main")
            os.makedirs(os.path.join(main, "scripts"))
            shutil.copy(os.path.join(ROOT, "scripts", "privacy-guard.sh"), os.path.join(main, "scripts"))
            with open(os.path.join(main, "scripts", "privacy-public-keys.txt"), "w") as f:
                f.write(key + "\n")
            shutil.copytree(SRC, os.path.join(main, "solada-prefilter"), ignore=shutil.ignore_patterns("tests", "__pycache__"))
            cfg_path = os.path.join(main, "solada-prefilter", "config.json")
            with open(cfg_path) as f:
                cfg = json.load(f)
            cfg["whale"]["stake"] = key
            with open(cfg_path, "w") as f:
                json.dump(cfg, f)
            desk = os.path.join(d, "desk-data")
            os.makedirs(os.path.join(desk, "solada-prefilter"))
            with open(os.path.join(desk, "solada-prefilter", "config.json"), "w") as f:
                json.dump({"whale": {"stake": key}}, f)
            subprocess.run(["git", "init", "-q"], cwd=desk, check=True)
            subprocess.run(["git", "add", "solada-prefilter/config.json"], cwd=desk, check=True)
            env = dict(os.environ, PRIVACY_GUARD_PATTERNS="^zzOWNERzz$")
            r = subprocess.run(
                ["bash", "main/scripts/privacy-guard.sh", "--root", "desk-data"],
                cwd=d, env=env, capture_output=True)
        self.assertNotIn(b"No such file", r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(key.encode(), r.stderr + r.stdout)


class SF4ReplaceRules(unittest.TestCase):
    def setUp(self):
        self.led = fresh()
        WL.apply(self.led, {"hash": full("d001"), "time": 1, "inputs": [u("i#0", "whale", 5_000_000, {COCK: 1000})],
                            "outputs": [u(f"{full('d001')}#0", "order", 4_000_000, {COCK: 1000})]}, STAKE, LP, WC)
        self.cred = F.b32_decode(STAKE)[1][1:].hex()

    def spend(self, h, outs, signed):
        ins = [u(f"{full('d001')}#0", "order", 4_000_000, {COCK: 1000}), u("b#0", "batcher", 9_000_000)]
        if signed:
            ins.append(u("w#0", "whale", 2_000_000))
        return WL.apply(self.led, {"hash": full(h), "time": 2, "inputs": ins, "outputs": outs}, STAKE, LP, WC)

    def test_small_partial_fill_is_a_partial_fill_not_a_replace(self):
        rows = self.spend("d002", [u(f"{full('d002')}#0", "order", 4_000_000, {COCK: 995}, datum=self.cred),
                                   u(f"{full('d002')}#1", "whale", 7_000_000)], signed=False)
        self.assertEqual([(r["kind"], r["order_status"], r["qty"]) for r in rows], [("order_fill", "partial", 5)])
        self.assertEqual(sum(1 for o in self.led["orders"].values() if o["status"] == "open"), 1)

    def test_batcher_move_is_order_moved_with_paid_ada(self):
        rows = self.spend("d003", [u(f"{full('d003')}#0", "vault", 2_000_000, {COCK: 1000}, datum=self.cred),
                                   u(f"{full('d003')}#1", "whale", 930_300_000)], signed=False)
        self.assertEqual([(r["kind"], r["paid_ada"]) for r in rows], [("order_moved", 930.3)])

    def test_owner_signed_qty_change_is_a_replace(self):
        rows = self.spend("d004", [u(f"{full('d004')}#0", "order", 4_000_000, {COCK: 800}, datum=self.cred),
                                   u(f"{full('d004')}#1", "whale", 1_800_000, {COCK: 200})], signed=True)
        self.assertEqual([(r["kind"], r.get("order_status"), r["qty"]) for r in rows],
                         [("order_cancel", "replaced", 1000), ("order_open", "open", 800)])

    def test_datum_hash_order_edit_is_a_replace(self):
        rows = self.spend("d005", [u(f"{full('d005')}#0", "order", 4_000_000, {COCK: 1000}),
                                   u(f"{full('d005')}#1", "whale", 1_800_000)], signed=True)
        self.assertEqual([r["kind"] for r in rows], ["order_cancel", "order_open"])

    def test_third_party_send_to_whale_stake_script_is_not_an_order(self):
        own = own_script_addr()
        rows = WL.apply(fresh(), {"hash": full("d006"), "time": 1, "inputs": [u("x#0", "friend", 60_000_000)],
                                  "outputs": [u(f"{full('d006')}#0", own, 50_000_000, stake=STAKE)]}, STAKE, LP, WC)
        self.assertEqual([(r["kind"], r["token"]) for r in rows], [("transfer_in", "ADA")])


class SF5TrackedPrice(unittest.TestCase):
    def test_tracked_rows_carry_price_and_decimals(self):
        seen = {}

        def bm(http, cfg, meta, *a, **k):
            seen.update(meta)
            return {}, None
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run_main(d, quiet_req, ser_run(4000) + [mock.patch.object(P.Chain, "decimals", lambda self, u: 6),
                                                     mock.patch.object(BM, "run", bm)], dry=False)
        (m,) = seen.values()
        self.assertEqual((m["price_ada"], m["decimals"]), (1.0, 6))


class SF6Coverage(unittest.TestCase):
    def test_capped_unit_pages_back_and_reports_the_gap(self):
        txs = [((f"{i:02x}" * 32), 9_000 + i * 10) for i in range(30)]
        calls = []
        cfg = {"big_moves": {"max_txs_per_unit": 5, "max_pages_per_unit": 3, "max_calls": 200}}
        with mock.patch.object(P.Http, "req", bm_fake(calls, txs)), tempfile.TemporaryDirectory() as d:
            BM.run(P.Http({}), cfg, META, d, d, False, 100_000, LP, [])
            _, doc = BM.run(P.Http({}), cfg, META, d, d, False, 100_000, LP, [])
        cov = doc["coverage"]["T"]
        self.assertEqual(cov["pages"], 3)
        self.assertFalse(cov["complete"])
        self.assertGreater(cov["gap_blocks"], 0)
        self.assertTrue(any("offset=5" in c[0] and "desc" in c[0] for c in calls))


class SF7FlagsAssert(unittest.TestCase):
    def test_flags_privacy_assert_cannot_stop_the_webhook(self):
        log = []
        real = F.assert_public

        def strict(obj):
            if isinstance(obj, dict) and obj.get("flag_only"):
                raise SystemExit("private value in flags")
            return real(obj)
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run = run_main(d, quiet_req, ser_run(4000) + [ok_hook(log), mock.patch.object(F, "assert_public", strict)],
                           dry=False, env=HOOK)
            self.assertFalse(os.path.exists(os.path.join(d, "flags.json")))
            self.assertTrue(os.path.exists(os.path.join(d, "state.json")))
        self.assertEqual(kills(run), ["SER"])
        self.assertEqual(log, ["webhook"])


class SF8FlagsTxInfo(unittest.TestCase):
    def test_flagchain_uses_tx_info_and_bf_skips_collateral(self):
        urls = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            urls.append((src, url, quiet404))
            if src == "koios":
                return None
            return {"inputs": [{"address": "a1", "amount": [{"unit": "lovelace", "quantity": "5"}]},
                               {"address": "a2", "amount": [{"unit": "lovelace", "quantity": "7"}], "collateral": True},
                               {"address": "a3", "amount": [{"unit": "lovelace", "quantity": "9"}], "reference": True}],
                    "outputs": [{"address": "a4", "amount": [{"unit": "lovelace", "quantity": "3"}], "collateral": True}]}
        with mock.patch.object(P.Http, "req", req):
            out = F.FlagChain(P.Http({}), "k").tx_utxos(["d" * 64])
        self.assertTrue(urls[0][1].endswith("/tx_info"))
        self.assertEqual(out["d" * 64], {"inputs": [("a1", 5, {})], "outputs": []})
        self.assertEqual({q for _, _, q in urls}, {False})


# ======================================================== nice-to-haves + state handling
class Nice(unittest.TestCase):
    def test_word_boundary_patterns_fail_closed(self):
        self.assertEqual(BM.owner_patterns(r"\<stake1abc\>")[1], 1)

    def test_leftover_tmp_files_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "state.json.tmp-abc"), "w").write("{")
            open(os.path.join(d, "keep.txt"), "w").write("x")
            KT.write_json(os.path.join(d, "state.json"), {"a": 1})
            self.assertEqual(KT.clean_tmp(d), 1)
            self.assertEqual(sorted(os.listdir(d)), ["keep.txt", "state.json"])

    def test_research_deadline_capped_and_last_run_written_first(self):
        seen = {}

        def bm(http, cfg, meta, cache_dir, data_dir, *a, **k):
            seen["deadline"] = k["deadline"]
            seen["last_run"] = os.path.exists(os.path.join(data_dir, "last-run.json"))
            return {}, None
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run_main(d, quiet_req, ser_run(4000) + [mock.patch.object(BM, "run", bm)], dry=False)
        self.assertTrue(seen["last_run"])
        self.assertEqual(P.research_deadline({"research_budget_s": 100_000}, 1000.0, 1500.0), 1720.0)
        self.assertEqual(P.research_deadline({}, 1000.0, 1100.0), 1340.0)

    def test_concentration_basis_change_is_not_a_jump(self):
        fc = dict(json.load(open(os.path.join(SRC, "config.json")))["flags"])
        prev = {"conc": {"top_cluster_pct": 20.0}}
        cur = {"conc": {"top_cluster_pct": 60.0, "basis": "ex-abc123"}}
        self.assertFalse([f for f in F.metric_flags("X", cur, prev, fc) if f["kind"] == "concentration_jump"])
        prev2 = {"conc": {"top_cluster_pct": 20.0, "basis": "ex-abc123"}}
        self.assertTrue([f for f in F.metric_flags("X", cur, prev2, fc) if f["kind"] == "concentration_jump"])

    def test_counterparty_owner_filter_and_fail_closed(self):
        def tin(pats):
            wc = dict(WL.wl_cfg(), _owner_pats=pats)
            return WL.apply(fresh(), {"hash": full("e101"), "time": 1, "inputs": [u("x#0", "friend", 60_000_000)],
                                      "outputs": [u(f"{full('e101')}#0", "whale", 50_000_000)]}, STAKE, LP, wc)[0]["counterparty_tag"]
        friend_stake = F.addr_info(addr("friend"))[0]
        self.assertTrue(tin([re.compile("^zzOWNERzz$")]))
        self.assertIsNone(tin([re.compile(friend_stake[:20])]))  # the owner wallet is never tagged
        self.assertIsNone(tin(None))  # no usable owner patterns -> no counterparty tags at all


class LedgerState(unittest.TestCase):
    def go(self, d, cache, **kw):
        for k, v in kw.items():
            setattr(FakeKL, k, v)
        with mock.patch.object(WL, "KoiosLedger", FakeKL):
            return WL.run(P.Http({}), {"whale": {"stake": STAKE}, "whale_ledger": {}}, d, False, 1793050000, LP,
                          cache_dir=cache, owner_env="^zzOWNERzz$")

    def tearDown(self):
        FakeKL.txs, FakeKL.tips, FakeKL.bal, FakeKL.spent_map = [], [99], (0, {}), {}

    def test_state_goes_to_the_cache_never_desk_data_and_rows_not_twice(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            self.go(d, c, txs=[TIP_TX], tips=[99], bal=(50_000_000, {}))
            self.go(d, c, txs=[TIP_TX, NEW_TX], tips=[100], bal=(100_000_000, {}))
            self.assertFalse(os.path.exists(os.path.join(d, "whale_ledger_state.json")))
            st = json.load(open(os.path.join(c, "whale-ledger-state.json")))
            doc = json.load(open(os.path.join(d, "whale_ledger.json")))
        self.assertNotIn("rows", st)
        self.assertEqual(len(doc["rows"]), 1)
        self.assertNotIn(STAKE, json.dumps(st))

    def test_migrates_from_the_29_desk_data_state_file_without_deleting_it(self):
        legacy = dict(fresh(), height=99, baseline_height=99, lovelace=50_000_000, seen={TIP_TX[0]: 99})
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            json.dump(legacy, open(os.path.join(d, "whale_ledger_state.json"), "w"))
            r = self.go(d, c, txs=[TIP_TX, NEW_TX], tips=[100], bal=(100_000_000, {}))
            self.assertTrue(os.path.exists(os.path.join(d, "whale_ledger_state.json")))  # left alone (ask first)
            self.assertTrue(os.path.exists(os.path.join(c, "whale-ledger-state.json")))
            doc = json.load(open(os.path.join(d, "whale_ledger.json")))
        self.assertEqual(doc["state_source"], "desk-data-legacy")
        self.assertTrue(r["reconcile_ok"])

    def test_reconcile_skipped_when_tip_moves(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            self.go(d, c, txs=[TIP_TX], tips=[99], bal=(50_000_000, {}))
            self.go(d, c, txs=[TIP_TX, NEW_TX], tips=[100, 101], bal=(999, {}))
            doc = json.load(open(os.path.join(d, "whale_ledger.json")))
        self.assertNotIn("reconcile", doc)
        self.assertIn("reconcile_skipped", doc)


class DevChecksFileGuard(unittest.TestCase):
    """desk-data-guard scans solada-prefilter/dev_checks.json. Patterns and the allowlist stay as they are."""

    def _run(self, doc, args):
        with tempfile.TemporaryDirectory() as d:
            main = os.path.join(d, "main")
            os.makedirs(os.path.join(main, "scripts"))
            shutil.copy(os.path.join(ROOT, "scripts", "privacy-guard.sh"), os.path.join(main, "scripts"))
            shutil.copy(os.path.join(ROOT, "scripts", "privacy-public-keys.txt"), os.path.join(main, "scripts"))
            shutil.copytree(SRC, os.path.join(main, "solada-prefilter"),
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
            desk = os.path.join(d, "desk-data", "solada-prefilter")
            os.makedirs(desk)
            with open(os.path.join(desk, "dev_checks.json"), "w") as f:
                json.dump(doc, f)
            with open(os.path.join(desk, "note.json"), "w") as f:
                json.dump({"ok": True}, f)
            cfg = json.load(open(os.path.join(main, "solada-prefilter", "config.json")))
            with open(os.path.join(desk, "config.json"), "w") as f:
                json.dump({"whale": {"stake": cfg["whale"]["stake"]}}, f)
            data = os.path.join(d, "desk-data")
            subprocess.run(["git", "init", "-q"], cwd=data, check=True)
            subprocess.run(["git", "add", "-A"], cwd=data, check=True)
            env = dict(os.environ, PRIVACY_GUARD_PATTERNS="^zzOWNERzz$")
            r = subprocess.run(["bash", "main/scripts/privacy-guard.sh", "--root", "desk-data", *args],
                               cwd=d, env=env, capture_output=True)
            return r, cfg["whale"]["stake"]

    def test_prefix_fails_even_if_the_caller_omits_the_file(self):
        raw = F.b32_encode("stake", b"\xe1" + kh("dev-tag-guard"))
        prefix = raw[6:16]
        doc = {"entries": [{"ticker": "T", "dev_tag": prefix}, {"ticker": "U", "dev_tag": "w-0123abcd"}]}
        r, _ = self._run(doc, ["solada-prefilter/note.json"])
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"dev_tag is not a role label or w- hash", r.stderr)
        self.assertFalse(prefix.encode() in r.stdout + r.stderr)
        self.assertFalse(raw.encode() in r.stdout + r.stderr)

    def test_role_and_hash_pass_and_the_allowlist_does_not_mask_this_file(self):
        good = {"entries": [{"dev_tag": "w-0123abcd"}, {"dev_tag": "repeat_launcher"}, {"dev_tag": None}]}
        r, key = self._run(good, [])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(key.encode() in r.stdout + r.stderr)
        bad = {"entries": [{"dev_tag": key}]}
        r, key = self._run(bad, [])
        self.assertEqual(r.returncode, 1)
        self.assertFalse(key.encode() in r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
