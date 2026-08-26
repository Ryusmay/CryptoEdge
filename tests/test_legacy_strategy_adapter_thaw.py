"""Zamrozona domena nie moze wjechac do kodu legacy.

26.08.2026: MarketSnapshot zamraza ramki na MappingProxyType + tuple, a
LegacyV2StrategyAdapter podawal je wprost do DayTradingEngineV2. Silnik
buduje z nich V2MarketSnapshot i wola .to_dict() -> dataclasses.asdict(),
ktore probuje deepcopy i wywala sie na "cannot pickle 'mappingproxy'".

Skutek: KAZDY replay przez production_signal_provider_v2 byl martwy -
walk-forward, zakladka Replay w UI i bramka parytetu. Zaden test tego nie
lapal, bo zaden nie przepuszczal zamrozonego snapshotu przez adapter.
"""
import unittest
from dataclasses import asdict
from types import MappingProxyType

from cryptoedge.domain import MarketSnapshot
from cryptoedge.strategy import LegacyV2StrategyAdapter
from v2_market_snapshot import V2MarketSnapshot


FRAME = {"timestamps": [1, 2, 3], "opens": [1.0, 2.0, 3.0], "closes": [1.5, 2.5, 3.5]}


class _Engine:
    """Dubler silnika: zapamietuje, co dostal przez podmieniony _fetch."""

    def __init__(self):
        self.seen = []

        def _fetch(symbol, bar, limit):
            return None

        self._fetch = _fetch

    def evaluate(self, ticker, now_ts=0):
        for bar in ("5m", "1H"):
            self.seen.append(self._fetch("BTC", bar, 300))
        return {"symbol": "BTC", "direction": "NEUTRAL"}


def _snapshot():
    return MarketSnapshot(
        symbol="BTC", event_ts_ms=3, decision_ts_ms=10_000_000,
        frames={"5m": dict(FRAME), "1H": dict(FRAME)}, ticker={"price": 1.0},
    )


class TestFrozenFramesNeverReachLegacy(unittest.TestCase):
    def test_market_snapshot_really_freezes_frames(self):
        """Gdyby domena przestala zamrazac, ten test ma o tym powiedziec."""
        snap = _snapshot()
        self.assertIsInstance(snap.frames["5m"], MappingProxyType)
        self.assertIsInstance(snap.frames["5m"]["closes"], tuple)

    def test_asdict_on_frozen_frames_is_the_trap(self):
        frozen = _snapshot().frames
        with self.assertRaises(TypeError):
            asdict(V2MarketSnapshot(
                symbol="BTC", event_ts_ms=3, decision_ts_ms=3, frames=dict(frozen),
            ))

    def test_adapter_hands_plain_dicts_to_engine(self):
        engine = _Engine()
        LegacyV2StrategyAdapter(engine).evaluate(_snapshot())
        self.assertEqual(2, len(engine.seen))
        for frame in engine.seen:
            self.assertIsInstance(frame, dict)
            self.assertNotIsInstance(frame, MappingProxyType)
            self.assertIsInstance(frame["closes"], list)

    def test_thawed_frame_survives_asdict(self):
        engine = _Engine()
        LegacyV2StrategyAdapter(engine).evaluate(_snapshot())
        payload = asdict(V2MarketSnapshot(
            symbol="BTC", event_ts_ms=3, decision_ts_ms=3,
            frames={"5m": engine.seen[0]},
        ))
        self.assertEqual([1.5, 2.5, 3.5], payload["frames"]["5m"]["closes"])

    def test_thaw_happens_once_per_frame_not_per_fetch(self):
        """Konwersja jest w goracej petli replaya - nie moze sie powtarzac."""
        engine = _Engine()

        def evaluate(ticker, now_ts=0):
            engine.seen.append(engine._fetch("BTC", "5m", 300))
            engine.seen.append(engine._fetch("BTC", "5m", 300))
            return {}

        engine.evaluate = evaluate
        LegacyV2StrategyAdapter(engine).evaluate(_snapshot())
        self.assertIs(engine.seen[0], engine.seen[1])

    def test_missing_frame_still_falls_back_to_engine_fetch(self):
        calls = []

        class Engine(_Engine):
            def __init__(self):
                super().__init__()

                def _fetch(symbol, bar, limit):
                    calls.append(bar)
                    return {"closes": [9.0]}

                self._fetch = _fetch

            def evaluate(self, ticker, now_ts=0):
                self.seen.append(self._fetch("BTC", "4H", 300))
                return {}

        engine = Engine()
        LegacyV2StrategyAdapter(engine).evaluate(_snapshot())
        self.assertEqual(["4H"], calls)
        self.assertEqual([9.0], engine.seen[0]["closes"])


if __name__ == "__main__":
    unittest.main()
