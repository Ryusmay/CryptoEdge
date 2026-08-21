"""Publiczny WebSocket Blofin (ceny w czasie rzeczywistym).

Zastępuje częste zapytania REST po bieżącą cenę (najbardziej krytyczna,
najczęstsza ścieżka - ochrona otwartych pozycji, patrz fetch_last_prices w
blofin_feed.py) stałym połączeniem WS, zgodnie z oficjalną specyfikacją:

- Endpoint: wss://openapi.blofin.com/ws/public (bez autoryzacji)
- Subskrypcja: {"op":"subscribe","args":[{"channel":"tickers","instId":"BTC-USDT"}, ...]}
  Max 4096 bajtów argumentów na wiadomość - batchujemy po _MAX_ARGS_PER_SUBSCRIBE.
- Push tickera: dane co najmniej raz na 1s dla subskrybowanego instId.
- Stabilność: jeśli w ciągu N<30s nie przyjdzie żadna wiadomość, wyślij
  string 'ping' (nie JSON), oczekuj 'pong'. Brak jakiejkolwiek wiadomości
  przez >30s = Blofin sam zrywa połączenie.
- Nowe połączenia: max 1/s na IP - stąd jedna, współdzielona instancja
  (PUBLIC_WS) na cały proces, tak jak PUBLIC_BUCKET/TRADING_BUCKET.

Degraduje się w pełni bezpiecznie: brak pakietu websocket-client, błąd
połączenia, timeout - wszystko po prostu skutkuje brakiem świeżych danych w
cache. Kod wywołujący (blofin_feed.py) ma REST jako pełny fallback i nie
wie/nie musi wiedzieć, czy WS w ogóle działa.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable, Dict, Optional

try:
    import websocket  # websocket-client
    _WS_AVAILABLE = True
except ImportError:
    websocket = None
    _WS_AVAILABLE = False

PUBLIC_WS_URL = "wss://openapi.blofin.com/ws/public"
_PING_IDLE_SECONDS = 15.0     # < 30s wymagane przez specyfikację Blofin
_PONG_TIMEOUT_SECONDS = 10.0
_MAX_ARGS_PER_SUBSCRIBE = 80  # margines ponizej limitu 4096 bajtow/wiadomosc


def _chunk(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _safe_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BlofinPublicWebSocket:
    """Wątek w tle utrzymujący połączenie WS i cache ostatnich cen (tickers)."""

    def __init__(self):
        self._prices: Dict[str, dict] = {}
        self._candles: Dict[tuple, dict] = {}  # (symbol, bar) -> {"open","high","low","close","volume","ts","local_ts"} - MOZE byc wciaz formujaca sie
        self._last_closed_candles: Dict[tuple, dict] = {}  # (symbol, bar) -> ostatnia FAKTYCZNIE zamknieta swieca (bezpieczna dla wskaznikow)
        self._lock = threading.Lock()
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._last_message_ts = 0.0
        self._subscribed_symbols: set = set()
        self._subscribed_candle_pairs: set = set()  # {(symbol, bar)}

    @property
    def available(self) -> bool:
        """Czy pakiet websocket-client w ogole jest zainstalowany."""
        return _WS_AVAILABLE

    def start(self, symbols: Optional[list] = None) -> bool:
        """Uruchamia watek polaczenia (no-op jesli juz dziala). Zwraca False,
        jesli websocket-client nie jest zainstalowany - wolajacy powinien
        wtedy po prostu dalej uzywac REST bez zadnych zmian."""
        if not _WS_AVAILABLE:
            return False
        if symbols:
            self._subscribed_symbols.update(s.upper() for s in symbols if s)
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._run_forever, daemon=True, name="BlofinPublicWS")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def subscribe(self, symbols: list) -> None:
        """Dodaje symbole do subskrypcji. Jesli juz polaczeni, wysyla od razu;
        w przeciwnym razie zostaja zapamietane do wyslania po polaczeniu."""
        new_symbols = [s.upper() for s in symbols if s and s.upper() not in self._subscribed_symbols]
        if not new_symbols:
            return
        self._subscribed_symbols.update(new_symbols)
        if self._connected and self._ws is not None:
            self._send_subscribe(new_symbols)

    def subscribe_candles(self, symbol: str, bars: list) -> None:
        """Dodaje zywe swiece OHLCV (kanal candle{bar}) dla danego symbolu i
        listy interwalow (np. ["5m","15m","1H","4H","1D"]). To jest zrodlo
        near-real-time danych, ktore fetch_klines_ohlcv() w blofin_feed.py
        nakleja na najswiezszy bar zamiast czekac na wygasniecie TTL cache
        REST - patrz get_live_candle()."""
        symbol = symbol.upper()
        new_pairs = [(symbol, b) for b in (bars or []) if (symbol, b) not in self._subscribed_candle_pairs]
        if not new_pairs:
            return
        self._subscribed_candle_pairs.update(new_pairs)
        if self._connected and self._ws is not None:
            self._send_subscribe_candles(new_pairs)

    def get_price(self, symbol: str, max_age_s: float = 5.0) -> Optional[float]:
        with self._lock:
            row = self._prices.get(symbol.upper())
        if not row or "local_ts" not in row or time.time() - row["local_ts"] > max_age_s:
            return None
        return row.get("last")

    def get_ticker(self, symbol: str, max_age_s: float = 5.0) -> Optional[dict]:
        with self._lock:
            row = self._prices.get(symbol.upper())
        if not row or "local_ts" not in row or time.time() - row["local_ts"] > max_age_s:
            return None
        return dict(row)

    def get_mark_price(self, symbol: str, max_age_s: float = 5.0) -> Optional[float]:
        with self._lock:
            row = self._prices.get(symbol.upper())
        if not row or "mark_price_local_ts" not in row or time.time() - row["mark_price_local_ts"] > max_age_s:
            return None
        return row.get("mark_price")

    def get_order_book_top(self, symbol: str, max_age_s: float = 5.0) -> Optional[dict]:
        with self._lock:
            row = self._prices.get(symbol.upper())
        if not row or "book_local_ts" not in row or time.time() - row["book_local_ts"] > max_age_s:
            return None
        return {
            "best_bid": row.get("book_best_bid"), "best_ask": row.get("book_best_ask"),
            "bids_top5": list(row.get("book_bids_top5") or []),
            "asks_top5": list(row.get("book_asks_top5") or []),
        }

    def get_live_candle(self, symbol: str, bar: str, max_age_s: float = 5.0) -> Optional[dict]:
        """Aktualna (moze wciaz trwac) swieca - NIE do wskaznikow, tylko do
        podgladu/monitoringu. Do wskaznikow uzyj get_last_closed_candle()."""
        with self._lock:
            row = self._candles.get((symbol.upper(), bar))
        if not row or time.time() - row["local_ts"] > max_age_s:
            return None
        return dict(row)

    def get_last_closed_candle(self, symbol: str, bar: str, max_age_s: float = 30.0) -> Optional[dict]:
        """Ostatnia FAKTYCZNIE zamknieta swieca (wykryta przez rollover
        timestampu w strumieniu WS) - bezpieczna do naklejenia na koniec
        serii z REST/cache w fetch_klines_ohlcv(). Domyslny max_age_s wiekszy
        niz get_live_candle(), bo zamkniecie bara to rzadkie zdarzenie (raz
        na caly bar), nie cos co powinno "wygasac" po kilku sekundach."""
        with self._lock:
            row = self._last_closed_candles.get((symbol.upper(), bar))
        if not row or time.time() - row["local_ts"] > max_age_s:
            return None
        return dict(row)

    def is_connected(self) -> bool:
        return self._connected

    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            return {symbol: dict(row) for symbol, row in self._prices.items()}

    # --- wewnetrzne ---

    def _run_forever(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                self._connect_once()
            except Exception as e:
                print(f"[BlofinWS] błąd połączenia: {e}")
            self._connected = False
            if not self._running:
                break
            time.sleep(min(backoff, 30.0))
            backoff = min(backoff * 2, 30.0)

    def _connect_once(self) -> None:
        self._ws = websocket.WebSocketApp(
            PUBLIC_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        # ping_interval=0: wlasny mechanizm ping wg specyfikacji Blofin
        # (string 'ping', nie ramka protokolu WS), nie wbudowany w bibliotece.
        self._ws.run_forever(ping_interval=0)

    def _on_open(self, ws) -> None:
        self._connected = True
        self._last_message_ts = time.time()
        print("[BlofinWS] połączono")
        if self._subscribed_symbols:
            self._send_subscribe(list(self._subscribed_symbols))
        if self._subscribed_candle_pairs:
            self._send_subscribe_candles(list(self._subscribed_candle_pairs))
        threading.Thread(target=self._watchdog, daemon=True, name="BlofinWS-watchdog").start()

    def _send_subscribe_candles(self, pairs: list) -> None:
        # {bar}: 1m/5m/15m/1H/4H/1D - dokladnie te same napisy co REST bar,
        # kanal to "candle{bar}" (potwierdzone w dokumentacji Blofin).
        for batch in _chunk(pairs, _MAX_ARGS_PER_SUBSCRIBE):
            args = [{"channel": f"candle{bar}", "instId": f"{sym}-USDT"} for sym, bar in batch]
            try:
                self._ws.send(json.dumps({"op": "subscribe", "args": args}))
            except Exception as e:
                print(f"[BlofinWS] subscribe candles błąd: {e}")

    def _send_subscribe(self, symbols: list) -> None:
        for batch in _chunk(symbols, _MAX_ARGS_PER_SUBSCRIBE // 3):
            args = []
            for s in batch:
                inst = f"{s.upper()}-USDT"
                args.append({"channel": "tickers", "instId": inst})
                args.append({"channel": "mark-price-candle1m", "instId": inst})
                args.append({"channel": "books5", "instId": inst})
            try:
                self._ws.send(json.dumps({"op": "subscribe", "args": args}))
            except Exception as e:
                print(f"[BlofinWS] subscribe błąd: {e}")

    def _on_message(self, ws, message: str) -> None:
        self._last_message_ts = time.time()
        if message == "pong":
            return
        try:
            payload = json.loads(message)
        except (ValueError, TypeError):
            return
        event = payload.get("event")
        if event in ("subscribe", "unsubscribe"):
            return
        if event == "error":
            print(f"[BlofinWS] błąd subskrypcji: {payload.get('msg')}")
            return
        arg = payload.get("arg") or {}
        channel = arg.get("channel")
        inst_id = arg.get("instId")
        if channel == "tickers":
            for row in payload.get("data") or []:
                self._store_ticker(row)
        elif channel == "mark-price-candle1m":
            for row in payload.get("data") or []:
                self._store_mark_price(inst_id, row)
        elif channel == "books5":
            self._store_order_book(inst_id, payload.get("data") or {})
        elif isinstance(channel, str) and channel.startswith("candle") and channel != "mark-price-candle1m":
            bar = channel[len("candle"):]
            for row in payload.get("data") or []:
                self._store_candle(inst_id, bar, row)

    def _store_ticker(self, row: dict) -> None:
        symbol = str(row.get("instId") or "").split("-")[0].upper()
        last = _safe_float(row.get("last"))
        if not symbol or not last or last <= 0:
            return
        with self._lock:
            existing = self._prices.get(symbol, {})
            existing.update({
                "last": last,
                "bid": _safe_float(row.get("bidPrice")),
                "ask": _safe_float(row.get("askPrice")),
                "ts": row.get("ts"),
                "local_ts": time.time(),
            })
            self._prices[symbol] = existing

    def _store_candle(self, inst_id, bar: str, row) -> None:
        # Format wg dokumentacji Blofin: [ts, open, high, low, close, vol, volCcy, volCcyQuote]
        #
        # WAZNE: REST (fetch_klines_ohlcv) swiadomie odrzuca jeszcze
        # niezamkniete swiece przed przekazaniem do wskaznikow ("Never pass
        # a known-open candle into indicators") - wciaz zmieniajaca sie
        # swieca destabilizowalaby ATR/RSI/swing. WS musi uszanowac te sama
        # zasade: NIE eksponujemy wciaz-formujacej sie swiecy jako "gotowej"
        # (get_live_candle) - eksponujemy tylko moment jej ZAMKNIECIA
        # (get_last_closed_candle), wykryty przez rollover timestampu w
        # strumieniu pushy. To daje realny zysk (zamkniecie widoczne niemal
        # natychmiast, nie po TTL cache REST rzedu minut), bez naruszania
        # ustalonej zasady bezpieczenstwa wskaznikow.
        symbol = str(inst_id or "").split("-")[0].upper()
        if not symbol or not isinstance(row, (list, tuple)) or len(row) < 5:
            return
        o, h, l, c = _safe_float(row[1]), _safe_float(row[2]), _safe_float(row[3]), _safe_float(row[4])
        if c is None or c <= 0:
            return
        key = (symbol, bar)
        new_ts = row[0]
        candle = {
            "ts": new_ts, "open": o, "high": h, "low": l, "close": c,
            "volume": _safe_float(row[5]) if len(row) > 5 else None,
            "local_ts": time.time(),
        }
        with self._lock:
            previous = self._candles.get(key)
            if previous is not None and previous.get("ts") not in (None, new_ts):
                # Timestamp sie zmienil - poprzednia swieca wlasnie sie
                # zamknela (Blofin zaczal pchac nowy bar).
                self._last_closed_candles[key] = previous
            self._candles[key] = candle

    def _store_mark_price(self, inst_id, row) -> None:
        # Kanal mark-price-candle1m: brak osobnego "plain" kanalu mark price
        # w publicznym WS Blofin - najblizsza swieca 1m, "close" ~ biezaca
        # mark price (dokladnosc do <1min, wystarczajaca do monitoringu).
        symbol = str(inst_id or "").split("-")[0].upper()
        if not symbol or not isinstance(row, (list, tuple)) or len(row) < 5:
            return
        mark = _safe_float(row[4])  # [ts, open, high, low, close, ...]
        if not mark or mark <= 0:
            return
        with self._lock:
            existing = self._prices.get(symbol, {})
            existing["mark_price"] = mark
            existing["mark_price_local_ts"] = time.time()
            self._prices[symbol] = existing

    def _store_order_book(self, inst_id, data) -> None:
        symbol = str(inst_id or "").split("-")[0].upper()
        if not symbol or not isinstance(data, dict):
            return
        asks = data.get("asks") or []
        bids = data.get("bids") or []
        best_ask = _safe_float(asks[0][0]) if asks else None
        best_bid = _safe_float(bids[0][0]) if bids else None
        with self._lock:
            existing = self._prices.get(symbol, {})
            existing["book_best_ask"] = best_ask
            existing["book_best_bid"] = best_bid
            existing["book_asks_top5"] = [[_safe_float(p), _safe_float(q)] for p, q in asks[:5]]
            existing["book_bids_top5"] = [[_safe_float(p), _safe_float(q)] for p, q in bids[:5]]
            existing["book_local_ts"] = time.time()
            self._prices[symbol] = existing

    def _on_error(self, ws, error) -> None:
        print(f"[BlofinWS] błąd: {error}")

    def _on_close(self, ws, status_code, msg) -> None:
        self._connected = False
        print(f"[BlofinWS] rozłączono (code={status_code})")

    def _watchdog(self) -> None:
        """Wysyla 'ping' po _PING_IDLE_SECONDS ciszy; jesli dalej cisza po
        _PONG_TIMEOUT_SECONDS, zamyka polaczenie sam (przyspiesza reconnect
        zamiast czekac az Blofin zerwie je po swojej stronie po >30s)."""
        while self._connected and self._running:
            time.sleep(1.0)
            idle = time.time() - self._last_message_ts
            if idle >= _PING_IDLE_SECONDS:
                try:
                    self._ws.send("ping")
                except Exception:
                    break
                time.sleep(_PONG_TIMEOUT_SECONDS)
                if time.time() - self._last_message_ts >= _PING_IDLE_SECONDS + _PONG_TIMEOUT_SECONDS:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                    break


# Wspoldzielona (modulowa) instancja - limit Blofin to max 1 nowe polaczenie/s
# na IP, wiec caly proces trzyma jedno polaczenie WS, tak jak PUBLIC_BUCKET/
# TRADING_BUCKET dziela jeden budzet REST.
PUBLIC_WS = BlofinPublicWebSocket()
