"""Count rules, freeze, missed hours, and the end verdict. No network."""

import base64
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lst_count.gecko import largest_sol_pool, reserve_ok
from lst_count.http import HTTPResult, assert_allowed, with_retries
from lst_count.logic import (
    WINDOW_HOURS,
    Arm,
    build_verdict,
    consider,
    discount_bps,
    previous_missing,
)
from lst_count.quote import buy_url, sell_amount, sell_url, sol_per_lst
from lst_count.rate import read_spl
from lst_count import SPEC_SHA256
from lst_count.run import load_manifest, run_once
from lst_count.speccheck import deviations_path, file_sha256, spec_path
from lst_count.store import read_jsonl

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ROOT = Path(__file__).resolve().parents[3]
SOL = "So11111111111111111111111111111111111111112"
JITOSOL = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
MSOL = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
T0 = datetime(2026, 11, 11, 19, 17, tzinfo=timezone.utc)


def _mint(decimals=9):
    raw = bytearray(82)
    raw[44] = decimals
    raw[45] = 1
    return bytes(raw)


def _acct(raw):
    return {"data": [base64.b64encode(raw).decode(), "base64"]}


def _rpc(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def _quote(out_amount, label="Meteora"):
    body = {
        "outAmount": str(out_amount),
        "priceImpactPct": "0",
        "routePlan": [{"swapInfo": {"label": label}}],
    }
    return json.dumps(body).encode()


def _gecko(mint, reserve="6000000.0"):
    return json.dumps({
        "data": [{
            "attributes": {"reserve_in_usd": reserve, "name": "LST / SOL", "address": "pool"},
            "relationships": {
                "base_token": {"data": {"id": "solana_" + mint}},
                "quote_token": {"data": {"id": "solana_" + SOL}},
            },
        }],
    }).encode()


class FakeTransport:
    def __init__(self, epoch=1083, jito_out=381759336, msol_out=353000000, reserve="6000000.0", jito_pool=None):
        self.calls = []
        self.epoch = epoch
        self.jito_out = jito_out
        self.msol_out = msol_out
        self.reserve = reserve
        self.jito_pool = jito_pool if jito_pool is not None else (FIX / "jitosol_stake_pool.bin").read_bytes()
        self.msol = (FIX / "msol_state.bin").read_bytes()

    def request(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body))
        text = body.decode() if body else ""
        if "getEpochInfo" in text:
            return HTTPResult(200, _rpc({"epoch": self.epoch}), None)
        if "getMultipleAccounts" in text:
            params = json.loads(text)["params"][0]
            values = []
            for address in params:
                if address == "Jito4APyf642JPZPx3hGc6WWJ8zPKtRbRs4P815Awbb":
                    values.append(_acct(self.jito_pool))
                else:
                    values.append(_acct(_mint()))
            return HTTPResult(200, _rpc({"value": values}), None)
        if "getAccountInfo" in text:
            return HTTPResult(200, _rpc({"value": _acct(self.msol)}), None)
        if "geckoterminal" in url:
            mint = url.split("/tokens/")[1].split("/")[0]
            return HTTPResult(200, _gecko(mint, self.reserve), None)
        if "lite-api.jup.ag" in url:
            out = self.jito_out if JITOSOL in url else self.msol_out
            # Sell quotes also carry the LST mint. A sell's output is SOL, so
            # any positive outAmount is enough for the display log.
            if "outputMint=" + SOL in url or url.find("outputMint=" + SOL) >= 0:
                out = 650000000
            return HTTPResult(200, _quote(out), None)
        return HTTPResult(404, b"", None)


def _manifest(*symbols):
    full = load_manifest()
    wanted = set(symbols)
    full["candidates"] = [row for row in full["candidates"] if row["symbol"] in wanted]
    return full


def _run(transport, when, data, manifest):
    return run_once(data, transport=transport, now=when, sleep=lambda _s: None, manifest=manifest)


class QuoteMath(unittest.TestCase):
    def test_probe_buy_is_about_nine_bps_under_the_fixture_rate(self):
        price = sol_per_lst(381759336, 9)
        rate = read_spl((FIX / "jitosol_stake_pool.bin").read_bytes())["rate"]
        d_bps = discount_bps(price, rate)
        self.assertAlmostEqual(float(price), 1.30972566, places=5)
        self.assertGreater(d_bps, 9)
        self.assertLess(d_bps, 10)
        self.assertFalse(d_bps >= 30)

    def test_buy_url_is_exact_in_half_sol_slippage_50(self):
        url = buy_url(SOL, JITOSOL)
        self.assertIn("lite-api.jup.ag/swap/v1/quote", url)
        self.assertIn("amount=500000000", url)
        self.assertIn("slippageBps=50", url)
        self.assertIn("swapMode=ExactIn", url)
        self.assertNotIn("onlyDirectRoutes", url)
        self.assertEqual(sell_amount(9), 500000000)
        sell = sell_url(SOL, JITOSOL, 9)
        self.assertIn("inputMint=" + JITOSOL, sell)
        self.assertIn("outputMint=" + SOL, sell)
        self.assertIn("amount=500000000", sell)


class OnsetRules(unittest.TestCase):
    def test_thresholds_and_hysteresis(self):
        arm = Arm()
        self.assertTrue(arm.armed)
        self.assertFalse(consider(arm, Fraction(299999, 10000), T0))
        self.assertTrue(consider(arm, 30, T0))
        self.assertFalse(arm.armed)
        self.assertFalse(consider(arm, 40, T0 + timedelta(hours=1)))
        self.assertFalse(consider(arm, Fraction(150001, 10000), T0 + timedelta(hours=2)))
        self.assertFalse(consider(arm, 15, T0 + timedelta(hours=3)))
        self.assertTrue(arm.armed)
        self.assertTrue(consider(arm, 30, T0 + timedelta(hours=4)))

    def test_seventy_two_hours_rearms_and_that_hour_can_onset(self):
        arm = Arm()
        self.assertTrue(consider(arm, 40, T0))
        self.assertFalse(consider(arm, 40, T0 + timedelta(hours=71)))
        self.assertTrue(consider(arm, 40, T0 + timedelta(hours=72)))

    def test_gap_flag_needs_a_missing_hour_before_the_onset(self):
        rows = [
            {"symbol": "JitoSOL", "slot": 0, "type": "missing"},
            {"symbol": "JitoSOL", "slot": 1, "type": "capture"},
        ]
        self.assertFalse(previous_missing(rows, "JitoSOL", 0))
        self.assertTrue(previous_missing(rows, "JitoSOL", 1))
        self.assertFalse(previous_missing([{"symbol": "JitoSOL", "slot": 0, "type": "capture"}], "JitoSOL", 1))


class GeckoRule(unittest.TestCase):
    def test_largest_sol_pool_and_the_five_million_line(self):
        payload = json.loads(_gecko(JITOSOL, "4817876.7958"))
        pool = largest_sol_pool(payload, JITOSOL, SOL)
        self.assertEqual(pool["name"], "LST / SOL")
        self.assertFalse(reserve_ok(pool))
        payload["data"].append({
            "attributes": {"reserve_in_usd": "5000000", "name": "LST / SOL", "address": "big"},
            "relationships": {
                "base_token": {"data": {"id": "solana_" + JITOSOL}},
                "quote_token": {"data": {"id": "solana_" + SOL}},
            },
        })
        # A non-SOL pool with a larger reserve is ignored.
        payload["data"].append({
            "attributes": {"reserve_in_usd": "9000000", "name": "LST / USDC", "address": "usdc"},
            "relationships": {
                "base_token": {"data": {"id": "solana_EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}},
                "quote_token": {"data": {"id": "solana_" + JITOSOL}},
            },
        })
        best = largest_sol_pool(payload, JITOSOL, SOL)
        self.assertEqual(best["address"], "big")
        self.assertTrue(reserve_ok(best))


class BannedSource(unittest.TestCase):
    def test_sanctum_sol_value_is_refused(self):
        with self.assertRaises(SystemExit):
            assert_allowed("https://extra-api.sanctum.so/v1/sol-value/current")
        with self.assertRaises(SystemExit):
            assert_allowed("https://example.com/sol-value")

    def test_retries_are_twenty_then_sixty_and_a_4xx_is_final(self):
        sleeps = []

        class Flaky:
            def __init__(self):
                self.n = 0

            def request(self, method, url, body=None, headers=None):
                self.n += 1
                if self.n < 3:
                    return HTTPResult(503, b"", None)
                return HTTPResult(200, b"ok", None)

        flaky = Flaky()
        got = with_retries(lambda: flaky.request("GET", "https://lite-api.jup.ag/x"), lambda s: sleeps.append(s))
        self.assertEqual(got.status, 200)
        self.assertEqual(sleeps, [20, 60])
        sleeps.clear()
        once = with_retries(lambda: HTTPResult(400, b"no", None), lambda s: sleeps.append(s))
        self.assertEqual(once.status, 400)
        self.assertEqual(sleeps, [])


class ManifestRules(unittest.TestCase):
    def test_named_set_and_inf_is_not_a_candidate(self):
        manifest = load_manifest()
        symbols = [row["symbol"] for row in manifest["candidates"]]
        self.assertEqual(symbols, [
            "JitoSOL", "mSOL", "bSOL", "JupSOL", "bbSOL", "hSOL", "vSOL", "jSOL", "laineSOL", "dSOL",
        ])
        self.assertEqual(manifest["candidates"][7]["on_chain_symbol"], "JSOL")
        self.assertEqual(manifest["candidates"][7]["mint"], "7Q2afV64in6N6SeZsAAB81TJzwDoD6zpqmHkzi9Dcavn")
        mints = {row["mint"] for row in manifest["candidates"]}
        self.assertNotIn("5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm", mints)
        self.assertEqual(manifest["excluded_by_rule"][0]["symbol"], "INF")
        files = [
            path for path in (ROOT / "prereg" / "lst_count").rglob("*.py")
            if "tests" not in path.parts
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
        for banned in ("pnl", "p&l", "profit", "sharpe", "backtest", "simulated trade"):
            self.assertNotIn(banned, text.lower())
        self.assertNotIn("import races", text)
        self.assertNotIn("import desk", text)
        self.assertIn('"solana-desk"', text)

    def test_deviations_are_logged_before_any_capture(self):
        text = deviations_path().read_text(encoding="utf-8")
        self.assertIn("before any capture", text)
        self.assertIn("mSOL fee = 0", text)
        self.assertIn("7Q2afV64in6N6SeZsAAB81TJzwDoD6zpqmHkzi9Dcavn", text)
        self.assertIn("exact 72h", text)
        self.assertIn("that hour can onset", text)
        self.assertIn("D < 30 bps", text)
        self.assertIn("finer splits of `rpc_fail`", text)
        self.assertIn("slot hour's UTC date", text)
        self.assertIn("plus any other mint", text)
        self.assertIn("stricter for a PASS", text)
        self.assertIn("no bot reads the onset or d_bps fields", text)
        self.assertIn("status.json only", text)
        self.assertEqual(file_sha256(spec_path()), SPEC_SHA256)
        readme = (deviations_path().parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("if: false", readme)

    def test_workflow_is_hourly_and_maps_the_token(self):
        text = (ROOT / ".github" / "workflows" / "lst-count.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "17 * * * *"', text)
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("\n  pull_request:", text)
        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", text)
        self.assertIn("privacy-guard.sh", text)
        self.assertIn("lst-count-data", text)
        self.assertNotIn("sol-value", text)
        self.assertNotIn("JUPITER_API_KEY", text)
        self.assertNotIn("ref: desk-data", text)


def _rows(data):
    return read_jsonl(Path(data) / "captures.jsonl")


class CaptureRun(unittest.TestCase):
    def test_freeze_then_gap_then_no_backfill_then_verdict(self):
        transport = FakeTransport()
        manifest = _manifest("JitoSOL", "mSOL")
        with tempfile.TemporaryDirectory() as tmp:
            first = _run(transport, T0, tmp, manifest)
            self.assertTrue(first["frozen"])
            self.assertEqual(first["t0"], "2026-11-11T19:00:00Z")
            self.assertEqual(first["window_end"], "2026-11-25T19:00:00Z")
            self.assertNotIn("d_bps", json.dumps(first))
            self.assertNotIn("onset", json.dumps(first))
            self.assertEqual(first["symbols"], ["JitoSOL", "mSOL"])
            self.assertNotIn("verdict", first)
            self.assertNotIn("onsets", first)
            rows = _rows(tmp)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["type"] == "capture" for row in rows))
            self.assertFalse(any(row["onset"] for row in rows))
            jito = next(row for row in rows if row["symbol"] == "JitoSOL")
            self.assertEqual(jito["buy"]["route_labels"], ["Meteora"])
            self.assertEqual(jito["last_update_epoch"], 1083)
            self.assertEqual(jito["sell"]["ok"], True)
            self.assertGreater(float(jito["d_bps"]), 9)
            self.assertLess(float(jito["d_bps"]), 10)
            calls_after_freeze = len(transport.calls)
            # Three hours later: two missed hours per LST are no_run, then one capture.
            _run(transport, T0 + timedelta(hours=3), tmp, manifest)
            rows = _rows(tmp)
            missing = [row for row in rows if row["type"] == "missing"]
            self.assertEqual(len(missing), 4)
            self.assertTrue(all(row["reason"] == "no_run" for row in missing))
            self.assertEqual({row["slot"] for row in missing}, {1, 2})
            self.assertEqual(sum(1 for row in rows if row["type"] == "capture" and row["slot"] == 3), 2)
            # The gap itself was not quoted. One epoch, one batch, one marinade, four quotes.
            new_calls = transport.calls[calls_after_freeze:]
            urls = [url for _method, url, _body in new_calls]
            self.assertEqual(sum(1 for url in urls if "lite-api.jup.ag" in url), 4)
            self.assertEqual(sum(1 for url in urls if "geckoterminal" in url), 0)
            # Same slot again does not fetch or append.
            before = len(rows)
            quoted = len(transport.calls)
            _run(transport, T0 + timedelta(hours=3, minutes=5), tmp, manifest)
            self.assertEqual(len(_rows(tmp)), before)
            self.assertEqual(len(transport.calls), quoted)
            # The window does not grow. Slot 336 is not a capture.
            closed = _run(transport, T0 + timedelta(hours=400), tmp, manifest)
            self.assertIn(closed["verdict"], ("KILL", "BLOCKED", "PASS"))
            self.assertEqual(closed["evidence"], "NONE")
            self.assertNotIn("pnl", json.dumps(closed).lower())
            slots = {row["slot"] for row in _rows(tmp)}
            self.assertLess(max(slots), WINDOW_HOURS)
            self.assertEqual(max(slots), 335)
            again = len(_rows(tmp))
            _run(transport, T0 + timedelta(hours=401), tmp, manifest)
            self.assertEqual(len(_rows(tmp)), again)

    def test_stale_epoch_is_missing_and_a_mint_mismatch_is_not_in_the_set(self):
        manifest = _manifest("JitoSOL", "mSOL")
        with tempfile.TemporaryDirectory() as tmp:
            status = _run(FakeTransport(epoch=1084), T0, tmp, manifest)
            self.assertEqual(status["symbols"], ["JitoSOL", "mSOL"])
            jito = next(row for row in _rows(tmp) if row["symbol"] == "JitoSOL")
            self.assertEqual(jito["type"], "missing")
            self.assertEqual(jito["reason"], "rate_stale")
            msol = next(row for row in _rows(tmp) if row["symbol"] == "mSOL")
            self.assertEqual(msol["type"], "capture")
        broken = bytearray((FIX / "jitosol_stake_pool.bin").read_bytes())
        broken[162:194] = b"\x22" * 32
        with tempfile.TemporaryDirectory() as tmp:
            status = _run(FakeTransport(jito_pool=bytes(broken)), T0, tmp, _manifest("JitoSOL"))
            self.assertFalse(status["frozen"])
            self.assertEqual(status["freeze_log"][0]["reason"], "pool_mint_mismatch")
            self.assertFalse((Path(tmp) / "captures.jsonl").exists())

    def test_a_shared_rpc_failure_does_not_freeze(self):
        class Down(FakeTransport):
            def request(self, method, url, body=None, headers=None):
                self.calls.append((method, url, body))
                return HTTPResult(503, b"", None)

        with tempfile.TemporaryDirectory() as tmp:
            status = _run(Down(), T0, tmp, _manifest("JitoSOL", "mSOL"))
            self.assertFalse(status["frozen"])
            self.assertEqual(status["reason"], "shared_rpc_fail")

    def test_refuses_a_desk_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "desk-data"
            target.mkdir()
            with self.assertRaises(SystemExit):
                run_once(target, transport=FakeTransport(), now=T0, sleep=lambda _s: None, manifest=_manifest("mSOL"))

    def test_jitter_each_later_hour_captures_its_own_slot(self):
        """Freeze at :21. Later :17, :19, :45, and :59 each take their own UTC hour."""
        freeze = datetime(2026, 11, 11, 19, 21, tzinfo=timezone.utc)
        transport = FakeTransport()
        manifest = _manifest("JitoSOL")
        with tempfile.TemporaryDirectory() as tmp:
            first = _run(transport, freeze, tmp, manifest)
            self.assertEqual(first["t0"], "2026-11-11T19:00:00Z")
            self.assertEqual(first["window_end"], "2026-11-25T19:00:00Z")
            same_hour = len(_rows(tmp))
            _run(transport, datetime(2026, 11, 11, 19, 59, tzinfo=timezone.utc), tmp, manifest)
            self.assertEqual(len(_rows(tmp)), same_hour)
            for when in (
                datetime(2026, 11, 11, 20, 17, tzinfo=timezone.utc),
                datetime(2026, 11, 11, 21, 19, tzinfo=timezone.utc),
                datetime(2026, 11, 11, 22, 45, tzinfo=timezone.utc),
                datetime(2026, 11, 11, 23, 59, tzinfo=timezone.utc),
            ):
                _run(transport, when, tmp, manifest)
            captured = [row for row in _rows(tmp) if row["type"] == "capture"]
            self.assertEqual([row["slot"] for row in captured], [0, 1, 2, 3, 4])
            self.assertEqual([row["hour_start"] for row in captured], [
                "2026-11-11T19:00:00Z",
                "2026-11-11T20:00:00Z",
                "2026-11-11T21:00:00Z",
                "2026-11-11T22:00:00Z",
                "2026-11-11T23:00:00Z",
            ])

    def test_onset_after_a_gap_is_flagged(self):
        transport = FakeTransport()
        manifest = _manifest("JitoSOL")
        with tempfile.TemporaryDirectory() as tmp:
            _run(transport, T0, tmp, manifest)
            transport.jito_out = 430000000
            _run(transport, T0 + timedelta(hours=2), tmp, manifest)
            rows = _rows(tmp)
            onset = next(row for row in rows if row["type"] == "capture" and row["slot"] == 2)
            self.assertTrue(onset["onset"])
            self.assertTrue(onset["onset_after_gap"])
            gap = next(row for row in rows if row["slot"] == 1)
            self.assertEqual(gap["reason"], "no_run")


def _capture(symbol, slot, d_bps, t0, fee_num=0, fee_den=1):
    d_bps = Fraction(d_bps)
    price = 1 - (d_bps / 10000)
    hour = (t0 + timedelta(hours=slot)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "type": "capture",
        "symbol": symbol,
        "slot": slot,
        "hour_start": hour,
        "d_bps_num": str(d_bps.numerator),
        "d_bps_den": str(d_bps.denominator),
        "price_num": str(price.numerator),
        "price_den": str(price.denominator),
        "rate_num": "1",
        "rate_den": "1",
        "withdrawal_fees": {
            "sol_withdrawal_fee": {"numerator": fee_num, "denominator": fee_den},
            "stake_withdrawal_fee": {"numerator": fee_num, "denominator": fee_den},
        },
        "onset_after_gap": False,
    }


def _book(onsets, captured, symbols=("JitoSOL",), fee_num=0):
    t0 = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    rows = []
    for symbol in symbols:
        for slot in range(WINDOW_HOURS):
            if slot < captured:
                d_bps = 30 if slot in onsets else 10
                row = _capture(symbol, slot, d_bps, t0, fee_num=fee_num, fee_den=1000)
                if slot - 1 in onsets or (slot > 0 and slot - 1 not in range(captured)):
                    pass
                rows.append(row)
            else:
                rows.append({
                    "type": "missing",
                    "symbol": symbol,
                    "slot": slot,
                    "reason": "no_run",
                    "hour_start": (t0 + timedelta(hours=slot)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
    return rows


class Verdict(unittest.TestCase):
    def test_pass_kill_and_blocked(self):
        passed = build_verdict(_book({0, 24, 48, 96}, 336), ["JitoSOL"])
        self.assertEqual(passed["verdict"], "PASS")
        self.assertEqual(passed["onsets"], 4)
        self.assertGreaterEqual(len(passed["onset_days"]), 3)
        self.assertEqual(passed["evidence"], "NONE")
        killed = build_verdict(_book({0, 24, 48}, 336), ["JitoSOL"])
        self.assertEqual(killed["verdict"], "KILL")
        self.assertEqual(killed["reason"], "onsets < 4")
        one_day = build_verdict(_book({0, 2, 4, 6}, 336), ["JitoSOL"])
        self.assertEqual(one_day["verdict"], "KILL")
        self.assertIn("distinct UTC days", one_day["reason"])
        blocked = build_verdict(_book({0, 24, 48, 96}, 100), ["JitoSOL"])
        self.assertEqual(blocked["verdict"], "BLOCKED")
        self.assertNotEqual(blocked["verdict"], "KILL")

    def test_coverage_line_is_seventy_percent(self):
        under = build_verdict(_book({0, 24, 48, 96}, 235), ["JitoSOL"])
        self.assertEqual(under["verdict"], "BLOCKED")
        exact = build_verdict(_book({0, 24, 48, 96}, 236), ["JitoSOL"])
        self.assertEqual(exact["verdict"], "PASS")

    def test_fee_adjustment_does_not_decide_the_gate(self):
        # 20 bps of fee turns a raw 30 bps onset into about 10 bps. The gate stays on raw D.
        report = build_verdict(_book({0, 24, 48, 96}, 336, fee_num=20), ["JitoSOL"])
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["onset_counts"]["raw"], 4)
        self.assertEqual(report["onset_counts"]["sol_withdrawal_fee"], 0)
        self.assertEqual(report["onset_counts"]["stake_withdrawal_fee"], 0)
        self.assertEqual(
            report["median_resting_definition"],
            "median D over captured hours with D < 30 bps",
        )


if __name__ == "__main__":
    unittest.main()
