# ============================================================
# Bybit Public Market Data (bez klucza API)
# ============================================================

import requests
import time
from typing import Dict, List, Optional

BYBIT_BASE = "https://api.bybit.com/v5"


class BybitFeed:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.ticker_cache: Dict[str, Dict] = {}
        self.ticker_cache_ts = 0
        self.ohlc_cache = {}
        self.last_error = None
        self.available = True
        self.fail_count = 0

    def _get(self, path: str, params: dict = None, timeout: int = 12) -> Optional[dict]:
        url = f"{BYBIT_BASE}/{path}"
        try:
            r = self.session.get(url, params=params or {}, timeout=timeout)
            if r.status_code == 403:
                self.last_error = "403 geo-blocked / forbidden"
                self.available = False
                self.fail_count += 1
                return None
            if r.status_code == 429:
                self.last_error = "429 rate limit"
                self.fail_count += 1
                print("[Bybit] Rate limit – czekam 12s")
                time.sleep(12)
                r = self.session.get(url, params=params or {}, timeout=timeout)
            if r.status_code != 200:
                self.last_error = f"HTTP {r.status_code}"
                self.fail_count += 1
                return None
            data = r.json()
            if data.get("retCode") not in (0, "0", None):
                self.last_error = f"retCode={data.get('retCode')} {data.get('retMsg')}"
                self.fail_count += 1
                return None
            self.fail_count = max(0, self.fail_count - 1)
            self.available = True
            self.last_error = None
            return data
        except requests.Timeout:
            self.last_error = "timeout"
            self.fail_count += 1
            return None
        except requests.RequestException as e:
            self.last_error = str(e)[:80]
            self.fail_count += 1
            return None
        except Exception as e:
            self.last_error = str(e)[:80]
            self.fail_count += 1
            return None

    def fetch_all_tickers(self) -> Dict[str, Dict]:
        """Spot tickers – jedna request. Zwraca BASE -> dane."""
        if time.time() - self.ticker_cache_ts < 10 and self.ticker_cache:
            return self.ticker_cache
        if not self.available and self.fail_count >= 3:
            # po 3 failach nie spamuj
            if time.time() - self.ticker_cache_ts < 120:
                return self.ticker_cache

        data = self._get("market/tickers", {"category": "spot"})
        if not data:
            return self.ticker_cache

        result = {}
        for t in data.get("result", {}).get("list", []):
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            try:
                result[base] = {
                    "bybit_price": float(t["lastPrice"]),
                    "bybit_change_24h": float(t.get("price24hPcnt", 0)) * 100,  # Bybit daje ułamek
                    "bybit_volume": float(t.get("turnover24h", 0)),
                    "bybit_high": float(t.get("highPrice24h", 0)),
                    "bybit_low": float(t.get("lowPrice24h", 0)),
                }
            except (KeyError, ValueError, TypeError):
                continue

        if result:
            self.ticker_cache = result
            self.ticker_cache_ts = time.time()
        return self.ticker_cache

    def fetch_klines_closes(self, symbol: str, interval: str = "60", limit: int = 50) -> List[float]:
        """
        interval Bybit: 1,3,5,15,30,60,120,240,D
        60 = 1h
        """
        pair = f"{symbol.upper()}USDT"
        cache_key = f"{pair}_{interval}_{limit}"
        if cache_key in self.ohlc_cache:
            ts, closes = self.ohlc_cache[cache_key]
            if time.time() - ts < 60:
                return closes

        data = self._get("market/kline", {
            "category": "spot",
            "symbol": pair,
            "interval": interval,
            "limit": limit
        })
        if not data:
            return []

        rows = data.get("result", {}).get("list", [])
        # Bybit: newest-first → reverse; drop forming (last)
        closes = []
        for row in reversed(rows):
            try:
                closes.append(float(row[4]))  # close
            except (IndexError, ValueError, TypeError):
                continue
        if len(closes) >= 2:
            closes = closes[:-1]
        if closes:
            self.ohlc_cache[cache_key] = (time.time(), closes)
        return closes

    def status(self) -> str:
        if self.last_error:
            return f"Bybit: ERROR ({self.last_error})"
        if not self.ticker_cache:
            return "Bybit: no data"
        return f"Bybit: OK ({len(self.ticker_cache)} pairs)"
