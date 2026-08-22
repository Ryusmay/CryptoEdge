import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from daytrading_backtester import ReplayTrade
import historical_replay
from historical_replay import (
    ReplayRequest, _apply_funding, _cache_is_fresh, _metrics, _run_window,
    discover_replay_symbols, run_historical_replay,
)


class TestHistoricalReplay(unittest.TestCase):
    def test_defaults_are_daytrading_oos_profile(self):
        request = ReplayRequest()
        self.assertEqual(("BTC", "ETH", "SOL"), request.symbols)
        self.assertEqual(90, request.days)
        self.assertEqual(0.30, request.oos_fraction)
        self.assertTrue(request.counterfactual_audit)

    def test_metrics_include_drawdown_and_expectancy(self):
        trades = [type("T", (), {"realised_r": value}) for value in (1.0, -0.5, -0.5, 2.0)]
        result = _metrics(trades)
        self.assertEqual(4, result["trades"])
        self.assertAlmostEqual(0.5, result["win_rate"])
        self.assertAlmostEqual(2.0, result["net_r"])
        self.assertAlmostEqual(1.0, result["max_drawdown_r"])

    def test_metrics_breaks_down_by_exit_reason(self):
        # Kluczowe do diagnozy: raport z replaya 90d pokazal katastrofalny
        # win rate (~0.3-0.5%) mimo poprawnej mechaniki SL/TP/trailing
        # (zweryfikowane recznie) - podejrzenie pada na day_setup_invalidated
        # jako dominujacy, zawsze-stratny powod wyjscia w prawdziwych,
        # zaszumionych danych. Ten rozklad pozwala to potwierdzic/wykluczyc
        # bez zgadywania przy kolejnym raporcie.
        trades = [
            type("T", (), {"realised_r": r, "exit_reason": reason})
            for r, reason in [
                (-0.3, "day_setup_invalidated"), (-0.4, "day_setup_invalidated"),
                (-0.2, "day_setup_invalidated"), (1.8, "tp2"), (-1.0, "sl_first_intrabar"),
            ]
        ]
        result = _metrics(trades)
        breakdown = result["exit_reason_breakdown"]
        self.assertEqual({"day_setup_invalidated", "tp2", "sl_first_intrabar"}, set(breakdown))
        dsi = breakdown["day_setup_invalidated"]
        self.assertEqual(3, dsi["n"])
        self.assertEqual(0.0, dsi["win_rate"])
        self.assertAlmostEqual(-0.9, dsi["sum_r"])
        self.assertAlmostEqual(-0.3, dsi["avg_r"])
        self.assertEqual(1, breakdown["tp2"]["n"])
        self.assertEqual(1.0, breakdown["tp2"]["win_rate"])

    def test_positive_funding_costs_long_and_credits_short(self):
        timestamps = [1_000, 2_000, 3_000]
        funding = [{"ts_ms": 2_000, "rate": 0.001}]
        long = ReplayTrade("LONG", 0, 100.0, 99.0, 101.5, 102.2, 0.5, 1.0,
                           exit_i=2, realised_r=0.0)
        short = ReplayTrade("SHORT", 0, 100.0, 101.0, 98.5, 97.8, 0.5, 1.0,
                            exit_i=2, realised_r=0.0)
        _apply_funding({"trades": [long, short]}, timestamps, funding)
        self.assertAlmostEqual(-0.1, long.realised_r)
        self.assertAlmostEqual(0.1, short.realised_r)

    def test_liquid_universe_ranks_quote_volume_and_excludes_stables(self):
        class Feed:
            def fetch_all_tickers(self):
                def row(volume):
                    return {"blofin_quote_volume": volume, "blofin_bid": 99, "blofin_ask": 100}
                return {"ETH": row(20_000_000), "BTC": row(50_000_000),
                        "USDC": row(99_000_000), "TINY": row(100_000)}
        request = ReplayRequest(universe_mode="LIQUID", liquid_limit=2, min_quote_volume=1_000_000)
        symbols, audit = discover_replay_symbols(Feed(), request)
        self.assertEqual(("BTC", "ETH"), symbols)
        self.assertEqual("automatic_universe; manual symbol list is not applied", audit["symbols_policy"])
        self.assertEqual(["BTC", "ETH", "SOL"], audit["ignored_symbols"])
        self.assertEqual("LIQUID", audit["mode"])

    def test_all_universe_rejects_crossed_or_empty_books(self):
        class Feed:
            def fetch_all_tickers(self):
                return {
                    "BTC": {"blofin_quote_volume": 10, "blofin_bid": 99, "blofin_ask": 100},
                    "BAD": {"blofin_quote_volume": 10, "blofin_bid": 101, "blofin_ask": 100},
                }
        symbols, _ = discover_replay_symbols(Feed(), ReplayRequest(universe_mode="ALL"))
        self.assertEqual(("BTC",), symbols)

    def test_market_data_cache_expires(self):
        fresh = {"downloaded_at": datetime.now(timezone.utc).isoformat()}
        stale = {"downloaded_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}
        self.assertTrue(_cache_is_fresh(fresh))
        self.assertFalse(_cache_is_fresh(stale))

    def test_counterfactual_audit_counts_only_single_filter_removal(self):
        payload = {"bundle": {"5m": {
            "opens": [100, 100, 100], "highs": [101, 101, 101],
            "lows": [99, 99, 99], "closes": [100, 100, 100],
            "timestamps": [1, 2, 3],
        }}, "funding": []}

        def provider(_symbol, _bundle, drive_tf="5m", audit_relax=None):
            relaxed = set(audit_relax or ())
            def signal(index):
                reason = "DAY_HTF_CONFLICT" if index == 0 else "DAY_ADX_WEAK(10.0)"
                family = reason.split("(", 1)[0]
                if family in relaxed:
                    return {"direction": "LONG", "price": 100, "sl_price": 99,
                            "tp_plan": {"tp1_r": 1.5, "tp2_r": 2.2, "frac_tp1": 0.5}, "atr": 1}
                return {"direction": "NEUTRAL", "reject_reason": reason}
            return signal

        with patch("historical_replay.production_signal_provider", side_effect=provider):
            result = _run_window("BTC", payload, 0, 3, ReplayRequest(counterfactual_audit=True))
        audit = result["counterfactual_filters"]
        self.assertEqual(1, audit["DAY_HTF_CONFLICT"]["baseline_blocks"])
        self.assertEqual(1, audit["DAY_HTF_CONFLICT"]["passed_full_funnel"])
        self.assertEqual(1, audit["DAY_ADX_WEAK"]["baseline_blocks"])
        self.assertEqual(1, audit["DAY_ADX_WEAK"]["passed_full_funnel"])


def _synthetic_bar(n: int, seed: int) -> dict:
    rnd = random.Random(seed)
    price = 100.0
    opens, highs, lows, closes, volumes, timestamps = [], [], [], [], [], []
    ts = 1_700_000_000
    for i in range(n):
        move = rnd.uniform(-0.6, 0.6)
        o = price
        c = max(1.0, price + move)
        h = max(o, c) + abs(rnd.uniform(0.05, 0.3))
        l = min(o, c) - abs(rnd.uniform(0.05, 0.3))
        opens.append(o); highs.append(h); lows.append(l); closes.append(c)
        volumes.append(1000.0 + rnd.uniform(0, 200))
        timestamps.append(ts + i * 60)
        price = c
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes,
            "volumes": volumes, "timestamps": timestamps}


class FakeFeedForReplay:
    """Feed minimalny do end-to-end testu run_historical_replay - dane
    syntetyczne (losowy walk, stały seed per symbol) o rozmiarze wystarczajacym
    zeby przejsc minimalne wymagania download_bundle dla days=1."""
    _COUNTS = {"5m": 500, "15m": 500, "1h": 250, "4h": 260}

    def __init__(self):
        self.last_error = None

    def fetch_klines_ohlcv(self, symbol: str, bar: str = "5m", limit: int = 120) -> dict:
        n = min(limit, self._COUNTS.get(bar, limit))
        return _synthetic_bar(n, seed=hash((symbol, bar)) % 10_000)

    def fetch_funding_rate_history(self, symbol: str, limit: int = 50):
        return []


class TestHistoricalReplayParallelExecution(unittest.TestCase):
    """Koniec-do-konca test run_historical_replay() z ProcessPoolExecutor -
    zaden z pozostalych testow w tym pliku nie wywoluje calej funkcji, wiec
    to jedyne miejsce, ktore realnie sprawdza rownolegla sciezke (pickling,
    laczenie wynikow, poprawnosc checkpointu/raportu)."""

    def _run_isolated(self, request):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with patch.object(historical_replay, "CACHE_DIR", base / "data" / "replay"), \
                 patch.object(historical_replay, "RESULT_CACHE_DIR", base / "data" / "replay" / "results"), \
                 patch.object(historical_replay, "REPORT_DIR", base / "reports" / "replay"):
                return run_historical_replay(FakeFeedForReplay(), request)

    def test_parallel_replay_reports_live_progress_from_worker_processes(self):
        # Tani test jednostkowy zamiast pelnego e2e (ktory kosztowal ~30s przez
        # narzut multiprocessing.Manager) - sprawdza, ze _run_window realnie
        # wypycha wiadomosci postepu do dowolnego obiektu z .put_nowait()
        # (kolejka wieloprocesowa jest wtedy tylko szczegolem wywolujacego).
        class _FakeQueue:
            def __init__(self):
                self.items = []
            def put_nowait(self, item):
                self.items.append(item)

        payload = {"bundle": {"5m": {
            "opens": [100.0] * 300, "highs": [101.0] * 300,
            "lows": [99.0] * 300, "closes": [100.0] * 300,
            "timestamps": list(range(300)),
        }}, "funding": []}

        def provider(_symbol, _bundle, drive_tf="5m", audit_relax=None):
            return lambda index: {"direction": "NEUTRAL", "reject_reason": "DAY_CHOP(70.0)"}

        queue = _FakeQueue()
        with patch("historical_replay.production_signal_provider", side_effect=provider):
            _run_window("BTC", payload, 0, 300, ReplayRequest(counterfactual_audit=False),
                       progress_queue=queue, phase="IS")
        self.assertTrue(queue.items, "spodziewano sie co najmniej jednej wiadomosci postepu")
        symbols_seen = {item[0] for item in queue.items}
        phases_seen = {item[1] for item in queue.items}
        self.assertEqual({"BTC"}, symbols_seen)
        self.assertEqual({"IS"}, phases_seen)
        last_seen, last_total = queue.items[-1][2], queue.items[-1][3]
        self.assertEqual(300, last_total)
        # replay_daytrading nie pyta o sygnal na samej ostatniej swiecy (brak
        # kolejnego "next open" do wypelnienia), wiec ostatni raport postepu
        # jest tuz przed koncem okna (przy total_bars=300, report_every=50 ->
        # ostatnia wielokrotnosc 50 ponizej 299 to 250), nie dokladnie na 300.
        self.assertEqual(250, last_seen)

    def test_parallel_replay_completes_and_merges_all_symbols(self):
        request = ReplayRequest(symbols=("BTC", "ETH"), universe_mode="MANUAL",
                                days=1, oos_fraction=0.3, counterfactual_audit=False,
                                max_workers=2)
        report = self._run_isolated(request)
        self.assertEqual(2, len(report["symbols"]))
        self.assertEqual({"BTC", "ETH"}, set(report["symbols"].keys()))
        self.assertEqual(2, report["universe"]["completed_count"])
        self.assertEqual(0, len(report["skipped"]))
        self.assertIn("portfolio", report)
        for sym in ("BTC", "ETH"):
            self.assertIn("in_sample", report["symbols"][sym])
            self.assertIn("out_of_sample", report["symbols"][sym])

    def test_replay_report_records_bot_version(self):
        # Kluczowe dla porownywania raportow przed/po zmianach w silniku -
        # bez tego nie da sie odroznic, ktory kod policzyl ktory replay.
        from version import tag
        request = ReplayRequest(symbols=("BTC",), universe_mode="MANUAL",
                                days=1, oos_fraction=0.3, counterfactual_audit=False,
                                max_workers=1)
        report = self._run_isolated(request)
        self.assertEqual(tag(), report["bot_version"])


class TestDownloadBundleDeltaOnStaleCache(unittest.TestCase):
    """Regresja 21.08.2026: przestarzaly (>24h, CACHE_MAX_AGE_HOURS), ale
    KOMPLETNY cache w data/replay/ byl do tej pory ignorowany w calosci -
    kazdy re-run tego samego replaya po >24h robil pelny fetch WSZYSTKICH
    5 interwalow od zera (w tym 5m/15m - dziesiatki tysiecy swiec/symbol).
    Teraz stary cache jest baza pod doszycie tylko brakujacego ogona."""

    def _recent_bundle(self, days: int):
        # Kazdy interwal konczy sie ~2 bary przed "teraz", zeby gap_ms
        # wyszedl mały i fetch_count w download_bundle byl realnie duzo
        # mniejszy niz pelne wymagane okno - to wlasnie dowodzimy w tescie.
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        bundle = {}
        for tf, (bar, per_day) in historical_replay.TIMEFRAMES.items():
            n = historical_replay._required_bars(days, per_day, tf)
            bar_ms = historical_replay._bar_duration_ms(bar)
            end_ts = now_ms - 2 * bar_ms
            data = _synthetic_bar(n, seed=hash(tf) % 10_000)
            data["timestamps"] = [end_ts - (n - 1 - i) * bar_ms for i in range(n)]
            bundle[tf] = data
        return bundle

    def test_stale_but_complete_cache_fetches_only_delta_not_full_window(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with patch.object(historical_replay, "CACHE_DIR", base):
                days = 1
                old_bundle = self._recent_bundle(days)
                stale_payload = {
                    "source": "BloFin", "symbol": "ZZZ", "requested_days": days,
                    "downloaded_at": (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(),
                    "bundle": old_bundle, "funding": [],
                }
                historical_replay._atomic_json(historical_replay._cache_path("ZZZ", days), stale_payload)

                calls = []

                class DeltaFeed:
                    last_error = None

                    def fetch_klines_ohlcv(self, symbol, bar="5m", limit=120):
                        calls.append((bar, limit))
                        bar_ms = historical_replay._bar_duration_ms(bar)
                        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                        n = min(limit, 30)
                        data = _synthetic_bar(n, seed=12345)
                        data["timestamps"] = [now_ms - (n - 1 - i) * bar_ms for i in range(n)]
                        return data

                    def fetch_funding_rate_history(self, symbol, limit=50):
                        return []

                payload = historical_replay.download_bundle(DeltaFeed(), "ZZZ", days)

        self.assertTrue(calls, "fetch_klines_ohlcv powinien byc wywolany dla doszycia delty")
        for bar, limit in calls:
            full_count = historical_replay._required_bars(
                days, historical_replay.TIMEFRAMES[
                    next(tf for tf, (b, _) in historical_replay.TIMEFRAMES.items() if b == bar)
                ][1], next(tf for tf, (b, _) in historical_replay.TIMEFRAMES.items() if b == bar))
            self.assertLess(limit, full_count,
                            f"{bar}: zadano {limit} swiec, pelne okno to {full_count} - delta powinna byc mniejsza")
        # Zmergowany bundle zawiera zarowno stara historie, jak i nowa delte,
        # posortowane i bez duplikatow (dlugosc >= dlugosci starych danych).
        for tf, (bar, per_day) in historical_replay.TIMEFRAMES.items():
            self.assertGreaterEqual(len(payload["bundle"][tf]["closes"]), len(old_bundle[tf]["closes"]))
            ts = payload["bundle"][tf]["timestamps"]
            self.assertEqual(ts, sorted(ts))
            self.assertEqual(len(ts), len(set(ts)))


if __name__ == "__main__":
    unittest.main()
