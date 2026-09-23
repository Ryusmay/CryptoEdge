# -*- coding: utf-8 -*-
"""Odcisk configu ma zalezec od kodu, nie od maszyny.

Hash liczony razem z kluczami z .env byl inny u autora i inny w CI, wiec
kazda bramka charakteryzacyjna byla w CI czerwona z samej zasady - takze
wtedy, gdy zachowanie bylo identyczne.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import config  # noqa: E402
import parity  # noqa: E402

SECRETS = ("BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BLOFIN_API_PASSPHRASE",
           "COINGECKO_API_KEY", "COINMARKETCAP_API_KEY", "ENGINE_API_TOKEN")


class TestConfigFingerprint(unittest.TestCase):

    def setUp(self):
        self._saved = {k: getattr(config, k) for k in SECRETS + ("HEADLESS", "MIN_SIGNAL_STRENGTH")}

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)

    def test_secrets_are_recognised_as_environment(self):
        env = parity._env_names()
        for name in SECRETS + ("HEADLESS",):
            self.assertIn(name, env)

    def test_only_environment_is_excluded(self):
        """Wyrazenie nie moze polknac zwyklego progu - wtedy hash stracilby zeby."""
        env = parity._env_names()
        self.assertEqual(len(env), 7, sorted(env))
        self.assertNotIn("MIN_SIGNAL_STRENGTH", env)

    def test_hash_is_the_same_with_and_without_env_values(self):
        clean = parity.config_fingerprint()["hash"]
        for name in SECRETS:
            setattr(config, name, "x" * 32)
        config.HEADLESS = not config.HEADLESS
        self.assertEqual(parity.config_fingerprint()["hash"], clean)

    def test_a_real_threshold_still_moves_the_hash(self):
        clean = parity.config_fingerprint()["hash"]
        config.MIN_SIGNAL_STRENGTH = config.MIN_SIGNAL_STRENGTH + 0.01
        self.assertNotEqual(parity.config_fingerprint()["hash"], clean)


if __name__ == "__main__":
    unittest.main()
