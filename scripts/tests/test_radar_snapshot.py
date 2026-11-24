"""Radar snapshot: derived chips, placeholders, and the embargo allowlist."""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.radar_snapshot import FETCH_TIMEOUT_S, _name_and_slugs, build_from, buy_streak, second_leg

NOW = datetime.datetime(2026, 11, 24, 18, 0, tzinfo=datetime.timezone.utc)
MINT_A = "Mint1111111111111111111111111111pump"
MINT_B = "Mint2222222222222222222222222222pump"
ETH_ADDR = "0x" + ("ab" * 20)


def _snap(mint, t, sol, buys, sells):
    return {
        "mint": mint, "pool": "Pool1111111111111111111111111111111111",
        "t_s": t, "sol": sol, "token": 1000.0,
        "txns_h1": {"buys": buys, "sells": sells},
    }


class RadarSnapshotTest(unittest.TestCase):
    def test_streak_and_second_leg_rules(self):
        self.assertTrue(buy_streak([{"buys": 3, "sells": 1}, {"buys": 2, "sells": 1}]))
        self.assertFalse(buy_streak([{"buys": 3, "sells": 1}, {"buys": 1, "sells": 2}]))
        self.assertFalse(buy_streak([{"buys": 3, "sells": 1}]))
        self.assertTrue(second_leg([1, 1.5, 0.9, 1.6]))
        self.assertFalse(second_leg([1, 1.1, 1.2, 1.3]))
        self.assertFalse(second_leg([1, 2, 1.9, 1.8]))

    def test_solana_placeholder_and_derived_chips(self):
        holders = [[5, 0, 0] for _ in range(10)]
        manifest = [
            {"type": "scan", "t": "2026-11-24T17:30:00Z"},
            {"type": "candidate", "mint": MINT_A, "t_s": 1000, "first_pick": True,
             "inputs": {"age_s": 600, "holders": holders, "sol": 1000, "token": 1000}},
            {"type": "candidate", "mint": MINT_B, "t_s": 1000, "first_pick": False,
             "inputs": {"age_s": 7200, "holders": [[1, 0, 0]], "sol": 10, "token": 1000}},
        ]
        snaps = [
            _snap(MINT_A, 1000, 1000, 5, 1),
            _snap(MINT_A, 2800, 1500, 4, 1),
            _snap(MINT_A, 4600, 900, 6, 2),
            _snap(MINT_A, 6400, 1600, 3, 1),
            _snap(MINT_B, 1000, 10, 1, 4),
            _snap(MINT_B, 2800, 10, 1, 4),
        ]
        deaths = [{"type": "label", "mint": MINT_A, "dead": True, "reason": "price"}]
        doc = build_from({"manifest": manifest, "snaps": snaps, "deaths": deaths}, NOW)
        sol = [row for row in doc["rows"] if row["chain"] == "SOL"]
        self.assertEqual([row["name"] for row in sol], ["SOL #1", "SOL #2"])
        self.assertEqual(sol[0]["seen_at"], "1970-01-01T00:06:40Z")
        self.assertNotIn("age", sol[0])
        self.assertNotIn("next_read", doc)
        chips = [chip["id"] for chip in sol[0]["chips"]]
        for needed in ("seen", "pool", "heavy", "streak", "leg"):
            self.assertIn(needed, chips)
        self.assertNotIn("desk", chips)
        self.assertNotIn("skull", chips)
        self.assertNotIn("thin", chips)
        self.assertIn("thin", [chip["id"] for chip in sol[1]["chips"]])
        self.assertNotIn("streak", [chip["id"] for chip in sol[1]["chips"]])
        self.assertNotIn("leg", [chip["id"] for chip in sol[1]["chips"]])
        # The flat name is first_pick and dead. That must not outrank the raw move.
        self.assertGreater(sol[0]["change"], sol[1]["change"])
        row_blob = json.dumps(sol).lower()
        for banned in ("desk", "first_pick", "kill", "dead", "skull", "outcome", "verdict", "first snap"):
            self.assertNotIn(banned, row_blob)
        blob = json.dumps(doc)
        self.assertNotIn(MINT_A, blob)
        self.assertNotIn(MINT_B, blob)
        self.assertNotIn("NIX", blob)
        self.assertNotRegex(sol[0]["name"], r"[1-9A-HJ-NP-Za-km-z]{32,44}")
        self.assertLess(abs(sol[0]["change"]), 1000)

    def test_dust_reserve_does_not_invent_a_runner(self):
        mint = "Mint3333333333333333333333333333pump"
        snaps = [
            _snap(mint, 1000, 0.08, 2, 1),
            {"mint": mint, "t_s": 2800, "sol": 0.002, "token": 1e-6, "txns_h1": {"buys": 9, "sells": 1}},
        ]
        # The second snap's token amount is below 1, so the pair is not a price path.
        snaps[0]["token"] = 1000.0
        doc = build_from({"snaps": snaps, "manifest": [], "deaths": []}, NOW)
        self.assertFalse(any(row["chain"] == "SOL" for row in doc["rows"]))
        self.assertNotIn(mint, json.dumps(doc))
        self.assertTrue(any("dropped 1 series" in gap for gap in doc["gaps"]))

    def test_null_floor_stays_null_and_addresses_are_dropped(self):
        hood = {"generated_at": "2026-11-24T18:15:00Z", "tokens": [{
            "symbol": "CAT", "tier": "BEST", "price_usd": 1.2, "chg_h24": 10,
            "vol24_usd": 1000, "buyers24": 4, "ca": ETH_ADDR, "image_url": "https://cdn.example/cat.png",
        }]}
        discover = {"generated_at": "2026-11-24T05:20:00Z", "collections": [{
            "name": "Cash Cats", "slug": "cash-cats", "floor_price": 1.25,
            "floor_symbol": "ETH", "floor_chg_pct": None, "volume_24h": 2, "sales_24h": 1,
        }]}
        doc = build_from({"hood": hood, "nft_discover": discover}, NOW)
        cat = next(row for row in doc["rows"] if row["name"] == "CAT")
        cats = next(row for row in doc["rows"] if row["name"] == "Cash Cats")
        self.assertIsNone(cats["change"])
        self.assertEqual(cat["chain"], "RH")
        self.assertNotIn("desk", [chip["id"] for chip in cat["chips"]])
        blob = json.dumps(doc)
        self.assertNotIn(ETH_ADDR, blob)
        self.assertNotIn('"ca"', blob)

    def test_lst_and_outcome_fields_stay_out(self):
        doc = build_from({
            "lst": {
                "written_at": "2026-11-24T18:17:19Z",
                "runs_ok": True,
                "chain_ok": True,
                "onset": 4,
                "d_bps": 30,
                "verdict": "go",
                "coverage": "0.9",
                "freeze_log": [{"mint": MINT_A}],
            },
            "nft_ran_at": "2026-11-24T17:27:14Z",
            "units": [{
                "chain": "eip155:1", "slug": "sample-collection", "floor_eth": 0.2,
                "snapshot_t": "2026-11-24T12:00:00Z", "contract": ETH_ADDR,
                "nh04_verdict": "go", "asset_id": "nft_collection:eip155:1:" + ETH_ADDR,
            }],
            "flags": {"new": [{"kind": "whale_move", "ticker": "NJAK", "tag": "w-aaaaaaaa", "at": "2026-11-24T17:10:00Z"}]},
            "whale_roster": {"whales": [{"codename": "Lead whale"}]},
            "last_run": {"at": "2026-11-24T17:17:38Z", "results": [{
                "ticker": "NJAK", "price_ada": 0.01, "vol_h24_ada": 10,
                "native_holders": 3, "unit": "ab" * 28, "verdict": "pass",
            }], "triggers": [
                {"kind": "whale_move", "name": "Lead whale", "metrics": {"tag": "w-aaaaaaaa"}},
                {"kind": "whale_move", "name": "Not A Real Whale"},
            ]},
        }, NOW)
        blob = json.dumps(doc)
        for banned in ("onset", "d_bps", "verdict", "nh04", "coverage", "freeze_log", MINT_A, ETH_ADDR, "w-aaaaaaaa"):
            self.assertNotIn(banned, blob)
        lst = next(feed for feed in doc["feeds"] if feed["id"] == "LST")
        self.assertEqual(lst["status"], "green")
        self.assertEqual(lst["at"], "2026-11-24T18:17:19Z")
        njak = next(row for row in doc["rows"] if row["name"] == "NJAK")
        self.assertEqual([chip["id"] for chip in njak["chips"]], ["whale"])
        self.assertTrue(any("Lead whale" in fact for fact in njak["facts"]))
        self.assertNotIn("Not A Real Whale", blob)
        eth = next(row for row in doc["rows"] if row["chain"] == "ETH")
        self.assertEqual(eth["name"], "sample collection")
        self.assertNotIn("BASE", [row["chain"] for row in doc["rows"]])

    def test_builder_does_not_name_the_embargoed_log(self):
        path = os.path.join(os.path.dirname(__file__), "..", "radar_snapshot.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotRegex(source, r"https://\S*captures\.jsonl")
        self.assertIn("lst-count-data/status.json", source)
        self.assertIn("status.json only", source)
        self.assertNotIn("death-", source)
        self.assertLessEqual(FETCH_TIMEOUT_S, 15)


    def test_reserve_filter_edges(self):
        kept_ratio = "Mint4444444444444444444444444444pump"
        kept_token = "Mint5555555555555555555555555555pump"
        over = "Mint6666666666666666666666666666pump"
        dust = "Mint7777777777777777777777777777pump"
        single = "Mint8888888888888888888888888888pump"
        snaps = [
            _snap(kept_ratio, 1, 0.05, 3, 1),
            _snap(kept_ratio, 2, 1.25, 3, 1),
            {"mint": kept_token, "t_s": 1, "sol": 1, "token": 1, "txns_h1": {"buys": 3, "sells": 1}},
            {"mint": kept_token, "t_s": 2, "sol": 1, "token": 1, "txns_h1": {"buys": 3, "sells": 1}},
            _snap(over, 1, 1, 3, 1),
            _snap(over, 2, 25.01, 3, 1),
            _snap(dust, 1, 0.049, 3, 1),
            {"mint": dust, "t_s": 2, "sol": 1, "token": 0.5, "txns_h1": {"buys": 3, "sells": 1}},
            _snap(single, 1, 10, 3, 1),
        ]
        doc = build_from({"snaps": snaps}, NOW)
        names = [row["name"] for row in doc["rows"] if row["chain"] == "SOL"]
        self.assertEqual(len(names), 2)
        self.assertTrue(any("dropped 3 series" in gap for gap in doc["gaps"]))
        blob = json.dumps(doc)
        for mint in (kept_ratio, kept_token, over, dust, single):
            self.assertNotIn(mint, blob)

    def test_creator_handle_and_duplicate_tickers(self):
        other = "0x" + ("cd" * 20)
        hood = {"generated_at": "2026-11-24T18:00:00Z", "tokens": [
            {"symbol": "NVDA", "tier": "WATCH", "price_usd": 1, "chg_h24": 1, "age_days": 10,
             "ca": ETH_ADDR, "vol24_usd": 10, "buyers24": 2},
            {"symbol": "NVDA", "tier": "WATCH", "price_usd": 2, "chg_h24": 2, "age_days": 2,
             "ca": other, "vol24_usd": 11, "buyers24": 3},
        ]}
        self.assertEqual(_name_and_slugs("Cats @wfit")[0], "Cats")
        self.assertIn("wfit", _name_and_slugs("Cats @wfit")[1])
        self.assertEqual(_name_and_slugs("Quotrons (by WFit)")[0], "Quotrons")
        self.assertEqual(_name_and_slugs("Killed by death")[:2], ("Killed by death", []))
        self.assertEqual(_name_and_slugs("Made by apes")[:2], ("Made by apes", []))
        self.assertEqual(_name_and_slugs("Stand by Me")[:2], ("Stand by Me", []))
        self.assertFalse(_name_and_slugs("Killed by death")[2])
        self.assertEqual(_name_and_slugs("Punks @eth")[0], "Punks")
        self.assertEqual(_name_and_slugs("Mood (by sol)")[0], "Mood")
        discover = {"collections": [
            {"name": "Companions by WFit", "slug": "companions-by-wfit", "floor_price": 1,
             "floor_symbol": "ETH", "volume_24h": 50, "floor_chg_pct": None,
             "image_url": "https://cdn.example/companions-by-wfit/a.png"},
            {"name": "Punks @eth", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 40,
             "image_url": "https://cdn.example/punks.png"},
            {"name": "Mood (by sol)", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 30},
            {"name": "Made by apes", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 20},
            {"name": "Bored Apes", "floor_price": 3, "floor_symbol": "ETH", "volume_24h": 80,
             "image_url": "https://cdn.example/bored-apes.png"},
            {"name": "Killed by death", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 10},
            {"name": "Ghost @ethereum", "floor_price": None, "floor_symbol": "ETH"},
        ]}
        hood["tokens"].append({
            "symbol": "Stand by Me", "tier": "WATCH", "price_usd": 1, "chg_h24": 1,
            "vol24_usd": 5, "buyers24": 1,
        })
        doc = build_from({
            "hood": hood,
            "nft_discover": discover,
            "snaps": [
                _snap(MINT_A, 1000, 80, 4, 1),
                _snap(MINT_A, 2800, 120, 4, 1),
            ],
            "manifest": [{"type": "candidate", "mint": MINT_A, "t_s": 1000,
                          "inputs": {"age_s": 8000, "sol": 80, "token": 1000}}],
        }, NOW)
        blob = json.dumps(doc)
        self.assertNotIn("WFit", blob)
        self.assertNotIn("wfit", blob.lower())
        self.assertNotIn("companions-by", blob.lower())
        self.assertNotIn("https://cdn.example/companions-by-wfit/a.png", blob)
        self.assertNotIn("https://cdn.example/wfit/cat.png", blob)
        self.assertNotIn(ETH_ADDR, blob)
        self.assertNotIn(other, blob)
        companions = next(row for row in doc["rows"] if row["name"] == "Companions")
        self.assertIsNone(companions["image"])
        self.assertIsNone(next(row for row in doc["rows"] if row["name"] == "Punks")["image"])
        self.assertTrue(any(row["name"] == "Mood" for row in doc["rows"]))
        self.assertTrue(any(row["name"] == "Made by apes" for row in doc["rows"]))
        self.assertTrue(any(row["name"] == "Killed by death" for row in doc["rows"]))
        self.assertTrue(any(row["name"] == "Stand by Me" for row in doc["rows"]))
        apes = next(row for row in doc["rows"] if row["name"] == "Bored Apes")
        self.assertEqual(apes["image"], "https://cdn.example/bored-apes.png")
        sol = next(row for row in doc["rows"] if row["name"] == "SOL #1")
        self.assertEqual(sol["chain"], "SOL")
        self.assertEqual(sol["price_unit"], "SOL")
        self.assertEqual(sol["size_unit"], "SOL")
        self.assertTrue(any(feed["id"] == "SOL" and feed["label"] == "SOL" for feed in doc["feeds"]))
        self.assertTrue(any("SOL #1" in gap for gap in doc["gaps"]))
        nvda = [row for row in doc["rows"] if row["name"] == "NVDA"]
        self.assertEqual(len(nvda), 2)
        self.assertEqual(len({row["id"] for row in nvda}), 2)
        self.assertEqual(len({row["hint"] for row in nvda}), 2)
        for row in nvda:
            self.assertLess(len(row["hint"]), 20)
            self.assertNotIn(ETH_ADDR, row["hint"])

    def test_slug_creator_marks_stay_on_their_row(self):
        hood = {"generated_at": "2026-11-24T18:00:00Z", "tokens": [{
            "symbol": "somehandle club", "tier": "WATCH", "price_usd": 1, "chg_h24": 1,
            "vol24_usd": 9, "buyers24": 2,
            "image_url": "https://cdn.example/somehandle-logo.png",
        }]}
        discover = {"collections": [
            {"name": "companions-by-somehandle", "floor_price": 1, "floor_symbol": "ETH",
             "volume_24h": 90, "image_url": "https://cdn.example/companions-by-somehandle/a.png"},
            {"name": "cats_by_lowcase", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 80,
             "image_url": "https://cdn.example/cats_by_lowcase.png"},
            {"name": "buddies by zxqw", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 70,
             "image_url": "https://cdn.example/buddies.png"},
            {"name": "mosaic-by-café", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 60,
             "image_url": "https://cdn.example/mosaic-by-café/a.png"},
            {"name": "crew-by-Apes_1", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 50,
             "image_url": "https://cdn.example/crew-by-Apes_1.png"},
            {"name": "Dots @abc", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 40,
             "image_url": "https://cdn.example/dots.png"},
        ]}
        doc = build_from({
            "hood": hood,
            "nft_discover": discover,
            "snaps": [_snap(MINT_A, 1000, 80, 4, 1), _snap(MINT_A, 2800, 120, 4, 1)],
            "manifest": [{"type": "candidate", "mint": MINT_A, "t_s": 1000,
                          "inputs": {"age_s": 8000, "sol": 80, "token": 1000}}],
        }, NOW)
        names = {row["name"] for row in doc["rows"]}
        self.assertIn("companions", names)
        self.assertIn("cats", names)
        self.assertIn("buddies", names)
        self.assertIn("mosaic", names)
        self.assertIn("crew", names)
        self.assertIn("Dots", names)
        self.assertIn("somehandle club", names)
        self.assertIn("SOL #1", names)
        marked = {row["name"]: row for row in doc["rows"] if row["name"] in (
            "companions", "cats", "buddies", "mosaic", "crew", "Dots")}
        for row in marked.values():
            self.assertIsNone(row["image"], row["name"])
        self.assertNotIn("somehandle", json.dumps(marked["companions"]))
        self.assertNotIn("lowcase", json.dumps(marked["cats"]))
        self.assertNotIn("zxqw", json.dumps(marked["buddies"]))
        self.assertNotIn("café", json.dumps(marked["mosaic"]))
        self.assertNotIn("Apes_1", json.dumps(marked["crew"]))
        self.assertNotIn("@abc", json.dumps(marked["Dots"]))
        keeper = next(row for row in doc["rows"] if row["name"] == "somehandle club")
        self.assertEqual(keeper["image"], "https://cdn.example/somehandle-logo.png")
        self.assertIn("somehandle", keeper["name"])
        sol = next(row for row in doc["rows"] if row["name"] == "SOL #1")
        self.assertEqual(sol["chain"], "SOL")
        self.assertEqual(sol["price_unit"], "SOL")
        self.assertEqual(sol["size_unit"], "SOL")
        blob = json.dumps(doc)
        self.assertNotIn("companions-by-somehandle", blob)
        self.assertNotIn("cats_by_lowcase", blob)
        self.assertNotIn("mosaic-by-", blob)
        self.assertNotIn("Apes_1", blob)
        self.assertIn("somehandle-logo", blob)
        self.assertIn("SOL #1", blob)

    def test_snapshot_error_skips_the_file(self):
        import importlib.util
        import pathlib
        import tempfile
        root = os.path.join(os.path.dirname(__file__), "..", "..")
        spec = importlib.util.spec_from_file_location("build_site", os.path.join(root, "scripts", "build-site.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        import scripts.radar_snapshot as snap
        original = snap.write_snapshot

        def boom(dest, now=None):
            pathlib.Path(dest).write_text("{", encoding="utf-8")
            raise RuntimeError("boom")

        snap.write_snapshot = boom
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = pathlib.Path(tmp)
                mod.publish_radar(dest)
                self.assertFalse((dest / "radar.json").exists())
                self.assertTrue((dest / "radar.html").is_file())
        finally:
            snap.write_snapshot = original


if __name__ == "__main__":
    unittest.main()
