"""Fetch failures log a short reason. The skip decision stays the one main already made."""
import csv, io, json, os, tempfile, unittest, urllib.error
from desk import ledger, run, skips

FX = os.path.join(os.path.dirname(__file__), "fixtures")
NOW = 1793383000  # 2026-10-30T17:56:40Z; +1800 stays on that UTC day
MINT = "MintFail1111111111111111111111111111111111"
DECISION = ("result", "skip_type", "reason", "error_src", "mint", "source", "task")


def _load(name):
    with open(os.path.join(FX, name)) as fh:
        return json.load(fh)


PAIRS = {p["baseToken"]["address"][:6]: p for p in _load("dex_pairs.json")}
PAIR = dict(PAIRS["DUxnHr"])
RC = _load("rugcheck_full_DUxnHr.json")
QUOTE = _load("jup_lite_quote.json")


def _http(code, body=b""):
    return urllib.error.HTTPError("https://api.example.test/x", code, "x", {}, io.BytesIO(body))


def _rows(d):
    with open(os.path.join(d, "tasks.csv"), newline="") as fh:
        return list(csv.DictReader(fh))


def _scan(rug_exc=None, dex_exc=None, jup_exc=None, jup_then=None, now=NOW, data_dir=None, sleep=None):
    """One lead. jup_then is returned after the first Jupiter error (the existing retry)."""
    calls, sleeps = [], []
    jup_n = {"n": 0}

    def get(url, headers=None):
        calls.append(url)
        if "token-profiles" in url or "token-boosts" in url:
            return [{"chainId": "solana", "tokenAddress": MINT}]
        if "/latest/dex/tokens/" in url:
            if dex_exc:
                raise dex_exc
            return {"pairs": [PAIR]}
        if "rugcheck" in url:
            if rug_exc:
                raise rug_exc
            return RC
        if "jup.ag" in url:
            jup_n["n"] += 1
            if jup_exc and (jup_then is None or jup_n["n"] == 1):
                raise jup_exc
            return jup_then if jup_then is not None else QUOTE
        raise AssertionError(url)

    def _sleep(s):
        sleeps.append(s)

    d = data_dir or tempfile.mkdtemp()
    h = run.scan(d, get, key=None, now=now, mode="scan", sleep=sleep or _sleep)
    return d, h, _rows(d), calls, sleeps


class FetchFailReason(unittest.TestCase):
    def test_rugcheck_429_and_timeout_match_main_decision(self):
        _, h_main, rows_main, calls_main, sleeps_main = _scan(rug_exc=RuntimeError("down"))
        d429, h429, rows429, calls429, sleeps429 = _scan(rug_exc=_http(429))
        d_to, h_to, rows_to, calls_to, sleeps_to = _scan(rug_exc=TimeoutError("timed out"))
        self.assertEqual(len(rows429), 1)
        self.assertEqual(rows429[0]["fail_reason"], "rugcheck:429")
        self.assertEqual(rows_to[0]["fail_reason"], "rugcheck:timeout")
        self.assertEqual(rows_main[0]["fail_reason"], "rugcheck:error")
        for rows in (rows429, rows_to, rows_main):
            self.assertEqual(tuple(rows[0][k] for k in DECISION), tuple(rows_main[0][k] for k in DECISION))
            self.assertEqual((rows[0]["skip_type"], rows[0]["reason"]), ("data", "missing_data"))
        want = skips.classify(PAIR, None, QUOTE, NOW, {"rug"})
        self.assertEqual((rows429[0]["skip_type"], rows429[0]["reason"]), want)
        self.assertEqual(calls429, calls_main)
        self.assertEqual(calls_to, calls_main)
        self.assertEqual(sleeps429, [])
        self.assertEqual(sleeps_to, [])
        self.assertEqual(sleeps_main, [])
        for h in (h429, h_to, h_main):
            self.assertEqual(h["data_failure_rate"], h_main["data_failure_rate"])
            self.assertEqual(h["void_day"], h_main["void_day"])
            self.assertEqual(h["error_rate"], h_main["error_rate"])
            self.assertEqual(h["data_fail_by_source"], {"rugcheck": 1, "jupiter": 0, "dexscreener": 0})
        self.assertEqual(h429["data_fail_by_reason"], {"rugcheck:429": 1})
        self.assertEqual(h_to["data_fail_by_reason"], {"rugcheck:timeout": 1})
        with open(os.path.join(d_to, "tasks.csv")) as fh:
            self.assertNotIn("timed out", fh.read())
        with open(os.path.join(d429, "state.json")) as fh:
            st = json.load(fh)
        self.assertNotIn("data_fail_by_source", st)
        self.assertNotIn("fail_reason", st)

    def test_day_counts_reset_on_the_next_utc_day(self):
        d, h1, _, _, _ = _scan(rug_exc=_http(429), now=NOW)
        h2 = _scan(rug_exc=TimeoutError("timed out"), now=NOW + 1800, data_dir=d)[1]
        self.assertEqual(h2["checks"], 2)
        self.assertEqual(h2["data_failure_rate"], 1.0)
        self.assertTrue(h2["void_day"])
        self.assertEqual(h2["data_fail_by_source"], {"rugcheck": 2, "jupiter": 0, "dexscreener": 0})
        self.assertEqual(h2["data_fail_by_reason"], {"rugcheck:429": 1, "rugcheck:timeout": 1})
        # Same 30-min slot as the timeout run: no second task row, counts stay.
        h_dup = _scan(rug_exc=_http(500), now=NOW + 1801, data_dir=d)[1]
        self.assertTrue(h_dup["duplicate"])
        self.assertEqual(h_dup["data_fail_by_source"]["rugcheck"], 2)
        self.assertEqual(len(_rows(d)), 2)
        h3 = _scan(rug_exc=_http(503), now=NOW + 86400, data_dir=d)[1]
        self.assertEqual(h3["checks"], 1)
        self.assertEqual(h3["data_failure_rate"], 1.0)
        self.assertEqual(h3["data_fail_by_source"], {"rugcheck": 1, "jupiter": 0, "dexscreener": 0})
        self.assertEqual(h3["data_fail_by_reason"], {"rugcheck:5xx": 1})
        self.assertEqual(h1["data_fail_by_reason"], {"rugcheck:429": 1})

    def test_prefixes_for_parse_conn_and_5xx(self):
        exc = json.JSONDecodeError("Expecting value", "x", 0)
        self.assertEqual(run.fetch_fail_code(exc), "parse")
        self.assertEqual(run.fetch_fail_code(urllib.error.URLError(TimeoutError("t"))), "timeout")
        self.assertEqual(run.fetch_fail_code(urllib.error.URLError(ConnectionResetError("reset"))), "conn")
        self.assertEqual(run.fetch_fail_code(_http(500)), "5xx")
        self.assertEqual(run.fetch_fail_code(_http(503)), "5xx")
        self.assertEqual(run.fetch_fail_code(_http(404)), "404")
        _, _, rows_main, calls_main, _ = _scan(
            dex_exc=RuntimeError("dex"), rug_exc=RuntimeError("rug"), jup_exc=RuntimeError("jup"))
        _, h, rows, calls, sleeps = _scan(
            dex_exc=exc, rug_exc=_http(502), jup_exc=urllib.error.URLError(ConnectionResetError("reset")))
        self.assertEqual(rows[0]["fail_reason"], "dexscreener:parse;jupiter:conn;rugcheck:5xx")
        self.assertEqual(tuple(rows[0][k] for k in DECISION), tuple(rows_main[0][k] for k in DECISION))
        self.assertEqual(calls, calls_main)
        self.assertEqual(sleeps, [])
        self.assertEqual(h["data_fail_by_source"], {"rugcheck": 1, "jupiter": 1, "dexscreener": 1})
        self.assertEqual(h["data_fail_by_reason"], {
            "dexscreener:parse": 1, "jupiter:conn": 1, "rugcheck:5xx": 1})
        self.assertEqual(h["data_failure_rate"], 1.0)

    def test_jupiter_retry_is_unchanged_and_a_saved_quote_is_not_a_failure(self):
        _, h_ok, rows_ok, calls_ok, sleeps_ok = _scan(jup_exc=_http(429), jup_then=QUOTE)
        self.assertEqual(sleeps_ok, [run.RETRY_WAIT_S])
        self.assertEqual(sum(1 for u in calls_ok if "jup.ag" in u), 2)
        self.assertEqual(rows_ok[0]["fail_reason"], "")
        self.assertEqual(rows_ok[0]["error_src"], "")
        want = skips.classify(PAIR, RC, QUOTE, NOW, set())
        self.assertEqual((rows_ok[0]["skip_type"], rows_ok[0]["reason"]), want)
        self.assertEqual(h_ok["data_fail_by_source"], {"rugcheck": 0, "jupiter": 0, "dexscreener": 0})
        self.assertEqual(h_ok["data_fail_by_reason"], {})
        _, h_bad, rows_bad, calls_bad, sleeps_bad = _scan(jup_exc=_http(429))
        _, _, rows_main, calls_main, _ = _scan(jup_exc=RuntimeError("down"))
        self.assertEqual(sum(1 for u in calls_bad if "jup.ag" in u), 2)
        self.assertEqual(sleeps_bad, [run.RETRY_WAIT_S])
        self.assertEqual(sum(1 for u in calls_main if "jup.ag" in u), 1)
        self.assertEqual(rows_bad[0]["fail_reason"], "jupiter:429")
        self.assertEqual(tuple(rows_bad[0][k] for k in DECISION), tuple(rows_main[0][k] for k in DECISION))
        self.assertEqual((rows_bad[0]["skip_type"], rows_bad[0]["reason"]), ("data", "quote_error"))
        self.assertEqual(h_bad["data_fail_by_reason"], {"jupiter:429": 1})
        # A 400 no-route is still an execution skip, not a logged fetch failure.
        body = b'{"errorCode":"COULD_NOT_FIND_ANY_ROUTE"}'
        _, h_nr, rows_nr, calls_nr, sleeps_nr = _scan(jup_exc=_http(400, body))
        self.assertEqual(sleeps_nr, [])
        self.assertEqual(sum(1 for u in calls_nr if "jup.ag" in u), 1)
        self.assertEqual(rows_nr[0]["fail_reason"], "")
        self.assertEqual(rows_nr[0]["error_src"], "")
        self.assertEqual((rows_nr[0]["skip_type"], rows_nr[0]["reason"]), skips.classify(PAIR, RC, {}, NOW, set()))
        self.assertEqual(h_nr["data_fail_by_source"]["jupiter"], 0)
        self.assertEqual(h_nr["data_failure_rate"], 0.0)

    def test_header_upgrade_keeps_error_src_and_blanks_fail_reason(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "tasks.csv")
        old = [c for c in ledger.TASK_COLS if c != "fail_reason"]
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(old)
            w.writerow(["1"] * len(old))
        ledger.task(d, dict(time=2, result="skip", error_src="jupiter", fail_reason="jupiter:timeout"))
        rows = _rows(d)
        self.assertEqual(list(rows[0].keys()), ledger.TASK_COLS)
        self.assertEqual(rows[0]["error_src"], "1")
        self.assertEqual(rows[0]["fail_reason"], "")
        self.assertEqual(rows[1]["fail_reason"], "jupiter:timeout")


if __name__ == "__main__":
    unittest.main()
