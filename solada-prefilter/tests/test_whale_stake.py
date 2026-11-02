"""Lead stake resolution. No network: the private fetch is stubbed."""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import whale_stake as WS  # noqa: E402
from test_whale_ledger import STAKE  # noqa: E402


class ResolveTests(unittest.TestCase):
    def test_env_wins_over_memory(self):
        other = STAKE[:-1] + ("a" if STAKE[-1] != "a" else "b")
        # other may be an invalid checksum; shape is what resolve checks, and STAKE is the env value.
        with mock.patch.dict(os.environ, {"WHALE_STAKE": STAKE, "SIGHTLINE_PRIVATE_TOKEN": ""}):
            self.assertEqual(WS.resolve({"whale": {"stake": other}}), STAKE)

    def test_memory_when_env_empty_does_not_touch_the_network(self):
        with mock.patch.dict(os.environ, {"WHALE_STAKE": "", "SIGHTLINE_PRIVATE_TOKEN": ""}):
            with mock.patch.object(WS, "_from_private", side_effect=AssertionError("network")):
                self.assertEqual(WS.resolve({"whale": {"stake": STAKE, "stake_prefix": "uylu74g0"}}), STAKE)

    def test_private_lead_file_then_roster(self):
        roster = json.dumps({"whales": [{"codename": "Grumpy Walrus", "role": "lead", "wallets": [STAKE]}]})
        with mock.patch.dict(os.environ, {"WHALE_STAKE": "", "SIGHTLINE_PRIVATE_TOKEN": "token"}):
            with mock.patch.object(WS, "_repo_is_private", return_value=True), \
                    mock.patch.object(WS, "_private_repo", return_value="Zirqo-Dev/sightline-private"), \
                    mock.patch.object(WS, "_raw_file", side_effect=[None, roster]):
                self.assertEqual(WS.resolve({"whale": {"stake_prefix": STAKE[6:14]}}), STAKE)

    def test_refuses_a_repo_that_is_not_private(self):
        with mock.patch.dict(os.environ, {"WHALE_STAKE": "", "SIGHTLINE_PRIVATE_TOKEN": "token"}):
            with mock.patch.object(WS, "_repo_is_private", return_value=False), \
                    mock.patch.object(WS, "_private_repo", return_value="Zirqo-Dev/sightline-private"), \
                    mock.patch.object(WS, "_raw_file", side_effect=AssertionError("fetched")):
                self.assertEqual(WS.resolve({}), "")

    def test_committed_config_has_no_stake(self):
        here = os.path.dirname(os.path.abspath(__file__))
        cfg = json.load(open(os.path.join(here, "..", "config.json")))
        self.assertNotIn("stake", cfg["whale"])


if __name__ == "__main__":
    unittest.main()
