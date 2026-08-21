"""WebSocket Binance USDⓈ-M Futures (potwierdzenie ceny dla BTC/ETH/majors).

WAZNE (sprawdzone 20.08.2026, tego samego dnia co ostatnia aktualizacja
dokumentacji Binance): stare, niereroutowane URL-e (`wss://fstream.binance.com/ws`,
`/stream`) zostaly WYCOFANE dla strumieni kategorii "market" (w tym `@ticker`)
- termin migracji byl 2026-04-23, juz po nim. Trzeba uzywac nowej struktury:

  Public (wysokoczestotliwosciowe): wss://fstream.binance.com/public
  Market (zwykle dane rynkowe, w tym @ticker):  wss://fstream.binance.com/market
  Private (dane uzytkownika): wss://fstream.binance.com/private

`@ticker` (24hr statystyki tickera) nalezy do kategorii "market", wiec URL to:
  wss://fstream.binance.com/market/stream?streams=btcusdt@ticker/ethusdt@ticker

Odpowiedz w trybie combined stream jest zawijana:
  {"stream": "<streamName>", "data": <surowy payload tickera>}

Ping/pong: warstwa protokolu WS (nie string jak w Blofin) - biblioteka
websocket-client odpowiada automatycznie, nie trzeba wlasnego mechanizmu.

Degraduje sie w pelni bezpiecznie: brak websocket-client, blad polaczenia,
nieoczekiwany schemat wiadomosci - wszystko po prostu skutkuje brakiem
swiezych danych w cache (Binance i tak jest tu tylko POTWIERDZENIEM, REST
przez binance_feed.py zostaje pelnym fallbackiem)."""

from __future__ import annotations

import json
import threading
import time
from typing import Dict, Optional

try:
    import websocket  # websocket-client
    _WS_AVAILABLE = True
except ImportError:
    websocket = None
    _WS_AVAILABLE = False

MARKET_WS_BASE = "wss://fstream.binance.com/market/stream"


def _safe_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BinancePublicWebSocket:
    """Watek w tle utrzymujacy polaczenie WS Binance i cache ostatnich cen
    (24hr ticker) dla ustalonej listy symboli (BTC/ETH/majors, nie cale
    uniwersum - to tylko potwierdzenie, nie zrodlo pierwotne)."""

    def __init__(self):
        self._prices: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._symbols: list = []

    @property
    def available(self) -> bool:
        return _WS_AVAILABLE

    def start(self, symbols: list) -> bool:
        """Uruchamia watek polaczenia dla ustalonej listy symboli (BTC/ETH/
        majors - lista sie NIE rozszerza w locie jak w Blofin, bo to tylko
        potwierdzenie dla stalego, niewielkiego zestawu). Zwraca False, jesli
        websocket-client nie jest zainstalowany."""
        if not _WS_AVAILABLE:
            return False
        if self._running:
            return True
        self._symbols = [s.upper() for s in symbols if s]
        if not self._symbols:
            return False
        self._running = True
        self._thread = threading.Thread(target=self._run_forever, daemon=True, name="BinancePublicWS")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def get_price(self, symbol: str, max_age_s: float = 5.0) -> Optional[float]:
        with self._lock:
            row = self._prices.get(symbol.upper())
        if not row or time.time() - row["local_ts"] > max_age_s:
            return None
        return row.get("last")

    def is_connected(self) -> bool:
        return self._connected

    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            return {symbol: dict(row) for symbol, row in self._prices.items()}

    # --- wewnetrzne ---

    def _stream_url(self) -> str:
        streams = "/".join(f"{s.lower()}usdt@ticker" for s in self._symbols)
        return f"{MARKET_WS_BASE}?streams={streams}"

    def _run_forever(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                self._connect_once()
            except Exception as e:
                print(f"[BinanceWS] błąd połączenia: {e}")
            self._connected = False
            if not self._running:
                break
            time.sleep(min(backoff, 30.0))
            backoff = min(backoff * 2, 30.0)

    def _connect_once(self) -> None:
        self._ws = websocket.WebSocketApp(
            self._stream_url(),
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        # Domyslny ping_interval - to warstwa protokolu WS u Binance (co 3
        # min), biblioteka odpowiada pong automatycznie. Inaczej niz Blofin
        # (string 'ping'/'pong' na poziomie aplikacji).
        self._ws.run_forever()

    def _on_open(self, ws) -> None:
        self._connected = True
        print(f"[BinanceWS] połączono ({len(self._symbols)} symboli)")

    def _on_message(self, ws, message: str) -> None:
        try:
            payload = json.loads(message)
        except (ValueError, TypeError):
            return
        # combined stream: {"stream": "...", "data": {...}}; pojedynczy
        # stream (bez ?streams=) dawalby surowy payload bez opakowania -
        # obslugujemy oba na wszelki wypadek.
        data = payload.get("data") if isinstance(payload, dict) and "stream" in payload else payload
        if not isinstance(data, dict) or data.get("e") != "24hrTicker":
            return
        self._store_ticker(data)

    def _store_ticker(self, data: dict) -> None:
        symbol = str(data.get("s") or "").upper()
        if symbol.endswith("USDT"):
            symbol = symbol[:-4]
        last = _safe_float(data.get("c"))
        if not symbol or not last or last <= 0:
            return
        with self._lock:
            self._prices[symbol] = {
                "last": last,
                "open_24h": _safe_float(data.get("o")),
                "high_24h": _safe_float(data.get("h")),
                "low_24h": _safe_float(data.get("l")),
                "change_pct_24h": _safe_float(data.get("P")),
                "event_ts": data.get("E"),
                "local_ts": time.time(),
            }

    def _on_error(self, ws, error) -> None:
        print(f"[BinanceWS] błąd: {error}")

    def _on_close(self, ws, status_code, msg) -> None:
        self._connected = False
        print(f"[BinanceWS] rozłączono (code={status_code})")


# Wspoldzielona instancja - jedno polaczenie na proces.
PUBLIC_WS = BinancePublicWebSocket()
