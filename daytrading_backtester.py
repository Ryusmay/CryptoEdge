"""Event-driven daytrading replay with conservative intrabar execution.

Signals are decided from data closed at bar t and filled at bar t+1 open.
When a stop and target are both touched in one candle, the stop is assumed to
occur first. This deliberately avoids optimistic OHLC path assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
from typing import Callable, Dict, List, Optional
import hashlib
import config


class AsOfBlofinFeed:
    """Historical feed enforcing an as-of boundary for production strategy code."""
    def __init__(self, bundle: Dict[str, dict]):
        self.bundle = bundle
        self.asof_ts = 0
        self._timestamps = {}
        self._series = {}
        self._window_cache = {}
        for tf, data in (bundle or {}).items():
            if not isinstance(data, dict):
                continue
            self._timestamps[tf] = [
                int(value) for value in ((data.get("timestamps") or data.get("ts") or []))
            ]
            self._series[tf] = {
                key: tuple(data.get(key) or ())
                for key in ("opens", "highs", "lows", "closes", "volumes")
            }
        self._funding = list(bundle.get("funding") or []) if isinstance(bundle, dict) else []

    def fetch_klines_ohlcv(self, symbol: str, bar: str = "5m", limit: int = 120) -> dict:
        tf = {"1H": "1h", "4H": "4h", "1D": "1d", "1W": "1w"}.get(bar, bar)
        timestamps = self._timestamps.get(tf) or []
        duration = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                    "1h": 3_600_000, "4h": 14_400_000,
                    "1d": 86_400_000, "1w": 604_800_000}.get(tf, 0)
        # Production timestamps are candle opens; only bars whose close is
        # known are visible.  Tiny synthetic test indices (1,2,3...) represent
        # already-closed events and have no timeframe duration semantics.
        observed_step = (timestamps[-1] - timestamps[-2]) if len(timestamps) >= 2 else duration
        candle_open_axis = duration > 0 and observed_step >= duration * 0.5
        boundary = int(self.asof_ts) - duration if candle_open_axis else int(self.asof_ts)
        end = bisect_right(timestamps, boundary)
        if end <= 0:
            return {}
        start = max(0, end - int(limit))
        cache_key = (tf, end, int(limit))
        cached = self._window_cache.get(cache_key)
        if cached is not None:
            return cached
        series = self._series.get(tf) or {}
        out = {key: values[start:min(end, len(values))]
               for key, values in series.items()}
        out["timestamps"] = tuple(timestamps[start:end])
        out["candles_confirmed"] = True
        self._window_cache[cache_key] = out
        return out

    def fetch_order_book(self, symbol: str, size: int = 20) -> dict:
        return {}

    def fetch_funding_rate(self, symbol: str) -> dict:
        asof = int(self.asof_ts or 0)
        rate = 0.0
        ts_ms = 0
        for row in self._funding:
            try:
                ts = int(row.get("ts_ms") or 0)
                r = float(row.get("rate") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if ts <= asof:
                rate, ts_ms = r, ts
            else:
                break
        if ts_ms <= 0 and not self._funding:
            return {}
        raw = {
            "funding_rate": rate,
            "funding_rate_pct": round(rate * 100, 6),
            "funding_time": ts_ms,
            "funding_interval": 8,
        }
        try:
            from funding_model import enrich_funding
            return enrich_funding(raw)
        except Exception:
            return raw


class AsOfDataFeeder:
    def __init__(self, bundle: Dict[str, dict]):
        self.blofin = AsOfBlofinFeed(bundle)


def production_signal_provider(symbol: str, bundle: Dict[str, dict], drive_tf: str = "5m",
                               audit_relax: Optional[set[str]] = None):
    """Return ``signal_at`` backed by the real DayTradingEngine and causal MTF data."""
    from daytrading_engine import DayTradingEngine
    feeder = AsOfDataFeeder(bundle)
    engine = DayTradingEngine(feeder)
    drive = bundle.get(drive_tf) or {}
    timestamps = list(drive.get("timestamps") or drive.get("ts") or [])
    closes = list(drive.get("closes") or [])

    def signal_at(i: int) -> dict:
        feeder.blofin.asof_ts = int(timestamps[i])
        return engine.evaluate({"symbol": symbol, "price": closes[i],
                                "blofin_quote_volume_24h": 1e12}, audit_relax=audit_relax)
    return signal_at


def production_signal_provider_v2(symbol: str, bundle: Dict[str, dict], drive_tf: str = "5m"):
    """Analog production_signal_provider() dla silnika V2 - as-of, bez
    look-ahead, jedna trwala instancja DayTradingEngineV2 na caly replay
    (zeby hamulce czestotliwosci - cooldown, jedno wejscie na swing -
    dzialaly poprawnie w calym oknie, nie resetowaly sie co wywolanie)."""
    from cryptoedge.strategy.daytrading_v2 import DayTradingEngineV2
    feeder = AsOfDataFeeder(bundle)
    from day_expectancy_calibration import DayExpectancyCalibrator
    calibrator = DayExpectancyCalibrator(path="")
    calibrator.data = {"n": 0, "tp1": 0, "tp2": 0, "profiles": {}, "regimes": {}}
    engine = DayTradingEngineV2(feeder, expectancy_calibrator=calibrator)
    from cryptoedge.domain import MarketSnapshot
    from cryptoedge.services import DecisionPipeline
    from cryptoedge.strategy import LegacyV2StrategyAdapter
    drive = bundle.get(drive_tf) or {}
    timestamps = list(drive.get("timestamps") or drive.get("ts") or [])
    closes = list(drive.get("closes") or [])

    class ReplayMarketData:
        ticker = {}

        def snapshot(self, requested_symbol, decision_ts_ms=None):
            frames = {
                tf: feeder.blofin.fetch_klines_ohlcv(requested_symbol, bar=tf, limit=300) or {}
                for tf in ("1D", "4H", "1H", "15m", "5m")
            }
            ts = int(decision_ts_ms or feeder.blofin.asof_ts or 0)
            return MarketSnapshot(
                symbol=requested_symbol, event_ts_ms=ts, decision_ts_ms=ts,
                frames=frames, ticker=self.ticker, source="replay",
            )

    market_data = ReplayMarketData()
    pipeline = DecisionPipeline(market_data, LegacyV2StrategyAdapter(engine), risk=None)

    def signal_at(i: int) -> dict:
        feeder.blofin.asof_ts = int(timestamps[i])
        now_ts = float(timestamps[i]) / 1000.0
        from v2_parity_policy import apply_market_gates, causal_change_pct
        change_24h = causal_change_pct(closes, i)
        coin = {"symbol": symbol, "price": closes[i], "change_24h": change_24h}
        market_data.ticker = coin
        signal = pipeline.analyze(symbol, decision_ts_ms=int(timestamps[i])).decision
        apply_market_gates([signal], [coin], "UNKNOWN")
        return signal

    return signal_at, engine


def htf_bias_provider_v2(symbol: str, bundle: Dict[str, dict], drive_tf: str = "5m"):
    """Callable (i) -> "LONG"/"SHORT"/"NEUTRAL"/None dla monitorowania
    odwrocenia 4h/1D W TRAKCIE trwania pozycji (nie tylko przy wejsciu) -
    uzywane jako htf_bias_at w replay_daytrading_v2()/portfolio_replay_v2().
    None = brak wystarczajacych danych as-of tego momentu (nigdy nie
    wymusza zamkniecia - patrz _is_htf_reversed w daytrading_backtester)."""
    from daytrading_engine_v2 import _bias_from_indicators
    try:
        from indicators_full import compute_indicators
    except ImportError:  # pragma: no cover
        def compute_indicators(ohlcv, tf="1h"):
            return {}
    feeder = AsOfDataFeeder(bundle)
    drive = bundle.get(drive_tf) or {}
    timestamps = list(drive.get("timestamps") or drive.get("ts") or [])

    def htf_bias_at(i: int) -> Optional[str]:
        if i >= len(timestamps):
            return None
        feeder.blofin.asof_ts = int(timestamps[i])
        frame_1d = feeder.blofin.fetch_klines_ohlcv(symbol, bar="1D", limit=260) or {}
        frame_4h = feeder.blofin.fetch_klines_ohlcv(symbol, bar="4H", limit=260) or {}
        if not frame_4h.get("closes"):
            return None
        ind_4h = compute_indicators(frame_4h, tf="4h")
        if not ind_4h:
            return None
        bias_4h = _bias_from_indicators(ind_4h)
        if bias_4h == "NEUTRAL":
            return "NEUTRAL"
        return bias_4h

    return htf_bias_at


def htf_trail_anchor_provider_v2(symbol: str, bundle: Dict[str, dict], drive_tf: str = "5m"):
    """Callable (i, direction) -> cena kotwicy trailingu TP3, liczona z
    ostatniego potwierdzonego swingu 1h AS-OF danego momentu (nie z ATR 5m -
    patrz punkt 7 planu: 'kotwica tez 1h ... nie trail z 5m')."""
    from swing_structure import find_last_confirmed_swing
    try:
        from indicators_full import compute_indicators
    except ImportError:  # pragma: no cover
        def compute_indicators(ohlcv, tf="1h"):
            return {}
    feeder = AsOfDataFeeder(bundle)
    drive = bundle.get(drive_tf) or {}
    timestamps = list(drive.get("timestamps") or drive.get("ts") or [])

    def htf_trail_anchor_at(i: int, direction: str) -> Optional[float]:
        if i >= len(timestamps):
            return None
        feeder.blofin.asof_ts = int(timestamps[i])
        frame_1h = feeder.blofin.fetch_klines_ohlcv(symbol, bar="1H", limit=260) or {}
        closes_1h = list(frame_1h.get("closes") or [])
        highs_1h = list(frame_1h.get("highs") or [])
        lows_1h = list(frame_1h.get("lows") or [])
        if not closes_1h:
            return None
        ind_1h = compute_indicators(frame_1h, tf="1h")
        atr_1h = float((ind_1h or {}).get("atr") or 0)
        if atr_1h <= 0:
            return None
        atr_series = [atr_1h] * len(closes_1h)
        swing = find_last_confirmed_swing(
            highs_1h, lows_1h, closes_1h, atr_series,
            min_move_atr=float(getattr(config, "DAYTRADING_V2_SWING_MIN_MOVE_ATR", 2.0)),
            min_bars=int(getattr(config, "DAYTRADING_V2_SWING_MIN_BARS", 8)),
            right_confirm=int(getattr(config, "DAYTRADING_V2_SWING_RIGHT_CONFIRM", 5)),
        )
        if swing is None:
            return None
        expected_dir = "UP" if direction == "LONG" else "DOWN"
        if swing["direction"] != expected_dir:
            return None
        # Kotwica trailingu: koniec (najswiezszy pivot) swingu ZGODNEGO z
        # kierunkiem pozycji - dla LONG to najnowszy potwierdzony swing high
        # (jesli struktura wciaz rosnie) uzyty jako podnoszona podloga poprzez
        # bufor ATR; dla SHORT analogicznie w dol.
        buffer = float(getattr(config, "DAYTRADING_V2_SL_ATR_BUFFER", 1.0)) * atr_1h
        return swing["end"]["price"] - buffer if direction == "LONG" else swing["end"]["price"] + buffer

    return htf_trail_anchor_at


@dataclass
class ReplayTrade:
    direction: str
    entry_i: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp1_frac: float
    initial_risk: float
    atr: float = 0.0
    remaining: float = 1.0
    realised_r: float = 0.0
    tp1_done: bool = False
    exit_i: Optional[int] = None
    exit_reason: str = ""
    highest: float = 0.0
    lowest: float = 0.0
    trailing_active: bool = False
    invalidation_count: int = 0
    last_invalidation_bar: object = None


def _hit(direction: str, low: float, high: float, level: float, kind: str) -> bool:
    if kind == "sl":
        return low <= level if direction == "LONG" else high >= level
    return high >= level if direction == "LONG" else low <= level


def replay_daytrading(
    ohlcv_5m: Dict[str, List[float]],
    signal_at: Callable[[int], Optional[dict]],
    fee_frac_round_trip: float = 0.0012,
    slippage_frac_round_trip: float = 0.0006,
    max_bars: int = 120,
) -> dict:
    """Replay the production TP1/TP2/SL skeleton using next-open entries.

    ``signal_at(i)`` must use candles only through index i. It can call the
    production DayTradingEngine via an as-of feeder. Costs are charged in R.
    """
    opens = list(ohlcv_5m.get("opens") or [])
    highs = list(ohlcv_5m.get("highs") or [])
    lows = list(ohlcv_5m.get("lows") or [])
    closes = list(ohlcv_5m.get("closes") or opens)
    n = min(len(opens), len(highs), len(lows), len(closes))
    trades, active, pending = [], None, None
    for i in range(n):
        if pending is not None and active is None:
            sig = pending
            entry = float(opens[i])
            risk = abs(float(sig["price"]) - float(sig["sl_price"]))
            if risk > 0:
                direction = sig["direction"]
                # Preserve planned distances around the actual next-open fill.
                sign = 1.0 if direction == "LONG" else -1.0
                plan = sig.get("tp_plan") or {}
                active = ReplayTrade(direction, i, entry, entry - sign * risk,
                                     entry + sign * risk * float(plan.get("tp1_r", 1.5)),
                                     entry + sign * risk * float(plan.get("tp2_r", 2.2)),
                                     float(plan.get("frac_tp1", 0.5)), risk,
                                     atr=float(sig.get("atr") or 0), highest=entry, lowest=entry)
            pending = None
        if active is not None:
            risk = active.initial_risk
            active.highest = max(active.highest, float(highs[i]))
            active.lowest = min(active.lowest, float(lows[i]))
            # Conservative ordering when OHLC cannot reveal the tick path.
            if _hit(active.direction, lows[i], highs[i], active.sl, "sl"):
                stop_r = ((active.sl - active.entry) / risk if active.direction == "LONG"
                          else (active.entry - active.sl) / risk)
                active.realised_r += active.remaining * stop_r
                active.exit_i, active.exit_reason = i, "sl_first_intrabar"
            elif not active.tp1_done and _hit(active.direction, lows[i], highs[i], active.tp1, "tp"):
                active.realised_r += active.tp1_frac * abs(active.tp1 - active.entry) / risk
                active.remaining -= active.tp1_frac
                active.tp1_done = True
                active.sl = active.entry
                active.trailing_active = True
            elif active.tp1_done and _hit(active.direction, lows[i], highs[i], active.tp2, "tp"):
                active.realised_r += active.remaining * abs(active.tp2 - active.entry) / risk
                active.exit_i, active.exit_reason = i, "tp2"
            elif i - active.entry_i >= max_bars:
                mark_r = ((float(closes[i]) - active.entry) / risk if active.direction == "LONG"
                          else (active.entry - float(closes[i])) / risk)
                active.realised_r += active.remaining * mark_r
                active.exit_i, active.exit_reason = i, "hard_time_stop"
            if active.exit_i is not None:
                cost_r = (fee_frac_round_trip + slippage_frac_round_trip) / max(risk / active.entry, 1e-9)
                active.realised_r -= cost_r
                trades.append(active)
                active = None
            elif active.trailing_active and active.atr > 0:
                distance = active.atr * float(getattr(config, "DAYTRADING_TRAIL_ATR_MULT", 1.10))
                if active.direction == "LONG":
                    active.sl = max(active.sl, active.highest - distance)
                else:
                    active.sl = min(active.sl, active.lowest + distance)
        sig = signal_at(i) if i + 1 < n else None
        if active is not None and sig:
            intra = sig.get("intraday") or {}
            frames = intra.get("tf") or {}
            m5, m15 = frames.get("5m") or {}, frames.get("15m") or {}
            ts = (intra.get("bar_ts") or {}).get("5m", i)
            invalid = ((active.direction == "LONG" and (m5.get("supertrend") or {}).get("is_up") is False
                        and m15.get("ema_fast_above_slow") is False)
                       or (active.direction == "SHORT" and (m5.get("supertrend") or {}).get("is_up") is True
                           and m15.get("ema_fast_above_slow") is True))
            if ts != active.last_invalidation_bar:
                active.last_invalidation_bar = ts
                active.invalidation_count = active.invalidation_count + 1 if invalid else 0
            if active.invalidation_count >= 2:
                mark_r = ((float(closes[i]) - active.entry) / active.initial_risk
                          if active.direction == "LONG" else
                          (active.entry - float(closes[i])) / active.initial_risk)
                active.realised_r += active.remaining * mark_r
                active.exit_i, active.exit_reason = i, "day_setup_invalidated"
                cost_r = (fee_frac_round_trip + slippage_frac_round_trip) / max(active.initial_risk / active.entry, 1e-9)
                active.realised_r -= cost_r
                trades.append(active)
                active = None
        if active is None and pending is None and i + 1 < n:
            if sig and sig.get("direction") in ("LONG", "SHORT") and not sig.get("reject_reason"):
                pending = sig
    rs = [t.realised_r for t in trades]
    return {
        "trades": trades,
        "count": len(trades),
        "win_rate": (sum(r > 0 for r in rs) / len(rs)) if rs else 0.0,
        "net_r": sum(rs),
        "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
        "execution": "next_open_stop_first",
    }


# ============================================================
# DAYTRADING V2 - rownolegly silnik replay (nie zmienia replay_daytrading()
# powyzej). Kluczowe roznice wg planu hierarchii timeframe (20.08.2026):
# - BRAK wyjscia na "day_setup_invalidated" (punkt 11 planu - wylaczone
#   calkowicie, nie tylko zlagodzone).
# - SL/TP1/TP2 pochodza WPROST z sygnalu silnika V2 (juz obliczone ze swingu
#   1h), nie z genericznego mnoznika R.
# - TP3: trailing PO TP2, kotwiczony o 1h (nie o ATR 5m jak V1) - patrz
#   htf_trail_anchor_at.
# - Wyjscie 4H/1D wylaczone (DAYTRADING_V2_EXIT_ON_HTF_REVERSAL=False).
# - Backtester wywoluje notify_exit() po kazdym zamknieciu, zeby silnik V2
#   mogl zasilic wlasne hamulce czestotliwosci (cooldown, one-entry-per-swing
#   jest juz w silniku samym, nie wymaga nic tutaj).
# ============================================================

@dataclass
class ReplayTradeV2:
    direction: str
    entry_i: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp1_frac: float
    initial_risk: float
    remaining: float = 1.0
    realised_r: float = 0.0
    tp1_done: bool = False
    tp2_done: bool = False
    exit_i: Optional[int] = None
    exit_reason: str = ""
    highest: float = 0.0
    lowest: float = 0.0
    funding_r: float = 0.0
    slip_rt: Optional[float] = None
    mae_r: float = 0.0
    mfe_r: float = 0.0
    fill_kind: str = "market"
    fee_rt: Optional[float] = None
    symbol: str = ""
    v2_profile: str = "unknown"
    market_regime: str = "unknown"


def apply_observed_funding(trade, timestamps: List, funding: List[dict]) -> float:
    """Księguje settlement BloFin między entry a exit jako ΔR. Mutuje trade."""
    if trade is None or not funding:
        return 0.0
    entry_i = getattr(trade, "entry_i", None)
    exit_i = getattr(trade, "exit_i", None)
    if entry_i is None or exit_i is None:
        return 0.0
    n = len(timestamps or [])
    if entry_i >= n or exit_i >= n:
        return 0.0
    start, end = int(timestamps[entry_i]), int(timestamps[exit_i])
    rates = []
    for row in funding:
        try:
            ts = int(row.get("ts_ms") or 0)
            if start < ts <= end:
                rates.append(float(row.get("rate") or 0.0))
        except (TypeError, ValueError, AttributeError):
            continue
    signed_cost = sum(rates) if trade.direction == "LONG" else -sum(rates)
    risk_fraction = float(trade.initial_risk) / max(float(trade.entry), 1e-12)
    funding_r = -signed_cost / max(risk_fraction, 1e-9)
    trade.realised_r = float(trade.realised_r) + funding_r
    trade.funding_r = funding_r
    return funding_r


def _is_htf_reversed(bias: Optional[str], entry_direction: str) -> bool:
    """True tylko jesli bias 4h/1D jest ZNANY i JEDNOZNACZNIE przeciwny
    kierunkowi wejscia. Brak danych (None) albo NEUTRAL nigdy nie zamyka.
    Sama funkcja nic nie zamyka — patrz DAYTRADING_V2_EXIT_ON_HTF_REVERSAL."""
    if bias in (None, "NEUTRAL"):
        return False
    return bias != entry_direction


def _open_trade_v2(i: int, entry: float, sig: dict, tp1_frac: float) -> Optional[ReplayTradeV2]:
    """SL/TP ze struktury (absolute). risk = |fill − SL|."""
    try:
        sl = float(sig["sl_price"])
        tp1 = float(sig["tp1_price"])
        tp2 = float(sig["tp2_price"])
        entry = float(entry)
    except (TypeError, ValueError, KeyError):
        return None
    direction = sig.get("direction")
    if direction == "LONG":
        if sl >= entry or tp1 <= entry:
            return None
    elif direction == "SHORT":
        if sl <= entry or tp1 >= entry:
            return None
    else:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    trade = ReplayTradeV2(
        direction, i, entry, sl, tp1, tp2,
        float(tp1_frac), risk, highest=entry, lowest=entry,
    )
    trade.fill_kind = str(sig.get("fill_kind") or "market")
    trade.symbol = str(sig.get("symbol") or "").upper()
    trade.v2_profile = str(sig.get("v2_profile") or "unknown").lower()
    trade.market_regime = str(
        sig.get("market_regime") or sig.get("regime") or
        ((sig.get("intraday") or {}).get("regime")) or "unknown"
    ).upper()
    return trade


def v2_limit_timeout_5m() -> int:
    from v2_parity_policy import limit_timeout_5m_bars
    return limit_timeout_5m_bars()


def _try_limit_fill(direction: str, open_: float, low: float, high: float, limit: float) -> Optional[float]:
    from v2_parity_policy import limit_touched
    return limit_touched(direction, limit, open_price=open_, low=low, high=high)


def resolve_v2_fill(sig: dict, i: int, signal_i: int,
                    open_: float, high: float, low: float,
                    timeout_bars: int | None = None) -> tuple:
    """Resolve a V2 entry without chasing price.

    ``kind == "expired"`` means the pullback never reached the planned
    entry zone.  An expired setup is cancelled; it must never be converted
    into a market order because that changes a retest setup into a chase.
    """
    use = bool(getattr(config, "DAYTRADING_V2_LIMIT_IN_ZONE", True))
    limit = sig.get("limit_price") if use else None
    if limit is None:
        return float(open_), "market"
    try:
        limit = float(limit)
    except (TypeError, ValueError):
        return float(open_), "market"
    fill = _try_limit_fill(sig.get("direction"), float(open_), float(low), float(high), limit)
    if fill is not None:
        return fill, "limit"
    timeout_bars = v2_limit_timeout_5m() if timeout_bars is None else max(1, int(timeout_bars))
    if (i - int(signal_i)) >= timeout_bars:
        return None, "expired"
    return None, ""


def _process_trade_bar_v2(
    trade: ReplayTradeV2, i: int, high: float, low: float, close: float,
    htf_bias: Optional[str], htf_trail_anchor: Optional[float],
    fee_frac_round_trip: float, slippage_frac_round_trip: float,
    max_bars: int, tp2_frac: float,
) -> bool:
    """Przetwarza JEDEN bar dla JEDNEJ otwartej pozycji V2 (SL/odwrocenie
    HTF/TP1/TP2/hard_time_stop/trailing TP3). Mutuje `trade` w miejscu.
    Zwraca True, jesli pozycja zostala zamknieta w tym barze (exit_i/
    exit_reason sa juz ustawione, koszt round-trip juz odjety)."""
    risk = trade.initial_risk
    trade.highest = max(trade.highest, float(high))
    trade.lowest = min(trade.lowest, float(low))
    if risk > 0:
        if trade.direction == "LONG":
            trade.mae_r = max(trade.mae_r, (trade.entry - trade.lowest) / risk)
            trade.mfe_r = max(trade.mfe_r, (trade.highest - trade.entry) / risk)
        else:
            trade.mae_r = max(trade.mae_r, (trade.highest - trade.entry) / risk)
            trade.mfe_r = max(trade.mfe_r, (trade.entry - trade.lowest) / risk)

    def _close(reason: str, mark_price: Optional[float] = None) -> None:
        if mark_price is not None:
            mark_r = ((mark_price - trade.entry) / risk if trade.direction == "LONG"
                      else (trade.entry - mark_price) / risk)
            trade.realised_r += trade.remaining * mark_r
        cost_r = ((float(trade.fee_rt) if trade.fee_rt is not None else fee_frac_round_trip) + (
            float(trade.slip_rt) if trade.slip_rt is not None else slippage_frac_round_trip
        )) / max(risk / trade.entry, 1e-9)
        trade.realised_r -= cost_r
        trade.exit_i, trade.exit_reason = i, reason

    from v2_trade_lifecycle import (
        V2Observation, V2TradeView, decide_v2_lifecycle,
    )
    decision = decide_v2_lifecycle(
        V2TradeView(
            trade.direction, trade.entry, trade.sl, trade.tp1, trade.tp2,
            trade.tp1_done, trade.tp2_done, trade.mfe_r,
        ),
        V2Observation(
            float(high), float(low), float(close),
            max(0, i - trade.entry_i) * 300.0,
            htf_bias, htf_trail_anchor,
        ),
        initial_risk=risk,
        hard_stop_seconds=max_bars * 300.0,
    )

    if decision.action == "sl":
        stop_r = ((float(decision.price) - trade.entry) / risk if trade.direction == "LONG"
                  else (trade.entry - float(decision.price)) / risk)
        trade.realised_r += trade.remaining * stop_r
        _close("sl")
    elif decision.action == "htf_reversal":
        _close("htf_reversal", mark_price=float(decision.price))
    elif decision.action == "tp1":
        trade.realised_r += trade.tp1_frac * abs(trade.tp1 - trade.entry) / risk
        trade.remaining -= trade.tp1_frac
        trade.tp1_done = True
        if decision.new_sl is not None:
            trade.sl = float(decision.new_sl)
    elif decision.action == "tp2":
        tp2_take = min(tp2_frac, trade.remaining)
        trade.realised_r += tp2_take * abs(trade.tp2 - trade.entry) / risk
        trade.remaining -= tp2_take
        trade.tp2_done = True
        if trade.remaining <= 1e-9:
            _close("tp2")
        elif decision.new_sl is not None:
            trade.sl = float(decision.new_sl)
    elif decision.action in ("time_stop", "hard_time_stop"):
        _close(decision.action, mark_price=float(decision.price))
    elif decision.new_sl is not None:
        trade.sl = float(decision.new_sl)

    return trade.exit_i is not None


def v2_unclog_bars_5m() -> int:
    """Miękki unclog (Freqtrade): DAYTRADING_V2_TIME_STOP_HOURS w barach 5m."""
    hours = float(getattr(config, "DAYTRADING_V2_TIME_STOP_HOURS", 24.0) or 24.0)
    return max(12, int(round(hours * 12.0)))


def _v2_unclog_due(trade: ReplayTradeV2, i: int, close: float, risk: float) -> bool:
    """Martwy trade: brak TP1, wiek ≥ 24h, mark R < MIN_R, nigdy nie było MFE ≥ skip."""
    if trade.tp1_done:
        return False
    if (i - trade.entry_i) < v2_unclog_bars_5m():
        return False
    skip_mfe = float(getattr(config, "DAYTRADING_V2_UNCLOG_SKIP_MFE_R", 0.5) or 0.0)
    if skip_mfe > 0 and float(getattr(trade, "mfe_r", 0) or 0) >= skip_mfe:
        return False
    mark_r = ((close - trade.entry) / risk if trade.direction == "LONG"
              else (trade.entry - close) / risk)
    min_r = float(getattr(config, "DAYTRADING_V2_TIME_STOP_MIN_R", 0.35) or 0.0)
    return mark_r < min_r


def v2_max_bars_5m() -> int:
    """Hard time-stop w barach 5m = DAYTRADING_V2_HARD_TIME_STOP_HOURS (nie magiczne 400)."""
    hours = float(getattr(config, "DAYTRADING_V2_HARD_TIME_STOP_HOURS", 96.0) or 96.0)
    return max(24, int(round(hours * 12.0)))


def replay_daytrading_v2(
    ohlcv_5m: Dict[str, List[float]],
    signal_at: Callable[[int], Optional[dict]],
    htf_bias_at: Optional[Callable[[int], Optional[str]]] = None,
    htf_trail_anchor_at: Optional[Callable[[int, str], Optional[float]]] = None,
    notify_exit: Optional[Callable[[str, str, str, float], None]] = None,
    fee_frac_round_trip: float = 0.0012,
    slippage_frac_round_trip: float = 0.0006,
    max_bars: int | None = None,
    tp1_frac: float = 0.5,
    tp2_frac: float = 0.3,
    funding: Optional[List[dict]] = None,
) -> dict:
    """Replay V2: SL/TP1/TP2 ze swingu 1h (z sygnalu), TP3 trailing po TP2
    kotwiczony o 1h, wyjscie na odwrocenie biasu 4h/1D. BRAK wyjscia na
    zniknieciu setupu 15m (punkt 10-11 planu) - jedyne wyjscia to: SL,
    odwrocenie 4h/1D, TP1(czesciowy)/TP2(czesciowy)/TP3(trailing reszty),
    hard_time_stop jako ostatnia deska ratunku.

    `htf_bias_at(i)` -> "LONG"/"SHORT"/"NEUTRAL"/None (brak danych = brak
    odwrocenia, nie blokuje). `htf_trail_anchor_at(i, direction)` -> cena
    kotwicy trailingu 1h (np. ostatni potwierdzony swing low/high) albo None
    (bez zmiany SL w tym barze). `notify_exit(symbol, side, reason, ts)`
    wolane po kazdym zamknieciu - pozwala silnikowi V2 zasilic wlasne
    hamulce czestotliwosci; symbol brany z sig["symbol"] przy wejsciu."""
    if max_bars is None:
        max_bars = v2_max_bars_5m()
    opens = list(ohlcv_5m.get("opens") or [])
    highs = list(ohlcv_5m.get("highs") or [])
    lows = list(ohlcv_5m.get("lows") or [])
    closes = list(ohlcv_5m.get("closes") or opens)
    timestamps = list(ohlcv_5m.get("timestamps") or [])
    n = min(len(opens), len(highs), len(lows), len(closes))
    trades: List[ReplayTradeV2] = []
    active: Optional[ReplayTradeV2] = None
    pending: Optional[dict] = None
    active_symbol: Optional[str] = None

    for i in range(n):
        if pending is not None and active is None:
            sig = pending["sig"]
            fill, kind = resolve_v2_fill(
                sig, i, pending["signal_i"],
                float(opens[i]), float(highs[i]), float(lows[i]),
            )
            if kind == "expired":
                pending = None
            elif fill is not None:
                sig = dict(sig)
                sig["fill_kind"] = kind
                active = _open_trade_v2(i, fill, sig, tp1_frac)
                active_symbol = sig.get("symbol")
                if active is not None:
                    from v2_profiles import replay_slip_round_trip
                    active.slip_rt = replay_slip_round_trip(
                        active_symbol, ohlcv_5m, i, fill, slippage_frac_round_trip,
                    )
                pending = None

        if active is not None:
            htf_bias = htf_bias_at(i) if htf_bias_at is not None else None
            htf_anchor = htf_trail_anchor_at(i, active.direction) if htf_trail_anchor_at is not None else None
            closed = _process_trade_bar_v2(
                active, i, float(highs[i]), float(lows[i]), float(closes[i]),
                htf_bias, htf_anchor, fee_frac_round_trip, slippage_frac_round_trip,
                max_bars, tp2_frac,
            )
            if closed:
                trades.append(active)
                ts = timestamps[i] if i < len(timestamps) else i
                if notify_exit is not None and active_symbol:
                    notify_exit(active_symbol, active.direction, active.exit_reason,
                               float(ts) / 1000.0 if timestamps else float(ts))
                active = None

        sig = signal_at(i) if i + 1 < n else None
        if active is None and pending is None and i + 1 < n:
            if sig and sig.get("direction") in ("LONG", "SHORT") and not sig.get("reject_reason"):
                pending = {"sig": sig, "signal_i": i}

    if funding:
        for t in trades:
            apply_observed_funding(t, timestamps, funding)
    rs = [t.realised_r for t in trades]
    return {
        "trades": trades,
        "count": len(trades),
        "win_rate": (sum(r > 0 for r in rs) / len(rs)) if rs else 0.0,
        "net_r": sum(rs),
        "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
        "execution": "next_open_stop_first_v2",
    }


# ============================================================
# Portfelowy replay V2 - konkurencja o sloty MAX_POSITIONS MIEDZY symbolami
# (punkt 25/30 planu). replay_daytrading_v2() powyzej liczy kazdy symbol
# NIEZALEZNIE (nieograniczona wlasna ksiazka pozycji) - dobre do oceny
# jakosci sygnalu per-symbol, ale nie odzwierciedla live, gdzie
# MAX_POSITIONS jest DZIELONY przez cale uniwersum. Ten silnik to naprawia:
# jedna, wspolna ksiazka pozycji, jedna chronologiczna petla po WSZYSTKICH
# symbolach rownoczesnie.
#
# Zalozenie: wszystkie symbole dziela ta sama siatke barow 5m (te same
# indeksy 0..n-1, ten sam zakres czasu) - typowy przypadek przy pobieraniu
# replay w tym samym oknie/interwale dla calego uniwersum. Jesli dlugosci
# sie roznia, uzywany jest wspolny (najkrotszy) zakres.
# ============================================================

def portfolio_replay_v2(
    symbols_data: Dict[str, dict],
    max_positions: int,
    fee_frac_round_trip: float = 0.0012,
    slippage_frac_round_trip: float = 0.0006,
    max_bars: int | None = None,
    tp1_frac: float = 0.5,
    tp2_frac: float = 0.3,
    max_same_direction: int | None = None,
    latency_ms: int = 0,
    cancel_latency_ms: int = 0,
    touch_model: str = "pessimistic",
    maker_fee: float | None = None,
    taker_fee: float | None = None,
    random_seed: int = 0,
) -> dict:
    """`symbols_data[symbol]` = {"ohlcv_5m":..., "signal_at":..., opcjonalnie
    "htf_bias_at":..., "htf_trail_anchor_at":...} - dokladnie ten sam ksztalt
    danych, co pojedynczy replay_daytrading_v2(), tylko przetwarzany razem
    dla calego uniwersum z jednym, dzielonym limitem otwartych pozycji.

    Gdy sygnal jest dobry, ale brak wolnego slotu - PRZEPADA (nie kolejkuje
    sie na pozniej), dokladnie jak w live: nowy sygnal na innym symbolu w tym
    samym momencie po prostu nie ma gdzie wejsc."""
    if not symbols_data:
        return {"trades": [], "count": 0, "win_rate": 0.0, "net_r": 0.0, "avg_r": 0.0,
                "rejected_for_slots": 0, "by_symbol": {}}
    if max_bars is None:
        max_bars = v2_max_bars_5m()

    n = min(len(data["ohlcv_5m"].get("opens") or []) for data in symbols_data.values())
    open_positions: Dict[str, ReplayTradeV2] = {}
    pending: Dict[str, dict] = {}
    all_trades: List[tuple] = []  # (symbol, ReplayTradeV2)
    rejected_for_slots = 0
    rejected_for_direction = 0
    rejected_funnel: Dict[str, int] = {}
    open_at_end = 0
    if max_same_direction is None:
        max_same_direction = max_positions
    max_same_direction = max(1, int(max_same_direction))

    for i in range(n):
        # 1) Wypelnij pending (z poprzedniego bara), o ile jest wolny slot -
        # w kolejnosci alfabetycznej symboli, zeby wynik byl deterministyczny
        # (bez ukrytej zaleznosci od kolejnosci w dict).
        for symbol in sorted(pending.keys()):
            if symbol in open_positions:
                continue
            if len(open_positions) >= max_positions:
                rejected_for_slots += 1
                pending.pop(symbol, None)
                continue
            row = pending[symbol]
            sig = row["sig"] if isinstance(row, dict) and "sig" in row else row
            signal_i = row.get("signal_i", i - 1) if isinstance(row, dict) else i - 1
            earliest_i = row.get("earliest_i", signal_i + 1) if isinstance(row, dict) else signal_i + 1
            if i < int(earliest_i):
                continue
            ohlcv = symbols_data[symbol]["ohlcv_5m"]
            cancel_bars = (max(0, int(cancel_latency_ms)) + 299_999) // 300_000
            fill, kind = resolve_v2_fill(
                sig, i, signal_i,
                float(ohlcv["opens"][i]), float(ohlcv["highs"][i]), float(ohlcv["lows"][i]),
                timeout_bars=v2_limit_timeout_5m() + cancel_bars,
            )
            if (fill is not None and kind == "limit" and
                    str(touch_model or "").lower() == "probabilistic"):
                token = f"{int(random_seed)}:{symbol}:{signal_i}:{i}".encode("utf-8")
                u = int(hashlib.sha256(token).hexdigest()[:8], 16) / 0xFFFFFFFF
                if u > 0.50:
                    fill, kind = None, ""
            if kind == "expired":
                pending.pop(symbol, None)
                continue
            if fill is None:
                continue
            direction = str(sig.get("direction") or "").upper()
            same_open = sum(1 for trade in open_positions.values() if trade.direction == direction)
            if same_open >= max_same_direction:
                rejected_for_direction += 1
                pending.pop(symbol, None)
                continue
            pending.pop(symbol, None)
            sig = dict(sig)
            sig["fill_kind"] = kind
            trade = _open_trade_v2(i, fill, sig, tp1_frac)
            if trade is not None:
                from v2_profiles import replay_slip_round_trip
                trade.slip_rt = replay_slip_round_trip(
                    symbol, ohlcv, i, fill, slippage_frac_round_trip,
                )
                mf = float(maker_fee if maker_fee is not None else getattr(config, "MAKER_FEE", 0.0002))
                tf = float(taker_fee if taker_fee is not None else getattr(config, "TAKER_FEE", 0.0006))
                trade.fee_rt = (mf + tf) if kind == "limit" else (2.0 * tf)
                open_positions[symbol] = trade
                notify_entry = symbols_data[symbol].get("notify_entry_fill")
                swing_end = (((sig.get("swing") or {}).get("end") or {}).get("index"))
                if notify_entry is not None and swing_end is not None:
                    notify_entry(symbol, int(swing_end))

        # 2) Aktualizuj/sprawdz wyjscia dla WSZYSTKICH otwartych pozycji.
        for symbol in list(open_positions.keys()):
            trade = open_positions[symbol]
            data = symbols_data[symbol]
            ohlcv = data["ohlcv_5m"]
            if i >= len(ohlcv.get("closes") or []):
                continue
            htf_bias_at = data.get("htf_bias_at")
            htf_trail_anchor_at = data.get("htf_trail_anchor_at")
            htf_bias = htf_bias_at(i) if htf_bias_at is not None else None
            htf_anchor = htf_trail_anchor_at(i, trade.direction) if htf_trail_anchor_at is not None else None
            closed = _process_trade_bar_v2(
                trade, i, float(ohlcv["highs"][i]), float(ohlcv["lows"][i]), float(ohlcv["closes"][i]),
                htf_bias, htf_anchor, fee_frac_round_trip, slippage_frac_round_trip,
                max_bars, tp2_frac,
            )
            if closed:
                all_trades.append((symbol, trade))
                del open_positions[symbol]
                notify_exit = data.get("notify_exit")
                if notify_exit is not None:
                    timestamps = ohlcv.get("timestamps") or []
                    ts = timestamps[i] if i < len(timestamps) else i
                    when = float(ts) / 1000.0 if timestamps else float(ts)
                    try:
                        notify_exit(symbol, trade.direction, trade.exit_reason, when, pnl=trade.realised_r)
                    except TypeError:
                        notify_exit(symbol, trade.direction, trade.exit_reason, when)

        # 3) Nowe sygnaly - tylko dla symboli bez otwartej pozycji i bez juz
        # oczekujacego wejscia.
        for symbol, data in symbols_data.items():
            if symbol in open_positions or symbol in pending:
                continue
            if i + 1 >= n:
                continue
            sig = data["signal_at"](i)
            if not sig:
                continue
            reason = str(sig.get("reject_reason") or "")
            if reason:
                rejected_funnel[reason] = rejected_funnel.get(reason, 0) + 1
                continue
            if sig.get("direction") in ("LONG", "SHORT"):
                final_gate = data.get("final_gate")
                if final_gate is not None:
                    ok, gate_reason = final_gate(sig)
                    if not ok:
                        gate_reason = str(gate_reason or "FINAL_GATE")
                        rejected_funnel[gate_reason] = rejected_funnel.get(gate_reason, 0) + 1
                        continue
                latency_bars = max(1, (max(0, int(latency_ms)) + 299_999) // 300_000)
                pending[symbol] = {"sig": sig, "signal_i": i, "earliest_i": i + latency_bars}

    # Jawnie rozlicz pozycje pozostajace na koncu okna. Bez tego znikaly z
    # metryk i sztucznie zmienialy liczbe transakcji oraz Net R.
    for symbol, trade in list(open_positions.items()):
        data = symbols_data[symbol]
        ohlcv = data["ohlcv_5m"]
        last_i = min(n, len(ohlcv.get("closes") or [])) - 1
        if last_i < 0:
            continue
        close = float(ohlcv["closes"][last_i])
        risk = max(float(trade.initial_risk), 1e-12)
        mark_r = ((close - trade.entry) / risk if trade.direction == "LONG" else
                  (trade.entry - close) / risk)
        trade.realised_r += trade.remaining * mark_r
        costs = (float(trade.fee_rt) if trade.fee_rt is not None else fee_frac_round_trip)
        costs += float(trade.slip_rt) if trade.slip_rt is not None else slippage_frac_round_trip
        trade.realised_r -= costs / max(risk / trade.entry, 1e-9)
        trade.exit_i, trade.exit_reason = last_i, "window_end_mark"
        all_trades.append((symbol, trade))
        open_at_end += 1
        notify_exit = data.get("notify_exit")
        if notify_exit is not None:
            timestamps = ohlcv.get("timestamps") or []
            ts = timestamps[last_i] if last_i < len(timestamps) else last_i
            when = float(ts) / 1000.0 if timestamps else float(ts)
            try:
                notify_exit(symbol, trade.direction, trade.exit_reason, when, pnl=trade.realised_r)
            except TypeError:
                notify_exit(symbol, trade.direction, trade.exit_reason, when)

    for symbol, trade in all_trades:
        data = symbols_data.get(symbol) or {}
        ohlcv = data.get("ohlcv_5m") or {}
        apply_observed_funding(trade, ohlcv.get("timestamps") or [], data.get("funding") or [])

    rs = [trade.realised_r for _, trade in all_trades]
    by_symbol: Dict[str, dict] = {}
    for symbol, trade in all_trades:
        by_symbol.setdefault(symbol, {"trades": 0, "net_r": 0.0})
        by_symbol[symbol]["trades"] += 1
        by_symbol[symbol]["net_r"] += trade.realised_r

    return {
        "trades": [t for _, t in all_trades],
        "trades_with_symbol": all_trades,
        "count": len(all_trades),
        "win_rate": (sum(r > 0 for r in rs) / len(rs)) if rs else 0.0,
        "net_r": sum(rs),
        "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
        "rejected_for_slots": rejected_for_slots,
        "rejected_for_direction": rejected_for_direction,
        "rejected_funnel": rejected_funnel,
        "open_at_end": open_at_end,
        "by_symbol": by_symbol,
        "max_positions": max_positions,
        "max_same_direction": max_same_direction,
        "execution": "portfolio_next_open_stop_first_v2",
    }
