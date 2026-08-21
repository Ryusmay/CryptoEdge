# ============================================================
# Regresja (21.08.2026, upload logow "nowe logi... cos nam dalej
# blokuje wejscia"): PaperTrader.open_position liczyl half-spread
# z order booka na signal["price"] DOPIERO na samym koncu, tuz przed
# stworzeniem Position - PO calculate_position_size() i
# can_open_position(). Dla illikwidnych symboli (szeroki
# ob_spread_pct) to poszerzalo realny entry_px WZGLEDEM stalego,
# strukturalnego SL (z 1h swingu), na ktorym juz policzono sizing
# (w tym per-trade sufit ryzyka DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE).
# Efekt: notional zostawal niescapowany, realne ryzyko$ po fillu
# (notional * sl_dist_post_spread) przekraczalo RISK_PCT_MAX * 1.15,
# a post-fill RISK_INVARIANT (Position.recalculate_after_fill) lapal
# to dopiero PO otwarciu -> natychmiastowe wymuszone zamkniecie ze
# strata (RISK_INVARIANT_FAIL) + ENGINE_COOLDOWN(20min) na symbolu,
# co realnie blokowalo kolejne, poprawne wejscia.
#
# Fix: half-spread jest teraz liczony na starcie open_position(),
# PRZED sizingiem i bramkami ryzyka, zeby wszystkie dostaly jedna,
# spojna (juz-wykonawcza) cene.
# ============================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from paper_trader import PaperTrader
from risk_manager import RiskManager


def make_v2_signal(**overrides):
    sig = {
        "symbol": "SPCX",
        "direction": "LONG",
        "strength": 0.75,
        "price": 100.0,
        "sl_price": 98.7,  # 1.3% dystans wzgledem PRE-spread ceny
        "engine": "daytrading_v2",
        "strategy_mode": "DAYTRADING_V2",
        "risk_pct_of_capital": 0.5,
        "market_regime": "TREND",
        # illikwidny symbol - szeroki spread (typowy dla ILLIQUID bucket
        # w realnym uploadzie: SPCX byl oznaczony liquidity_bucket=ILLIQUID)
        "order_book": {"ob_spread_pct": 1.7},
    }
    sig.update(overrides)
    return sig


class TestOrderBookSpreadAppliedBeforeSizing(unittest.TestCase):
    def setUp(self):
        self.risk = RiskManager(starting_capital=100.0)
        self.risk._positions_ref = []
        self.trader = PaperTrader(self.risk)
        # Wartosci jak w realnym uploadzie (config.py v19.13.1 domyslne),
        # jawnie przypiete zeby test nie zalezal od przyszlych zmian defaultow.
        self._patches = [
            patch.object(config, "LEVERAGE", 10),
            patch.object(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 7.5),
            patch.object(config, "DAYTRADING_V2_MARGIN_STRENGTH_SCALED", False),
            patch.object(config, "DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE", 1.0),
            patch.object(config, "RISK_PCT_MAX", 0.0090),
            patch.object(config, "USE_ORDERBOOK_SPREAD", True),
            patch.object(config, "MAX_PORTFOLIO_OPEN_RISK", 0.025),
            # can_open_position() laczy w sobie caly szereg NIEZALEZNYCH bramek
            # (OB impact/liquidity, expected-net-R, dynamic spread z-score,
            # portfolio risk, korelacje...) - w realnym uploadzie ktoregos
            # przeciecia parametrow (regime PANIC, ATR, historia spreadu z
            # trackera) SPCX je przeszedl. Ta regresja NIE jest o tych
            # bramkach - jest o kolejnosci "spread -> sizing -> risk
            # invariant" wewnatrz open_position(), wiec bramki portfela
            # zaslepiamy na "przepusc", zeby test byl stabilny niezaleznie
            # od ich przyszlego strojenia.
            patch.object(RiskManager, "can_open_position", lambda self, signal, open_directions=None: (True, "OK")),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_wide_ob_spread_on_illiquid_symbol_does_not_trip_risk_invariant(self):
        # Sprzed poprawki: sizing liczony na cenie=100 (sl_dist=1.3%, notional
        # ~$75, per-trade sufit NIE zdazyl scapowac) -> spread dopiero potem
        # przesuwal fill do ~$100.85, realny sl_dist rosl do ~2.13%, ryzyko$
        # (~$1.60) przebijalo RISK_INVARIANT (~$1.035) -> natychmiastowe
        # wymuszone zamkniecie, open_position() zwracalo None.
        pos = self.trader.open_position(make_v2_signal())

        self.assertIsNotNone(
            pos, "pozycja zostala natychmiast zamknieta przez RISK_INVARIANT_FAIL "
                 "(spread OB policzony po sizingu, nie przed)"
        )
        self.assertEqual(len(self.trader.positions), 1)
        self.assertIs(self.trader.positions[0], pos)
        self.assertTrue(getattr(pos, "risk_invariant_ok", None), pos.risk_invariant_reason)

        # entry faktycznie uwzglednia polowe spreadu (LONG -> w gore od 100.0)
        self.assertGreater(pos.entry_price, 100.0)

        # a realne ryzyko$ na tym, juz-wykonawczym entry miesci sie w sufit
        # RISK_PCT_MAX * 1.15 tolerancji post-fill (ta sama formula co
        # w Position.recalculate_after_fill)
        equity = self.risk.equity_for_sizing()
        max_risk_usd = equity * float(config.RISK_PCT_MAX) * 1.15
        self.assertLessEqual(pos.actual_risk_usd, max_risk_usd + 1e-6)

    def test_sizing_sees_the_same_spread_adjusted_price_as_the_final_position(self):
        # Regresja wprost na kolejnosc krokow: notional zwrocony przez
        # calculate_position_size (widoczny w signal["_planned_notional"]
        # / decision_telemetry) musi odpowiadac POST-spread cenie, nie
        # przedspreadowej - inaczej sizing i faktyczny fill patrza na dwie
        # rozne odleglosci do SL.
        captured = {}
        orig_calc = self.risk.calculate_position_size

        def spy(signal):
            captured["price_seen_by_sizing"] = signal.get("price")
            return orig_calc(signal)

        with patch.object(self.risk, "calculate_position_size", side_effect=spy):
            pos = self.trader.open_position(make_v2_signal())

        self.assertIsNotNone(pos)
        self.assertAlmostEqual(
            captured["price_seen_by_sizing"], pos.entry_price, places=6,
            msg="sizing widzial inna cene (bez spreadu) niz faktyczny fill Position"
        )


if __name__ == "__main__":
    unittest.main()
