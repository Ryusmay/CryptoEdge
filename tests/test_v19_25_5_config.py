# ============================================================
# v19.25.5 — martwy config: podpiąć albo usunąć
# ============================================================

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import config
from daytrading_engine_v2 import klines_stale_reason
from funding_model import normalize_interval_hours
from runtime import BotRuntime


DELETED = (
    "UNIVERSE_MODE",
    "STALE_POSITION_MINUTES",
    "STALE_POSITION_MIN_PNL_PCT",
    "CLOSE_ONLY_MAX_TP",
    "MIN_SIGNAL_STRENGTH_TREND",
    "DAYTRADING_TIMING_REQUIRE_ST",
    "DAYTRADING_TIMING_REQUIRE_MACD",
    "REVERSAL_SL_PCT",
    "REVERSAL_TP_PCT",
    "REVERSAL_MULTI_TP",
    "ACCOUNTING_DECIMAL",
    "MIN_ORDERBOOK_DEPTH",
    "REGIME_TREND_ADX_PROXY",
    "CONTROL_PAUSE_FILE",
    "CONTROL_CLOSE_ALL_FILE",
    "CONTROL_RESUME_FILE",
    "REVERSAL_LIVE_EXECUTION_ENABLED",
)

WIRED = (
    "LIVE_SYNC_BALANCE",
    "RECONCILE_EVERY_CYCLES",
    "FILTER_UNIVERSE_BY_REGISTRY",
    "STALE_KLINES_SECONDS",
    "LOG_FILE",
    "SIGNALS_FILE",
    "STATE_FILE",
    "REGIME_PANIC_SIZE_MULT",
    "REGIME_PANIC_TREND_SIZE_MULT",
    "ORDER_WAIT_FILL_SECONDS",
    "ENTRY_DELAY_SECONDS",
    "STRATEGY_AGG_4H_FROM_1H",
    "FUNDING_PERIOD_HOURS",
    "RECOVERY_REATTACH_EXCHANGE_SL",
    "TOP_N_FETCH",
    "REGIME_ATR_PERIOD",
    "REGIME_ATR_MA",
    "REVERSAL_TP_R_MULT",
    "BLOFIN_IPV4_ONLY",
    "BLOFIN_WAF_BROWSER_HEADERS",
    "DAYTRADING_V2_IMPULSE_MAX_AGE_BARS",
    "DAYTRADING_V2_FIB_ZONE_NEAR",
    "DAYTRADING_V2_FIB_ZONE_FAR",
    "DAYTRADING_V2_FIB_RECLAIM",
    "DAYTRADING_V2_15M_LOOKBACK",
    "DAYTRADING_V2_MAX_ENTRIES_PER_SWING",
    "DAYTRADING_V2_ALLOW_ADDON",
    "DAYTRADING_V2_LOSS_STREAK_PAUSE_N",
    "DAYTRADING_V2_LOSS_STREAK_PAUSE_MIN",
    "DAYTRADING_V2_BE_AFTER_TP2",
    "DAYTRADING_V2_4H_OPPOSE_SIZE_MULT",
    "DAYTRADING_V2_SL_RATCHET_AFTER",
    "DAYTRADING_V2_CHANDELIER_ATR_MULT",
    "DAYTRADING_V2_WF_TRAIN_DAYS",
    "DAYTRADING_V2_WF_TEST_DAYS",
    "DAYTRADING_V2_WF_PURGE_HOURS",
    "DAYTRADING_V2_WF_EMBARGO_HOURS",
    "DAYTRADING_V2_PROFILE_MAJOR_TOP_N",
    "DAYTRADING_V2_ALT_SWING_MIN_MOVE_ATR",
    "DAYTRADING_V2_ALT_SKIP_RANGE",
    "DAYTRADING_V2_ALT_MARGIN_PCT",
    "DAYTRADING_V2_METAL_USE_4H_CONTEXT",
    "DAYTRADING_V2_FUNDING_SKIP_EXTREME",
    "DAYTRADING_V2_SLIP_ALT",
    "DAYTRADING_V2_SLIP_MAJOR",
    "DAYTRADING_V2_EXIT_ON_HTF_REVERSAL",
    "DAYTRADING_V2_15M_RECLAIM_BARS",
    "DAYTRADING_V2_METAL_TRADE",
    "DAYTRADING_V2_SL_ATR_BUFFER",
    "DAYTRADING_V2_ENTRY_SL",
    "DAYTRADING_V2_BE_AFTER_TP1",
    "DAYTRADING_V2_TP1_R",
    "DAYTRADING_V2_TP2_R_FALLBACK",
    "DAYTRADING_V2_HARD_TIME_STOP_HOURS",
    "DAYTRADING_V2_TIME_STOP_HOURS",
    "DAYTRADING_V2_TIME_STOP_MIN_R",
    "DAYTRADING_V2_UNCLOG_SKIP_MFE_R",
    "DAYTRADING_V2_LIMIT_IN_ZONE",
    "DAYTRADING_V2_LIMIT_TIMEOUT_15M_BARS",
)


class TestDeadFlagsRemoved(unittest.TestCase):
    def test_legacy_flags_deleted(self):
        for name in DELETED:
            self.assertFalse(hasattr(config, name), name)

    def test_wired_flags_exist(self):
        for name in WIRED:
            self.assertTrue(hasattr(config, name), name)

    def test_fib_confluence_module_gone(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "fib_confluence.py").exists())
        self.assertNotIn("fib_confluence", (root / "METHODOLOGY.md").read_text(encoding="utf-8"))


class TestKlinesStale(unittest.TestCase):
    def test_missing_timestamps_do_not_reject(self):
        frames = {"4H": {"closes": [1]}, "1H": {"closes": [1]}, "15m": {"closes": [1]}}
        self.assertIsNone(klines_stale_reason(frames, now_ts=1_000_000))

    def test_fresh_15m_passes(self):
        now = 1_700_000_000.0
        frames = {"15m": {"timestamps": [(now - 60) * 1000]}}
        with patch.object(config, "STALE_KLINES_SECONDS", 600):
            self.assertIsNone(klines_stale_reason(frames, now_ts=now))

    def test_stale_15m_rejects(self):
        now = 1_700_000_000.0
        # 15m: 2*bar + 600s = 2400s; age 4000s is stale
        frames = {"15m": {"timestamps": [(now - 4000) * 1000]}}
        with patch.object(config, "STALE_KLINES_SECONDS", 600):
            reason = klines_stale_reason(frames, now_ts=now)
        self.assertIsNotNone(reason)
        self.assertIn("V2_STALE_KLINES_15m", reason)

    def test_closed_4h_18min_into_next_bar_is_fresh(self):
        # 23.08 log: last closed 4H open 20:00, now 00:18 → 15480s.
        # Stary próg bar+10min = 15000s → fałszywy STALE.
        now = 1_700_000_000.0
        last_open = now - 15480
        frames = {
            "4H": {"timestamps": [last_open * 1000]},
            "1H": {"timestamps": [(now - 1800) * 1000]},
            "15m": {"timestamps": [(now - 200) * 1000]},
        }
        with patch.object(config, "STALE_KLINES_SECONDS", 600):
            self.assertIsNone(klines_stale_reason(frames, now_ts=now))

    def test_4h_stale_only_after_missing_next_close(self):
        now = 1_700_000_000.0
        # 2*14400+600 = 29400s. 30000s = naprawdę brakuje zamkniętej 4H.
        frames = {"4H": {"timestamps": [(now - 30000) * 1000]}}
        with patch.object(config, "STALE_KLINES_SECONDS", 600):
            reason = klines_stale_reason(frames, now_ts=now)
        self.assertIsNotNone(reason)
        self.assertIn("V2_STALE_KLINES_4H", reason)

    def test_zero_slack_disables(self):
        now = 1_700_000_000.0
        frames = {"15m": {"timestamps": [(now - 4000) * 1000]}}
        with patch.object(config, "STALE_KLINES_SECONDS", 0):
            self.assertIsNone(klines_stale_reason(frames, now_ts=now))


class TestControlFiles(unittest.TestCase):
    def test_pause_resume_close_all_files(self):
        rt = BotRuntime()
        rt.risk = SimpleNamespace(paused=False, is_halted=False, halt_reason=None)
        closed = {"n": 0}

        class Lock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        trader = SimpleNamespace(positions=["x"], lock=Lock())
        trader.close_all = lambda *a, **k: closed.__setitem__("n", closed["n"] + 1)
        rt.trader = trader
        rt.last_price_map = {}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "PAUSE").write_text("", encoding="utf-8")
            notes = rt.apply_control_files(root)
            self.assertTrue(rt.risk.paused)
            self.assertTrue(any("PAUSED" in str(n) for n in notes))

            (root / "RESUME").write_text("", encoding="utf-8")
            notes = rt.apply_control_files(root)
            self.assertFalse(rt.risk.paused)
            self.assertFalse((root / "RESUME").exists())
            self.assertFalse((root / "PAUSE").exists())

            (root / "CLOSE_ALL").write_text("", encoding="utf-8")
            notes = rt.apply_control_files(root)
            self.assertEqual(closed["n"], 1)
            self.assertFalse((root / "CLOSE_ALL").exists())


class TestReconcileEveryCycles(unittest.TestCase):
    def test_skips_paper(self):
        rt = BotRuntime()
        rec = MagicMock()
        rt.reconciler = rec
        rt.trader = SimpleNamespace(positions=[])
        with patch.object(config, "PAPER_TRADING", True), \
             patch.object(config, "RECONCILE_EVERY_CYCLES", 1):
            self.assertIsNone(rt.maybe_reconcile(1))
            rec.reconcile.assert_not_called()

    def test_runs_on_nth_live_cycle(self):
        rt = BotRuntime()
        rec = MagicMock()
        rec.reconcile.return_value = {"in_sync": True}
        rec.summary_text.return_value = "ok"
        rt.reconciler = rec
        rt.trader = SimpleNamespace(positions=[])
        with patch.object(config, "PAPER_TRADING", False), \
             patch.object(config, "RECONCILE_EVERY_CYCLES", 30):
            self.assertIsNone(rt.maybe_reconcile(29))
            rec.reconcile.assert_not_called()
            self.assertIsNotNone(rt.maybe_reconcile(30))
            rec.reconcile.assert_called_once()


class TestFundingPeriod(unittest.TestCase):
    def test_none_uses_config(self):
        with patch.object(config, "FUNDING_PERIOD_HOURS", 4.0):
            self.assertEqual(normalize_interval_hours(None), 4.0)

    def test_bad_string_uses_config(self):
        with patch.object(config, "FUNDING_PERIOD_HOURS", 6.0):
            self.assertEqual(normalize_interval_hours("nope"), 6.0)


class TestRecoveryReattachFlag(unittest.TestCase):
    def test_place_exchange_false_when_flag_off(self):
        from restart_recovery import RestartRecovery
        from protection import ProtectionManager

        pos = SimpleNamespace(
            symbol="SOL", direction="LONG", sl_price=100.0,
            tp_price=150.0, entry_price=120.0, size_contracts=1.0,
        )
        trader = SimpleNamespace(positions=[pos])
        pm = ProtectionManager()
        seen = {}

        orig = pm.attach_protection

        def wrapped(*a, **k):
            seen["place_exchange"] = k.get("place_exchange", True)
            return orig(*a, **k)

        pm.attach_protection = wrapped
        with tempfile.TemporaryDirectory() as td:
            pm._state_path = Path(td) / "protection_state.json"
            with patch.object(config, "RECOVERY_REATTACH_EXCHANGE_SL", False):
                RestartRecovery(
                    risk=SimpleNamespace(is_halted=False, halt_reason=None, paused=False),
                    trader=trader, protection=pm,
                ).run()
        self.assertEqual(seen.get("place_exchange"), False)


class TestStrategyAggFlag(unittest.TestCase):
    def test_app_loop_reads_control_and_reconcile(self):
        src = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("rt.apply_control_files(BASE)", src)
        self.assertIn("rt.maybe_reconcile(cycle)", src)

    def test_proxy_respects_agg_flag(self):
        src = (Path(__file__).resolve().parents[1] / "signal_engine.py").read_text(encoding="utf-8")
        self.assertIn('getattr(config, "STRATEGY_AGG_4H_FROM_1H", True)', src)

    def test_top_n_fetch_used(self):
        src = (Path(__file__).resolve().parents[1] / "data_feeder.py").read_text(encoding="utf-8")
        self.assertIn('getattr(config, "TOP_N_FETCH", 250)', src)
        self.assertIn("_registry_allows", src)
        self.assertIn("FILTER_UNIVERSE_BY_REGISTRY", src)


class TestPanicSizeAlias(unittest.TestCase):
    def test_trend_panic_mult_is_full_size(self):
        self.assertEqual(config.REGIME_PANIC_SIZE_MULT, 1.0)
        self.assertEqual(config.REGIME_PANIC_TREND_SIZE_MULT, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
