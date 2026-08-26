"""Silnik daytrading V2 - hierarchia timeframe:

1H  -> kierunek (bias LONG/SHORT) + mapa setupu: swing, Fibo, SL, TP.
4H  -> kontekst (align/oppose/NA), jak 1D. Nie veto, oppose = mniejszy size.
1D  -> kontekst w UI (align/oppose/NA), NIE bramka.
15m -> jedyny trigger wejscia (retest/reclaim/potwierdzenie).
5m  -> tylko potwierdzenie wejscia (1-2 swiece). Nie sygnal, nie SL, nie invalidation.

Zasady wejscia/wyjscia:
- Jedna pozycja na impuls naraz. Po zamknieciu slot wraca od razu.
- Brak kary czasowej po TP/SL. Seria 5 przegranych na parze = 15 min pauzy.
- Sygnal na zamknieciu 15m, fill na nastepnym open.
- 15m setup "znika" -> pozycja NIE jest zamykana z tego powodu.
- 5m brak danych != twardy blok.
"""

from __future__ import annotations

import time
from typing import Dict, Iterable, List, Optional

import config
from swing_structure import find_last_confirmed_swing, swing_fib_retracement, swing_fib_extension
from blofin_ws import PUBLIC_WS
from v2_profiles import profile_for, params_for, refresh_volume_ranks
from v2_market_snapshot import V2MarketSnapshot
from signal_research import rank_candidates
from setup_quality import (
    candle_rejection_features, probability_quality_multiplier,
    rsi_structure_features, structure_aware_target,
)
from universe_policy import crypto_perpetual_allowed

try:
    from indicators_full import compute_indicators
except ImportError:  # pragma: no cover
    def compute_indicators(ohlcv, tf="1h"):
        return {}


def _finite(value, default=0.0) -> float:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _fib_key(ratio) -> str:
    """Klucz w swing_fib_retracement: 0.5 -> '0.5', 0.382 -> '0.382'."""
    r = _finite(ratio)
    text = f"{r:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


_BAR_SECONDS = {
    "5m": 300, "15m": 900, "15M": 900,
    "1H": 3600, "1h": 3600,
    "4H": 14400, "4h": 14400,
    "1D": 86400, "1d": 86400,
}


def _last_ts_seconds(frame: dict) -> Optional[float]:
    ts_list = (frame or {}).get("timestamps") or (frame or {}).get("ts") or []
    if not ts_list:
        return None
    try:
        t = float(ts_list[-1])
    except (TypeError, ValueError):
        return None
    if t > 1e16:  # ns
        t /= 1e9
    elif t > 1e11:  # ms (unix ms ~1.7e12)
        t /= 1000.0
    return t if t > 0 else None


def klines_stale_reason(frames: dict, now_ts: Optional[float] = None,
                       tfs: Iterable[str] = ("4H", "1H", "15m")) -> Optional[str]:
    """V2_STALE_KLINES gdy brakuje już *następnej* zamkniętej świecy.

    Parser drop_unclosed zostawia OPEN ostatniej ZAMKNIĘTEJ. Wiek od open
    = 1 bar w momencie zamknięcia. Próg `bar + slack` (stary) robił 4H
    martwe ~10 min po każdym close (logi: STALE_4H ~15470s o 00:18 UTC).
    Teraz: 2 * bar + slack — pełny bieżący bar na zamknięcie + 10 min.
    slack<=0 wyłącza filtr.
    evaluate() woła z tfs=("1H","15m") — 4H jest kontekstem, nie bramką.
    """
    slack = _finite(getattr(config, "STALE_KLINES_SECONDS", 600), 600)
    if slack <= 0:
        return None
    now_ts = time.time() if now_ts is None else float(now_ts)
    for tf in tfs:
        last = _last_ts_seconds((frames or {}).get(tf) or {})
        if last is None:
            continue
        max_age = 2 * _BAR_SECONDS.get(tf, 3600) + slack
        age = now_ts - last
        if age > max_age:
            return f"V2_STALE_KLINES_{tf}({age:.0f}s)"
    return None


def _bias_from_indicators(ind: Optional[dict], min_agree: Optional[int] = None) -> str:
    """LONG/SHORT/NEUTRAL z pojedynczego interwalu: EMA trend + SuperTrend.
    Uzywane identycznie dla 1D i 4h (pkt 1, 2). Trzy sygnaly (price>EMA_slow,
    EMA_fast>EMA_slow, SuperTrend up/down) glosuja; domyslnie
    (DAYTRADING_V2_BIAS_MIN_AGREE=2) wystarczy wiekszosc 2 z 3 zamiast
    wymagac jednomyslnosci wszystkich trzech - patrz config.py przy tej
    stalej po uzasadnienie (telemetria z 21.08.2026). Kazdy z 3 sygnalow
    glosuje dokladnie na jedna strone, WYJATEK: SuperTrend z is_up=None
    (niewystarczajace dane) nie glosuje na zadna strone (wstrzymuje sie),
    wiec suma glosow moze wynosic 2 zamiast 3."""
    if not ind:
        return "NEUTRAL"
    if min_agree is None:
        min_agree = int(_finite(getattr(config, "DAYTRADING_V2_BIAS_MIN_AGREE", 2), 2))
    min_agree = max(1, min(3, min_agree))
    st = ind.get("supertrend") or {}
    price_up = bool(ind.get("price_above_ema_slow"))
    ema_up = bool(ind.get("ema_fast_above_slow"))
    st_up = st.get("is_up") is True
    st_down = st.get("is_up") is False
    long_votes = sum((price_up, ema_up, st_up))
    short_votes = sum((not price_up, not ema_up, st_down))
    if long_votes >= min_agree and long_votes > short_votes:
        return "LONG"
    if short_votes >= min_agree and short_votes > long_votes:
        return "SHORT"
    return "NEUTRAL"


class DayTradingEngineV2:
    def __init__(self, feeder=None, expectancy_calibrator=None):
        self.feeder = feeder
        self._expectancy_calibrator = expectancy_calibrator
        # The process-wide STORE belongs to live runtime. Replay and test
        # feeders own point-in-time data and must not be shadowed by candles
        # left in STORE by an earlier session/test.
        explicit_store = getattr(feeder, "use_market_store", None)
        self._use_market_store = (
            bool(explicit_store) if explicit_store is not None
            else feeder is not None and feeder.__class__.__module__ == "data_feeder"
        )
        # Stan miedzy wywolaniami (per-instancja silnika - w backteście swiezy
        # silnik na kazde okno/symbol, w runtime jeden dlugozyjacy silnik):
        self._consumed_swing_end: Dict[str, int] = {}   # symbol -> last swing end index
        self._swing_entry_count: Dict[tuple, int] = {}  # (symbol, swing_end) -> liczba faktycznych filli
        self._active_swing_key: Dict[str, tuple] = {}   # SYMBOL -> swing_key aktualnej pozycji
        self._loss_streak: Dict[str, int] = {}          # SYMBOL -> kolejne przegrane
        self._loss_streak_ts: Dict[str, float] = {}     # SYMBOL -> ts ostatniej przegranej
        # 21.08.2026 (druga iteracja rate-limitingu, patrz generate()):
        # symbole, ktore juz przynajmniej raz przeszly przez _fetch_frames w
        # tej instancji silnika - a wiec ich ohlc_cache w blofin_feed.py jest
        # "cieply" i dalsze cykle trzyma go swiezym tanio (TTL+WS-merge), nie
        # wymagajac powtarzania calej kaskady REST. Uzywane do dawkowania
        # cold-startu partiami (DAYTRADING_V2_COLD_START_BATCH_SIZE) zamiast
        # pobierania calego uniwersum naraz.
        self._warmed_symbols: set = set()
        self._funding_cache: Dict[str, tuple] = {}
        self._indicator_cache: Dict[tuple, dict] = {}
        # Runtime may scan every few seconds, but an entry decision is valid
        # only once per newly closed 15m candle.  Replay already enforces the
        # same cadence at portfolio-clock level.
        self._last_decision_15m: Dict[str, float] = {}
        self._decision_cache: Dict[str, dict] = {}

    def _cached_indicators(self, symbol: str, frame: dict, tf: str) -> Optional[dict]:
        timestamps = frame.get("timestamps") or frame.get("ts") or ()
        if not timestamps:
            return compute_indicators(frame, tf=tf)
        key = (str(symbol).upper(), str(tf).lower(), int(timestamps[-1]), len(timestamps))
        hit = self._indicator_cache.get(key)
        if hit is not None:
            return hit
        value = compute_indicators(frame, tf=tf)
        if value:
            self._indicator_cache[key] = value
            # Replay is bounded by recent bars; runtime cache also stays small.
            if len(self._indicator_cache) > 4096:
                for old in list(self._indicator_cache)[:1024]:
                    self._indicator_cache.pop(old, None)
        return value

    def _absorb_store_warmup(self, symbols: List[str]) -> None:
        """Pary już w STORE (warmup backfill 4H+1H) nie idą w COLD_START."""
        need_4h = int(_finite(getattr(config, "WARMUP_NEED_4H", 40), 40))
        need_1h = int(_finite(getattr(config, "WARMUP_NEED_1H", 80), 80))
        try:
            from market_store import STORE
        except Exception:
            return
        for s in symbols:
            if s in self._warmed_symbols:
                continue
            if STORE.candle_count(s, "4H") >= need_4h and STORE.candle_count(s, "1H") >= need_1h:
                self._warmed_symbols.add(s)

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    def _fetch(self, symbol: str, bar: str, limit: int) -> dict:
        if self._use_market_store:
            try:
                from market_store import STORE
                stored = STORE.get_ohlcv(symbol, bar) or {}
                if stored.get("closes") and (
                    bar not in ("4H", "1H", "15m") or not klines_stale_reason({bar: stored})
                ):
                    return stored
            except Exception:
                pass
        feed = getattr(self.feeder, "blofin", None)
        data = feed.fetch_klines_ohlcv(symbol, bar=bar, limit=limit) or {} if feed is not None else {}
        if bar not in ("4H", "1H", "15m"):
            return data
        stale = klines_stale_reason({bar: data}) if data else "empty"
        if not stale and data.get("closes"):
            return data
        bn = getattr(self.feeder, "binance", None)
        if bn is None or not hasattr(bn, "fetch_klines_ohlcv"):
            return data
        bn_bar = {"4H": "4h", "1H": "1h", "15m": "15m"}.get(bar, bar)
        try:
            alt = bn.fetch_klines_ohlcv(symbol, interval=bn_bar, limit=limit) or {}
        except Exception as exc:
            print(f"[V2] Binance kline fallback {symbol} {bar}: {exc}")
            return data
        if alt.get("closes"):
            print(f"[V2] kline {symbol} {bar}: BloFin stale/empty -> Binance ({len(alt.get('closes') or [])} barów)")
            return alt
        return data

    def _fetch_frames(self, symbol: str) -> Dict[str, dict]:
        return {
            "1D": self._fetch(symbol, "1D", 260),
            "4H": self._fetch(symbol, "4H", 260),
            "1H": self._fetch(symbol, "1H", 260),
            "15m": self._fetch(symbol, "15m", 300),
            "5m": self._fetch(symbol, "5m", 60),
        }

    def _funding_for(self, symbol: str, coin: dict) -> dict:
        if not bool(getattr(config, "FUNDING_ENABLED", True)):
            return {}
        existing = coin.get("funding")
        if isinstance(existing, dict) and existing.get("funding_rate") is not None:
            return existing
        now = time.time()
        feed = getattr(self.feeder, "blofin", None)
        historical_asof = getattr(feed, "asof_ts", None) if feed is not None else None
        hit = self._funding_cache.get(symbol)
        if historical_asof is None and hit and now - float(hit[0]) < 60.0:
            return hit[1]
        fr = {}
        if feed is not None and hasattr(feed, "fetch_funding_rate"):
            try:
                fr = feed.fetch_funding_rate(symbol) or {}
            except Exception:
                fr = {}
        if not isinstance(fr, dict):
            fr = {}
        if historical_asof is None:
            self._funding_cache[symbol] = (now, fr)
        return fr

    @staticmethod
    def _funding_paying_extreme(direction: str, rate: float, extreme: float) -> bool:
        if extreme <= 0:
            return False
        if direction == "LONG" and rate > extreme:
            return True
        if direction == "SHORT" and rate < -extreme:
            return True
        return False

    # ------------------------------------------------------------------
    # Notyfikacje stanu (wolane przez warstwe wykonawcza - backtester/runtime)
    # ------------------------------------------------------------------
    def notify_exit(self, symbol: str, side: str, reason: str, ts: Optional[float] = None,
                    pnl: Optional[float] = None) -> None:
        """Zamkniecie pozycji i aktualizacja serii wynikow per para."""
        sym = (symbol or "").upper()
        ts_f = float(ts if ts is not None else time.time())
        self._active_swing_key.pop(sym, None)

        lost: Optional[bool] = None
        if pnl is not None:
            try:
                lost = float(pnl) < 0.0
            except (TypeError, ValueError):
                lost = None
        if lost is None:
            lost = str(reason or "").lower() in ("sl", "stop_loss", "margin_call")
        if lost:
            self._loss_streak[sym] = int(self._loss_streak.get(sym, 0)) + 1
            self._loss_streak_ts[sym] = ts_f
        else:
            self._loss_streak[sym] = 0
            self._loss_streak_ts.pop(sym, None)

    def notify_entry_fill(self, symbol: str, swing_end_index: int) -> None:
        """Zuzyj impuls dopiero po faktycznym fillu, nigdy przy samym sygnale."""
        sym = str(symbol or "").upper()
        key = (sym, int(swing_end_index))
        self._swing_entry_count[key] = int(self._swing_entry_count.get(key, 0)) + 1
        self._consumed_swing_end[sym] = int(swing_end_index)
        self._active_swing_key[sym] = key

    def _cooldown_reject(self, symbol: str, direction: str, now_ts: float) -> Optional[str]:
        """Jedyna kara czasowa: N przegranych z rzedu na tej parze → pauza."""
        n_need = int(_finite(getattr(config, "DAYTRADING_V2_LOSS_STREAK_PAUSE_N", 5), 5))
        pause_min = _finite(getattr(config, "DAYTRADING_V2_LOSS_STREAK_PAUSE_MIN", 15), 15)
        if n_need <= 0 or pause_min <= 0:
            return None
        sym = (symbol or "").upper()
        if int(self._loss_streak.get(sym, 0)) < n_need:
            return None
        pause_from = self._loss_streak_ts.get(sym)
        if pause_from is None:
            return None
        elapsed_min = (now_ts - float(pause_from)) / 60.0
        if elapsed_min < pause_min:
            return "V2_LOSS_STREAK_PAUSE"
        return None

    # ------------------------------------------------------------------
    # 15m trigger: retest/reclaim strefy fib 1h
    # ------------------------------------------------------------------
    @staticmethod
    def _check_15m_trigger(frame_15m: dict, zone_near: float, zone_far: float, direction: str,
                           lookback: int = 8, reclaim_level: Optional[float] = None,
                           reclaim_bars: int = 1, max_touch_age_bars: Optional[int] = None) -> bool:
        """Cena musiala przeciac pasmo retracement (domyslnie 0.382-0.618)
        w ostatnich `lookback` swiecach 15m; reclaim na ostatnich `reclaim_bars`
        zamknieciach wzgledem `reclaim_level` (domyslnie 0.5).
        `max_touch_age_bars` odrzuca technicznie poprawne, ale juz nieaktualne
        retesty, zeby bot nie wchodzil kilka godzin po reakcji ze strefy."""
        closes = list(frame_15m.get("closes") or [])
        highs = list(frame_15m.get("highs") or [])
        lows = list(frame_15m.get("lows") or [])
        n = min(len(closes), len(highs), len(lows))
        need = max(1, int(reclaim_bars))
        if n < max(lookback, need) + 1:
            return False
        lo = min(zone_near, zone_far)
        hi = max(zone_near, zone_far)
        if hi <= lo:
            return False
        reclaim = zone_near if reclaim_level is None else float(reclaim_level)
        window_start = n - lookback
        window_highs = highs[window_start:n]
        window_lows = lows[window_start:n]
        recent = closes[n - need:n]
        touch_indices = [
            idx for idx in range(window_start, n)
            if lows[idx] <= hi and highs[idx] >= lo
        ]
        if not touch_indices:
            return False
        if max_touch_age_bars is not None:
            max_age = max(0, int(max_touch_age_bars))
            if (n - 1 - touch_indices[-1]) > max_age:
                return False
        if direction == "LONG":
            return all(c >= reclaim for c in recent)
        return all(c <= reclaim for c in recent)

    @staticmethod
    def _check_5m_veto(frame_5m: dict, direction: str, candles: int = 2) -> bool:
        """Opcjonalne weto (pkt 5, 12): True = 5m WYRAZNIE przeciwny kierunek
        (odrzuc). Brak danych 5m -> False (nigdy nie blokuje - to wolajacy
        decyduje czy w ogole wolac te funkcje, gdy dane sa)."""
        closes = list(frame_5m.get("closes") or [])
        opens = list(frame_5m.get("opens") or closes)
        n = min(len(closes), len(opens))
        if n < candles:
            return False
        recent_closes = closes[n - candles:n]
        recent_opens = opens[n - candles:n]
        if direction == "LONG":
            # weto tylko jesli WSZYSTKIE ostatnie swiece sa wyraznie spadkowe
            return all(c < o for c, o in zip(recent_closes, recent_opens))
        return all(c > o for c, o in zip(recent_closes, recent_opens))

    # ------------------------------------------------------------------
    # Glowna ocena
    # ------------------------------------------------------------------
    def evaluate(self, coin: dict, now_ts: Optional[float] = None) -> dict:
        symbol = str(coin.get("symbol") or "").upper()
        price = _finite(coin.get("price"))
        now_ts = time.time() if now_ts is None else float(now_ts)

        excluded = {s.upper() for s in (getattr(config, "DAYTRADING_V2_EXCLUDED_SYMBOLS", None) or [])}
        if symbol in excluded:
            return self._neutral(symbol, price, "V2_SYMBOL_EXCLUDED")

        profile = profile_for(symbol, coin)
        p = params_for(profile)
        if not p.get("trade", True):
            return self._neutral(symbol, price, "V2_PROFILE_OFF", {"v2_profile": profile})

        frames = self._fetch_frames(symbol)
        stale = klines_stale_reason(frames, now_ts, tfs=("1H", "15m"))
        if stale:
            return self._neutral(symbol, price, stale)
        for tf in ("1H", "15m"):
            if not (frames.get(tf) or {}).get("closes"):
                return self._neutral(symbol, price, f"V2_{tf}_DATA_NA")

        ind_1h = self._cached_indicators(symbol, frames["1H"], "1h")
        if not ind_1h:
            return self._neutral(symbol, price, "V2_INDICATORS_NA")
        # Reversal / UI: RSI, MACD, swing highs/lows z 1h (coin z tickera ich nie ma)
        try:
            coin["rsi"] = ind_1h.get("rsi")
            coin["atr"] = ind_1h.get("atr")
            coin["atr_pct"] = ind_1h.get("atr_pct") or ind_1h.get("atr_percent")
            macd = ind_1h.get("macd") or {}
            if isinstance(macd, dict):
                hist = macd.get("histogram") or macd.get("hist")
                if hist is not None:
                    coin["macd_signal"] = "bullish" if float(hist) > 0 else "bearish"
            coin["highs"] = list(frames["1H"].get("highs") or [])
            coin["lows"] = list(frames["1H"].get("lows") or [])
        except Exception:
            pass

        # 1H = kierunek. 4H/1D = kontekst (align/oppose/NA), nie bramka.
        d1_closes = (frames.get("1D") or {}).get("closes")
        ind_1d = self._cached_indicators(symbol, frames["1D"], "1d") if d1_closes else None
        h4_closes = (frames.get("4H") or {}).get("closes")
        ind_4h = self._cached_indicators(symbol, frames["4H"], "4h") if h4_closes else None
        bias_1h = _bias_from_indicators(ind_1h)
        bias_4h = _bias_from_indicators(ind_4h) if ind_4h else None
        bias_1d = _bias_from_indicators(ind_1d) if ind_1d else None
        if bias_1d == "NEUTRAL":
            bias_1d = None
        if bias_4h == "NEUTRAL":
            bias_4h = None
        if bias_1h not in ("LONG", "SHORT"):
            return self._neutral(symbol, price, "V2_1H_NO_BIAS", {"bias_1d": bias_1d, "bias_4h": bias_4h, "bias_1h": bias_1h, "v2_profile": profile})
        direction = bias_1h

        if not p["use_4h_context"]:
            bias_4h = None

        if p["skip_range"]:
            adx = ind_1h.get("adx")
            try:
                adx_f = float(adx) if adx is not None else None
            except (TypeError, ValueError):
                adx_f = None
            if adx_f is not None and adx_f < p["range_adx_max"]:
                return self._neutral(symbol, price, "V2_RANGE_SKIP", {
                    "bias_1d": bias_1d, "bias_4h": bias_4h, "v2_profile": profile, "adx": adx_f,
                })

        if p["skip_4h_oppose"] and bias_4h in ("LONG", "SHORT") and bias_4h != direction:
            return self._neutral(symbol, price, "V2_4H_CTX_OPPOSE", {
                "bias_1d": bias_1d, "bias_4h": bias_4h, "v2_profile": profile,
            })

        # 3) 1h mapa setupu: swing + fib (pkt 3, 6)
        highs_1h = list(frames["1H"].get("highs") or [])
        lows_1h = list(frames["1H"].get("lows") or [])
        closes_1h = list(frames["1H"].get("closes") or [])
        atr_1h_now = _finite(ind_1h.get("atr"))
        if atr_1h_now <= 0:
            return self._neutral(symbol, price, "V2_1H_NO_ATR", {"bias_1d": bias_1d, "bias_4h": bias_4h})
        atr_series_1h = [atr_1h_now] * len(closes_1h)  # przyblizenie: ATR "na koncu" dla filtra swingu
        swing_kwargs = dict(
            min_move_atr=_finite(p.get("swing_min_move_atr"), 2.0),
            min_bars=int(_finite(getattr(config, "DAYTRADING_V2_SWING_MIN_BARS", 8), 8)),
            right_confirm=int(_finite(getattr(config, "DAYTRADING_V2_SWING_RIGHT_CONFIRM", 5), 5)),
        )
        swing_any = find_last_confirmed_swing(
            highs_1h, lows_1h, closes_1h, atr_series_1h, **swing_kwargs,
        )
        if swing_any is None:
            return self._neutral(symbol, price, "V2_NO_1H_SWING", {"bias_1d": bias_1d, "bias_4h": bias_4h})
        swing_expected_dir = "UP" if direction == "LONG" else "DOWN"
        if swing_any["direction"] == swing_expected_dir:
            swing = swing_any
        else:
            # Ostatni swing to korekta - Fibo/SL z impulsu zgodnego z 1h.
            swing = find_last_confirmed_swing(
                highs_1h, lows_1h, closes_1h, atr_series_1h,
                prefer_direction=swing_expected_dir, **swing_kwargs,
            )
            if swing is None:
                return self._neutral(symbol, price, "V2_NO_IMPULSE_SWING", {
                    "bias_1d": bias_1d, "bias_4h": bias_4h,
                    "last_swing_dir": swing_any["direction"],
                })

        n_1h = len(closes_1h)
        impulse_age = n_1h - 1 - int(swing["end"]["index"])
        max_age = int(_finite(getattr(config, "DAYTRADING_V2_IMPULSE_MAX_AGE_BARS", 36), 36))
        if max_age > 0 and impulse_age > max_age:
            return self._neutral(symbol, price, "V2_IMPULSE_TOO_OLD", {
                "bias_1d": bias_1d, "bias_4h": bias_4h, "impulse_age_bars": impulse_age,
            })
        if direction == "LONG" and price < swing["start"]["price"]:
            return self._neutral(symbol, price, "V2_IMPULSE_BROKEN", {"bias_1d": bias_1d, "bias_4h": bias_4h})
        if direction == "SHORT" and price > swing["start"]["price"]:
            return self._neutral(symbol, price, "V2_IMPULSE_BROKEN", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 3b) max N wejsc na jeden impuls 1h
        swing_key = (symbol, int(swing["end"]["index"]))
        max_entries = max(1, int(_finite(getattr(config, "DAYTRADING_V2_MAX_ENTRIES_PER_SWING", 1), 1)))
        already = int(self._swing_entry_count.get(swing_key, 0))
        if already >= max_entries:
            return self._neutral(symbol, price, "V2_SWING_ALREADY_TRADED", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 4) hamulce czestotliwosci (pkt 21-23, 26)
        cooldown_reason = self._cooldown_reject(symbol, direction, now_ts)
        if cooldown_reason:
            return self._neutral(symbol, price, cooldown_reason, {"bias_1d": bias_1d, "bias_4h": bias_4h})

        retracement = swing_fib_retracement(swing)
        extension = swing_fib_extension(swing)
        near_r = _finite(getattr(config, "DAYTRADING_V2_FIB_ZONE_NEAR", 0.382), 0.382)
        far_r = _finite(getattr(config, "DAYTRADING_V2_FIB_ZONE_FAR", 0.618), 0.618)
        reclaim_r = _finite(getattr(config, "DAYTRADING_V2_FIB_RECLAIM", 0.5), 0.5)
        zone_near = retracement.get(_fib_key(near_r))
        zone_far = retracement.get(_fib_key(far_r))
        zone_reclaim = retracement.get(_fib_key(reclaim_r))
        if zone_near is None or zone_far is None or zone_reclaim is None:
            return self._neutral(symbol, price, "V2_NO_FIB_ZONE", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 5) SL = swing 1h +/- bufor ATR (pkt 13, 14). "Swing low" dla LONG
        # to POCZATEK impulsu UP (swing["start"]) - dolek, od ktorego impuls
        # wystartowal - nie jego koniec/szczyt (swing["end"]).
        sl_buffer = _finite(p.get("sl_atr_buffer"), 1.5) * atr_1h_now
        if direction == "LONG":
            sl_price = swing["start"]["price"] - sl_buffer
        else:
            sl_price = swing["start"]["price"] + sl_buffer
        if direction == "LONG" and sl_price >= price:
            return self._neutral(symbol, price, "V2_INVALID_SL", {"bias_1d": bias_1d, "bias_4h": bias_4h})
        if direction == "SHORT" and sl_price <= price:
            return self._neutral(symbol, price, "V2_INVALID_SL", {"bias_1d": bias_1d, "bias_4h": bias_4h})
        risk = abs(price - sl_price)
        if risk <= 0:
            return self._neutral(symbol, price, "V2_INVALID_SL", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 6) filtr koszt vs SL: ten sam dynamiczny slip RT co paper/replay.
        from v2_profiles import replay_slip_round_trip
        m5 = frames.get("5m") or {}
        slip_idx = max(0, len(m5.get("volumes") or m5.get("closes") or []) - 1)
        slip_rt = replay_slip_round_trip(symbol, m5, slip_idx, price)
        round_trip_cost_frac = (
            2.0 * _finite(getattr(config, "TAKER_FEE", getattr(config, "COMMISSION_RATE", 0.0006)), 0.0006)
            + slip_rt
        )
        min_sl_frac = round_trip_cost_frac * _finite(getattr(config, "DAYTRADING_V2_MIN_SL_VS_COST_MULT", 3.5), 3.5)
        if price > 0 and (risk / price) < min_sl_frac:
            return self._neutral(symbol, price, "V2_SL_TOO_TIGHT_VS_COST", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 7-9) TP1 = dawne TP2 (1.618 / ≥2R). TP2 wyżej (2.618 / ≥3R).
        tp1_r_target = _finite(getattr(config, "DAYTRADING_V2_TP1_R", 2.0), 2.0)
        min_ratio = _finite(getattr(config, "DAYTRADING_V2_MIN_TP1_R_RATIO", 0.6), 0.6)
        tp1_by_r = price + risk * tp1_r_target if direction == "LONG" else price - risk * tp1_r_target
        ext1 = _fib_key(_finite(getattr(config, "DAYTRADING_V2_TP1_EXTENSION_RATIO", 1.618), 1.618))
        tp1_ext = extension.get(ext1) or extension.get("1.618")
        if tp1_ext is None:
            tp1_price = tp1_by_r
        elif direction == "LONG":
            tp1_price = max(float(tp1_ext), tp1_by_r)
        else:
            tp1_price = min(float(tp1_ext), tp1_by_r)
        raw_tp1_price = tp1_price

        # Realna droga do targetu: rozszerzenie Fibo nie moze ignorowac
        # pierwszego potwierdzonego oporu/wsparcia 1h.  Gdy przed raw TP1
        # lezy przeszkoda z wystarczajaca przestrzenia, realizujemy TP1 tuz
        # przed nia.  Bardzo bliska przeszkoda nie jest arbitralnym veto -
        # obniza P(TP) i finalny Expected Net R.
        obstacle = self._nearest_1h_level(ind_1h, price, direction)
        tp1_price, target_path = structure_aware_target(
            price, raw_tp1_price, risk, direction, obstacle=obstacle,
            atr=atr_1h_now,
            buffer_atr=_finite(getattr(config, "DAYTRADING_V2_TARGET_BUFFER_ATR", 0.15), 0.15),
            min_r=min_ratio,
        )
        tp1_r_actual = abs(tp1_price - price) / risk
        target_clearance = _finite(target_path.get("clearance"), 1.0)

        if tp1_r_actual < min_ratio:
            return self._neutral(symbol, price, "V2_TP1_TOO_SMALL_VS_RISK", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        r2 = _finite(getattr(config, "DAYTRADING_V2_TP2_R_FALLBACK", 3.0), 3.0)
        tp2_by_r = price + risk * r2 if direction == "LONG" else price - risk * r2
        ext2 = _fib_key(_finite(getattr(config, "DAYTRADING_V2_TP2_EXTENSION_RATIO", 2.618), 2.618))
        tp2_ext = extension.get(ext2) or extension.get("2.618")
        if tp2_ext is None:
            tp2_price = tp2_by_r
        elif direction == "LONG":
            tp2_price = max(float(tp2_ext), tp2_by_r)
        else:
            tp2_price = min(float(tp2_ext), tp2_by_r)
        if direction == "LONG" and tp2_price <= tp1_price:
            tp2_price = tp1_price + risk * max(0.5, r2 - tp1_r_target)
        elif direction == "SHORT" and tp2_price >= tp1_price:
            tp2_price = tp1_price - risk * max(0.5, r2 - tp1_r_target)

        # 10) 15m trigger: touch 0.382-0.618, reclaim 0.5, lookback z configu
        lookback_15 = max(3, int(_finite(getattr(config, "DAYTRADING_V2_15M_LOOKBACK", 12), 12)))
        reclaim_bars = max(1, int(_finite(getattr(config, "DAYTRADING_V2_15M_RECLAIM_BARS", 2), 2)))
        max_touch_age = max(reclaim_bars, int(_finite(
            getattr(config, "DAYTRADING_V2_15M_MAX_TOUCH_AGE_BARS", 3), 3,
        )))
        if not self._check_15m_trigger(
            frames["15m"], zone_near, zone_far, direction,
            lookback=lookback_15, reclaim_level=zone_reclaim, reclaim_bars=reclaim_bars,
            max_touch_age_bars=max_touch_age,
        ):
            return self._neutral(symbol, price, "V2_NO_15M_TRIGGER", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        candle_quality = candle_rejection_features(
            frames["15m"], direction, zone_near, zone_far,
            touch_lookback=lookback_15,
        )
        rsi_quality = rsi_structure_features(frames["1H"], direction)
        setup_probability_multiplier = probability_quality_multiplier(
            candle_quality, rsi_quality, target_clearance,
        )

        # 11) 5m opcjonalne weto (pkt 5, 12) - NIGDY twardy blok na brak danych
        frame_5m = frames.get("5m") or {}
        if p["use_5m_veto"] and frame_5m.get("closes") and self._check_5m_veto(frame_5m, direction):
            return self._neutral(symbol, price, "V2_5M_VETO", {"bias_1d": bias_1d, "bias_4h": bias_4h, "v2_profile": profile})

        fr = self._funding_for(symbol, coin)
        rate = _finite((fr or {}).get("funding_rate"))
        extreme = _finite(getattr(config, "FUNDING_EXTREME", 0.001), 0.001)
        if bool(getattr(config, "DAYTRADING_V2_FUNDING_SKIP_EXTREME", True)) and self._funding_paying_extreme(direction, rate, extreme):
            return self._neutral(symbol, price, "V2_FUNDING_EXTREME", {
                "bias_1d": bias_1d, "bias_4h": bias_4h, "v2_profile": profile,
                "funding": fr,
            })

        # 12) sizing z ryzyka % kapitalu (pkt 20) - tu tylko procent ryzyka i
        # odleglosc SL; przelozenie na wielkosc pozycji robi warstwa
        # wykonawcza (risk_manager), ktora zna kapital.
        risk_pct = _finite(getattr(config, "DAYTRADING_V2_RISK_PCT_OF_CAPITAL", 0.5), 0.5)

        htf_mult = 1.0
        if p["use_4h_context"] and bias_4h in ("LONG", "SHORT") and bias_4h != direction:
            htf_mult = float(p.get("oppose_size_mult") or 0.70)
            htf_mult = min(1.0, max(0.5, htf_mult))

        invalidation_atr = abs(price - sl_price) / max(atr_1h_now, 1e-12)
        book = coin.get("order_book") or {}
        bid = _finite(coin.get("blofin_bid") or book.get("bid"))
        ask = _finite(coin.get("blofin_ask") or book.get("ask"))
        spread_frac = max(0.0, (ask - bid) / price) if bid > 0 and ask >= bid and price > 0 else slip_rt / 2.0
        adverse_selection = min(1.0, spread_frac / max(abs(price - sl_price) / price, 1e-12))
        fill_probability = max(0.05, min(0.95, 0.75 - adverse_selection * 0.50))
        net_reward_potential_r = tp1_r_actual - (
            round_trip_cost_frac / max(abs(price - sl_price) / price, 1e-12)
        )

        signal = {
            "symbol": symbol, "direction": direction, "price": price,
            "strategy_price": price, "decision_price": price,
            "submitted_price": float(zone_reclaim),
            "sl_price": round(sl_price, 10), "tp1_price": round(tp1_price, 10),
            "tp2_price": round(tp2_price, 10), "tp1_r": round(tp1_r_actual, 4),
            "limit_price": round(float(zone_reclaim), 10),
            "risk_pct_of_capital": risk_pct,
            "margin_pct": p["margin_pct"],
            "v2_profile": profile,
            "funding": fr,
            "slip_rt": slip_rt,
            "expected_net_r": round(net_reward_potential_r, 4),
            "net_reward_potential_r": round(net_reward_potential_r, 4),
            "fill_probability": round(fill_probability, 4),
            "distance_from_invalidation_atr": round(invalidation_atr, 4),
            "adverse_selection_score": round(adverse_selection, 4),
            "market_snapshot": V2MarketSnapshot(
                symbol=symbol,
                event_ts_ms=int(((m5.get("timestamps") or [0])[-1]) or 0),
                decision_ts_ms=int(((m5.get("timestamps") or [0])[-1]) or 0),
                frames=frames,
                ticker={"price": price},
                order_book=coin.get("order_book") or {},
                funding=fr or {},
                source="runtime",
            ).to_dict(),
            # Stala, nie-dryfujaca wartosc - sizing V2 CELOWO nie jest
            # sterowany strength (punkt 20: "strength tylko jako mnoznik, nie
            # zamiast ATR"), ale downstream (paper_trader.open_position) i tak
            # wymaga skonczonej liczby w tym polu, wiec podajemy neutralna
            # stala zamiast zostawiac brak (co crashowaloby otwarcie pozycji).
            "strength": 0.75,
            "reject_reason": None, "engine": "daytrading_v2", "strategy_mode": "DAYTRADING_V2",
            "setup": "htf_swing_retest",
            "swing": swing, "fib_retracement": retracement, "fib_extension": extension,
            "trailing_anchor_tf": "1h",
            "_htf_size_mult": htf_mult,
            "reasons": (
                [f"V2_PROFILE_{profile.upper()}", "V2_1H_BIAS", "V2_1H_SWING", "V2_15M_TRIGGER"]
                + (
                    ["V2_4H_CTX_ALIGN"] if bias_4h == direction else
                    [f"V2_4H_CTX_OPPOSE({bias_4h})"] if bias_4h in ("LONG", "SHORT") else
                    ["V2_4H_CTX_NA"]
                )
                + (
                    ["V2_1D_CTX_ALIGN"] if bias_1d == direction else
                    [f"V2_1D_CTX_OPPOSE({bias_1d})"] if bias_1d in ("LONG", "SHORT") else
                    ["V2_1D_CTX_NA"]
                )
            ),
            "change_24h": coin.get("change_24h"),
            "order_book": coin.get("order_book"),
            "hide_strength": True,
            "bias_1d": bias_1d, "bias_4h": bias_4h, "bias_1h": bias_1h,
            "atr": atr_1h_now,
            "candle_confirmation": candle_quality,
            "candle_confirmation_score": candle_quality.get("score"),
            "rsi_structure": rsi_quality,
            "rsi_divergence_confirmed": bool(rsi_quality.get("divergence")),
            "rsi_failure_swing": bool(rsi_quality.get("failure_swing")),
            "target_path": target_path,
            "setup_probability_multiplier": setup_probability_multiplier,
            "tp_plan": {
                "tp1_r": round(tp1_r_actual, 4),
                "tp2_r": round(abs(tp2_price - price) / risk, 4),
                "tp2": "extension",
                "tp3": "trailing",
                "be_after": "tp2",
                "frac_tp1": float(getattr(config, "DAYTRADING_V2_TP1_FRAC", 0.50)),
                "frac_tp2": float(getattr(config, "DAYTRADING_V2_TP2_FRAC", 0.50)),
            },
        }
        try:
            from day_expectancy_calibration import get_day_calibrator
            calibrator = self._expectancy_calibrator or get_day_calibrator()
            signal["day_expectancy_calibration"] = calibrator.snapshot(
                profile=profile, regime=coin.get("market_regime"),
            )
            from expected_net_r import expected_net_r
            expected_net_r(signal)
        except Exception:
            # Brak/awaria telemetrycznej kalibracji nie moze zmienic decyzji.
            signal["expected_net_r"] = round(net_reward_potential_r, 4)
        return signal

    @staticmethod
    def _nearest_1h_level(ind_1h: dict, price: float, direction: str,
                          min_distance: float = 0.0) -> Optional[float]:
        """Najblizszy obiektywny poziom 1h w kierunku ruchu, nie blizej niz
        min_distance (żeby szum S/R nie scinal TP1 ponizej min R)."""
        structure = ind_1h.get("support_resistance") or {}
        pivots = ind_1h.get("pivot_points") or {}
        viper = ind_1h.get("viper") or {}
        min_distance = max(0.0, _finite(min_distance))
        candidates: List[float] = []
        if direction == "LONG":
            candidates.extend(_finite(x.get("price")) for x in structure.get("resistances") or [])
            candidates.extend(_finite(v) for k, v in pivots.items() if k.startswith("R"))
            candidates.extend(_finite(x.get("price")) for x in viper.get("levels") or []
                              if str(x.get("side") or "").lower() == "sell")
            candidates = [c for c in candidates if c >= price + min_distance]
            return min(candidates) if candidates else None
        candidates.extend(_finite(x.get("price")) for x in structure.get("supports") or [])
        candidates.extend(_finite(v) for k, v in pivots.items() if k.startswith("S"))
        candidates.extend(_finite(x.get("price")) for x in viper.get("levels") or []
                          if str(x.get("side") or "").lower() == "buy")
        candidates = [c for c in candidates if c <= price - min_distance]
        return max(candidates) if candidates else None

    @staticmethod
    def _neutral(symbol: str, price: float, reason: str, extra: Optional[dict] = None) -> dict:
        row = {
            "symbol": symbol, "direction": "NEUTRAL", "price": price,
            "reject_reason": reason, "reasons": [reason],
            "engine": "daytrading_v2", "strategy_mode": "DAYTRADING_V2",
            "setup": "intraday_wait", "strength": 0.05,
            "hide_strength": True,
            "change_24h": None,
            "v2_profile": profile_for(symbol),
        }
        if extra:
            row.update(extra)
        return row

    def generate(self, coins: Iterable[dict]) -> List[dict]:
        """Filtr uniwersum PRZED wywolaniem evaluate() - bez tego evaluate()
        (kosztowna kaskada 1D+4h+1h+15m+5m) odpalalby sie na CALYM uniwersum
        (setki symboli) co skan, zamiast tylko na top-N po wolumenie - a to
        dokladnie problem, ktory caly epik rate-limitingu (punkty 1-9) mial
        rozwiazac. Ten sam wzorzec co DayTradingEngine.generate() (V1)."""
        excluded = {s.upper() for s in (getattr(config, "DAYTRADING_V2_EXCLUDED_SYMBOLS", None) or [])}
        valid = [c for c in coins or []
                 if str(c.get("symbol") or "").upper() not in excluded
                 and crypto_perpetual_allowed(str(c.get("symbol") or ""), c.get("instrument") or c)]

        def quote_volume(coin: dict) -> float:
            explicit = _finite(coin.get("blofin_quote_volume_24h"))
            if explicit > 0:
                return explicit
            base = _finite(coin.get("blofin_base_volume_24h"))
            if base > 0:
                return base * _finite(coin.get("price"))
            return _finite(coin.get("blofin_volume_24h") or coin.get("volume_24h"))

        ranked = sorted(valid, key=quote_volume, reverse=True)
        refresh_volume_ranks(valid)
        # 23.08.2026: bez MIN_VOLUME. Sort po wolumenie = kolejnosc
        # cold-start (plynne pierwsze), nie wycinanie par.

        # 23.08.2026: top-N wylaczone domyslnie (MAX_CANDIDATES=0).
        # WS=pelne uniwersum bylo w kodzie, ale CF-403 trzyma WS martwy, wiec
        # REST-only 39 blokowal ~134 par na stale (V2_NOT_IN_LIQUID_TOP).
        # Hamulec REST = COLD_START_BATCH_SIZE, nie liquid top.
        # Dodatni MAX_CANDIDATES nadal tnie ranked (testy / awaryjny sufit).
        cap = int(_finite(getattr(config, "DAYTRADING_V2_MAX_CANDIDATES", 0), 0))
        target = ranked[:max(1, cap)] if cap > 0 else ranked
        target_symbols = [str(c.get("symbol") or "").upper() for c in target]
        target_set = set(target_symbols)

        # Dawkowanie partiami (wprost z prosby uzytkownika 21.08.2026: "REST
        # nie pobiera wszystkiego na raz, tylko w partiach, tak by nie
        # wywalilo limitu i bledu 429") - dotyczy ROWNIEZ listy 45
        # kandydatow REST-only, nie tylko ramp-upu WS. "Ciepłe" (juz raz
        # pobrane w tej instancji silnika) symbole z target sa oceniane co
        # cykl - to tanie, TTL+WS-merge trzyma ich cache swiezym.
        # "Zimne" (nigdy niepobierane) wchodza w gre najwyzej po
        # DAYTRADING_V2_COLD_START_BATCH_SIZE na cykl, w kolejnosci wolumenu
        # (ranked/target sa juz posortowane malejaco) - reszta czeka na
        # kolejny cykl. Patrz
        # test_cold_start_batch_pacing_stays_safe_regardless_of_universe_size.
        batch_size = max(1, int(_finite(getattr(config, "DAYTRADING_V2_COLD_START_BATCH_SIZE", 8), 8)))
        self._absorb_store_warmup(target_symbols)
        cold_in_target = [s for s in target_symbols if s not in self._warmed_symbols]
        newly_warming = set(cold_in_target[:batch_size])
        selected = {
            s for s in target_symbols
            if s in self._warmed_symbols or s in newly_warming
        }

        out = []
        for coin in valid:
            symbol = str(coin.get("symbol") or "").upper()
            if symbol in selected:
                frame_15m = self._fetch(symbol, "15m", 300)
                decision_bar_ts = _last_ts_seconds(frame_15m)
                cached = self._decision_cache.get(symbol)
                if (
                    decision_bar_ts is not None
                    and cached is not None
                    and self._last_decision_15m.get(symbol) == decision_bar_ts
                ):
                    row = dict(cached)
                    row["price"] = _finite(coin.get("price"), row.get("price"))
                    row["change_24h"] = coin.get("change_24h")
                    row["decision_fresh"] = False
                    row["decision_bar_15m_ts"] = decision_bar_ts
                else:
                    pipeline = getattr(self, "decision_pipeline", None)
                    runtime_data = getattr(self, "runtime_market_data_port", None)
                    if pipeline is not None and runtime_data is not None:
                        runtime_data.update(coin)
                        decision_ms = (int(decision_bar_ts + 900) * 1000
                                       if decision_bar_ts is not None else None)
                        row = pipeline.analyze(symbol, decision_ts_ms=decision_ms).decision
                    else:
                        row = self.evaluate(coin)
                    row["decision_fresh"] = True
                    row["decision_bar_15m_ts"] = decision_bar_ts
                    if decision_bar_ts is not None:
                        self._last_decision_15m[symbol] = decision_bar_ts
                        self._decision_cache[symbol] = dict(row)
                out.append(row)
                if out[-1].get("change_24h") is None:
                    out[-1]["change_24h"] = coin.get("change_24h")
                if not out[-1].get("order_book"):
                    out[-1]["order_book"] = coin.get("order_book")
                self._warmed_symbols.add(symbol)
                continue
            # Poza topem albo jeszcze nie rozgrzany w tej partii: tylko
            # spread/rozjazd ceny (juz w coin z bulk tickera Blofin, zero
            # dodatkowego kosztu sieciowego), nie pelna kaskada - ten sam
            # mechanizm co V1 (punkt 3 planu rate-limitingu).
            bid = _finite(coin.get("blofin_bid"), None)
            ask = _finite(coin.get("blofin_ask"), None)
            price = _finite(coin.get("price"))
            spread_pct = None
            if bid and ask and price:
                spread_pct = round((ask - bid) / price * 100.0, 4)
            reason = "V2_COLD_START_WARMING_UP" if symbol in target_set else "V2_NOT_IN_LIQUID_TOP"
            row = self._neutral(symbol, price, reason)
            row["decision_fresh"] = False
            row["details"] = {"spread_only": True, "bid": bid, "ask": ask, "spread_pct": spread_pct}
            out.append(row)
        return rank_candidates(out)
