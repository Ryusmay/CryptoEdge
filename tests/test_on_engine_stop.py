import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from paper_trader import PaperTrader


class _Risk:
    def __init__(self):
        self.current_capital = 10_000.0
        self.opened = 0

    def register_open(self):
        self.opened += 1

    def register_close(self, symbol=None, pnl=0.0, engine=None):
        return None

    def calculate_position_size(self, signal):
        return 1000.0

    def can_open_position(self, signal, open_directions=None):
        return True, "OK"

    def log_reject(self, *args, **kwargs):
        return None

    def update_capital(self, capital, pnl=0):
        self.current_capital = capital


class TestOnEngineStop(unittest.TestCase):
    """on_engine_stop() przy manualnym STOP: pozycje 'na plusie' maja byc
    zamykane od razu, reszta zostaje z dostosowanym TP. Regresja na dwa
    realne problemy znalezione w logach z 19.08: (1) '>0' na niezrealizowanym
    PnL ignorowalo koszty zamkniecia z dzwignia, wiec 'zysk' po realizacji
    czesto wychodzil na minus; (2) last_price_map bywal nieswiezy (15 min),
    a mimo to byl uzywany do decyzji o zamknieciu."""

    def _open(self, trader, symbol, direction, price, sl_price, leverage=10):
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "DECISION_TELEMETRY_PATH", str(Path(td) / "decision_telemetry.jsonl")
        ):
            return trader.open_position({
                "symbol": symbol, "direction": direction, "price": price,
                "strength": 0.8, "sl_price": sl_price, "leverage": leverage,
            })

    def test_small_unrealized_gain_below_cost_buffer_is_not_closed(self):
        # Niezrealizowany zysk (+0.3% * dzwignia 10x = +3% pnl_pct) mniejszy
        # niz koszt zamkniecia z dzwignia (COMMISSION*2+SLIPPAGE=0.2% * 10x = 2%)...
        # dobieram cene tak, zeby pnl_pct byl DODATNI, ale PONIZEJ bufora kosztow.
        trader = PaperTrader(_Risk())
        self._open(trader, "BTC", "LONG", 100.0, 90.0, leverage=10)
        pos = trader.positions[0]
        # cost buffer w pnl_pct = (0.0006*2+0.0008)*10*100 = 2.0%
        price_map = {"BTC": 100.15}  # +0.15% ruchu * lev10 = +1.5% pnl_pct < 2.0% bufora
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "DECISION_TELEMETRY_PATH", str(Path(td) / "decision_telemetry.jsonl")
        ):
            res = trader.on_engine_stop(price_map, take_profit_pct=5.0, price_map_age_s=1.0)
        self.assertNotIn("BTC", res["closed_profit"])
        self.assertEqual(1, len(res["kept_with_tp"]))
        self.assertEqual(1, len(trader.positions))

    def test_gain_clearly_above_cost_buffer_closes_normally(self):
        trader = PaperTrader(_Risk())
        self._open(trader, "ETH", "LONG", 100.0, 90.0, leverage=10)
        price_map = {"ETH": 105.0}  # +5% ruchu * lev10 = +50% pnl_pct, mocno powyzej bufora 2%
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "DECISION_TELEMETRY_PATH", str(Path(td) / "decision_telemetry.jsonl")
        ):
            res = trader.on_engine_stop(price_map, take_profit_pct=5.0, price_map_age_s=1.0)
        self.assertIn("ETH", res["closed_profit"])
        self.assertEqual(0, len(trader.positions))

    def test_dynamic_round_trip_slippage_prevents_false_profit_close(self):
        trader = PaperTrader(_Risk())
        self._open(trader, "BTC", "SHORT", 100.0, 110.0, leverage=10)
        trader.positions[0].slip_rt = 0.02
        # +1% gross price move, ale 2% slip + fee => strata netto.
        res = trader.on_engine_stop({"BTC": 99.0}, take_profit_pct=5.0, price_map_age_s=1.0)
        self.assertNotIn("BTC", res["closed_profit"])
        self.assertEqual(1, len(trader.positions))

    def test_stale_price_map_skips_close_even_for_large_gain(self):
        # To samo co wyzej (duzy, jednoznaczny zysk), ale ceny sprzed 15 minut -
        # zaden zamknieciowy sygnal nie moze byc na tym oparty.
        trader = PaperTrader(_Risk())
        self._open(trader, "SOL", "LONG", 100.0, 90.0, leverage=10)
        price_map = {"SOL": 105.0}
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "DECISION_TELEMETRY_PATH", str(Path(td) / "decision_telemetry.jsonl")
        ):
            res = trader.on_engine_stop(price_map, take_profit_pct=5.0, price_map_age_s=900.0)
        self.assertEqual([], res["closed_profit"])
        self.assertTrue(res["prices_stale"])
        self.assertEqual(1, len(res["kept_with_tp"]))
        self.assertEqual(1, len(trader.positions))

    def test_fresh_price_map_within_default_threshold_allows_normal_close(self):
        trader = PaperTrader(_Risk())
        self._open(trader, "XRP", "LONG", 100.0, 90.0, leverage=10)
        price_map = {"XRP": 105.0}
        max_age = float(config.STOP_ENGINE_MAX_PRICE_AGE_S)
        with tempfile.TemporaryDirectory() as td, patch.object(
            config, "DECISION_TELEMETRY_PATH", str(Path(td) / "decision_telemetry.jsonl")
        ):
            res = trader.on_engine_stop(price_map, take_profit_pct=5.0, price_map_age_s=max_age - 1)
        self.assertIn("XRP", res["closed_profit"])
        self.assertFalse(res["prices_stale"])


if __name__ == "__main__":
    unittest.main()
