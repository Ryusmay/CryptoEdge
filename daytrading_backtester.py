"""Event-driven daytrading replay with conservative intrabar execution.

Signals are decided from data closed at bar t and filled at bar t+1 open.
When a stop and target are both touched in one candle, the stop is assumed to
occur first. This deliberately avoids optimistic OHLC path assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
from typing import Callable, Dict, List, Optional
import config


class AsOfBlofinFeed:
    """Historical feed enforcing an as-of boundary for production strategy code."""
    def __init__(self, bundle: Dict[str, dict]):
        self.bundle = bundle
        self.asof_ts = 0
        self._timestamps = {
            tf: [int(value) for value in ((data or {}).get("timestamps") or (data or {}).get("ts") or [])]
            for tf, data in bundle.items()
        }

    def fetch_klines_ohlcv(self, symbol: str, bar: str = "5m", limit: int = 120) -> dict:
        tf = {"1H": "1h", "4H": "4h", "1D": "1d", "1W": "1w"}.get(bar, bar)
        src = self.bundle.get(tf) or {}
        timestamps = self._timestamps.get(tf) or []
        end = bisect_right(timestamps, int(self.asof_ts))
        if end <= 0:
            return {}
        start = max(0, end - int(limit))
        out = {}
        for key in ("opens", "highs", "lows", "closes", "volumes"):
            arr = list(src.get(key) or [])
            out[key] = arr[start:min(end, len(arr))]
        out["timestamps"] = timestamps[start:end]
        out["candles_confirmed"] = True
        return out

    def fetch_order_book(self, symbol: str, size: int = 20) -> dict:
        return {}

    def fetch_funding_rate(self, symbol: str) -> dict:
        return {}


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
    from daytrading_engine_v2 import DayTradingEngineV2
    feeder = AsOfDataFeeder(bundle)
    engine = DayTradingEngineV2(feeder)
    drive = bundle.get(drive_tf) or {}
    timestamps = list(drive.get("timestamps") or drive.get("ts") or [])
    closes = list(drive.get("closes") or [])

    def signal_at(i: int) -> dict:
        feeder.blofin.asof_ts = int(timestamps[i])
        now_ts = float(timestamps[i]) / 1000.0
        return engine.evaluate({"symbol": symbol, "price": closes[i]}, now_ts=now_ts)

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
        if not frame_1d.get("closes") or not frame_4h.get("closes"):
            return None
        ind_1d = compute_indicators(frame_1d, tf="1d")
        ind_4h = compute_indicators(frame_4h, tf="4h")
        if not ind_1d or not ind_4h:
            return None
        bias_1d = _bias_from_indicators(ind_1d)
        bias_4h = _bias_from_indicators(ind_4h)
        if bias_1d == "NEUTRAL" or bias_4h == "NEUTRAL":
            return "NEUTRAL"
        if bias_1d != bias_4h:
            return "NEUTRAL"  # niespojnosc HTF traktowana jako brak jednoznacznego biasu, nie jako odwrocenie
        return bias_1d

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
            min_move_atr=float(getattr(config, "DAYTRADING_V2_SWING_MIN_MOVE_ATR", 1.5)),
            min_bars=int(getattr(config, "DAYTRADING_V2_SWING_MIN_BARS", 3)),
            right_confirm=int(getattr(config, "DAYTRADING_V2_SWING_RIGHT_CONFIRM", 2)),
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
        buffer = float(getattr(config, "DAYTRADING_V2_SL_ATR_BUFFER", 0.5)) * atr_1h
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
# - Dodatkowe wyjscie: odwrocenie biasu 4h/1D (patrz htf_bias_at).
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


def _is_htf_reversed(bias: Optional[str], entry_direction: str) -> bool:
    """True tylko jesli bias 4h/1D jest ZNANY i JEDNOZNACZNIE przeciwny
    kierunkowi wejscia. Brak danych (None) albo NEUTRAL nigdy nie zamyka
    pozycji - to nie jest twardy blok, tylko realne odwrocenie."""
    if bias in (None, "NEUTRAL"):
        return False
    return bias != entry_direction


def _open_trade_v2(i: int, entry: float, sig: dict, tp1_frac: float) -> Optional[ReplayTradeV2]:
    """Buduje nowa pozycje V2 z sygnalu (SL/TP1/TP2 juz obliczone przez
    silnik ze swingu 1h) - wspoldzielone przez replay_daytrading_v2 i
    portfolio_replay_v2, zeby nie duplikowac tej samej matematyki dwa razy."""
    risk = abs(float(sig["price"]) - float(sig["sl_price"]))
    if risk <= 0:
        return None
    direction = sig["direction"]
    sign = 1.0 if direction == "LONG" else -1.0
    tp1_dist = abs(float(sig["tp1_price"]) - float(sig["price"]))
    tp2_dist = abs(float(sig["tp2_price"]) - float(sig["price"]))
    return ReplayTradeV2(
        direction, i, entry, entry - sign * risk,
        entry + sign * (tp1_dist / risk) * risk,
        entry + sign * (tp2_dist / risk) * risk,
        float(tp1_frac), risk, highest=entry, lowest=entry,
    )


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

    def _close(reason: str, mark_price: Optional[float] = None) -> None:
        if mark_price is not None:
            mark_r = ((mark_price - trade.entry) / risk if trade.direction == "LONG"
                      else (trade.entry - mark_price) / risk)
            trade.realised_r += trade.remaining * mark_r
        cost_r = (fee_frac_round_trip + slippage_frac_round_trip) / max(risk / trade.entry, 1e-9)
        trade.realised_r -= cost_r
        trade.exit_i, trade.exit_reason = i, reason

    if _hit(trade.direction, low, high, trade.sl, "sl"):
        stop_r = ((trade.sl - trade.entry) / risk if trade.direction == "LONG"
                  else (trade.entry - trade.sl) / risk)
        trade.realised_r += trade.remaining * stop_r
        _close("sl")
    elif _is_htf_reversed(htf_bias, trade.direction):
        _close("htf_reversal", mark_price=float(close))
    elif not trade.tp1_done and _hit(trade.direction, low, high, trade.tp1, "tp"):
        trade.realised_r += trade.tp1_frac * abs(trade.tp1 - trade.entry) / risk
        trade.remaining -= trade.tp1_frac
        trade.tp1_done = True
        trade.sl = trade.entry  # breakeven po TP1
    elif trade.tp1_done and not trade.tp2_done and _hit(trade.direction, low, high, trade.tp2, "tp"):
        tp2_take = min(tp2_frac, trade.remaining)
        trade.realised_r += tp2_take * abs(trade.tp2 - trade.entry) / risk
        trade.remaining -= tp2_take
        trade.tp2_done = True
        if trade.remaining <= 1e-9:
            _close("tp2")
    elif i - trade.entry_i >= max_bars:
        _close("hard_time_stop", mark_price=float(close))

    if trade.exit_i is None and trade.tp2_done and htf_trail_anchor is not None:
        if trade.direction == "LONG":
            trade.sl = max(trade.sl, float(htf_trail_anchor))
        else:
            trade.sl = min(trade.sl, float(htf_trail_anchor))

    return trade.exit_i is not None


def replay_daytrading_v2(
    ohlcv_5m: Dict[str, List[float]],
    signal_at: Callable[[int], Optional[dict]],
    htf_bias_at: Optional[Callable[[int], Optional[str]]] = None,
    htf_trail_anchor_at: Optional[Callable[[int, str], Optional[float]]] = None,
    notify_exit: Optional[Callable[[str, str, str, float], None]] = None,
    fee_frac_round_trip: float = 0.0012,
    slippage_frac_round_trip: float = 0.0006,
    max_bars: int = 400,
    tp1_frac: float = 0.5,
    tp2_frac: float = 0.3,
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
            active = _open_trade_v2(i, float(opens[i]), pending, tp1_frac)
            active_symbol = pending.get("symbol")
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
                pending = sig

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
    max_bars: int = 400,
    tp1_frac: float = 0.5,
    tp2_frac: float = 0.3,
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

    n = min(len(data["ohlcv_5m"].get("opens") or []) for data in symbols_data.values())
    open_positions: Dict[str, ReplayTradeV2] = {}
    pending: Dict[str, dict] = {}
    all_trades: List[tuple] = []  # (symbol, ReplayTradeV2)
    rejected_for_slots = 0

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
            sig = pending.pop(symbol)
            ohlcv = symbols_data[symbol]["ohlcv_5m"]
            trade = _open_trade_v2(i, float(ohlcv["opens"][i]), sig, tp1_frac)
            if trade is not None:
                open_positions[symbol] = trade

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
                    notify_exit(symbol, trade.direction, trade.exit_reason,
                               float(ts) / 1000.0 if timestamps else float(ts))

        # 3) Nowe sygnaly - tylko dla symboli bez otwartej pozycji i bez juz
        # oczekujacego wejscia.
        for symbol, data in symbols_data.items():
            if symbol in open_positions or symbol in pending:
                continue
            if i + 1 >= n:
                continue
            sig = data["signal_at"](i)
            if sig and sig.get("direction") in ("LONG", "SHORT") and not sig.get("reject_reason"):
                pending[symbol] = sig

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
        "by_symbol": by_symbol,
        "max_positions": max_positions,
        "execution": "portfolio_next_open_stop_first_v2",
    }


