"""G8 authority sample. Offline. The report does not change a rule."""
import json, os, sys, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "solana-desk"))

import g8_authorities as g8
from desk import config as C, v2log

NOW = 1_800_000_000
POOL = "1" * 32


def mint(i):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    return "Mint" + alphabet[i] + ("1" * 27) + "pump"


def pair(token, *, age_s=8 * 3600, sol=80, sells=2, buys=1):
    return {
        "chainId": "solana", "dexId": "pumpswap", "pairAddress": POOL,
        "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
        "baseToken": {"address": token},
        "pairCreatedAt": int((NOW - age_s) * 1000),
        "liquidity": {"quote": sol, "base": 1_000_000},
        "txns": {"h1": {"buys": buys, "sells": sells}},
    }


def rc(token, *, mint_auth=None):
    return {
        "mintAuthority": mint_auth, "freezeAuthority": None, "creator": "C" * 32,
        "topHolders": [{"pct": 4, "owner": "H" * 32, "address": "A" * 32}],
        "knownAccounts": {}, "score_normalised": 3,
    }


def account(mint_auth, freeze_auth):
    return {"value": {"data": {"parsed": {"type": "mint", "info": {
        "mintAuthority": mint_auth, "freezeAuthority": freeze_auth, "supply": "1", "decimals": 6,
    }}}}}


class Sample(unittest.TestCase):
    def test_samples_passers_and_reports_status_without_pubkeys(self):
        tokens = [mint(i) for i in range(25)]
        # First five are too young. The next 20 pass the other S2 rules. One authority is set.
        profiles = [{"chainId": "solana", "tokenAddress": token} for token in tokens]
        set_key = "Auth" + "1" * 30

        def get(url, headers=None):
            if url.endswith("/token-boosts/top/v1") or url.endswith("/token-boosts/latest/v1"):
                return []
            if url.endswith("/token-profiles/latest/v1"):
                return profiles
            if url.endswith("/latest/dex/search?q=pump"):
                return {"pairs": []}
            if "/latest/dex/tokens/" in url:
                token = url.rstrip("/").split("/")[-1]
                i = tokens.index(token)
                age = 60 if i < 5 else 8 * 3600
                return {"pairs": [pair(token, age_s=age)]}
            if "rugcheck" in url:
                token = url.split("/tokens/")[1].split("/report")[0]
                auth = set_key if token == tokens[5] else None
                return rc(token, mint_auth=auth)
            raise AssertionError(url)

        rpc_calls = []

        def rpc(token):
            rpc_calls.append(token)
            if token == tokens[5]:
                return account(set_key, None)
            return account(None, None)

        report = g8.sample(get, rpc, NOW, want=20, inspect_cap=80, sleep=lambda _s: None)
        self.assertEqual(report["sampled"], 20)
        self.assertEqual(report["rpc_calls"], 20)
        self.assertEqual(len(rpc_calls), 20)
        self.assertEqual(report["recommendation"], "keep_authority_rule")
        self.assertFalse(report["all_revoked"])
        self.assertFalse(report["authority_rule_changed"])
        self.assertTrue(C.FROZEN)
        self.assertEqual(C.CFG_VERSION, "v1-v6.5")
        text = json.dumps(report)
        self.assertNotIn(set_key, text)
        self.assertNotIn("C" * 32, text)
        flagged = next(row for row in report["rows"] if row["mint"] == tokens[5])
        self.assertEqual(flagged["mint_authority"], "set")
        self.assertEqual(flagged["freeze_authority"], "revoked")
        self.assertTrue(all(row["mint_authority"] == "revoked" for row in report["rows"] if row["mint"] != tokens[5]))

    def test_all_revoked_recommends_dropping_the_rule_and_does_not_drop_it(self):
        token = mint(1)
        def get(url, headers=None):
            if url.endswith("/token-profiles/latest/v1"):
                return [{"chainId": "solana", "tokenAddress": token}]
            if url.endswith("/token-boosts/top/v1") or url.endswith("/token-boosts/latest/v1") or url.endswith("q=pump"):
                return []
            if "/latest/dex/tokens/" in url:
                return {"pairs": [pair(token)]}
            if "rugcheck" in url:
                return rc(token)
            raise AssertionError(url)
        report = g8.sample(get, lambda _m: account(None, None), NOW, want=1, inspect_cap=5, sleep=lambda _s: None)
        self.assertEqual(report["recommendation"], "drop_authority_rule")
        self.assertTrue(report["all_revoked"])
        self.assertFalse(report["authority_rule_changed"])
        self.assertTrue(v2log.RULE_IDS[-1] == "authorities")

    def test_a_short_sample_is_incomplete(self):
        def get(url, headers=None):
            if "token-profiles" in url or "token-boosts" in url or "search" in url:
                return []
            raise AssertionError(url)
        report = g8.sample(get, lambda _m: account(None, None), NOW, want=20, inspect_cap=5, sleep=lambda _s: None)
        self.assertEqual(report["sampled"], 0)
        self.assertEqual(report["recommendation"], "incomplete")
        self.assertFalse(report["authority_rule_changed"])

    def test_authority_parser_hides_the_pubkey(self):
        key = "Auth" + "2" * 30
        status = g8.authority_status(account(key, key))
        self.assertEqual(status, {"mint_authority": "set", "freeze_authority": "set"})
        self.assertEqual(g8.authority_status({"value": None})["mint_authority"], "unknown")
        missing = {"value": {"data": {"parsed": {"type": "mint", "info": {"supply": "1", "decimals": 6}}}}}
        self.assertEqual(g8.authority_status(missing), {"mint_authority": "unknown", "freeze_authority": "unknown"})
        self.assertEqual(v2log.authority_status({}, "mintAuthority"), "unknown")
        self.assertEqual(v2log.authority_status({"mintAuthority": None}, "mintAuthority"), "revoked")


if __name__ == "__main__":
    unittest.main()
