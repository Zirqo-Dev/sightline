"""v2 prerequisite logs. Decisions and trades stay byte-identical with the log on or off."""
import io, json, os, tempfile, time, unittest, urllib.error
from desk import config as C, observe, run, skips, v2log
from tests.decision_market import address_n, snapshot

T0 = 1_800_000_000
POOL = "1" * 32
CREATOR = "CreatorWalletMustNotBeWritten0000000000"


def _mint(n):
    """32-byte address. 0, O, I, and l are not base58 and must not appear."""
    return address_n(5000 + n)


MINT_A = _mint(1)
MINT_B = _mint(2)
MINT_C = _mint(3)
MINT_D = _mint(4)


def lines(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def manifest(data_dir):
    folder = os.path.join(data_dir, "v2")
    rows = []
    for name in sorted(os.listdir(folder)):
        if name.startswith("manifest-") and name.endswith(".jsonl"):
            rows.extend(lines(os.path.join(folder, name)))
    return rows


def pair(mint, *, sol=80, token=1_000_000, age_s=7 * 3600, dex="pumpswap", buys=3, sells=2, pool=POOL):
    return {
        "chainId": "solana", "dexId": dex, "pairAddress": pool,
        "quoteToken": {"address": run.SOL},
        "baseToken": {"address": mint, "symbol": "T"},
        "pairCreatedAt": int((T0 - age_s) * 1000),
        "liquidity": {"quote": sol, "base": token},
        "txns": {"h1": {"buys": buys, "sells": sells}},
    }


def rc_for(*, dev_pct=0, top=5, mint_auth=None, freeze_auth=None, creator=CREATOR):
    holders = []
    if dev_pct:
        holders.append({"pct": dev_pct, "owner": creator, "address": "D" * 32})
    if top:
        holders.append({"pct": top, "owner": "H" * 32, "address": "A" * 32})
    return {
        "mintAuthority": mint_auth, "freezeAuthority": freeze_auth,
        "creator": creator, "topHolders": holders, "knownAccounts": {},
        "score_normalised": 4,
    }


def fetched(mint, pair_doc, rc, lookup="ok"):
    return dict(mint=mint, pair=pair_doc, rc=rc, pair_at=T0, rug_at=T0 + 1, pool_lookup=lookup)


class Rules(unittest.TestCase):
    def test_each_s2_rule_and_recompute_from_the_logged_inputs(self):
        doc = pair(MINT_A)
        rc = rc_for()
        inputs = v2log._inputs_from(doc, rc, T0)
        series = [{"t_s": T0, "k": inputs["k"], "pool": inputs["pairAddress"]}]
        rules = v2log.rules_from_logged(inputs, series, True)
        self.assertEqual(rules, {key: "pass" for key in v2log.RULE_IDS})
        self.assertEqual(v2log.rules_from_logged(inputs, series, True), rules)
        self.assertEqual(inputs["top10_ex_pool"], skips.top10_ex_pool(rc))
        self.assertNotIn(CREATOR, json.dumps(inputs))
        self.assertNotIn("owner", json.dumps(inputs))
        self.assertEqual(inputs["txns_h1"], {"buys": 3, "sells": 2})
        self.assertEqual(inputs["pairCreatedAt"], doc["pairCreatedAt"])
        young = v2log._inputs_from(pair(MINT_A, age_s=5 * 3600), rc, T0)
        self.assertEqual(v2log.rules_from_logged(young, series, True)["graduated_age"], "fail")
        thin = v2log._inputs_from(pair(MINT_A, sol=49.9), rc, T0)
        self.assertEqual(v2log.rules_from_logged(thin, series, True)["sol_reserve"], "fail")
        quiet = v2log._inputs_from(pair(MINT_A, buys=0, sells=0), rc, T0)
        self.assertEqual(v2log.rules_from_logged(quiet, series, True)["activity_h1"], "fail")
        sell_only = v2log._inputs_from(pair(MINT_A, buys=0, sells=1), rc, T0)
        self.assertEqual(v2log.rules_from_logged(sell_only, series, True)["activity_h1"], "pass")
        missing_buys = dict(sell_only, txns_h1={"sells": 1})
        self.assertEqual(v2log.rules_from_logged(missing_buys, series, True)["activity_h1"], "unknown")
        heavy = v2log._inputs_from(pair(MINT_A), rc_for(top=30), T0)
        self.assertEqual(v2log.rules_from_logged(heavy, series, True)["holders"], "fail")
        at_dev = v2log._inputs_from(pair(MINT_A), rc_for(dev_pct=10), T0)
        self.assertEqual(v2log.rules_from_logged(at_dev, series, True)["holders"], "pass")
        over_dev = v2log._inputs_from(pair(MINT_A), rc_for(dev_pct=10.1), T0)
        self.assertEqual(v2log.rules_from_logged(over_dev, series, True)["holders"], "fail")
        open_auth = v2log._inputs_from(pair(MINT_A), rc_for(mint_auth="So" + "1" * 32), T0)
        self.assertEqual(open_auth["mint_authority"], "set")
        self.assertNotIn("So" + "1" * 32, json.dumps(open_auth))
        self.assertEqual(v2log.rules_from_logged(open_auth, series, True)["authorities"], "fail")
        dropped = [{"t_s": T0, "k": 100.0, "pool": POOL}, {"t_s": T0 + 60, "k": 50.0, "pool": POOL}]
        self.assertEqual(v2log.rules_from_logged(inputs, dropped, True)["k_stable"], "fail")
        kept = [{"t_s": T0, "k": 100.0, "pool": POOL}, {"t_s": T0 + 60, "k": 50.1, "pool": POOL}]
        self.assertEqual(v2log.rules_from_logged(inputs, kept, True)["k_stable"], "pass")
        skipped = [
            {"t_s": T0, "k": 100.0, "pool": POOL},
            {"t_s": T0 + 30, "k": None, "pool": POOL},
            {"t_s": T0 + 60, "k": 40.0, "pool": POOL},
        ]
        self.assertTrue(v2log.k_dropped(skipped))
        other = "2" * 32
        switched = [
            {"t_s": T0, "k": 100.0, "pool": POOL},
            {"t_s": T0 + 60, "k": 10.0, "pool": other},
        ]
        self.assertFalse(v2log.k_dropped(switched))
        self.assertEqual(v2log.rules_from_logged(inputs, series, False)["sol_reserve"], "unknown")
        bare = {"freezeAuthority": None, "topHolders": [], "score_normalised": 1}
        missing_key = v2log._inputs_from(pair(MINT_A), bare, T0)
        self.assertEqual(missing_key["mint_authority"], "unknown")
        self.assertEqual(missing_key["freeze_authority"], "revoked")
        self.assertEqual(v2log.rules_from_logged(missing_key, series, True)["authorities"], "unknown")

    def test_top10_excludes_pool_accounts_without_storing_them(self):
        pool_owner = "P" * 32
        rc = {
            "creator": CREATOR,
            "knownAccounts": {pool_owner: {"type": "AMM"}},
            "topHolders": [
                {"pct": 80, "owner": pool_owner, "address": "Z" * 32},
                {"pct": 4, "owner": "H" * 32, "address": "A" * 32},
            ],
            "mintAuthority": None, "freezeAuthority": None,
        }
        view = v2log.holder_view(rc)
        self.assertEqual(v2log.top10_from_rows(view["rows"]), skips.top10_ex_pool(rc))
        self.assertLess(v2log.top10_from_rows(view["rows"]), 30)
        self.assertNotIn(pool_owner, json.dumps(view))

    def test_twin_list_excludes_s2_picks_and_does_not_draw(self):
        src = open(v2log.__file__, encoding="utf-8").read()
        self.assertNotIn("import random", src)
        self.assertNotIn("Random(", src)
        pick = v2log._inputs_from(pair(MINT_A), rc_for(), T0)
        twin = v2log._inputs_from(pair(MINT_B), rc_for(dev_pct=40), T0)
        young = v2log._inputs_from(pair(MINT_C, age_s=1000), rc_for(), T0)
        series = [{"t_s": T0, "k": 1, "pool": POOL}]
        pick_rules = v2log.rules_from_logged(pick, series, True)
        twin_rules = v2log.rules_from_logged(twin, series, True)
        young_rules = v2log.rules_from_logged(young, series, True)
        self.assertTrue(v2log.s2_pick(pick_rules, "ok"))
        self.assertFalse(v2log.twin_candidate(pick, pick_rules, "ok"))
        self.assertTrue(v2log.twin_candidate(twin, twin_rules, "ok"))
        self.assertFalse(v2log.twin_candidate(young, young_rules, "ok"))


class Death(unittest.TestCase):
    def test_price_drop_zero_swaps_and_pending(self):
        def obs(t, sol, buys=0, sells=0):
            return {"t_s": t, "sol": sol, "token": 100.0, "txns_h1": {"buys": buys, "sells": sells}}
        t0 = 1_000_000
        pending = v2log.death_label([obs(t0, 10)])
        self.assertTrue(pending["pending"])
        self.assertIsNone(pending["dead"])
        hit = v2log.death_label([obs(t0, 10), obs(t0 + 1800, 2)])
        self.assertEqual(hit["dead"], True)
        self.assertEqual(hit["reason"], "price")
        late = v2log.death_label([obs(t0, 10), obs(t0 + v2log.DEATH_WINDOW_S + 10, 1, buys=1, sells=1)])
        self.assertNotEqual(late["reason"], "price")
        zeros = [obs(t0 + i * 1800, 10) for i in range(5)]
        dead = v2log.death_label(zeros)
        self.assertEqual((dead["dead"], dead["reason"]), (True, "swaps"))
        traded = [obs(t0 + i * 1800, 10, buys=1, sells=1) for i in range(5)]
        live = v2log.death_label(traded)
        self.assertEqual(live["dead"], False)
        self.assertFalse(live["pending"])
        gap = v2log.death_label([obs(t0, 10), obs(t0 + v2log.DEATH_WINDOW_S, 10)])
        self.assertEqual(gap["reason"], "swaps_gap")
        self.assertIsNone(gap["dead"])
        # A stored price is ignored. The drop is recomputed from reserves.
        lied = [obs(t0, 10), obs(t0 + 1800, 10)]
        lied[1]["price"] = 0
        self.assertNotEqual(v2log.death_label(lied)["reason"], "price")
        # The t0 hour looks backward. Trades before t0 do not keep a quiet pool alive.
        before = [obs(t0, 10, buys=4, sells=2)] + [obs(t0 + i * 1800, 10) for i in range(1, 5)]
        quiet_after = v2log.death_label(before)
        self.assertEqual((quiet_after["dead"], quiet_after["reason"]), (True, "swaps"))
        failed = {"t_s": t0 + 1800, "sol": None, "token": None, "txns_h1": None, "error": "lookup_failed"}
        with_hole = [obs(t0, 10, buys=3, sells=1), failed, obs(t0 + 3600, 10), obs(t0 + 5400, 10), obs(t0 + 7200, 10)]
        hole = v2log.death_label(with_hole)
        self.assertEqual((hole["dead"], hole["reason"]), (True, "swaps"))
        self.assertNotEqual(hole["reason"], "swaps_unknown")


class Files(unittest.TestCase):
    def test_manifest_g5_twin_and_uncapped_snaps(self):
        d = tempfile.mkdtemp()
        seen = [(MINT_A, "token-profiles"), (MINT_B, "token-boosts"), (MINT_C, "token-profiles"), ("short", "token-profiles")]
        rows = [
            fetched(MINT_A, pair(MINT_A), rc_for()),
            fetched(MINT_B, pair(MINT_B), rc_for(dev_pct=40)),
            fetched(MINT_C, None, None, lookup="failed"),
        ]
        summary = v2log.record(
            d, seen, rows, T0, "20261101T0000",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=lambda _m: (_ for _ in ()).throw(AssertionError("no follow on first scan")),
            sleep=lambda _s: None, leads_per_run=2, cap=24, log_errors=0,
        )
        self.assertIsNone(summary["error"])
        body = manifest(d)
        header = body[0]
        self.assertEqual(header["type"], "scan")
        self.assertEqual(header["page_limits"], {"leads_per_run": 2, "pages_per_source": 1})
        self.assertEqual(header["pre_filters"], list(run.LEAD_PRE_FILTERS))
        self.assertEqual(header["sources"], list(run.LEAD_PATHS))
        self.assertEqual(header["pool_lookups_attempted"], 3)
        self.assertEqual(header["pool_lookups_failed"], 1)
        self.assertEqual(header["g5_rate"], round(1 / 3, 6))
        self.assertIsNone(header["twin_draws"])
        self.assertEqual(header["s2_picks"], [MINT_A])
        self.assertEqual(header["twin_candidates"], [MINT_B])
        self.assertGreaterEqual(header["log_errors"], 1)
        cands = {row["mint"]: row for row in body if row["type"] == "candidate"}
        self.assertEqual(set(cands), {MINT_A, MINT_B, MINT_C})
        self.assertNotIn("short", json.dumps(body))
        self.assertNotIn(CREATOR, json.dumps(body))
        self.assertTrue(cands[MINT_A]["selected"])
        self.assertFalse(cands[MINT_C]["selected"])
        self.assertEqual(cands[MINT_C]["pool_lookup"], "failed")
        self.assertEqual(cands[MINT_C]["rules"]["sol_reserve"], "unknown")
        self.assertNotIn("k_series", cands[MINT_A])
        self.assertEqual(cands[MINT_A]["k_series_n"], 1)
        self.assertIsNone(cands[MINT_A]["k_max_drop"])
        self.assertTrue(cands[MINT_A]["first_pick"])
        self.assertFalse(cands[MINT_B]["first_pick"])
        self.assertEqual(cands[MINT_A]["pair_at_s"], T0)
        self.assertEqual(cands[MINT_A]["inputs"]["sol"], 80)
        self.assertEqual(cands[MINT_A]["inputs"]["pairAddress"], POOL)
        self.assertNotIn("liquidity_quote", cands[MINT_A]["inputs"])
        self.assertNotIn("boost_window", cands[MINT_A]["inputs"])
        self.assertNotIn("top10_ex_pool", cands[MINT_A]["inputs"])
        self.assertNotIn("k", cands[MINT_A]["inputs"])
        self.assertIsInstance(cands[MINT_A]["inputs"]["holders"][0], list)
        first_snaps = []
        for name in sorted(os.listdir(os.path.join(d, "v2"))):
            if name.startswith("pool-snaps-"):
                first_snaps.extend(lines(os.path.join(d, "v2", name)))
        series = [
            {"t_s": row["t_s"], "k": row["k"], "pool": row["pool"]}
            for row in first_snaps if row["mint"] == MINT_A
        ]
        again = v2log.rules_from_logged(cands[MINT_A]["inputs"], series, True)
        self.assertEqual(again, cands[MINT_A]["rules"])
        self.assertTrue(cands[MINT_A]["snap_stems"])
        self.assertEqual(cands[MINT_A]["inputs"]["txns_h1"], {"buys": 3, "sells": 2})
        # Second scan: k falls by half, and a mint that left the page is followed once.
        def follow(mint):
            calls.append(mint)
            return pair(mint, sol=40, token=1_000_000)
        calls = []
        summary = v2log.record(
            d, [(MINT_D, "token-profiles")],
            [fetched(MINT_D, pair(MINT_D, sol=10, age_s=1000), rc_for())],
            T0 + 1800, "20261101T0030",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=follow, sleep=lambda _s: None, leads_per_run=12, cap=5,
            now_fn=lambda: T0 + 1800,
        )
        self.assertEqual(summary["extra_calls"], 2)
        self.assertEqual(sorted(calls), sorted([MINT_A, MINT_B]))
        body = manifest(d)
        second = [row for row in body if row.get("scan_id") == "20261101T0030" and row["type"] == "scan"][0]
        self.assertEqual(second["extra_calls"], 2)
        self.assertIsNone(second["follow_stop"])
        self.assertEqual(second["follow_skipped_budget"], 0)
        self.assertEqual(second["g5_rate"], 0.0)
        self.assertEqual(second["pool_lookups"]["extra_attempted"], 2)
        # A's k series is on the follow path, not this scan's candidate. Death file has the drop.
        death = []
        for name in sorted(os.listdir(os.path.join(d, "v2"))):
            if name.startswith("death-"):
                death.extend(lines(os.path.join(d, "v2", name)))
        obs = [row for row in death if row["type"] == "obs" and row["mint"] == MINT_A]
        self.assertGreaterEqual(len(obs), 2)
        label = v2log.death_label(obs)
        written = [row for row in death if row["type"] == "label" and row["mint"] == MINT_A][-1]
        self.assertEqual(written["dead"], label["dead"])
        self.assertEqual(written["reason"], label["reason"])
        snaps = []
        for name in sorted(os.listdir(os.path.join(d, "v2"))):
            if name.startswith("pool-snaps-"):
                snaps.extend(lines(os.path.join(d, "v2", name)))
        self.assertGreaterEqual(sum(1 for row in snaps if row["mint"] == MINT_A), 2)
        self.assertEqual(observe.SNAP_CAP, 48)
        for i in range(49):
            v2log.append_jsonl(d, v2log.SNAPS, {
                "v": 1, "scan_id": "20261101T0030", "mint": MINT_A, "pool": POOL,
                "t": "2026-11-01T00:30:00Z", "t_s": T0 + i, "sol": 1, "token": 1, "k": 1,
                "txns_h1": {"buys": 0, "sells": 0}, "origin": "scan", "error": None,
            }, T0, max_bytes=10 ** 9)
        snap_rows = []
        for name in sorted(os.listdir(os.path.join(d, "v2"))):
            if name.startswith("pool-snaps-"):
                snap_rows.extend(lines(os.path.join(d, "v2", name)))
        self.assertGreaterEqual(sum(1 for row in snap_rows if row["mint"] == MINT_A), 49)

    def test_rollover_starts_a_new_file(self):
        d = tempfile.mkdtemp()
        row = {"v": 1, "scan_id": "20261101T0000", "mint": MINT_A, "pool": POOL,
               "t": "2026-11-01T00:00:00Z", "t_s": T0, "sol": 1, "token": 1, "k": 1,
               "txns_h1": None, "origin": "scan", "error": None}
        v2log.append_jsonl(d, v2log.SNAPS, row, T0, max_bytes=80)
        v2log.append_jsonl(d, v2log.SNAPS, row, T0, max_bytes=80)
        names = [n for n in os.listdir(os.path.join(d, "v2")) if n.startswith("pool-snaps-")]
        self.assertGreaterEqual(len(names), 2)

    def test_corrupt_seen_is_not_overwritten(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "v2"))
        path = os.path.join(d, "v2", "seen.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{")
        before = open(path, "rb").read()
        summary = v2log.record(
            d, [(MINT_A, "token-profiles")], [fetched(MINT_A, pair(MINT_A), rc_for())],
            T0, "20261101T0000", sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=None, sleep=lambda _s: None,
        )
        self.assertEqual(summary["error"], "seen_corrupt")
        self.assertGreaterEqual(summary["log_errors"], 1)
        self.assertEqual(open(path, "rb").read(), before)
        self.assertFalse(any(name.startswith("manifest-") for name in os.listdir(os.path.join(d, "v2"))))

    def test_rows_are_stamped_with_the_fetch_time(self):
        d = tempfile.mkdtemp()
        row = fetched(MINT_A, pair(MINT_A, age_s=7 * 3600), rc_for())
        row["pair_at"] = T0 + 25
        v2log.record(
            d, [(MINT_A, "token-profiles")], [row], T0, "20261101T0000",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=None, sleep=lambda _s: None,
        )
        body = manifest(d)
        cand = next(item for item in body if item["type"] == "candidate")
        self.assertEqual(body[0]["t_s"], T0)
        self.assertEqual(cand["pair_at_s"], T0 + 25)
        self.assertEqual(cand["inputs"]["age_s"], 7 * 3600 + 25)
        obs = []
        for name in os.listdir(os.path.join(d, "v2")):
            if name.startswith("death-"):
                obs.extend(item for item in lines(os.path.join(d, "v2", name)) if item["type"] == "obs")
        self.assertEqual(obs[0]["t_s"], T0 + 25)

    def test_follow_stops_for_budget_and_for_429(self):
        d = tempfile.mkdtemp()
        v2log.record(
            d, [(MINT_A, "token-profiles"), (MINT_B, "token-boosts")],
            [fetched(MINT_A, pair(MINT_A), rc_for()), fetched(MINT_B, pair(MINT_B), rc_for())],
            T0, "20261101T0000", sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=None, sleep=lambda _s: None,
        )
        calls = []

        def fetch(mint):
            calls.append(mint)
            return pair(mint)

        stopped = v2log.record(
            d, [], [], T0 + 30, "20261101T0030",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=fetch, sleep=lambda _s: None, cap=10, budget_s=0,
        )
        self.assertEqual(calls, [])
        self.assertEqual(stopped["follow_stop"], "budget")
        self.assertEqual(stopped["follow_skipped_budget"], 2)

        def limited(mint):
            calls.append(mint)
            raise urllib.error.HTTPError("u", 429, "rate", {}, io.BytesIO(b""))

        calls.clear()
        blocked = v2log.record(
            d, [], [], T0 + 60, "20261101T0100",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=limited, sleep=lambda _s: None, cap=10, budget_s=45,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(blocked["follow_stop"], "429")
        self.assertEqual(blocked["follow_skipped_budget"], 1)

    def test_a_logger_error_is_swallowed(self):
        d = tempfile.mkdtemp()

        def boom(_mint):
            raise RuntimeError("disk")

        summary = v2log.record(
            d, [(MINT_B, "token-boosts")], [], T0 + 10, "20261101T0000",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=boom, sleep=lambda _s: None, cap=4,
        )
        # MINT_B was never seen, so nothing is followed. Force a seen pool and a raising fetch.
        v2log.save_seen(d, {"v": 1, "pools": {MINT_A: {"t0_s": T0, "pool": POOL, "k_series": [], "obs": []}}})
        summary = v2log.record(
            d, [], [], T0 + 20, "20261101T0000",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=boom, sleep=lambda _s: None, cap=4,
        )
        self.assertEqual(summary["extra_calls"], 1)
        self.assertEqual(summary["pool_lookups_failed"], 0)
        self.assertIsNone(summary["error"])
        header = [row for row in manifest(d) if row["type"] == "scan"][-1]
        self.assertEqual(header["pool_lookups"]["extra_failed"], 1)


class ScanIntegration(unittest.TestCase):
    def _market(self, pages):
        calls = []

        def get(url, headers=None):
            calls.append(url)
            if url.endswith("/token-profiles/latest/v1"):
                return pages.get("profiles", [])
            if url.endswith("/token-boosts/latest/v1"):
                return pages.get("boosts", [])
            if "/latest/dex/tokens/" in url:
                mint = url.rstrip("/").split("/")[-1]
                doc = pages["pairs"].get(mint)
                if doc is None:
                    raise RuntimeError("dex")
                return {"pairs": [doc]}
            if "rugcheck" in url:
                mint = url.split("/tokens/")[1].split("/report")[0]
                rc = pages["rcs"].get(mint)
                if rc is None:
                    raise RuntimeError("rug")
                return rc
            if "jup.ag" in url:
                return {"outAmount": "1", "priceImpactPct": "0.01", "routePlan": [1]}
            raise AssertionError(url)

        return get, calls

    def test_page_limit_is_logged_and_not_fetched(self):
        mints = [_mint(i) for i in range(15)]
        profiles = [{"chainId": "solana", "tokenAddress": m} for m in mints]
        profiles.append({"chainId": "solana", "tokenAddress": mints[0]})
        boosts = [{"chainId": "ethereum", "tokenAddress": _mint(99)},
                  {"chainId": "solana", "tokenAddress": mints[1]}]
        pairs = {m: pair(m, sol=5, age_s=60) for m in mints}
        rcs = {m: rc_for() for m in mints}
        get, calls = self._market({"profiles": profiles, "boosts": boosts, "pairs": pairs, "rcs": rcs})
        d = tempfile.mkdtemp()
        h = run.scan(d, get, now=T0, mode="scan", sleep=lambda s: None, v2_log=True)
        self.assertEqual(h["leads_this_run"], C.LEADS_PER_RUN)
        dex_calls = [u for u in calls if "/latest/dex/tokens/" in u]
        self.assertEqual(len(dex_calls), C.LEADS_PER_RUN)
        body = manifest(d)
        header = next(row for row in body if row["type"] == "scan")
        self.assertEqual(header["candidates_seen"], 15)
        self.assertEqual(header["candidates_selected"], C.LEADS_PER_RUN)
        self.assertEqual(header["page_limits"]["leads_per_run"], C.LEADS_PER_RUN)
        tail = [row for row in body if row["type"] == "candidate" and not row["selected"]]
        self.assertEqual(len(tail), 3)
        self.assertTrue(all(row["pool_lookup"] == "not_attempted" for row in tail))
        self.assertTrue(all("inputs" not in row for row in tail))
        self.assertTrue(all("rules" not in row for row in tail))
        self.assertEqual(header["extra_calls"], 0)
        self.assertEqual(header["log_errors"], 0)
        self.assertEqual(len(header["source_pages"]), 2)
        self.assertTrue(all(page["status"] == "ok" and page["fetched_at"] for page in header["source_pages"]))
        self.assertEqual(header["rug_lookups_attempted"], C.LEADS_PER_RUN)
        self.assertEqual(header["rug_fail_rate"], 0.0)

    def test_follow_calls_are_capped_and_decisions_match_when_logging_is_off(self):
        first = [_mint(1), _mint(2), _mint(3)]
        second = [_mint(9)]
        pairs = {m: pair(m) for m in first + second}
        rcs = {m: rc_for() for m in first + second}

        def run_once(flag):
            pages = {"n": 0}
            def get(url, headers=None):
                if url.endswith("/token-profiles/latest/v1"):
                    src = first if pages["n"] == 0 else second
                    return [{"chainId": "solana", "tokenAddress": m} for m in src]
                if url.endswith("/token-boosts/latest/v1"):
                    return []
                if "/tokens/v1/solana/" in url:
                    addrs = url.split("/tokens/v1/solana/")[-1].split(",")
                    return [pairs[m] for m in addrs if m in pairs]
                if "/latest/dex/tokens/" in url:
                    mint = url.rstrip("/").split("/")[-1]
                    return {"pairs": [pairs[mint]]}
                if "rugcheck" in url:
                    mint = url.split("/tokens/")[1].split("/report")[0]
                    return rcs[mint]
                if "jup.ag" in url:
                    return {"outAmount": "100", "priceImpactPct": "0.01", "routePlan": [1]}
                raise AssertionError(url)
            calls = []
            def wrapped(url, headers=None):
                calls.append(url)
                if url.endswith("/token-profiles/latest/v1"):
                    out = get(url, headers)
                    pages["n"] += 1
                    return out
                return get(url, headers)
            d = tempfile.mkdtemp()
            # pair_at is time.time(). Freeze it on the scan clock so the 2h window
            # stays open for the follow, and decisions still see T0.
            os.environ["SOLANA_DESK_V2_EXTRA_CALLS"] = "1"
            real_time = time.time
            try:
                time.time = lambda: T0
                run.scan(d, wrapped, now=T0, mode="scan", sleep=lambda s: None, v2_log=flag)
                time.time = lambda: T0 + 1800
                run.scan(d, wrapped, now=T0 + 1800, mode="scan", sleep=lambda s: None, v2_log=flag)
            finally:
                time.time = real_time
                os.environ.pop("SOLANA_DESK_V2_EXTRA_CALLS", None)
            return d, calls

        on_dir, on_calls = run_once(True)
        off_dir, off_calls = run_once(False)
        self.assertEqual(on_calls[:len(off_calls)], off_calls)
        extra = [u for u in on_calls[len(off_calls):] if "/tokens/v1/solana/" in u]
        self.assertEqual(len(extra), 1)
        for mint in first:
            self.assertIn(mint, extra[0])
        self.assertEqual(len(on_calls), len(off_calls) + 1)
        for name in ("tasks.csv", "state.json", "health.json"):
            with open(os.path.join(on_dir, name), "rb") as fh:
                left = fh.read()
            with open(os.path.join(off_dir, name), "rb") as fh:
                right = fh.read()
            if name == "tasks.csv":
                def drop_delay(text):
                    rows = text.decode().splitlines()
                    head = rows[0].split(",")
                    idx = head.index("check_delay_s")
                    out = [rows[0]]
                    for row in rows[1:]:
                        cells = row.split(",")
                        cells[idx] = ""
                        out.append(",".join(cells))
                    return "\n".join(out).encode()
                self.assertEqual(drop_delay(left), drop_delay(right))
            else:
                self.assertEqual(left, right)
        header = [row for row in manifest(on_dir) if row["type"] == "scan"][-1]
        self.assertEqual(header["extra_calls"], 1)
        self.assertFalse(os.path.isdir(os.path.join(off_dir, "v2")))

    def test_decisions_and_trades_are_byte_identical_on_and_off(self):
        """Valid base58 mints, so the log actually writes. The golden mints contain 0 and are a different test."""
        pool = "3" * 32
        kept = _mint(1)
        dropped = _mint(2)
        fresh = _mint(3)
        rc = rc_for()
        rc["markets"] = [{"marketType": "pump_fun_amm", "pubkey": pool, "lp": {"lpLockedPct": 100}}]
        pairs = {
            kept: pair(kept, pool=pool),
            dropped: pair(dropped, pool=pool, sol=12, age_s=1000),
            fresh: pair(fresh, pool=pool, sol=12, age_s=1000),
        }
        rcs = {m: rc for m in pairs}

        def run_once(flag):
            step = {"n": 0}
            timeouts = []

            def get(url, headers=None, timeout=None):
                if timeout is not None:
                    timeouts.append(timeout)
                if url.endswith("/token-profiles/latest/v1"):
                    src = [kept, dropped] if step["n"] == 0 else [fresh]
                    step["n"] += 1
                    return [{"chainId": "solana", "tokenAddress": m} for m in src]
                if url.endswith("/token-boosts/latest/v1"):
                    return []
                if "/tokens/v1/solana/" in url:
                    addrs = url.split("/tokens/v1/solana/")[-1].split(",")
                    return [pairs[m] for m in addrs if m in pairs]
                if "/latest/dex/tokens/" in url:
                    mint = url.rstrip("/").split("/")[-1]
                    return {"pairs": [pairs[mint]]}
                if "rugcheck" in url:
                    mint = url.split("/tokens/")[1].split("/report")[0]
                    return rcs[mint]
                if "jup.ag" in url:
                    return {"outAmount": "1000000", "priceImpactPct": "0.01", "routePlan": [1]}
                raise AssertionError(url)

            calls = []

            def wrapped(url, headers=None, timeout=None):
                calls.append(url)
                return get(url, headers, timeout)

            d = tempfile.mkdtemp()
            # pair_at is time.time(). Freeze it on the scan clock. See the capped-follow test.
            real_time = time.time
            try:
                time.time = lambda: T0
                run.scan(d, wrapped, now=T0, mode="fill", sleep=lambda s: None, v2_log=flag)
                time.time = lambda: T0 + 1800
                run.scan(d, wrapped, now=T0 + 1800, mode="fill", sleep=lambda s: None, v2_log=flag)
            finally:
                time.time = real_time
            return d, calls, timeouts

        on_dir, on_calls, on_timeouts = run_once(True)
        off_dir, off_calls, off_timeouts = run_once(False)
        on_view = snapshot(on_dir, on_calls, observe.PUBLIC_KEYS)
        off_view = snapshot(off_dir, off_calls, observe.PUBLIC_KEYS)
        on_view.pop("calls")
        off_view.pop("calls")
        self.assertEqual(
            json.dumps(on_view, sort_keys=True, separators=(",", ":")).encode(),
            json.dumps(off_view, sort_keys=True, separators=(",", ":")).encode(),
        )
        for name in ("trades.csv", "tasks.csv", "state.json", "health.json", "positions.json", "paper.json"):
            with open(os.path.join(on_dir, name), "rb") as fh:
                left = fh.read()
            with open(os.path.join(off_dir, name), "rb") as fh:
                right = fh.read()
            self.assertEqual(left, right, name)
        self.assertEqual(on_calls[:len(off_calls)], off_calls)
        self.assertGreater(len(on_calls), len(off_calls))
        self.assertTrue(any("/tokens/v1/solana/" in url for url in on_calls[len(off_calls):]))
        self.assertEqual(off_timeouts, [])
        self.assertIn(v2log.FOLLOW_CALL_TIMEOUT_S, on_timeouts)
        body = manifest(on_dir)
        self.assertTrue(any(row["type"] == "candidate" for row in body))
        self.assertTrue(all(row["log_errors"] == 0 for row in body if row["type"] == "scan"))
        folder = os.path.join(on_dir, "v2")
        snaps, death = [], []
        for name in os.listdir(folder):
            if name.startswith("pool-snaps-"):
                snaps.extend(lines(os.path.join(folder, name)))
            if name.startswith("death-"):
                death.extend(lines(os.path.join(folder, name)))
        self.assertTrue(snaps)
        self.assertTrue(any(row["type"] == "obs" for row in death))
        self.assertTrue(any(row["mint"] == kept and row.get("origin") == "follow" for row in snaps))
        self.assertFalse(os.path.isdir(os.path.join(off_dir, "v2")))

    def test_a_raised_recorder_does_not_change_the_scan(self):
        def get(url, headers=None):
            if "token-profiles" in url or "token-boosts" in url:
                return []
            raise AssertionError(url)
        saved = v2log.record
        def boom(*_a, **_k):
            raise RuntimeError("disk")
        v2log.record = boom
        try:
            d = tempfile.mkdtemp()
            h = run.scan(d, get, now=T0, mode="scan", sleep=lambda s: None, v2_log=True)
        finally:
            v2log.record = saved
        self.assertEqual(h["runs"], 1)
        self.assertFalse(h.get("duplicate"))

    def test_tests_log_has_the_spec_header_and_no_rows(self):
        path = os.path.join(os.path.dirname(__file__), "..", "specs", "tests-log.csv")
        text = open(path, encoding="utf-8").read()
        self.assertEqual(text, "date,id,family,metric,result,planned\n")


class FollowPolicy(unittest.TestCase):
    def _pool(self, t0, obs_times, sol=10.0):
        obs, series = [], []
        for t in obs_times:
            obs.append({
                "t_s": t, "sol": sol, "token": 100.0, "k": sol * 100.0, "pool": POOL,
                "txns_h1": {"buys": 1, "sells": 1}, "error": None,
            })
            series.append({"t_s": t, "k": sol * 100.0, "pool": POOL})
        return {"t0_s": t0, "pool": POOL, "k_series": series, "obs": obs, "snap_stems": []}

    def _run(self, d, pools, now, *, fetch_pairs=None, fetch_pair=None, cap=24, batch=30, budget_s=45):
        v2log.save_seen(d, {"v": 1, "pools": pools})
        return v2log.record(
            d, [], [], now, "20261101T1200",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=fetch_pair, fetch_pairs=fetch_pairs, sleep=lambda _s: None,
            cap=cap, batch=batch, budget_s=budget_s, now_fn=lambda: now,
        )

    def _death(self, d):
        rows = []
        for name in os.listdir(os.path.join(d, "v2")):
            if name.startswith("death-"):
                rows.extend(lines(os.path.join(d, "v2", name)))
        return rows

    def test_one_call_covers_a_batch_of_mints(self):
        d = tempfile.mkdtemp()
        mints = [_mint(i) for i in range(40)]
        pools = {m: self._pool(T0, [T0]) for m in mints}
        batches = []

        def fetch(group):
            batches.append(list(group))
            return {m: pair(m) for m in group}

        summary = self._run(d, pools, T0 + 60, fetch_pairs=fetch, cap=2, batch=30)
        self.assertEqual(summary["extra_calls"], 2)
        self.assertEqual([len(b) for b in batches], [30, 10])
        self.assertEqual(sorted(m for b in batches for m in b), sorted(mints))
        header = [row for row in manifest(d) if row["type"] == "scan"][-1]
        self.assertEqual(header["pool_lookups"]["extra_attempted"], 40)
        self.assertEqual(header["pool_lookups"]["extra_failed"], 0)
        self.assertEqual(header["follow_skipped_budget"], 0)
        self.assertEqual(header["batch_size"], 30)
        self.assertIsNone(header["follow_stop"])

    def test_over_cap_keeps_the_lowest_mint_hash(self):
        d = tempfile.mkdtemp()
        mints = [_mint(i) for i in range(8)]
        ranked = sorted(mints, key=v2log.mint_rank)
        kept, skipped = ranked[:3], ranked[3:]
        pools = {m: self._pool(T0, [T0]) for m in reversed(ranked)}
        batches = []

        def fetch(group):
            batches.append(list(group))
            return {m: pair(m) for m in group}

        summary = self._run(d, pools, T0 + 60, fetch_pairs=fetch, cap=1, batch=3)
        self.assertEqual(batches, [kept])
        self.assertEqual(summary["follow_stop"], "cap")
        self.assertEqual(summary["follow_skipped_budget"], 5)
        skips = [row for row in self._death(d) if row["type"] == "skip"]
        self.assertEqual(sorted(row["mint"] for row in skips), sorted(skipped))
        self.assertTrue(all(row["reason"] == "cap" and row["tier"] == 0 for row in skips))
        header = [row for row in manifest(d) if row["type"] == "scan"][-1]
        self.assertEqual(header["follow_skip_reasons"], {"cap": 5, "budget": 0, "429": 0})
        again = tempfile.mkdtemp()
        pools_forward = {m: self._pool(T0, [T0]) for m in ranked}
        batches.clear()
        self._run(again, pools_forward, T0 + 60, fetch_pairs=fetch, cap=1, batch=3)
        self.assertEqual(batches, [kept])

    def test_open_window_beats_a_lower_hash_closer(self):
        d = tempfile.mkdtemp()
        left, right = _mint(1), _mint(2)
        low, high = (left, right) if v2log.mint_rank(left) < v2log.mint_rank(right) else (right, left)
        now = T0 + v2log.DEATH_WINDOW_S + 10
        end = now - 10
        t0 = end - v2log.DEATH_WINDOW_S
        times = [t0 + i * 1800 for i in range(4)] + [end - 30]
        self.assertLess(times[-1], end)
        self.assertLessEqual(now - times[-1], v2log.H1_S)
        pools = {
            high: self._pool(now - 1000, [now - 1000]),
            low: self._pool(t0, times),
        }
        calls = []

        def fetch(group):
            calls.append(list(group))
            return {m: pair(m) for m in group}

        self._run(d, pools, now, fetch_pairs=fetch, cap=1, batch=1)
        self.assertEqual(calls, [[high]])
        skips = [row for row in self._death(d) if row["type"] == "skip"]
        self.assertEqual([row["mint"] for row in skips], [low])
        self.assertEqual(skips[0]["tier"], 1)
        self.assertEqual(skips[0]["reason"], "cap")

    def test_a_final_label_is_not_fetched_again(self):
        d = tempfile.mkdtemp()
        mint = _mint(4)
        pools = {mint: self._pool(T0, [T0, T0 + 100], sol=10)}
        pools[mint]["obs"][1]["sol"] = 1
        pools[mint]["obs"][1]["k"] = 100

        def fetch(_group):
            raise AssertionError("final pool was fetched")

        summary = self._run(d, pools, T0 + 200, fetch_pairs=fetch, cap=4, batch=30)
        self.assertEqual(summary["extra_calls"], 0)
        self.assertIsNone(summary["follow_stop"])
        _doc, bad = v2log.load_seen(d)
        self.assertFalse(bad)
        info = _doc["pools"][mint]
        self.assertTrue(info["final"])
        self.assertNotIn("obs", info)
        self.assertTrue(info["k_series"])
        self.assertEqual(info["death"]["dead"], True)
        self.assertEqual(info["death"]["reason"], "price")
        self._run(d, _doc["pools"], T0 + 400, fetch_pairs=fetch, cap=4, batch=30)

    def test_a_closed_gap_is_marked_missed_without_a_fetch(self):
        d = tempfile.mkdtemp()
        mint = _mint(5)
        now = T0 + 5 * 3600
        pools = {mint: self._pool(T0, [T0])}

        def fetch(_group):
            raise AssertionError("missed pool was fetched")

        self._run(d, pools, now, fetch_pairs=fetch)
        labels = [row for row in self._death(d) if row["type"] == "label"]
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]["reason"], "missed")
        self.assertIsNone(labels[0]["dead"])
        self.assertFalse(labels[0]["pending"])
        self.assertEqual(v2log.death_label(pools[mint]["obs"], now=now)["reason"], "missed")
        stored = v2log.load_seen(d)[0]["pools"]
        self._run(d, stored, now + 1800, fetch_pairs=fetch)
        labels = [row for row in self._death(d) if row["type"] == "label" and row["reason"] == "missed"]
        self.assertEqual(len(labels), 1)

    def test_a_429_stops_later_batches(self):
        d = tempfile.mkdtemp()
        mints = [_mint(i) for i in range(40)]
        pools = {m: self._pool(T0, [T0]) for m in mints}

        def fetch(group):
            raise urllib.error.HTTPError("u", 429, "rate", {}, io.BytesIO(b""))

        summary = self._run(d, pools, T0 + 60, fetch_pairs=fetch, cap=2, batch=30)
        self.assertEqual(summary["extra_calls"], 1)
        self.assertEqual(summary["follow_stop"], "429")
        header = [row for row in manifest(d) if row["type"] == "scan"][-1]
        self.assertEqual(header["pool_lookups"]["extra_failed"], 30)
        self.assertEqual(header["follow_skip_reasons"]["429"], 10)
        self.assertEqual(header["follow_skipped_budget"], 10)

    def test_best_pairs_picks_the_sol_pair_with_the_most_quote(self):
        seen = []

        def get(url, headers=None, timeout=None):
            seen.append((url, timeout))
            return [
                pair(MINT_A, sol=5),
                pair(MINT_A, sol=50, pool="2" * 32),
                {
                    "chainId": "solana", "dexId": "pumpswap", "pairAddress": "3" * 32,
                    "quoteToken": {"address": "not-sol"}, "baseToken": {"address": MINT_B},
                    "liquidity": {"quote": 999, "base": 1},
                    "txns": {"h1": {"buys": 1, "sells": 1}},
                },
                pair(MINT_B, sol=3),
            ]

        got = run.best_pairs(get, [MINT_A, MINT_B, MINT_C])
        self.assertEqual(got[MINT_A]["liquidity"]["quote"], 50)
        self.assertEqual(got[MINT_B]["liquidity"]["quote"], 3)
        self.assertIsNone(got[MINT_C])
        self.assertEqual(len(seen), 1)
        self.assertIn(MINT_A + "," + MINT_B + "," + MINT_C, seen[0][0])
        self.assertIn("/tokens/v1/solana/", seen[0][0])

    def test_manifest_rows_drop_derivable_fields(self):
        d = tempfile.mkdtemp()
        mints = [_mint(i) for i in range(45)]
        rc = rc_for()
        rc["topHolders"] = [
            {"pct": (i + 1) / 10.0, "owner": "H" * 32, "address": "A" * 32} for i in range(20)
        ]
        seen = [(m, "token-profiles") for m in mints]
        rows = [fetched(m, pair(m), rc) for m in mints[:12]]
        v2log.record(
            d, seen, rows, T0, "20261101T0000",
            sources=run.LEAD_PATHS, pre_filters=run.LEAD_PRE_FILTERS,
            fetch_pair=None, sleep=lambda _s: None, leads_per_run=12,
        )
        names = [n for n in os.listdir(os.path.join(d, "v2")) if n.startswith("manifest-")]
        raw = open(os.path.join(d, "v2", names[0]), "rb").read()
        self.assertLess(len(raw), 28000)
        text = raw.decode()
        self.assertNotIn("H" * 32, text)
        self.assertNotIn("liquidity_quote", text)
        self.assertNotIn("boost_window", text)
        self.assertNotIn("top10_ex_pool", text)
        body = manifest(d)
        tail = [row for row in body if row["type"] == "candidate" and row["pool_lookup"] == "not_attempted"]
        self.assertEqual(len(tail), 33)
        for row in tail:
            self.assertNotIn("inputs", row)
            self.assertLess(len(json.dumps(row, separators=(",", ":"))), 420)
        ok = next(row for row in body if row["type"] == "candidate" and row["pool_lookup"] == "ok")
        self.assertLess(len(json.dumps(ok["inputs"], separators=(",", ":"))), 1200)
        snaps = []
        for name in os.listdir(os.path.join(d, "v2")):
            if name.startswith("pool-snaps-"):
                snaps.extend(lines(os.path.join(d, "v2", name)))
        series = [
            {"t_s": row["t_s"], "k": row["k"], "pool": row["pool"]}
            for row in snaps if row["mint"] == ok["mint"]
        ]
        self.assertEqual(v2log.rules_from_logged(ok["inputs"], series, True), ok["rules"])
        hydrated = v2log.hydrate_inputs(ok["inputs"])
        self.assertEqual(hydrated["top10_ex_pool"], v2log.top10_from_rows(v2log._expand_holders(ok["inputs"]["holders"])))
        self.assertEqual(hydrated["k"], hydrated["sol"] * hydrated["token"])


if __name__ == "__main__":
    unittest.main()
