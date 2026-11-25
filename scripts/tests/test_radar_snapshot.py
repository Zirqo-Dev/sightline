"""Radar snapshot: derived chips, placeholders, and the embargo allowlist."""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.radar_snapshot import FETCH_TIMEOUT_S, _apply_coin_icon, build_from, buy_streak, second_leg

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
        self.assertEqual(cats["name"], "Cash Cats")
        self.assertIsNone(cats["change"])
        self.assertNotIn("hidden_note", doc)
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
        self.assertEqual(eth["name"], "sample-collection")
        self.assertNotIn("hidden_note", doc)
        self.assertNotIn("BASE", [row["chain"] for row in doc["rows"]])

    def test_builder_does_not_name_the_embargoed_log(self):
        path = os.path.join(os.path.dirname(__file__), "..", "radar_snapshot.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotRegex(source, r"https://\S*captures\.jsonl")
        self.assertIn("lst-count-data/status.json", source)
        self.assertIn("status.json only", source)
        self.assertNotIn("def _scrub_row", source)
        self.assertNotIn("def _name_and_slugs", source)
        self.assertNotIn("_TITLE_WORDS", source)
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
        self.assertNotIn("Made by apes", blob)
        self.assertNotIn("Killed by death", blob)
        self.assertNotIn(ETH_ADDR, blob)
        self.assertNotIn(other, blob)
        self.assertEqual(doc["hidden_note"], "6 collections hidden for creator attribution")
        self.assertNotIn("WFit", doc["hidden_note"])
        names = {row["name"] for row in doc["rows"]}
        for gone in ("Companions", "Punks", "Mood", "Made by apes", "Killed by death", "Ghost"):
            self.assertNotIn(gone, names)
        self.assertTrue(all(row["name"] for row in doc["rows"]))
        self.assertEqual(len({row["id"] for row in doc["rows"]}), len(doc["rows"]))
        stood = [row for row in doc["rows"] if row["name"] == "Stand by Me"]
        self.assertEqual(len(stood), 1)
        self.assertEqual(stood[0]["kind"], "coin")
        apes = next(row for row in doc["rows"] if row["name"] == "Bored Apes")
        self.assertEqual(apes["name"], "Bored Apes")
        self.assertIsNone(apes["image"])
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

    def test_creator_credits_are_dropped_and_slugs_stay_apart(self):
        hood = {"generated_at": "2026-11-24T18:00:00Z", "tokens": [{
            "symbol": "somehandle club", "tier": "WATCH", "price_usd": 1, "chg_h24": 1,
            "vol24_usd": 9, "buyers24": 2,
            "image_url": "https://cdn.example/somehandle-logo.png",
        }]}
        discover = {"collections": [
            {"name": "Pixels @x", "slug": "pixels", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 10},
            {"name": "Quotrons (by x)", "slug": "quotrons", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 10},
            {"name": "Crew", "slug": "crew-by-someone", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 10,
             "image_url": "https://cdn.example/crew-by-someone.png"},
            {"name": "Cats", "slug": "cats_by_lowcase", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 10},
            {"name": "Powered by Ethereum", "slug": "powered", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 10},
            {"name": "Art by eta-labs", "slug": "art-by-eta-labs", "floor_price": 1, "floor_symbol": "ETH",
             "volume_24h": 40, "image_url": "https://cdn.example/art-by-eta-labs.png"},
            {"name": "Art by Mike", "slug": "art-mike", "floor_price": 1, "floor_symbol": "ETH", "volume_24h": 10},
            {"name": "companions-by-somehandle", "slug": "companions-by-somehandle", "floor_price": 1,
             "floor_symbol": "ETH", "volume_24h": 90,
             "image_url": "https://cdn.example/companions-by-somehandle/a.png"},
            {"name": "Bored Apes", "slug": "bored-apes", "floor_price": 3, "floor_symbol": "ETH", "volume_24h": 80,
             "image_url": "https://cdn.example/bored-apes.png"},
        ]}
        early = "2026-11-23T12:00:00Z"
        late = "2026-11-24T12:00:00Z"
        later = "2026-11-24T13:00:00Z"

        def eth(slug, name, floor, when):
            return {"chain": "eip155:1", "slug": slug, "name": name, "floor_eth": floor, "snapshot_t": when}

        units = [
            eth("art-by-alice", "art", 10, early),
            eth("art-by-bob", "art", 1, late),
            eth("made-by-hand-club", "made", 8, early),
            eth("made-by-hand-club", "made", 2, late),
            eth("side-by-side", "side-by-side", 1, early),
            eth("step-by-step", "step-by-step", 1, early),
            eth("made", "made", 10, early),
            eth("made", "made", 9, late),
            eth("blue-one", "Blue", 10, early),
            eth("blue-one", "Blue", 8, late),
            eth("blue-two", "Blue", 10, early),
            eth("blue-two", "Blue", 5, later),
        ]
        doc = build_from({"hood": hood, "nft_discover": discover, "units": units}, NOW)
        note = doc["hidden_note"]
        self.assertEqual(note, "13 collections hidden for creator attribution")
        self.assertRegex(note, r"^[1-9]\d* collections hidden for creator attribution$")
        for secret in (
            "@x", "(by x)", "crew-by-someone", "cats_by_lowcase", "eta-labs", "art-by-alice",
            "art-by-bob", "side-by-side", "step-by-step", "somehandle", "Mike", "Ethereum",
        ):
            self.assertNotIn(secret, note)
        blob = json.dumps(doc)
        for secret in (
            "Pixels @x", "Quotrons (by x)", "crew-by-someone", "cats_by_lowcase",
            "Powered by Ethereum", "Art by eta-labs", "art-by-eta-labs", "Art by Mike",
            "companions-by-somehandle", "art-by-alice", "art-by-bob", "made-by-hand-club",
            "side-by-side", "step-by-step", "https://cdn.example/art-by-eta-labs.png",
            "https://cdn.example/companions-by-somehandle/a.png",
        ):
            self.assertNotIn(secret, blob, secret)
        names = [row["name"] for row in doc["rows"]]
        self.assertNotIn("", names)
        self.assertEqual(len({row["id"] for row in doc["rows"]}), len(doc["rows"]))
        self.assertEqual(names.count("made"), 1)
        made = next(row for row in doc["rows"] if row["name"] == "made")
        self.assertEqual(made["id"], "eth-made")
        self.assertEqual(made["change"], -10.0)
        blues = [row for row in doc["rows"] if row["name"] == "Blue"]
        self.assertEqual(sorted(row["change"] for row in blues), [-50.0, -20.0])
        self.assertEqual(len({row["id"] for row in blues}), 2)
        for row in blues:
            self.assertTrue(row["id"].startswith("eth-blue-"))
        self.assertTrue(all(row["name"] == "Blue" for row in blues))
        for row in doc["rows"]:
            self.assertNotEqual(row["change"], -75.0)
            self.assertNotEqual(row["change"], -90.0)
        apes = next(row for row in doc["rows"] if row["name"] == "Bored Apes")
        self.assertIsNone(apes["image"])
        keeper = next(row for row in doc["rows"] if row["kind"] == "coin")
        self.assertEqual(keeper["name"], "somehandle club")
        self.assertEqual(keeper["image"], "https://cdn.example/somehandle-logo.png")

    def test_image_path_credit_hides_the_row(self):
        discover = {"collections": [
            {"name": "Companions", "slug": "companions", "floor_price": 1, "floor_symbol": "ETH",
             "volume_24h": 20, "image_url": "https://cdn.example/Companions-BY-maker/x.png"},
            {"name": "Cats", "slug": "cats", "floor_price": 1, "floor_symbol": "ETH",
             "volume_24h": 15, "image_url": "https://cdn.example/cats_by_maker/a.png"},
            {"name": "Dots", "slug": "dots", "floor_price": 1, "floor_symbol": "ETH",
             "volume_24h": 12, "image_url": "https://cdn.example/pics/%40maker/x.png"},
            {"name": "Plain", "slug": "plain", "floor_price": 1, "floor_symbol": "ETH",
             "volume_24h": 11, "image_url": "https://cdn.example/plain.png?src=companions-by-maker"},
            {"name": "Gallery", "slug": "gallery", "floor_price": 1, "floor_symbol": "ETH",
             "volume_24h": 9, "image_url": "https://cdn.example/gallery.png?w=32"},
            {"name": "Bored Apes", "slug": "bored-apes", "floor_price": 3, "floor_symbol": "ETH",
             "volume_24h": 80, "image_url": "https://cdn.example/bored-apes.png"},
        ]}
        doc = build_from({"nft_discover": discover}, NOW)
        note = doc["hidden_note"]
        self.assertEqual(note, "4 collections hidden for creator attribution")
        for secret in ("maker", "Companions", "cats_by", "@", "bored", "Plain"):
            self.assertNotIn(secret, note)
        blob = json.dumps(doc)
        for secret in ("Companions-BY-maker", "cats_by_maker", "%40maker", "@maker", "Companions", "Cats", "Dots", "Plain"):
            self.assertNotIn(secret, blob)
        names = {row["name"] for row in doc["rows"]}
        self.assertNotIn("Companions", names)
        self.assertNotIn("Cats", names)
        self.assertNotIn("Dots", names)
        self.assertNotIn("Plain", names)
        gallery = next(row for row in doc["rows"] if row["name"] == "Gallery")
        self.assertIsNone(gallery["image"])
        apes = next(row for row in doc["rows"] if row["name"] == "Bored Apes")
        self.assertIsNone(apes["image"])
        self.assertNotIn("https://", blob)

    def test_image_url_marks_hide_the_row_and_queries_are_stripped(self):
        def row(name, slug, image, volume):
            return {"name": name, "slug": slug, "floor_price": 1, "floor_symbol": "ETH",
                    "volume_24h": volume, "image_url": image}

        discover = {"collections": [
            row("Query", "query", "https://cdn.example/pic.png?src=companions-by-maker", 30),
            row("Fragment", "fragment", "https://cdn.example/pic.png#companions-by-maker", 29),
            row("Hosted", "hosted", "https://files.by.cdn.example/pic.png", 28),
            row("Encoded", "encoded", "https://cdn.example/companions%252Dby%252Dmaker/x.png", 27),
            row("Atmark", "atmark", "https://cdn.example/pics/%2540maker/x.png", 26),
            row("Dash", "dash", "https://cdn.example/companions\u2010by\u2010maker/x.png", 25),
            row("Wide", "wide", "https://cdn.example/pics/\uff20maker/x.png", 24),
            row("Dotted", "dotted", "https://cdn.example/companions.by.maker/x.png", 23),
            row("Frame", "frame", "https://cdn.example/_next/image?url=https%3A%2F%2Fcdn.example%2Fpic.png", 22),
            row("Gallery", "gallery", "https://cdn.example/gallery.png?w=32#top", 21),
        ]}
        doc = build_from({"nft_discover": discover}, NOW)
        note = doc["hidden_note"]
        self.assertEqual(note, "8 collections hidden for creator attribution")
        self.assertRegex(note, r"^[1-9]\d* collections hidden for creator attribution$")
        for secret in ("maker", "Query", "Fragment", "files.by", "companions", "@", "Frame", "Gallery"):
            self.assertNotIn(secret, note)
        names = {item["name"] for item in doc["rows"]}
        for gone in ("Query", "Fragment", "Hosted", "Encoded", "Atmark", "Dash", "Wide", "Dotted"):
            self.assertNotIn(gone, names)
        frame = next(item for item in doc["rows"] if item["name"] == "Frame")
        self.assertIsNone(frame["image"])
        gallery = next(item for item in doc["rows"] if item["name"] == "Gallery")
        self.assertIsNone(gallery["image"])
        for item in doc["rows"]:
            if item.get("kind") == "nft":
                self.assertIsNone(item["image"])
        blob = json.dumps(doc)
        for secret in (
            "companions-by-maker", "companions%252Dby", "%2540maker", "files.by.cdn",
            "companions.by.maker", "\uff20maker", "_next/image",
        ):
            self.assertNotIn(secret, blob)
        for item in doc["rows"]:
            if item.get("image"):
                self.assertNotIn("?", item["image"])
                self.assertNotIn("#", item["image"])

    def test_nft_rows_publish_no_image(self):
        hood = {"generated_at": "2026-11-24T18:00:00Z", "tokens": [{
            "symbol": "CAT", "tier": "WATCH", "price_usd": 1, "chg_h24": 1,
            "vol24_usd": 9, "buyers24": 2, "image_url": "https://cdn.example/cat.png",
        }]}
        discover = {"collections": [
            {"name": "Bored Apes", "slug": "bored-apes", "floor_price": 3, "floor_symbol": "ETH",
             "volume_24h": 80, "image_url": "https://cdn.example/bored-apes.png?w=1"},
            {"name": "Plain", "slug": "plain", "floor_price": 1, "floor_symbol": "ETH",
             "volume_24h": 5, "image_url": "https://cdn.example/plain.png?src=name-by-maker"},
        ]}
        units = [{"chain": "eip155:1", "slug": "sample-collection", "floor_eth": 1,
                  "snapshot_t": "2026-11-24T12:00:00Z"}]
        doc = build_from({"hood": hood, "nft_discover": discover, "units": units}, NOW)
        nfts = [row for row in doc["rows"] if row["kind"] == "nft"]
        self.assertTrue(any(row["chain"] == "RH" for row in nfts))
        self.assertTrue(any(row["chain"] == "ETH" for row in nfts))
        for row in nfts:
            self.assertIsNone(row["image"])
        blob = json.dumps(doc)
        self.assertNotIn("bored-apes.png", blob)
        self.assertNotIn("name-by-maker", blob)
        self.assertNotIn("Plain", {row["name"] for row in doc["rows"]})
        cat = next(row for row in doc["rows"] if row["name"] == "CAT")
        self.assertEqual(cat["kind"], "coin")
        self.assertEqual(cat["image"], "https://cdn.example/cat.png")
        self.assertEqual(doc["hidden_note"], "1 collections hidden for creator attribution")
        self.assertNotIn("maker", doc["hidden_note"])

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


    def test_coin_icons_match_ticker_and_chain(self):
        tickers = ["COCK", "SHARKY", "SOLADA", "SER", "CLOVE", "HIVE", "HOWDY", "GOLFY"]
        root = os.path.join(os.path.dirname(__file__), "..", "..")
        with open(os.path.join(root, "dashboard", "icons", "icons.json"), encoding="utf-8") as handle:
            icons = json.load(handle)
        last_run = {"at": "2026-11-25T15:39:46Z", "results": [
            {"ticker": name, "price_ada": 0.01, "vol_h24_ada": 1000 + index, "holders": 10 + index}
            for index, name in enumerate(tickers)
        ]}
        hood = {"generated_at": "2026-11-24T18:00:00Z", "tokens": [{
            "symbol": "COCK", "tier": "WATCH", "price_usd": 1, "chg_h24": 1,
            "vol24_usd": 9, "buyers24": 2, "image_url": "https://cdn.example/cock.png",
        }]}
        discover = {"collections": [{
            "name": "Pudgy Penguins", "slug": "pudgy-penguins", "floor_price": 1,
            "floor_symbol": "ETH", "volume_24h": 3,
            "image_url": "https://cdn.example/pudgy.png",
        }]}
        doc = build_from({"last_run": last_run, "hood": hood, "nft_discover": discover}, NOW)
        ada = [row for row in doc["rows"] if row["chain"] == "ADA"]
        self.assertEqual({row["name"] for row in ada}, set(tickers))
        for row in ada:
            self.assertEqual(icons["coins"][row["name"]]["chain"], "cardano")
            self.assertEqual(row["image"], icons["coins"][row["name"]]["src"])
            self.assertTrue(row["image"].startswith("dashboard/icons/"))
            self.assertNotIn("age", row)
            self.assertIsNone(row["seen_at"])
            self.assertIsNone(row["change"])
            self.assertEqual(row["spark"], [])
            self.assertIn("holders", row["stat"])
            self.assertNotEqual(row["stat"], "Cardano")
        hood_cock = next(row for row in doc["rows"] if row["chain"] == "RH" and row["name"] == "COCK")
        self.assertEqual(hood_cock["image"], "https://cdn.example/cock.png")
        pudgy = next(row for row in doc["rows"] if row["name"] == "Pudgy Penguins")
        self.assertEqual(pudgy["kind"], "nft")
        self.assertIsNone(pudgy["image"])
        blob = json.dumps(doc)
        self.assertNotIn("cdn.example/pudgy", blob)
        self.assertNotIn("dashboard/icons/nft-", blob)

    def test_cardano_seen_at_uses_a_real_first_seen_only(self):
        last_run = {"at": "2026-11-24T17:00:00Z", "results": [
            {"ticker": "OLD", "price_ada": 1, "native_holders": 4, "vol_h24_ada": 9},
            {"ticker": "TRK", "price_ada": 1, "holders": 15, "vol_h24_ada": 9},
            {"ticker": "VOL", "price_ada": 1, "vol_h24_ada": 2500},
            {"ticker": "SIDE", "price_ada": 1, "ada_side": 80},
            {"ticker": "NEW", "price_ada": 1, "vol_h24_ada": 10, "first_seen": "2026-11-01T00:00:00Z"},
            {"ticker": "BARE", "price_ada": 1, "at": "2026-11-20T00:00:00Z"},
        ]}
        doc = build_from({"last_run": last_run}, NOW)
        by_name = {row["name"]: row for row in doc["rows"] if row["chain"] == "ADA"}
        self.assertEqual(by_name["OLD"]["stat"], "4 holders")
        self.assertEqual(by_name["TRK"]["stat"], "15 holders")
        self.assertEqual(by_name["VOL"]["stat"], "2,500 ADA vol")
        self.assertIsNone(by_name["VOL"]["seen_at"])
        self.assertIsNone(by_name["VOL"]["change"])
        self.assertEqual(by_name["VOL"]["spark"], [])
        self.assertNotIn("age", by_name["VOL"])
        self.assertEqual(by_name["SIDE"]["stat"], "ADA side 80")
        self.assertEqual(by_name["NEW"]["seen_at"], "2026-11-01T00:00:00Z")
        self.assertIn("seen", [chip["id"] for chip in by_name["NEW"]["chips"]])
        self.assertIsNone(by_name["BARE"]["seen_at"])
        self.assertNotIn("seen", [chip["id"] for chip in by_name["BARE"]["chips"]])
        self.assertEqual(by_name["BARE"]["stat"], "last-run")
        self.assertNotIn("2026-11-24T17:00:00Z", json.dumps(by_name["BARE"]))
        self.assertNotIn("2026-11-20T00:00:00Z", json.dumps(by_name["BARE"]))

    def test_sol_placeholder_has_no_token_logo(self):
        snaps = [_snap(MINT_A, 1000, 80, 4, 1), _snap(MINT_A, 2800, 120, 4, 1)]
        doc = build_from({"snaps": snaps}, NOW)
        sol = next(row for row in doc["rows"] if row["name"] == "SOL #1")
        self.assertIsNone(sol["image"])
        self.assertNotIn("dashboard/icons/coin-", json.dumps(sol))

    def test_icon_lookup_skips_credited_names_and_prefers_local(self):
        icons = {
            "coins": {
                "COCK": {"chain": "cardano", "src": "dashboard/icons/coin-cock.png"},
                "GHOST": {"chain": "solana", "src": "dashboard/icons/coin-cock.png"},
            },
            "nft_names": {
                "studio (by maker)": "studio",
                "sample (one / two)": "sample",
            },
            "nfts": {
                "studio": {"name": "Studio (by maker)", "src": "dashboard/icons/coin-cock.png"},
                "sample": {"name": "Sample (one / two)", "src": "dashboard/icons/coin-cock.png"},
            },
        }
        local = {"kind": "coin", "name": "COCK", "chain": "ADA", "image": "https://cdn.example/cock.png"}
        _apply_coin_icon(local, icons)
        self.assertEqual(local["image"], "dashboard/icons/coin-cock.png")
        wrong = {"kind": "coin", "name": "COCK", "chain": "RH", "image": "https://cdn.example/cock.png"}
        _apply_coin_icon(wrong, icons)
        self.assertEqual(wrong["image"], "https://cdn.example/cock.png")
        sol = {"kind": "coin", "name": "SOL #3", "chain": "SOL", "image": "dashboard/icons/coin-cock.png"}
        _apply_coin_icon(sol, icons)
        self.assertIsNone(sol["image"])
        named = {"kind": "coin", "name": "GHOST", "chain": "SOL", "image": None}
        _apply_coin_icon(named, icons)
        self.assertIsNone(named["image"])
        nft = {"kind": "nft", "name": "COCK", "chain": "ADA", "image": None}
        _apply_coin_icon(nft, icons)
        self.assertIsNone(nft["image"])
        for name in ("Studio (by maker)", "Sample (one / two)"):
            row = {"kind": "coin", "name": name, "chain": "ADA", "image": None}
            icons["coins"][name.upper()] = {"chain": "cardano", "src": "dashboard/icons/coin-cock.png"}
            _apply_coin_icon(row, icons)
            self.assertIsNone(row["image"])

    def test_real_icons_skip_creator_credited_nft_names(self):
        root = os.path.join(os.path.dirname(__file__), "..", "..")
        with open(os.path.join(root, "dashboard", "icons", "icons.json"), encoding="utf-8") as handle:
            icons = json.load(handle)
        credited = [
            key for key in icons.get("nft_names") or {}
            if isinstance(key, str) and ("/" in key or " by " in key.lower() or "@" in key)
        ]
        self.assertGreaterEqual(len(credited), 1)
        for key in credited:
            row = {"kind": "coin", "name": key, "chain": "ETH", "image": None}
            packed = {
                "coins": {key.upper(): {"chain": "ethereum", "src": "dashboard/icons/coin-cock.png"}},
                "nft_names": {key: icons["nft_names"][key]},
                "nfts": icons.get("nfts") or {},
            }
            _apply_coin_icon(row, packed)
            self.assertIsNone(row["image"])

    def test_same_ticker_on_another_chain_does_not_take_the_icon(self):
        root = os.path.join(os.path.dirname(__file__), "..", "..")
        with open(os.path.join(root, "dashboard", "icons", "icons.json"), encoding="utf-8") as handle:
            icons = json.load(handle)
        entry = icons["coins"]["COCK"]
        self.assertEqual(entry["chain"], "cardano")
        self.assertTrue(str(entry["src"]).startswith("dashboard/icons/"))
        for chain in ("ETH", "BASE"):
            row = {"kind": "coin", "name": "COCK", "chain": chain, "image": None}
            _apply_coin_icon(row)
            self.assertIsNone(row["image"])
            self.assertNotEqual(row.get("image"), entry["src"])
        kept = {"kind": "coin", "name": "COCK", "chain": "ETH", "image": "https://cdn.example/eth-cock.png"}
        _apply_coin_icon(kept)
        self.assertEqual(kept["image"], "https://cdn.example/eth-cock.png")
        ada = {"kind": "coin", "name": "COCK", "chain": "ADA", "image": None}
        _apply_coin_icon(ada)
        self.assertEqual(ada["image"], entry["src"])

    def test_dashboard_icons_are_copied_into_dist(self):
        root = os.path.join(os.path.dirname(__file__), "..", "..")
        with open(os.path.join(root, "scripts", "build-site.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('shutil.copytree(ROOT / "dashboard", DIST / "dashboard")', source)
        self.assertTrue(os.path.isfile(os.path.join(root, "dashboard", "icons", "icons.json")))
        self.assertTrue(os.path.isfile(os.path.join(root, "dashboard", "icons", "chains", "solana.svg")))
        self.assertTrue(os.path.isfile(os.path.join(root, "dashboard", "icons", "chains", "cardano.svg")))


if __name__ == "__main__":
    unittest.main()
