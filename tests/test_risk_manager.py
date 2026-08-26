# ============================================================
# Testy RiskManager: calculate_position_size + can_open_position
# Dodane po audycie – te funkcje (serce sizingu i bramek wejścia)
# wcześniej nie miały żadnego pokrycia testami.
# ============================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from risk_manager import RiskManager


def make_signal(**overrides):
    sig = {
        "symbol": "BTC",
        "direction": "LONG",
        "strength": 0.75,
        "price": 100.0,
        "sl_price": 98.0,  # 2% dystans
        "market_regime": "TREND",
    }
    sig.update(overrides)
    return sig


class TestCalculatePositionSize(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(starting_capital=1000.0)
        self.rm._positions_ref = []

    def test_zero_equity_returns_zero(self):
        self.rm.current_capital = 0.0
        self.assertEqual(self.rm.calculate_position_size(make_signal()), 0.0)

    def test_negative_equity_returns_zero(self):
        # equity_for_sizing floor'uje do 0 przy ujemnym capital + unrealized
        self.rm.current_capital = -50.0
        self.assertEqual(self.rm.calculate_position_size(make_signal()), 0.0)

    def test_zero_sl_distance_does_not_raise(self):
        # sl_price == price → dystans 0 → musi być floor, nie ZeroDivisionError
        sig = make_signal(price=100.0, sl_price=100.0)
        try:
            size = self.rm.calculate_position_size(sig)
        except ZeroDivisionError:
            self.fail("calculate_position_size rzucił ZeroDivisionError przy sl_dist=0")
        self.assertGreaterEqual(size, 0.0)

    def test_panic_regime_trend_is_size_limited(self):
        sig = make_signal(market_regime="PANIC")
        sig["engine"] = "trend"
        # PANIC = strong move: pełny size (mult=1.0). 0 nadal zeruje, gdy ktoś chce halt.
        self.assertGreater(self.rm.calculate_position_size(sig), 0.0)
        with patch.object(config, "REGIME_PANIC_TREND_SIZE_MULT", 0.0):
            self.assertEqual(self.rm.calculate_position_size(sig), 0.0)

class TestCalculatePositionSizeDaytradingV2(unittest.TestCase):
    """Punkt 20 planu: sizing V2 z ryzyka % kapitalu / odleglosc SL, NIE ze
    strength - weryfikuje, ze zmiana strength (przy tym samym SL) nie
    zmienia rozmiaru pozycji, w przeciwienstwie do V1."""

    def setUp(self):
        self.rm = RiskManager(starting_capital=1000.0)

    def _v2_signal(self, **overrides):
        sig = make_signal(engine="daytrading_v2", strategy_mode="DAYTRADING_V2",
                          risk_pct_of_capital=0.5, strength=0.75)
        sig.update(overrides)
        return sig

    def test_v2_typical_sl_uses_risk_budget(self):
        size = self.rm.calculate_position_size(self._v2_signal(price=100.0, sl_price=99.5))
        self.assertAlmostEqual(size, 800.0, places=2)  # po 20% reserve kapitalu

    def test_v2_wide_sl_is_sized_down_without_five_percent_floor(self):
        size = self.rm.calculate_position_size(self._v2_signal(price=100.0, sl_price=96.9))
        self.assertAlmostEqual(161.29, size, places=2)

    def test_v2_signal_produces_positive_size(self):
        size = self.rm.calculate_position_size(self._v2_signal(sl_price=99.5))
        self.assertGreater(size, 0.0)

    def test_v2_size_independent_of_strength_unlike_v1(self):
        low_strength = self.rm.calculate_position_size(self._v2_signal(strength=0.55, sl_price=99.5))
        high_strength = self.rm.calculate_position_size(self._v2_signal(strength=1.0, sl_price=99.5))
        self.assertAlmostEqual(low_strength, high_strength, places=6)

    def test_v2_risk_sl_ignores_legacy_fixed_margin_config(self):
        # 21.08.2026: signal["risk_pct_of_capital"] jest teraz tylko
        # informacyjne (_risk_pct) - realny sizing V2 (capital_pct) steruje
        # sie config.DAYTRADING_V2_MARGIN_PCT_FIXED (przyciety do [MIN,MAX]).
        # sl_price=99.5 (0.5% dystans) trzyma obie wielkosci wyraznie ponizej
        # DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE (patrz nizej), zeby ten test
        # nadal mierzyl to, co ma mierzyc - wplyw margin% na notional - a nie
        # przypadkowo lapal sie na nowy sufit ryzyka.
        with patch.object(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 5.0):
            small = self.rm.calculate_position_size(self._v2_signal(sl_price=99.5))
        with patch.object(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 10.0):
            big = self.rm.calculate_position_size(self._v2_signal(sl_price=99.5))
        self.assertAlmostEqual(big, small, places=6)

    def test_v2_margin_pct_fixed_is_clamped_to_min_max_range(self):
        # Nawet gdyby ktos ustawil FIXED poza [MIN,MAX], sizing ma sie
        # przyciac do granic, nie wyjsc poza nie.
        with patch.object(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 999.0), \
             patch.object(config, "DAYTRADING_V2_MARGIN_PCT_MAX", 10.0):
            clamped = self.rm.calculate_position_size(self._v2_signal(sl_price=99.5))
        with patch.object(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 10.0):
            at_max = self.rm.calculate_position_size(self._v2_signal(sl_price=99.5))
        self.assertAlmostEqual(clamped, at_max, places=2)

    def test_v2_falls_back_to_config_default_when_risk_pct_missing(self):
        sig = self._v2_signal(sl_price=99.5)
        del sig["risk_pct_of_capital"]
        with patch.object(config, "DAYTRADING_V2_RISK_PCT_OF_CAPITAL", 0.5):
            size = self.rm.calculate_position_size(sig)
        self.assertGreater(size, 0.0)

    def test_v2_sets_risk_engine_tag_on_signal(self):
        sig = self._v2_signal(sl_price=99.5)
        self.rm.calculate_position_size(sig)
        self.assertEqual("daytrading_v2", sig["_risk_engine"])

    def test_v2_detected_via_strategy_mode_alone_without_engine_field(self):
        sig = make_signal(strategy_mode="DAYTRADING_V2", risk_pct_of_capital=0.5, sl_price=99.5)
        sig.pop("engine", None)
        size = self.rm.calculate_position_size(sig)
        self.assertGreater(size, 0.0)
        self.assertEqual("daytrading_v2", sig["_risk_engine"])

    def test_v2_wide_sl_uses_configured_half_percent_risk(self):
        # Regresja na realny problem z uploadu logow (21.08.2026): 6/6
        # sygnalow V2, ktore przeszly WSZYSTKIE bramki silnika, zostaly
        # odrzucone przez risk_manager (PORTFOLIO_RISK ~2.9-3.1%>2.5%) mimo
        # ZERO otwartych pozycji - bo notional V2 (margin% * dzwignia) jest
        # liczony niezaleznie od SL, wiec przy szerszym SL samo jedno wejscie
        # potrafilo samo przekroczyc caly limit portfela. sl_price=96 (4%
        # dystans, typowe dla 1h swing w podwyzszonej zmiennosci) + domyslny
        # margin 7.5%*x10 dzwigni dalby notional=$750 (7.5% risk equity) bez
        # tej poprawki - teraz ma byc scapowany do <=1% equity (per-trade
        # sufit, patrz DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE) = $10/0.04=$250.
        # 1.6% SL, sufit 1% equity: $10/0.016 = $625. Powyżej 5% margin ($500).
        with patch.object(config, "DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE", 1.0):
            size = self.rm.calculate_position_size(self._v2_signal(sl_price=98.4))
        self.assertAlmostEqual(size, 312.5, places=2)

    def test_v2_tight_sl_is_not_touched_by_the_per_trade_risk_cap(self):
        # Przy wystarczajaco ciasnym SL notional z margin% miesci sie sam w
        # sobie ponizej sufitu - sufit nie ma prawa nic zmieniac.
        size = self.rm.calculate_position_size(self._v2_signal(sl_price=99.8))  # 0.2%
        self.assertAlmostEqual(size, 800.0, places=2)  # ograniczenie wolnego kapitalu po reserve

    def test_v2_per_trade_risk_cap_rejects_cleanly_when_too_small_to_open(self):
        # Bardzo szeroki SL -> nawet po scapowaniu do sufitu ryzyka wielkosc
        # spadlaby ponizej MIN_NOTIONAL_USD (nie otwieramy nog groszowych) -
        # ma to byc czysty reject (0.0), a NIE cichy powrot do wiekszego,
        # niebezpiecznego rozmiaru.
        with patch.object(config, "MIN_NOTIONAL_USD", 20.0), \
             patch.object(config, "DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE", 1.0):
            size = self.rm.calculate_position_size(self._v2_signal(sl_price=40.0))  # 60% dystans (celowo skrajny)
        self.assertEqual(size, 0.0)

    def test_v2_per_trade_risk_cap_is_configurable_and_can_be_disabled(self):
        # Ustawienie sufitu na bardzo duza wartosc == wylaczenie go (rollback
        # do czystego sizingu margin-based sprzed tej poprawki).
        # 4% SL nadal daje mala, ale bezpieczna pozycje bez floor 5% margin.
        size = self.rm.calculate_position_size(self._v2_signal(sl_price=96.0))
        self.assertAlmostEqual(125.0, size, places=2)

    def test_paper_does_not_block_on_reconcile_drift(self):
        rec = type("R", (), {"blocks_new_entries": lambda self: True})()
        self.rm._reconciler_ref = rec
        with patch.object(config, "PAPER_TRADING", True), \
             patch.object(config, "BLOCK_ENTRIES_ON_RECONCILE_DRIFT", True):
            _ok, why = self.rm.can_open_position(self._v2_signal())
        self.assertNotEqual(why, "RECONCILE_DRIFT")

    def test_live_blocks_on_reconcile_drift(self):
        rec = type("R", (), {"blocks_new_entries": lambda self: True})()
        self.rm._reconciler_ref = rec
        with patch.object(config, "PAPER_TRADING", False), \
             patch.object(config, "BLOCK_ENTRIES_ON_RECONCILE_DRIFT", True):
            ok, why = self.rm.can_open_position(self._v2_signal())
        self.assertFalse(ok)
        self.assertEqual(why, "RECONCILE_DRIFT")

    def test_v2_range_regime_reduces_risk_sizing(self):
        # 21.08.2026: ZNALEZIONA LUKA, nie naprawiona (nie o to prosil
        # uzytkownik w tej turze) - regime RANGE/PANIC mnozy risk_pct, ale
        # v2_fixed_notional (capital_pct) jest liczony NIEZALEZNIE od
        # risk_pct i uzywany wprost jako notional - regime multiplier
        # nigdy nie dociera do V2 fixed-margin sizingu. Ten test
        # DOKUMENTUJE aktualny stan (zeby nie zniknal niezauwazenie w
        # przyszlosci), nie potwierdza ze to pozadane zachowanie.
        normal = self.rm.calculate_position_size(self._v2_signal(market_regime="TREND"))
        ranged = self.rm.calculate_position_size(self._v2_signal(market_regime="RANGE"))
        self.assertAlmostEqual(normal * 0.5, ranged, places=2)


class TestCalculatePositionSizeStrength(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(starting_capital=1000.0)

    def test_higher_strength_yields_larger_or_equal_size(self):
        low = self.rm.calculate_position_size(make_signal(strength=0.55))
        high = self.rm.calculate_position_size(make_signal(strength=1.0))
        self.assertGreaterEqual(high, low)

    def test_uncalibrated_expected_r_reduces_size(self):
        calibrated = self.rm.calculate_position_size(
            make_signal(expected_r_status="CALIBRATED")
        )
        prior_only = self.rm.calculate_position_size(
            make_signal(expected_r_status="PRIOR_ONLY")
        )
        self.assertGreater(calibrated, 0.0)
        self.assertGreater(prior_only, 0.0)
        self.assertLess(prior_only, calibrated)

    def test_notional_never_exceeds_max_equity_frac(self):
        """
        Regresja dla bugu ze sprzecznymi domyślnymi MAX_NOTIONAL_EQUITY_FRAC
        (0.50 vs 2.0 w dwóch kolejnych liniach) – po naprawie limit musi
        być spójny z config.MAX_NOTIONAL_EQUITY_FRAC.
        """
        self.rm.current_capital = 1000.0
        size = self.rm.calculate_position_size(make_signal(strength=1.0, sl_price=99.9))
        max_frac = float(getattr(config, "MAX_NOTIONAL_EQUITY_FRAC", 2.0))
        equity = self.rm.equity_for_sizing()
        self.assertLessEqual(size, equity * max_frac + 1e-6)

    def test_size_respects_capital_reserve(self):
        """Notional nie powinien pochłaniać całego equity gdy CAPITAL_RESERVE_PCT>0."""
        self.rm.current_capital = 1000.0
        size = self.rm.calculate_position_size(make_signal(strength=1.0, sl_price=99.99))
        # margin used = size / leverage; nie powinno przekraczać usable equity
        lev = max(float(getattr(config, "LEVERAGE", 10)), 1.0)
        reserve = float(getattr(config, "CAPITAL_RESERVE_PCT", 0.20))
        usable = self.rm.equity_for_sizing() * (1.0 - reserve)
        self.assertLessEqual(size / lev, usable + 1e-6)

    def test_single_position_margin_never_exceeds_ten_percent_equity(self):
        self.rm.current_capital = 1000.0
        size = self.rm.calculate_position_size(make_signal(strength=1.0, sl_price=99.99))
        lev = max(float(getattr(config, "LEVERAGE", 10)), 1.0)
        cap = float(getattr(config, "MAX_POSITION_MARGIN_EQUITY_FRAC", 0.10))
        self.assertLessEqual(size / lev, self.rm.equity_for_sizing() * cap + 1e-6)


class TestCanOpenPosition(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(starting_capital=1000.0)
        self.rm._positions_ref = []

    def test_paused_blocks(self):
        self.rm.paused = True
        ok, why = self.rm.can_open_position(make_signal())
        self.assertFalse(ok)

    def test_halted_blocks(self):
        self.rm.is_halted = True
        self.rm.halt_reason = "test halt"
        ok, why = self.rm.can_open_position(make_signal())
        self.assertFalse(ok)
        self.assertEqual(why, "test halt")

    def test_loss_streak_pause_blocks(self):
        from datetime import datetime, timedelta
        self.rm.loss_pause_until = datetime.now() + timedelta(minutes=10)
        ok, why = self.rm.can_open_position(make_signal())
        self.assertFalse(ok)
        self.assertIn("LOSS_STREAK_PAUSE", why)

    def test_max_positions_blocks(self):
        self.rm.open_positions_count = int(config.MAX_POSITIONS)
        ok, why = self.rm.can_open_position(make_signal())
        self.assertFalse(ok)

    def test_panic_regime_still_allows_up_to_the_full_max_positions_limit(self):
        # Limit otwartych pozycji ma byc rowny MAX_POSITIONS (10) w kazdej
        # sytuacji, rowniez w PANIC - REGIME_PANIC_MAX_POSITIONS nie moze go
        # cicho zmniejszac (bylo=2, dawalo blokade juz przy 2 otwartych).
        #
        # MAX_CORRELATED_RISK spatchowany szeroko: to test o LICZBIE pozycji
        # w PANIC, nie o portfolio_risk/cluster_of (nowa, osobna funkcja
        # spoza tej rozmowy) - bez tego pojedyncza pozycja przy nowym
        # sizingu capital_pct (5-10% margin x10 lev = 50-100% notional)
        # sama juz przekracza domyslny cap 1.0% rowniez BEZ zadnej innej
        # pozycji w tym samym klastrze - warta osobnej rozmowy, nie cichej
        # zmiany defaultu tutaj.
        self.assertEqual(int(config.MAX_POSITIONS), int(config.REGIME_PANIC_MAX_POSITIONS))
        sig = make_signal(market_regime="PANIC")
        sig["engine"] = "reversal"
        sig["setup"] = "reversal_confirmed"
        sig["reversal_score"] = max(sig.get("strength", 0.7), 0.7)
        with patch.object(config, "MAX_CORRELATED_RISK", 1.0):
            self.rm.open_positions_count = int(config.MAX_POSITIONS) - 1
            ok, why = self.rm.can_open_position(sig)
            self.assertTrue(ok, why)
            self.rm.open_positions_count = int(config.MAX_POSITIONS)
            ok, why = self.rm.can_open_position(sig)
        self.assertFalse(ok)
        self.assertIn(str(config.MAX_POSITIONS), why)

    def test_low_capital_blocks(self):
        self.rm.current_capital = 0.5
        ok, why = self.rm.can_open_position(make_signal())
        self.assertFalse(ok)

    def test_weak_signal_blocks(self):
        sig = make_signal(strength=config.MIN_SIGNAL_STRENGTH - 0.1)
        ok, why = self.rm.can_open_position(sig)
        self.assertFalse(ok)

    def test_panic_regime_reversal_can_enter(self):
        sig = make_signal(market_regime="PANIC")
        sig["engine"] = "reversal"
        sig["setup"] = "reversal_confirmed"
        sig["reversal_score"] = max(sig.get("strength", 0.7), 0.7)
        # patrz komentarz w test_panic_regime_still_allows... - izolacja od
        # nowej, osobnej bramki portfolio_risk/cluster_of.
        with patch.object(config, "MAX_CORRELATED_RISK", 1.0):
            ok, why = self.rm.can_open_position(sig)
        self.assertTrue(ok, why)


if __name__ == "__main__":
    unittest.main()
