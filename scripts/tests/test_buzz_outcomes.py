"""Buzz spike outcomes. Offline. No network."""
import json, os, subprocess, sys, tempfile, unittest, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import buzz_outcomes as buzz

VALID = os.path.join(ROOT, "scripts", "fixtures", "buzz-outcomes.jsonl")
VALID_SUM = os.path.join(ROOT, "scripts", "fixtures", "buzz-outcomes-summary.json")
INVALID = os.path.join(ROOT, "scripts", "fixtures", "buzz-outcomes-invalid.jsonl")
ASOF = "2026-10-29T00:00:00Z"
MINT = "9B5uDwoTPkXXb67vaHiqWmyvWuUEDBMSU6ssZH1Zpump"
UNIT = "ab" * 28  # 56 hex
SHORT = UNIT[:10]


def _row(**over):
    base = {
        "asof": ASOF,
        "ticker": "unknown",
        "chain": "solana",
        "id_short": "9B5u…pump",
        "mentions_1h": 40,
        "mentions_24h": 40,
        "ratio_1h_vs_prev_hr": None,
        "unique_authors": 12,
        "median_followers": 100,
        "bot_like_share": 0.0,
        "authors_over_10k": 1,
        "confidence": "high",
        "alert": False,
        "paper_position": None,
    }
    base.update(over)
    return base


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)


def _desk(history, tasks=None, extra=None):
    d = tempfile.TemporaryDirectory()
    lines = history if isinstance(history, list) else [history]
    with open(os.path.join(d.name, "x_buzz_history.jsonl"), "w", encoding="utf-8") as fh:
        for row in lines:
            fh.write(json.dumps(row) + "\n")
        fh.write("\n")
    mints = [MINT] if tasks is None else tasks
    _write(os.path.join(d.name, "solana-desk", "tasks.csv"), "time,mint\n" + "".join(f"1,{m}\n" for m in mints))
    files = {
        "solada-prefilter/vetoes.json": {"tokens": []},
        "solada-prefilter/kill_status.json": {},
        "solada-prefilter/dev_checks.json": {"entries": []},
        "solada-prefilter/liquidity_moves.json": {"moves": []},
        "solana-desk/paper.json": {"open": [], "closed": []},
        "solada/paper.json": {"open": [], "closed": []},
        "solana-desk/trades.csv": "time,mint,why\n",
    }
    files.update(extra or {})
    for rel, doc in files.items():
        _write(os.path.join(d.name, rel), doc)
    return d


def _market(price, liq=1000, calls=None, created_ms=None):
    def get(url):
        if calls is not None:
            calls.append(url)
        created = created_ms if created_ms is not None else int(buzz.parse_time(ASOF) * 1000) - 3_600_000
        if "geckoterminal" in url:
            return {"data": [{
                "attributes": {
                    "reserve_in_usd": liq,
                    "quote_token_price_usd": 0.5,
                    "base_token_price_quote_token": price,
                    "pool_created_at": "2026-10-28T00:00:00Z",
                },
                "relationships": {"base_token": {"data": {"id": UNIT}}},
            }]}
        if "/cardano/" in url:
            return [{"priceNative": str(price), "liquidity": {"quote": liq}, "pairCreatedAt": created}]
        return {"pairs": [{
            "chainId": "solana", "priceUsd": str(price),
            "liquidity": {"usd": liq, "quote": 10}, "pairCreatedAt": created,
        }]}
    return get


def _run(d, when, price=1.0, calls=None, network=True):
    return buzz.run(
        d.name, buzz.parse_time(when), network=network,
        get=_market(price, calls=calls), sleep=lambda _s: None,
    )


class SpikeRules(unittest.TestCase):
    def test_threshold_and_zero_start(self):
        self.assertEqual(buzz.spike_kind(_row(mentions_1h=20, ratio_1h_vs_prev_hr=3)), "ratio")
        self.assertEqual(buzz.spike_kind(_row(mentions_1h=20, ratio_1h_vs_prev_hr=3.0)), "ratio")
        self.assertIsNone(buzz.spike_kind(_row(mentions_1h=20, ratio_1h_vs_prev_hr=2.99)))
        self.assertIsNone(buzz.spike_kind(_row(mentions_1h=19, ratio_1h_vs_prev_hr=9)))
        self.assertIsNone(buzz.spike_kind(_row(mentions_1h=20, ratio_1h_vs_prev_hr=0)))
        self.assertEqual(buzz.spike_kind(_row(mentions_1h=20, ratio_1h_vs_prev_hr=None)), "zero_start")
        self.assertIsNone(buzz.spike_kind(_row(mentions_1h=19, ratio_1h_vs_prev_hr=None)))

    def test_exclusion_and_author_bands(self):
        self.assertEqual(buzz.exclude_reasons(_row(confidence="low", ticker="WIDGET")), ["low_confidence"])
        self.assertEqual(buzz.exclude_reasons(_row(confidence="high", ticker="NIGHT")), ["collision_ticker"])
        self.assertEqual(
            buzz.exclude_reasons(_row(confidence="low", ticker="min")),
            ["low_confidence", "collision_ticker"],
        )
        self.assertEqual(buzz.exclude_reasons(_row(confidence="medium", ticker="SNEK")), [])
        self.assertEqual(buzz.author_pattern(140, 8), "few_authors")
        self.assertEqual(buzz.author_pattern(80, 9), "few_authors")
        self.assertEqual(buzz.author_pattern(20, 9), "mixed")
        self.assertEqual(buzz.author_pattern(39, 12), "many_authors")
        self.assertEqual(buzz.author_pattern(40, None), "unknown")
        self.assertEqual(buzz.author_pattern(40, 0), "unknown")

    def test_quiet_lines_are_not_stored_and_collision_spikes_are(self):
        rows = [
            _row(mentions_1h=5, ratio_1h_vs_prev_hr=9),
            _row(asof="2026-10-29T01:00:00Z", ticker="NIGHT", chain="cardano", id_short=SHORT,
                 confidence="high", mentions_1h=25, ratio_1h_vs_prev_hr=4, unique_authors=30),
        ]
        d = _desk(rows, tasks=[])
        self.addCleanup(d.cleanup)
        events, summary = _run(d, "2026-10-29T01:10:00Z")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ticker"], "NIGHT")
        self.assertFalse(events[0]["scored"])
        self.assertEqual(events[0]["exclude_reasons"], ["collision_ticker"])
        self.assertEqual(events[0]["spike_kind"], "ratio")
        self.assertEqual(summary["totals"], {"events": 1, "scored": 0, "excluded": 1})
        self.assertEqual(summary["groups"], [])
        self.assertEqual(summary["excluded_reasons"]["collision_ticker"], 1)


class FollowUps(unittest.TestCase):
    def test_windows_one_checkpoint_and_idempotent_price(self):
        d = _desk(_row())
        self.addCleanup(d.cleanup)
        calls = []
        events, _ = _run(d, "2026-10-29T00:30:00Z", price=1.0, calls=calls)
        ev = events[0]
        self.assertEqual(ev["at_spike"]["status"], "filled")
        self.assertEqual(ev["at_spike"]["price"], 1.0)
        self.assertEqual(ev["at_spike"]["price_unit"], "usd")
        self.assertEqual(ev["at_spike"]["liquidity"], 1000)
        self.assertEqual(ev["features"]["token_age_hours"], 1.0)
        self.assertEqual(ev["followups"]["h1"]["status"], "pending")
        self.assertEqual(ev["followups"]["h1"]["reason"], "not due")
        self.assertIsNone(ev["followups"]["h1"]["price"])
        first = open(os.path.join(d.name, "buzz_outcomes.jsonl"), encoding="utf-8").read()
        calls.clear()
        _run(d, "2026-10-29T00:50:00Z", price=9.0, calls=calls)
        self.assertEqual(calls, [])
        second = open(os.path.join(d.name, "buzz_outcomes.jsonl"), encoding="utf-8").read()
        self.assertEqual(first, second)
        ev = json.loads(second)
        self.assertEqual(ev["at_spike"]["price"], 1.0)

        calls.clear()
        events, _ = _run(d, "2026-10-29T01:30:00Z", price=1.5, calls=calls)
        ev = events[0]
        self.assertEqual(ev["at_spike"]["price"], 1.0)
        self.assertEqual(ev["followups"]["h1"]["status"], "filled")
        self.assertEqual(ev["followups"]["h1"]["price"], 1.5)
        self.assertEqual(ev["followups"]["h1"]["price_change_pct"], 50.0)
        self.assertTrue(calls)

    def test_overlap_fills_spike_first_then_h1_on_the_next_run(self):
        d = _desk(_row())
        self.addCleanup(d.cleanup)
        events, _ = _run(d, "2026-10-29T01:30:00Z", price=2)
        ev = events[0]
        self.assertEqual(ev["at_spike"]["status"], "filled")
        self.assertEqual(ev["at_spike"]["price"], 2)
        self.assertEqual(ev["followups"]["h1"]["reason"], "deferred; one checkpoint per run")
        events, _ = _run(d, "2026-10-29T03:00:00Z", price=3)
        ev = events[0]
        self.assertEqual(ev["at_spike"]["price"], 2)
        self.assertEqual(ev["followups"]["h1"]["status"], "filled")
        self.assertEqual(ev["followups"]["h1"]["price"], 3)
        self.assertEqual(ev["followups"]["h1"]["price_change_pct"], 50.0)
        self.assertEqual(ev["followups"]["h1"]["liquidity_change_pct"], 0.0)
        self.assertEqual(ev["followups"]["h6"]["status"], "pending")

    def test_late_first_run_misses_closed_windows_and_does_not_invent_a_baseline(self):
        d = _desk(_row())
        self.addCleanup(d.cleanup)
        events, _ = _run(d, "2026-10-29T07:00:00Z", price=4)
        ev = events[0]
        self.assertEqual(ev["at_spike"]["status"], "missed")
        self.assertIsNone(ev["at_spike"]["price"])
        self.assertEqual(ev["at_spike"]["reason"], "window passed before a run observed it")
        self.assertEqual(ev["followups"]["h1"]["status"], "missed")
        self.assertEqual(ev["followups"]["h6"]["status"], "filled")
        self.assertEqual(ev["followups"]["h6"]["price"], 4)
        self.assertIsNone(ev["followups"]["h6"]["price_change_pct"])
        self.assertEqual(ev["followups"]["h6"]["reason"], "no price at spike")
        self.assertIsNone(ev["flags_at_spike"])
        events, _ = _run(d, "2026-10-29T08:00:00Z", price=99)
        ev = events[0]
        self.assertEqual(ev["followups"]["h6"]["price"], 4)

    def test_h24_window_and_a_run_after_it_closes(self):
        d = _desk(_row())
        self.addCleanup(d.cleanup)
        events, _ = _run(d, "2026-10-30T01:00:00Z", price=1)
        ev = events[0]
        self.assertEqual(ev["followups"]["h24"]["status"], "filled")
        self.assertEqual(ev["followups"]["h6"]["status"], "missed")
        d2 = _desk(_row())
        self.addCleanup(d2.cleanup)
        events, _ = _run(d2, "2026-10-30T07:00:00Z", price=1)
        self.assertEqual(events[0]["followups"]["h24"]["status"], "missed")
        self.assertIsNone(events[0]["followups"]["h24"]["price"])

    def test_unread_market_stays_null_and_retries_without_the_error_text(self):
        d = _desk(_row())
        self.addCleanup(d.cleanup)
        def boom(_url):
            raise urllib.error.URLError("secret-body")
        buzz.run(d.name, buzz.parse_time("2026-10-29T00:20:00Z"), get=boom, sleep=lambda _s: None)
        ev = json.loads(open(os.path.join(d.name, "buzz_outcomes.jsonl"), encoding="utf-8").read())
        self.assertEqual(ev["at_spike"]["status"], "pending")
        self.assertIsNone(ev["at_spike"]["price"])
        self.assertEqual(ev["at_spike"]["reason"], "market unread: unreachable")
        self.assertNotIn("secret-body", json.dumps(ev))
        calls = []
        buzz.run(d.name, buzz.parse_time("2026-10-29T00:40:00Z"), get=_market(2, calls=calls), sleep=lambda _s: None)
        ev = json.loads(open(os.path.join(d.name, "buzz_outcomes.jsonl"), encoding="utf-8").read())
        self.assertEqual(ev["at_spike"]["status"], "filled")
        self.assertEqual(ev["at_spike"]["price"], 2)
        self.assertTrue(calls)

    def test_no_pair_is_a_filled_null_not_a_guess(self):
        d = _desk(_row())
        self.addCleanup(d.cleanup)
        calls = []
        def get(url):
            calls.append(url)
            return {"pairs": None}
        buzz.run(d.name, buzz.parse_time("2026-10-29T00:10:00Z"), get=get, sleep=lambda _s: None)
        ev = json.loads(open(os.path.join(d.name, "buzz_outcomes.jsonl"), encoding="utf-8").read())
        self.assertEqual(ev["at_spike"]["status"], "filled")
        self.assertIsNone(ev["at_spike"]["price"])
        self.assertEqual(ev["at_spike"]["reason"], "no market pair")
        calls.clear()
        buzz.run(d.name, buzz.parse_time("2026-10-29T00:20:00Z"), get=get, sleep=lambda _s: None)
        self.assertEqual(calls, [])

    def test_two_asofs_are_two_events(self):
        d = _desk([
            _row(),
            _row(asof="2026-10-29T06:00:00Z", mentions_1h=30, ratio_1h_vs_prev_hr=4, unique_authors=20),
        ])
        self.addCleanup(d.cleanup)
        events, _ = _run(d, "2026-10-29T06:10:00Z", price=1)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["at_spike"]["status"], "missed")
        self.assertEqual(events[1]["at_spike"]["status"], "filled")
        self.assertEqual(events[1]["spike_kind"], "ratio")
        self.assertEqual(events[1]["author_pattern"], "many_authors")


class TokenIdAndFlags(unittest.TestCase):
    def test_query_tasks_and_ambiguous_short_id(self):
        d = _desk(_row(), tasks=[])
        self.addCleanup(d.cleanup)
        _write(os.path.join(d.name, "x_buzz.json"), {"tokens": [{
            "chain": "solana", "ticker": "unknown", "id_short": "9B5u…pump",
            "query": MINT + " -is:retweet",
        }]})
        events, _ = _run(d, "2026-10-29T00:10:00Z")
        self.assertEqual(events[0]["token_id"], MINT)
        self.assertEqual(events[0]["token_id_source"], "x_buzz_query")

        other = "9B5uAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump"
        d2 = _desk(_row(), tasks=[MINT, other])
        self.addCleanup(d2.cleanup)
        events, _ = buzz.run(d2.name, buzz.parse_time("2026-10-29T00:10:00Z"), network=False, sleep=lambda _s: None)
        self.assertIsNone(events[0]["token_id"])
        self.assertEqual(events[0]["token_id_reason"], "id_short matches more than one token")
        self.assertIsNone(events[0]["at_spike"]["price"])
        self.assertEqual(events[0]["at_spike"]["reason"], "id_short matches more than one token")

    def test_cardano_unit_and_later_flags(self):
        row = _row(
            ticker="WIDGET", chain="cardano", id_short=SHORT, confidence="high",
            mentions_1h=30, mentions_24h=30, ratio_1h_vs_prev_hr=4, unique_authors=20,
        )
        extra = {
            "solada-prefilter/known_units.json": {"units": {"WIDGET": {"unit": UNIT, "name": "WIDGET"}}},
            "solada-prefilter/dev_checks.json": {"entries": [{
                "ticker": "WIDGET", "unit": UNIT, "late_rug": False,
            }]},
            "solada-prefilter/kill_status.json": {"WIDGET": {
                "hit": True, "first_hit_at": "2026-10-29T05:00:00Z",
            }},
            "solada-prefilter/liquidity_moves.json": {"moves": [{
                "time": "2026-10-29T04:00:00Z", "kind": "lp_remove",
                "ticker": "WIDGET", "policy_short": SHORT, "ahead_of_dump": False,
            }]},
        }
        d = _desk(row, tasks=[], extra=extra)
        self.addCleanup(d.cleanup)
        events, _ = _run(d, "2026-10-29T00:10:00Z", price=1.25)
        ev = events[0]
        self.assertEqual(ev["token_id"], UNIT)
        self.assertEqual(ev["token_id_source"], "known_unit")
        self.assertEqual(ev["at_spike"]["price_unit"], "ada")
        self.assertEqual(ev["at_spike"]["source"], "geckoterminal")
        self.assertEqual(ev["at_spike"]["price"], 1.25)
        self.assertFalse(ev["flags_at_spike"]["veto"])
        self.assertFalse(ev["flags_at_spike"]["rug"])
        self.assertTrue(ev["later"]["kill"])
        self.assertTrue(ev["later"]["lp_pull"])
        self.assertFalse(ev["later"]["veto"])
        self.assertFalse(ev["later"]["paper"])

        veto = {"tokens": [{"ticker": "WIDGET", "unit": UNIT, "vetoes": [{"code": "dev_sold"}]}]}
        _write(os.path.join(d.name, "solada-prefilter", "vetoes.json"), veto)
        dev = {"entries": [{"ticker": "WIDGET", "unit": UNIT, "late_rug": True}]}
        _write(os.path.join(d.name, "solada-prefilter", "dev_checks.json"), dev)
        events, _ = _run(d, "2026-10-29T03:00:00Z", price=2.5)
        ev = events[0]
        self.assertEqual(ev["at_spike"]["price"], 1.25)
        self.assertEqual(ev["followups"]["h1"]["price_change_pct"], 100.0)
        self.assertTrue(ev["later"]["veto"])
        self.assertTrue(ev["later"]["rug"])
        self.assertTrue(ev["followups"]["h1"]["flags"]["veto"])
        self.assertTrue(ev["followups"]["h1"]["flags"]["rug"])

    def test_paper_open_after_the_spike_and_an_earlier_kill_is_not_later(self):
        extra = {
            "solana-desk/paper.json": {"open": [{
                "ticker": "NOPE", "mint": MINT, "status": "open",
                "opened_at": "2026-10-29T02:00:00Z",
            }], "closed": [], "books": {"F": {"open": 1, "closed": 2}}},
            "solana-desk/trades.csv": f"time,mint,why\n2026-10-29T04:00:00Z,{MINT},liquidity_pulled\n",
        }
        d = _desk(_row(), extra=extra)
        self.addCleanup(d.cleanup)
        events, _ = _run(d, "2026-10-29T00:10:00Z")
        self.assertTrue(events[0]["later"]["paper"])
        self.assertTrue(events[0]["later"]["lp_pull"])

        early = _row(ticker="WIDGET", chain="cardano", id_short=SHORT, confidence="medium",
                     mentions_1h=22, ratio_1h_vs_prev_hr=5, unique_authors=15)
        extra = {"solada-prefilter/kill_status.json": {"WIDGET": {
            "hit": True, "first_hit_at": "2026-10-28T00:00:00Z",
        }}}
        d2 = _desk(early, tasks=[], extra=extra)
        self.addCleanup(d2.cleanup)
        events, _ = _run(d2, "2026-10-29T00:10:00Z")
        self.assertFalse(events[0]["later"]["kill"])
        self.assertTrue(events[0]["scored"])

    def test_cardano_dex_fallback_when_gecko_is_empty(self):
        def get(url):
            if "geckoterminal" in url:
                return {"data": []}
            return [{"priceNative": "0.4", "liquidity": {"quote": 80}, "pairCreatedAt": 1}]
        reading = buzz.fetch_cardano(UNIT, get, lambda _s: None)
        self.assertEqual(reading["source"], "dexscreener")
        self.assertEqual(reading["unit"], "ada")
        self.assertEqual(reading["price"], 0.4)
        self.assertEqual(reading["liquidity"], 80)


class Summary(unittest.TestCase):
    def _ev(self, change, adverse=False, pattern="many_authors"):
        return {
            "scored": True,
            "author_pattern": pattern,
            "spike_kind": "ratio",
            "chain": "solana",
            "followups": {"h24": {"price_change_pct": change}},
            "later": {
                "veto": False, "rug": adverse, "kill": False, "lp_pull": False,
                "paper": False, "reason": None,
            },
        }

    def test_small_sample_withholds_rates_and_a_full_sample_reports_them(self):
        small = buzz.summarize([self._ev(50), self._ev(1)], "2026-10-29T00:00:00Z")
        row = next(g for g in small["groups"] if g["by"] == "author_pattern")
        self.assertTrue(row["small_sample"])
        self.assertEqual(row["n"], 2)
        self.assertEqual(row["n_h24_up"], 1)
        self.assertIsNone(row["up_24h_rate"])
        self.assertEqual(row["up_24h_reason"], "small_sample")
        self.assertIsNone(row["adverse_rate"])

        events = [self._ev(25 if i < 5 else 10, adverse=(i == 0)) for i in range(8)]
        events.append(dict(self._ev(None), scored=False, exclude_reasons=["low_confidence"]))
        full = buzz.summarize(events, "2026-10-29T00:00:00Z")
        self.assertEqual(full["totals"]["scored"], 8)
        self.assertEqual(full["totals"]["excluded"], 1)
        row = next(g for g in full["groups"] if g["by"] == "chain" and g["pattern"] == "solana")
        self.assertFalse(row["small_sample"])
        self.assertEqual(row["n_h24_priced"], 8)
        self.assertEqual(row["n_h24_up"], 5)
        self.assertEqual(row["up_24h_rate"], 0.625)
        self.assertIsNone(row["up_24h_reason"])
        self.assertEqual(row["n_adverse"], 1)
        self.assertEqual(row["adverse_rate"], 0.125)
        self.assertTrue(full["buy_signal"] is False and full["veto"] is False)


class Guard(unittest.TestCase):
    def test_fixtures_match_schema_and_wording_stays_out_of_the_script(self):
        import jsonschema
        line_v = jsonschema.Draft202012Validator(json.load(open(os.path.join(ROOT, "schema", "buzz-outcomes.schema.json"))))
        sum_v = jsonschema.Draft202012Validator(json.load(open(os.path.join(ROOT, "schema", "buzz-outcomes-summary.schema.json"))))
        rows = [json.loads(line) for line in open(VALID, encoding="utf-8") if line.strip()]
        self.assertGreaterEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(list(line_v.iter_errors(row)), [])
            self.assertEqual(buzz.privacy_problems(row), [])
            self.assertIs(row["buy_signal"], False)
            self.assertIs(row["veto"], False)
        summary = json.load(open(VALID_SUM, encoding="utf-8"))
        self.assertEqual(list(sum_v.iter_errors(summary)), [])
        self.assertTrue(list(line_v.iter_errors(json.loads(open(INVALID, encoding="utf-8").read()))))
        text = open(os.path.join(ROOT, "scripts", "buzz_outcomes.py"), encoding="utf-8").read().lower()
        self.assertNotIn("".join(chr(n) for n in (115, 117, 114, 105)), text)
        self.assertNotIn("".join(chr(n) for n in (102, 111, 117, 110, 100, 101, 114)), text)
        self.assertIn("python3 scripts/buzz_outcomes.py --data-dir", text)

    def test_validate_desks_checks_outcomes_and_privacy(self):
        script = os.path.join(ROOT, "scripts", "validate-desks.py")
        with open(script, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("buzz_outcomes.jsonl", text)
        self.assertIn("scripts/fixtures/buzz-outcomes.jsonl", text)
        self.assertNotIn("buzz-outcomes-invalid.jsonl", text)
        repo = subprocess.run([sys.executable, script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(repo.returncode, 0, repo.stdout + repo.stderr)
        self.assertIn("ok scripts/fixtures/buzz-outcomes.jsonl (buzz-outcomes)", repo.stdout)
        self.assertIn("ok scripts/fixtures/buzz-outcomes-summary.json (buzz-outcomes-summary)", repo.stdout)
        self.assertIn("skip buzz_outcomes.jsonl (not written yet)", repo.stdout)
        self.assertIn("skip buzz_outcomes_summary.json (not written yet)", repo.stdout)

        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "desks.json"), {"desks": []})
            missing = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 0, missing.stdout + missing.stderr)
            self.assertIn("skip buzz_outcomes.jsonl (not written yet)", missing.stdout)

            with open(VALID, encoding="utf-8") as src, open(os.path.join(d, "buzz_outcomes.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(src.read())
            with open(VALID_SUM, encoding="utf-8") as src, open(os.path.join(d, "buzz_outcomes_summary.json"), "w", encoding="utf-8") as fh:
                fh.write(src.read())
            ok = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

            handle = "@" + "alice"
            leaked = [json.loads(line) for line in open(VALID, encoding="utf-8") if line.strip()]
            leaked[0]["at_spike"]["reason"] = "see " + handle
            with open(os.path.join(d, "buzz_outcomes.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(leaked[0]) + "\n")
            rejected = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("privacy", rejected.stdout)
            self.assertNotIn(handle, rejected.stdout)
            self.assertNotIn(handle, rejected.stderr)

            stake = "stake1" + ("a" * 53)
            leaked[0]["at_spike"]["reason"] = stake
            with open(os.path.join(d, "buzz_outcomes.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(leaked[0]) + "\n")
            rejected = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertNotIn(stake, rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
