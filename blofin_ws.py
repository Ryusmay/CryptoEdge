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
import re
import threading
import time
from typing import Callable, Dict, List, Optional

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
_CF_BACKOFF_S = 30.0
# Polaczenie, ktore przezylo tyle sekund, uznajemy za udane - backoff wraca
# do 1 s. Bez tego jedna seria bledow podnosila backoff do 30 s na stale i
# kazdy pozniejszy reconnect kosztowal pol minuty bez cen mimo zdrowej sieci.
_BACKOFF_RESET_AFTER_S = 60.0
_CF_MARKED = False
_WS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_WS_ORIGIN = "https://blofin.com"


def looks_like_geo_block(error) -> bool:
    """403 na handshake WSS (Cloudflare). Szablon HTML bywa 'restricted countries',
    ale to nie jest ban kraju - ten sam IP dziala w przegladarce i na REST."""
    text = str(error or "")
    if not text:
        return False
    low = text.lower()
    if "restricted countries" in low or "restricted country" in low:
        return True
    if "handshake status 403" in low:
        return True
    return False


def summarize_handshake_error(error) -> str:
    """Jedna linia zamiast 5 KB HTML przy kazdym reconnect."""
    text = str(error or "")
    status = "?"
    m = re.search(r"Handshake status (\d+)", text, re.I)
    if m:
        status = m.group(1)
    ray = "?"
    m = re.search(r"'cf-ray':\s*'([^']+)'", text, re.I)
    if m:
        ray = m.group(1)
    return f"HTTP {status} cf-ray={ray}"


def summarize_ws_error(error) -> str:
    """Skracamy TYLKO handshake (5 KB HTML). Reszte pokazujemy w calosci.

    25.08.2026: wczesniej kazdy blad szedl przez summarize_handshake_error(),
    ktory dla bledu spoza handshake'u nie trafial zadnym regexem i zwracal
    "HTTP ? cf-ray=?". Prawdziwa tresc byla wyrzucana, wiec log pokazywal
    Cloudflare tam, gdzie realnie serwer zamykal polaczenie ramka CLOSE
    (opcode 8, kod 1000). Diagnoza szla w zla strone.
    """
    text = str(error or "")
    low = text.lower()
    if "handshake status" in low or "cf-ray" in low:
        return summarize_handshake_error(error)
    kind = type(error).__name__ if isinstance(error, BaseException) else ""
    body = " ".join(text.split())[:300]
    if kind and body:
        return f"{kind}: {body}"
    return kind or body or "brak szczegolow"


def ws_handshake_header_sets(ua: Optional[str] = None) -> List[List[str]]:
    """Warianty handshake. NIGDY Connection/Accept JSON - to naglowki REST.
    Chrome na Upgrade wysyla Connection: Upgrade (doklada biblioteka).
    Restowe `Connection: keep-alive` psuje handshake (RFC 6455) i CF 403."""
    ua = ua or _WS_UA
    return [
        [
            f"User-Agent: {ua}",
            f"Origin: {_WS_ORIGIN}",
            "Accept-Language: en-US,en;q=0.9,pl;q=0.8",
            "Cache-Control: no-cache",
            "Pragma: no-cache",
        ],
        [f"User-Agent: {ua}", f"Origin: {_WS_ORIGIN}"],
        [f"User-Agent: {ua}"],
    ]


def _headers_are_ws_safe(headers: List[str]) -> bool:
    for raw in headers or []:
        key = raw.split(":", 1)[0].strip().lower()
        if key == "connection":
            return False
        if key == "accept" and "json" in raw.lower():
            return False
    return True


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
        self._geo_blocked = False
        self._geo_reason = ""
        self._handshake_variant = 0

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
        if max_age_s <= 0:
            return None
        with self._lock:
            row = self._prices.get(symbol.upper())
        if not row or "local_ts" not in row or time.time() - row["local_ts"] > max_age_s:
            return None
        return row.get("last")

    def get_ticker(self, symbol: str, max_age_s: float = 5.0) -> Optional[dict]:
        if max_age_s <= 0:
            return None
        with self._lock:
            row = self._prices.get(symbol.upper())
        if not row or "local_ts" not in row or time.time() - row["local_ts"] > max_age_s:
            return None
        return dict(row)

    def get_mark_price(self, symbol: str, max_age_s: float = 5.0) -> Optional[float]:
        if max_age_s <= 0:
            return None
        with self._lock:
            row = self._prices.get(symbol.upper())
        if not row or "mark_price_local_ts" not in row or time.time() - row["mark_price_local_ts"] > max_age_s:
            return None
        return row.get("mark_price")

    def get_order_book_top(self, symbol: str, max_age_s: float = 5.0) -> Optional[dict]:
        if max_age_s <= 0:
            return None
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
        if max_age_s <= 0:
            return None
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
        if max_age_s <= 0:
            return None
        with self._lock:
            row = self._last_closed_candles.get((symbol.upper(), bar))
        if not row or time.time() - row["local_ts"] > max_age_s:
            return None
        return dict(row)

    def is_connected(self) -> bool:
        return self._connected

    def is_cf_blocked(self) -> bool:
        return bool(self._geo_blocked)

    def is_geo_blocked(self) -> bool:
        return self.is_cf_blocked()

    def _mark_geo_blocked(self, error) -> None:
        global _CF_MARKED
        self._geo_blocked = True
        self._geo_reason = str(error or "")[:240]
        if _CF_MARKED:
            return
        _CF_MARKED = True
        print(
            "[BlofinWS] CF-403 na WSS — to nie 429 i nie ban PL. "
            "Przeglądarka/REST mogą działać. Handshake bez Connection: keep-alive "
            "(wcześniej szły nagłówki REST i psuły Upgrade)."
        )

    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            return {symbol: dict(row) for symbol, row in self._prices.items()}

    # --- wewnetrzne ---

    def _run_forever(self) -> None:
        backoff = 1.0
        while self._running:
            attempt_started = time.time()
            try:
                self._connect_once()
            except Exception as e:
                print(f"[BlofinWS] błąd połączenia: {e}")
                try:
                    from feed_log import note
                    note("BlofinWS", "connect", e)
                except Exception:
                    pass
                if looks_like_geo_block(e):
                    self._mark_geo_blocked(e)
            self._connected = False
            if not self._running:
                break
            if time.time() - attempt_started >= _BACKOFF_RESET_AFTER_S:
                backoff = 1.0
            wait = _CF_BACKOFF_S if self._geo_blocked else min(backoff, 30.0)
            time.sleep(wait)
            backoff = min(backoff * 2, 30.0)
            if self._geo_blocked:
                self._handshake_variant += 1

    def _connect_once(self) -> None:
        sets = ws_handshake_header_sets()
        header = sets[self._handshake_variant % len(sets)]
        sslopt = None
        try:
            import blofin_feed as _bf
            sslopt = {"context": _bf._ssl_context()}
            ua = getattr(_bf, "_BROWSER_UA", None)
            if ua:
                sets = ws_handshake_header_sets(ua)
                header = sets[self._handshake_variant % len(sets)]
        except Exception as e:
            try:
                from feed_log import note
                note("BlofinWS", "ssl/UA handshake setup", e)
            except Exception:
                pass
        if not _headers_are_ws_safe(header):
            header = [f"User-Agent: {_WS_UA}"]
        names = ",".join(h.split(":", 1)[0] for h in header)
        print(f"[BlofinWS] handshake variant={self._handshake_variant % len(sets)} headers={names}")
        self._ws = websocket.WebSocketApp(
            PUBLIC_WS_URL,
            header=header,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        # ping_interval=0: wlasny ping (string 'ping'), nie ramka protokolu.
        # origin NIE tu - jest w header. run_forever(origin=) dublowalby Origin.
        kwargs = {"ping_interval": 0}
        if sslopt:
            kwargs["sslopt"] = sslopt
        self._ws.run_forever(**kwargs)

    def _on_open(self, ws) -> None:
        self._connected = True
        self._geo_blocked = False
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
        try:
            from market_store import STORE
            STORE.set_tickers({
                symbol: {
                    "symbol": symbol,
                    "price": last,
                    "blofin_price": last,
                    "blofin_bid": existing.get("bid"),
                    "blofin_ask": existing.get("ask"),
                }
            }, from_ws=True)
        except Exception:
            pass

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
        print(f"[BlofinWS] błąd: {summarize_ws_error(error)}")
        try:
            from feed_log import note
            note("BlofinWS", "on_error", error)
        except Exception:
            pass
        if looks_like_geo_block(error):
            self._mark_geo_blocked(error)

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
