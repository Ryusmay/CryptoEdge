# ============================================================
# Binance USDT-M Futures (perp) – public market data
# Porównanie cross-market: BN perp ↔ BF perp (nie Spot)
# ============================================================

import requests
import time
from typing import List, Dict, Optional

# USDT-M Futures API (fapi). Fallback lista przy geo/rate.
BINANCE_FAPI_BASES = [
    "https://fapi.binance.com/fapi/v1",
    "https://fapi1.binance.com/fapi/v1",
    "https://fapi2.binance.com/fapi/v1",
]
BINANCE_SPOT_FALLBACK = "https://data-api.binance.vision/api/v3"  # tylko gdy FAPI geo-blocked
BINANCE_BASE = BINANCE_FAPI_BASES[0]

# Mapowanie symboli CoinGecko -> Binance (najczestsze)
SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "BNB": "BNBUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "DOT": "DOTUSDT",
    "LINK": "LINKUSDT",
    "MATIC": "MATICUSDT",
    "POL": "POLUSDT",
    "LTC": "LTCUSDT",
    "BCH": "BCHUSDT",
    "ATOM": "ATOMUSDT",
    "UNI": "UNIUSDT",
    "NEAR": "NEARUSDT",
    "APT": "APTUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
    "SUI": "SUIUSDT",
    "FIL": "FILUSDT",
    "ICP": "ICPUSDT",
    "HBAR": "HBARUSDT",
    "INJ": "INJUSDT",
    "IMX": "IMXUSDT",
    "RENDER": "RENDERUSDT",
    "FET": "FETUSDT",
    "PEPE": "PEPEUSDT",
    "WIF": "WIFUSDT",
    "BONK": "BONKUSDT",
    "FLOKI": "FLOKIUSDT",
    "AAVE": "AAVEUSDT",
    "MKR": "MKRUSDT",
    "CRV": "CRVUSDT",
    "LDO": "LDOUSDT",
    "STX": "STXUSDT",
    "TIA": "TIAUSDT",
    "SEI": "SEIUSDT",
    "RUNE": "RUNEUSDT",
    "THETA": "THETAUSDT",
    "ALGO": "ALGOUSDT",
    "VET": "VETUSDT",
    "GRT": "GRTUSDT",
    "SAND": "SANDUSDT",
    "MANA": "MANAUSDT",
    "AXS": "AXSUSDT",
    "EGLD": "EGLDUSDT",
    "FTM": "FTMUSDT",
    "S": "SUSDT",
    "TRX": "TRXUSDT",
    "TON": "TONUSDT",
    "SHIB": "SHIBUSDT",
    "OKB": "OKBUSDT",
    "PUMP": "PUMPUSDT",
}


class BinanceFeed:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._exchange_info = None
        self._valid_symbols = set()
        self.ohlc_cache = {}  # key -> (ts, closes)
        self.ticker_cache = {}
        self.ticker_cache_ts = 0
        self.last_error = None
        self.available = True
        self.fail_count = 0
        self.market_mode = "usdtm_perp"  # usdtm_perp | spot_fallback
        self._active_base = BINANCE_FAPI_BASES[0]

    def _get(self, path: str, params: dict = None, timeout: int = 10):
        """
        GET: najpierw Binance USDT-M Futures (fapi).
        Przy geo-block / total fail → opcjonalny Spot (oznaczony spot_fallback).
        """
        last_err = None
        bases = list(BINANCE_FAPI_BASES)
        if getattr(self, "_active_base", None) in bases:
            bases = [self._active_base] + [b for b in bases if b != self._active_base]
        for base in bases:
            url = f"{base}/{path.lstrip('/')}"
            try:
                r = self.session.get(url, params=params or {}, timeout=timeout)
                if r.status_code in (451, 403):
                    last_err = f"{r.status_code} geo/forbidden"
                    continue
                if r.status_code == 429:
                    last_err = "429 rate limit"
                    print("[Binance FAPI] Rate limit – czekam 15s")
                    time.sleep(15)
                    r = self.session.get(url, params=params or {}, timeout=timeout)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                self._active_base = base
                self.market_mode = "usdtm_perp"
                self.fail_count = max(0, self.fail_count - 1)
                self.available = True
                self.last_error = None
                return r.json()
            except requests.Timeout:
                last_err = "timeout"
                continue
            except Exception as e:
                last_err = str(e)[:80]
                continue

        # Fallback Spot (tylko gdy FAPI niedostępne – nie idealne do BN perp↔BF perp)
        try:
            url = f"{BINANCE_SPOT_FALLBACK}/{path.lstrip('/')}"
            r = self.session.get(url, params=params or {}, timeout=timeout)
            if r.status_code == 200:
                if self.market_mode != "spot_fallback":
                    print("[Binance] FAPI niedostępne → SPOT fallback (divergence mniej wiarygodna)")
                self.market_mode = "spot_fallback"
                self._active_base = BINANCE_SPOT_FALLBACK
                self.available = True
                self.last_error = "using_spot_fallback"
                self.fail_count = max(0, self.fail_count - 1)
                return r.json()
            last_err = f"spot HTTP {r.status_code}"
        except Exception as e:
            last_err = f"spot {str(e)[:60]}"

        self.last_error = last_err or "fapi unavailable"
        self.fail_count += 1
        if self.fail_count >= 5:
            self.available = False
        if last_err:
            print(f"[Binance FAPI] Blad: {last_err}")
        return None

    
    def fetch_klines_ohlcv(self, symbol: str, interval: str = "1h", limit: int = 120) -> dict:
        """
        Zwraca closes/highs/lows/volumes/timestamps – tylko zamknięte świece.
        Paginacja: Binance max 1000/request; dla limit>1000 idziemy wstecz po endTime.
        """
        pair = self.to_binance_symbol(symbol)
        if not pair:
            return {}
        limit = int(max(1, limit))
        cache_key = f"ohlcv_{pair}_{interval}_{limit}"
        if cache_key in self.ohlc_cache:
            ts, data = self.ohlc_cache[cache_key]
            # dłuższy TF – dłuższy cache
            ttl = 120 if interval in ("4h", "1d", "1w") else 60
            if isinstance(data, dict) and time.time() - ts < ttl:
                return data

        max_per_req = 1000
        all_rows = []
        remaining = limit
        end_time = None
        while remaining > 0:
            batch = min(remaining, max_per_req)
            params = {"symbol": pair, "interval": interval, "limit": batch}
            if end_time is not None:
                params["endTime"] = int(end_time)
            raw = self._get("klines", params)
            if not raw or not isinstance(raw, list):
                break
            all_rows = raw + all_rows  # starsze z lewej
            if len(raw) < batch:
                break
            # kolejna strona: przed najstarszą świecą tej paczki
            try:
                end_time = int(raw[0][0]) - 1
            except (IndexError, TypeError, ValueError):
                break
            remaining = limit - len(all_rows)
            if remaining <= 0:
                break

        if not all_rows:
            return {}
        # dedupe po open_time + przytnij do limit od końca
        seen = set()
        uniq = []
        for k in all_rows:
            try:
                ot = int(k[0])
            except (TypeError, ValueError, IndexError):
                continue
            if ot in seen:
                continue
            seen.add(ot)
            uniq.append(k)
        uniq = uniq[-limit:]

        opens, closes, highs, lows, volumes, timestamps = [], [], [], [], [], []
        for k in uniq:
            try:
                timestamps.append(int(k[0]))
                opens.append(float(k[1]))
                highs.append(float(k[2]))
                lows.append(float(k[3]))
                closes.append(float(k[4]))
                volumes.append(float(k[5]))
            except (IndexError, ValueError, TypeError):
                continue
        data = {
            "opens": opens, "closes": closes, "highs": highs, "lows": lows,
            "volumes": volumes, "timestamps": timestamps,
        }
        try:
            from market_data import drop_unclosed_candle
            data = drop_unclosed_candle(data, interval)
        except Exception:
            if closes:
                data = {k: (v[:-1] if isinstance(v, list) else v) for k, v in data.items()}
        self.ohlc_cache[cache_key] = (time.time(), data)
        return data

    def status(self) -> str:
        mode = getattr(self, "market_mode", "usdtm_perp")
        tag = "FAPI" if mode == "usdtm_perp" else "SPOT-fb"
        if self.last_error and mode == "usdtm_perp" and not self.ticker_cache:
            return f"Binance{tag}: ERROR ({self.last_error})"
        if not self.ticker_cache:
            return f"Binance{tag}: no data"
        return f"Binance{tag}: OK ({len(self.ticker_cache)} pairs)"

    def ensure_symbols(self):
        """Lista par USDT-M perpetual (contractType=PERPETUAL)."""
        if self._valid_symbols:
            return
        data = self._get("exchangeInfo")
        if not data:
            return
        for s in data.get("symbols", []):
            # Futures: TRADING + USDT quote + perpetual
            if s.get("status") != "TRADING":
                continue
            if s.get("quoteAsset") and s.get("quoteAsset") != "USDT":
                continue
            ct = (s.get("contractType") or "").upper()
            if ct and ct not in ("PERPETUAL", ""):
                continue
            sym = s.get("symbol")
            if sym and sym.endswith("USDT"):
                self._valid_symbols.add(sym)

    def to_binance_symbol(self, symbol: str) -> Optional[str]:
        symbol = symbol.upper()
        if symbol in SYMBOL_MAP:
            pair = SYMBOL_MAP[symbol]
        else:
            pair = f"{symbol}USDT"
        self.ensure_symbols()
        if self._valid_symbols and pair not in self._valid_symbols:
            return None
        return pair

    def fetch_all_tickers(self) -> Dict[str, Dict]:
        """
        24h tickery USDT-M perpetual – jedna request (fapi).
        Zwraca map: BASE -> {price, change_24h, volume, high, low}
        """
        if time.time() - self.ticker_cache_ts < 8 and self.ticker_cache:
            return self.ticker_cache

        data = self._get("ticker/24hr")
        if not data or not isinstance(data, list):
            return self.ticker_cache

        # opcjonalnie filtruj tylko znane perpy
        self.ensure_symbols()
        result = {}
        for t in data:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if self._valid_symbols and sym not in self._valid_symbols:
                continue
            base = sym[:-4]
            try:
                result[base] = {
                    "binance_price": float(t["lastPrice"]),
                    "binance_change_24h": float(t["priceChangePercent"]),
                    "binance_volume": float(t["quoteVolume"]),
                    "binance_high": float(t["highPrice"]),
                    "binance_low": float(t["lowPrice"]),
                    "binance_market": getattr(self, "market_mode", "usdtm_perp"),
                    "binance_ts_ms": int(float(t.get("closeTime") or time.time() * 1000)),
                }
            except (KeyError, ValueError):
                continue

        self.ticker_cache = result
        self.ticker_cache_ts = time.time()
        return result

    def fetch_klines_closes(self, symbol: str, interval: str = "1h", limit: int = 50) -> List[float]:
        """Tylko zamknięte świece (drop forming via fetch_klines_ohlcv)."""
        data = self.fetch_klines_ohlcv(symbol, interval=interval, limit=max(int(limit) + 1, 2))
        closes = list((data or {}).get("closes") or [])
        return closes[-int(limit):] if closes else []


        data = self._get("klines", {
            "symbol": pair,
            "interval": interval,
            "limit": limit
        })
        if not data or not isinstance(data, list):
            return []

        # kline: [open_time, open, high, low, close, volume, ...]
        closes = []
        for k in data:
            try:
                closes.append(float(k[4]))
            except (IndexError, ValueError):
                continue

        self.ohlc_cache[cache_key] = (time.time(), closes)
        return closes
