"""Silnik daytrading V2 - hierarchia timeframe (plan z 20.08.2026):

1D  -> tylko kierunek/bias. Bez zgodnosci 1D nie ma pozycji.
4h  -> potwierdzenie trendu z 1D. Bez zgody 4h nie ma pozycji.
1h  -> mapa setupu: swing, Fibo, EMA. Stad SL, TP1, TP2.
15m -> jedyny trigger wejscia (retest/reclaim/potwierdzenie).
5m  -> tylko potwierdzenie wejscia (1-2 swiece). Nie sygnal, nie SL, nie invalidation.

Zasady wejscia/wyjscia:
- Jedno wejscie na jeden impuls/swing 1h (nie odnawia sie co 15m).
- Sygnal na zamknieciu 15m, fill na nastepnym open (obsluguje to backtester/
  runtime wywolujacy ten silnik, nie silnik sam).
- 15m setup "znika" -> pozycja NIE jest zamykana z tego powodu. Zamykaja:
  SL 1h, odwrocenie 4h/1D, TP, trailing (implementowane w warstwie
  wykonawczej - backtester/runtime - nie w evaluate()).
- 5m brak danych != twardy blok. 5m to opcjonalne weto, nie brama OR.

To jest RÓWNOLEGŁY silnik do DayTradingEngine (V1) - wlaczany wylacznie
przez config.DAYTRADING_V2_ENABLED / STRATEGY_MODE="DAYTRADING_V2". V1
pozostaje w pelni nietkniety, zeby dalo sie zrobic czyste A/B replay na tym
samym oknie 90-dniowym.
"""

from __future__ import annotations

import time
from typing import Dict, Iterable, List, Optional

import config
from swing_structure import find_last_confirmed_swing, swing_fib_retracement, swing_fib_extension
from blofin_ws import PUBLIC_WS

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
    def __init__(self, feeder=None):
        self.feeder = feeder
        # Stan miedzy wywolaniami (per-instancja silnika - w backteście swiezy
        # silnik na kazde okno/symbol, w runtime jeden dlugozyjacy silnik):
        self._consumed_swing_end: Dict[str, int] = {}   # symbol -> last swing end index
        self._swing_entry_count: Dict[tuple, int] = {}  # (symbol, swing_end) -> ile wejsc
        self._last_exit: Dict[str, dict] = {}            # symbol -> {"ts": float, "side": str, "reason": str} (pkt 21-23, 26)
        # 21.08.2026 (druga iteracja rate-limitingu, patrz generate()):
        # symbole, ktore juz przynajmniej raz przeszly przez _fetch_frames w
        # tej instancji silnika - a wiec ich ohlc_cache w blofin_feed.py jest
        # "cieply" i dalsze cykle trzyma go swiezym tanio (TTL+WS-merge), nie
        # wymagajac powtarzania calej kaskady REST. Uzywane do dawkowania
        # cold-startu partiami (DAYTRADING_V2_COLD_START_BATCH_SIZE) zamiast
        # pobierania calego uniwersum naraz.
        self._warmed_symbols: set = set()

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    def _fetch(self, symbol: str, bar: str, limit: int) -> dict:
        feed = getattr(self.feeder, "blofin", None)
        if feed is None:
            return {}
        return feed.fetch_klines_ohlcv(symbol, bar=bar, limit=limit) or {}

    def _fetch_frames(self, symbol: str) -> Dict[str, dict]:
        return {
            "1D": self._fetch(symbol, "1D", 260),
            "4H": self._fetch(symbol, "4H", 260),
            "1H": self._fetch(symbol, "1H", 260),
            "15m": self._fetch(symbol, "15m", 300),
            "5m": self._fetch(symbol, "5m", 60),
        }

    # ------------------------------------------------------------------
    # Notyfikacje stanu (wolane przez warstwe wykonawcza - backtester/runtime)
    # ------------------------------------------------------------------
    def notify_exit(self, symbol: str, side: str, reason: str, ts: Optional[float] = None) -> None:
        """Warstwa wykonawcza informuje silnik o zamknieciu pozycji - zasila
        to hamulce czestotliwosci (pkt 21-23, 26). `reason` in
        ("sl","tp","trailing","htf_reversal")."""
        self._last_exit[symbol.upper()] = {
            "ts": float(ts if ts is not None else time.time()),
            "side": side, "reason": reason,
        }

    def _cooldown_reject(self, symbol: str, direction: str, now_ts: float) -> Optional[str]:
        last = self._last_exit.get(symbol.upper())
        if not last:
            return None
        elapsed_min = (now_ts - last["ts"]) / 60.0
        if elapsed_min < 0:
            elapsed_min = 0.0
        base_cd = _finite(getattr(config, "DAYTRADING_V2_COOLDOWN_AFTER_EXIT_MIN", 60), 60)
        if elapsed_min < base_cd:
            return "V2_COOLDOWN_AFTER_EXIT"
        if last["reason"] == "sl" and last["side"] == direction:
            sl_cd = _finite(getattr(config, "DAYTRADING_V2_COOLDOWN_AFTER_SL_SAME_SIDE_MIN", 240), 240)
            if elapsed_min < sl_cd:
                return "V2_COOLDOWN_AFTER_SL_SAME_SIDE"
        if last["reason"] == "htf_reversal":
            inval_cd = _finite(getattr(config, "DAYTRADING_V2_COOLDOWN_AFTER_INVALIDATION_MIN", 180), 180)
            if elapsed_min < inval_cd:
                return "V2_COOLDOWN_AFTER_INVALIDATION"
        if last["side"] == direction:
            # drugie wejscie na ten sam swing: nie blokuj 10-min reentry
            if not bool(getattr(config, "DAYTRADING_V2_ALLOW_ADDON", True)):
                reentry_gap = _finite(getattr(config, "DAYTRADING_V2_MIN_REENTRY_GAP_MIN", 10), 10)
                if elapsed_min < reentry_gap:
                    return "V2_REENTRY_TOO_SOON"
        return None

    # ------------------------------------------------------------------
    # 15m trigger: retest/reclaim strefy fib 1h
    # ------------------------------------------------------------------
    @staticmethod
    def _check_15m_trigger(frame_15m: dict, zone_near: float, zone_far: float, direction: str, lookback: int = 8) -> bool:
        """Cena musiala wejsc w strefe retracement (0.5-0.618) w ostatnich
        `lookback` swiecach 15m, a OSTATNIA ZAMKNIETA swieca 15m musi
        pokazywac reclaim - zamkniecie z powrotem po wlasciwej stronie
        zone_near (pkt 4, 9: sygnal na zamknieciu 15m)."""
        closes = list(frame_15m.get("closes") or [])
        highs = list(frame_15m.get("highs") or [])
        lows = list(frame_15m.get("lows") or [])
        n = min(len(closes), len(highs), len(lows))
        if n < lookback + 1:
            return False
        window_highs = highs[n - lookback:n]
        window_lows = lows[n - lookback:n]
        last_close = closes[n - 1]
        if direction == "LONG":
            touched_zone = min(window_lows) <= zone_near
            reclaimed = last_close >= zone_near
            return touched_zone and reclaimed
        touched_zone = max(window_highs) >= zone_near
        reclaimed = last_close <= zone_near
        return touched_zone and reclaimed

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

        frames = self._fetch_frames(symbol)
        for tf in ("4H", "1H", "15m"):
            if not (frames.get(tf) or {}).get("closes"):
                return self._neutral(symbol, price, f"V2_{tf}_DATA_NA")

        ind_4h = compute_indicators(frames["4H"], tf="4h")
        ind_1h = compute_indicators(frames["1H"], tf="1h")
        if not ind_4h or not ind_1h:
            return self._neutral(symbol, price, "V2_INDICATORS_NA")

        # 1D jest TRAKTOWANY OSOBNO od 4h/1h/15m powyzej: brak/za krotka
        # historia 1D (nowszy listing bez 200+ dziennych swiec na EMA200)
        # NIE jest juz twardym blokerem - patrz kotwica 4h ponizej.
        d1_closes = (frames.get("1D") or {}).get("closes")
        ind_1d = compute_indicators(frames["1D"], tf="1d") if d1_closes else None

        # 1) 1D bias (pkt 1) / kotwica 4h gdy 1D niedostepny (pkt "nowsze monety")
        bias_4h = _bias_from_indicators(ind_4h)
        if ind_1d is not None:
            bias_1d = _bias_from_indicators(ind_1d)
            if bias_1d == "NEUTRAL":
                return self._neutral(symbol, price, "V2_1D_NO_BIAS", {"bias_1d": bias_1d})
            # 2) 4h potwierdzenie (pkt 2)
            if bias_4h != bias_1d:
                return self._neutral(symbol, price, "V2_4H_NOT_CONFIRMED", {"bias_1d": bias_1d, "bias_4h": bias_4h})
            direction = bias_1d  # "LONG"/"SHORT"
        elif bool(getattr(config, "DAYTRADING_V2_ALLOW_4H_ANCHOR_WITHOUT_1D", True)):
            # Para bez wystarczajacej historii 1D: 4h staje sie kotwica
            # kierunku zamiast twardego odrzutu. Jednomyslnosc z 1h (swing
            # kierunek, 15m trigger, 5m weto) jest i tak wymagana ponizej w
            # tym samym funnelu, wiec profil ryzyka nie jest wiekszy niz na
            # sciezce standardowej - kotwica jest po prostu plytsza.
            if bias_4h == "NEUTRAL":
                return self._neutral(symbol, price, "V2_4H_NO_BIAS_NO_1D", {"bias_1d": None, "bias_4h": bias_4h})
            bias_1d = None
            direction = bias_4h
        else:
            return self._neutral(symbol, price, "V2_1D_DATA_NA")

        # 3) 1h mapa setupu: swing + fib (pkt 3, 6)
        highs_1h = list(frames["1H"].get("highs") or [])
        lows_1h = list(frames["1H"].get("lows") or [])
        closes_1h = list(frames["1H"].get("closes") or [])
        atr_1h_now = _finite(ind_1h.get("atr"))
        if atr_1h_now <= 0:
            return self._neutral(symbol, price, "V2_1H_NO_ATR", {"bias_1d": bias_1d, "bias_4h": bias_4h})
        atr_series_1h = [atr_1h_now] * len(closes_1h)  # przyblizenie: ATR "na koncu" dla filtra swingu
        swing = find_last_confirmed_swing(
            highs_1h, lows_1h, closes_1h, atr_series_1h,
            min_move_atr=_finite(getattr(config, "DAYTRADING_V2_SWING_MIN_MOVE_ATR", 1.5), 1.5),
            min_bars=int(_finite(getattr(config, "DAYTRADING_V2_SWING_MIN_BARS", 3), 3)),
            right_confirm=int(_finite(getattr(config, "DAYTRADING_V2_SWING_RIGHT_CONFIRM", 2), 2)),
        )
        if swing is None:
            return self._neutral(symbol, price, "V2_NO_1H_SWING", {"bias_1d": bias_1d, "bias_4h": bias_4h})
        swing_expected_dir = "UP" if direction == "LONG" else "DOWN"
        if swing["direction"] != swing_expected_dir:
            return self._neutral(symbol, price, "V2_SWING_DIRECTION_MISMATCH", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 3b) max N wejsc na jeden impuls 1h
        swing_key = (symbol, int(swing["end"]["index"]))
        max_entries = max(1, int(_finite(getattr(config, "DAYTRADING_V2_MAX_ENTRIES_PER_SWING", 2), 2)))
        already = int(self._swing_entry_count.get(swing_key, 0))
        if already >= max_entries:
            return self._neutral(symbol, price, "V2_SWING_ALREADY_TRADED", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 4) hamulce czestotliwosci (pkt 21-23, 26)
        cooldown_reason = self._cooldown_reject(symbol, direction, now_ts)
        if cooldown_reason:
            return self._neutral(symbol, price, cooldown_reason, {"bias_1d": bias_1d, "bias_4h": bias_4h})

        retracement = swing_fib_retracement(swing)
        extension = swing_fib_extension(swing)
        if "0.5" not in retracement or "0.618" not in retracement:
            return self._neutral(symbol, price, "V2_NO_FIB_ZONE", {"bias_1d": bias_1d, "bias_4h": bias_4h})
        zone_near, zone_far = retracement["0.5"], retracement["0.618"]

        # 5) SL = swing 1h +/- bufor ATR (pkt 13, 14). "Swing low" dla LONG
        # to POCZATEK impulsu UP (swing["start"]) - dolek, od ktorego impuls
        # wystartowal - nie jego koniec/szczyt (swing["end"]).
        sl_buffer = _finite(getattr(config, "DAYTRADING_V2_SL_ATR_BUFFER", 0.5), 0.5) * atr_1h_now
        if direction == "LONG":
            sl_price = swing["start"]["price"] - sl_buffer
        else:
            sl_price = swing["start"]["price"] + sl_buffer
        risk = abs(price - sl_price)
        if risk <= 0:
            return self._neutral(symbol, price, "V2_INVALID_SL", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 6) filtr koszt vs SL (pkt 19)
        round_trip_cost_frac = 2.0 * _finite(getattr(config, "COMMISSION_RATE", 0.0006), 0.0006) + \
            _finite(getattr(config, "SLIPPAGE", 0.0008), 0.0008)
        min_sl_frac = round_trip_cost_frac * _finite(getattr(config, "DAYTRADING_V2_MIN_SL_VS_COST_MULT", 3.5), 3.5)
        if price > 0 and (risk / price) < min_sl_frac:
            return self._neutral(symbol, price, "V2_SL_TOO_TIGHT_VS_COST", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 7) TP1 = min(1R, najblizszy poziom 1h) (pkt 15)
        tp1_r_target = _finite(getattr(config, "DAYTRADING_V2_TP1_R", 1.0), 1.0)
        tp1_by_r = price + risk * tp1_r_target if direction == "LONG" else price - risk * tp1_r_target
        nearest_1h_level = self._nearest_1h_level(ind_1h, price, direction)
        if nearest_1h_level is not None:
            tp1_price = min(tp1_by_r, nearest_1h_level) if direction == "LONG" else max(tp1_by_r, nearest_1h_level)
        else:
            tp1_price = tp1_by_r
        tp1_r_actual = abs(tp1_price - price) / risk

        # 8) filtr R:R do TP1 (pkt 18)
        min_ratio = _finite(getattr(config, "DAYTRADING_V2_MIN_TP1_R_RATIO", 0.6), 0.6)
        if tp1_r_actual < min_ratio:
            return self._neutral(symbol, price, "V2_TP1_TOO_SMALL_VS_RISK", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 9) TP2 = extension 1.272-1.618 albo 2R (pkt 16)
        ext_ratio = str(_finite(getattr(config, "DAYTRADING_V2_TP2_EXTENSION_RATIO", 1.618), 1.618))
        tp2_price = extension.get(ext_ratio) or extension.get("1.618") or extension.get("1.272")
        if tp2_price is None:
            r2 = _finite(getattr(config, "DAYTRADING_V2_TP2_R_FALLBACK", 2.0), 2.0)
            tp2_price = price + risk * r2 if direction == "LONG" else price - risk * r2

        # 10) 15m trigger (pkt 4, 9)
        if not self._check_15m_trigger(frames["15m"], zone_near, zone_far, direction):
            return self._neutral(symbol, price, "V2_NO_15M_TRIGGER", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 11) 5m opcjonalne weto (pkt 5, 12) - NIGDY twardy blok na brak danych
        frame_5m = frames.get("5m") or {}
        if frame_5m.get("closes") and self._check_5m_veto(frame_5m, direction):
            return self._neutral(symbol, price, "V2_5M_VETO", {"bias_1d": bias_1d, "bias_4h": bias_4h})

        # 12) sizing z ryzyka % kapitalu (pkt 20) - tu tylko procent ryzyka i
        # odleglosc SL; przelozenie na wielkosc pozycji robi warstwa
        # wykonawcza (risk_manager), ktora zna kapital.
        risk_pct = _finite(getattr(config, "DAYTRADING_V2_RISK_PCT_OF_CAPITAL", 0.5), 0.5)

        self._consumed_swing_end[symbol] = swing["end"]["index"]
        self._swing_entry_count[swing_key] = already + 1

        return {
            "symbol": symbol, "direction": direction, "price": price,
            "sl_price": round(sl_price, 10), "tp1_price": round(tp1_price, 10),
            "tp2_price": round(tp2_price, 10), "tp1_r": round(tp1_r_actual, 4),
            "risk_pct_of_capital": risk_pct,
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
            "reasons": (
                ["V2_1D_BIAS", "V2_4H_CONFIRM", "V2_1H_SWING", "V2_15M_TRIGGER"]
                if bias_1d is not None else
                ["V2_4H_ANCHOR_NO_1D", "V2_1H_SWING", "V2_15M_TRIGGER"]
            ),
            "bias_1d": bias_1d, "bias_4h": bias_4h,
            "atr": atr_1h_now,
            "tp_plan": {
                "tp1_r": round(tp1_r_actual, 4),
                "tp2": "extension",
                "tp3": "trailing",
                "frac_tp1": float(getattr(config, "DAYTRADING_V2_TP1_FRAC", 0.50)),
                "frac_tp2": float(getattr(config, "DAYTRADING_V2_TP2_FRAC", 0.50)),
            },
        }

    @staticmethod
    def _nearest_1h_level(ind_1h: dict, price: float, direction: str) -> Optional[float]:
        """Najblizszy obiektywny poziom 1h (S/R potwierdzone albo klasyczny
        pivot) w kierunku ruchu - uzywany do TP1 = min(1R, poziom) (pkt 15)."""
        structure = ind_1h.get("support_resistance") or {}
        pivots = ind_1h.get("pivot_points") or {}
        candidates: List[float] = []
        if direction == "LONG":
            candidates.extend(_finite(x.get("price")) for x in structure.get("resistances") or [])
            candidates.extend(_finite(v) for k, v in pivots.items() if k.startswith("R"))
            candidates = [c for c in candidates if c > price]
            return min(candidates) if candidates else None
        candidates.extend(_finite(x.get("price")) for x in structure.get("supports") or [])
        candidates.extend(_finite(v) for k, v in pivots.items() if k.startswith("S"))
        candidates = [c for c in candidates if c < price]
        return max(candidates) if candidates else None

    @staticmethod
    def _neutral(symbol: str, price: float, reason: str, extra: Optional[dict] = None) -> dict:
        row = {
            "symbol": symbol, "direction": "NEUTRAL", "price": price,
            "reject_reason": reason, "reasons": [reason],
            "engine": "daytrading_v2", "strategy_mode": "DAYTRADING_V2",
            "setup": "intraday_wait", "strength": 0.05,
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
        valid = [c for c in coins or [] if str(c.get("symbol") or "").upper() not in excluded]

        def quote_volume(coin: dict) -> float:
            explicit = _finite(coin.get("blofin_quote_volume_24h"))
            if explicit > 0:
                return explicit
            base = _finite(coin.get("blofin_base_volume_24h"))
            if base > 0:
                return base * _finite(coin.get("price"))
            return _finite(coin.get("blofin_volume_24h") or coin.get("volume_24h"))

        ranked = sorted(valid, key=quote_volume, reverse=True)
        minimum = max(0.0, _finite(getattr(config, "MIN_VOLUME_24H_USD", 0)))
        ranked = [c for c in ranked if quote_volume(c) >= minimum]

        # 21.08.2026, druga iteracja: WS-connected NIE dostaje juz plaskiego
        # sufitu (bylo kolejno None, potem 60 - patrz historia w config.py) -
        # docelowo ("target") to CALE przefiltrowane wolumenem uniwersum, bo
        # samo bezpieczenstwo nie plynie juz z limitu liczby kandydatow, tylko
        # z pacingu ponizej. WS-down (brak WS) nadal ma twardy sufit 45
        # (DAYTRADING_V2_MAX_CANDIDATES) - bez WS nie ma taniego odswiezania
        # przez merge, wiec pelne uniwersum byloby trwale drogie w KAZDYM
        # cyklu, nie tylko podczas rozgrzewki.
        ws_connected = PUBLIC_WS.is_connected()
        if ws_connected:
            target = ranked
        else:
            limit = int(_finite(getattr(config, "DAYTRADING_V2_MAX_CANDIDATES", 45), 45))
            target = ranked[:max(1, limit)]
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
                out.append(self.evaluate(coin))
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
            row["details"] = {"spread_only": True, "bid": bid, "ask": ask, "spread_pct": spread_pct}
            out.append(row)
        return out
