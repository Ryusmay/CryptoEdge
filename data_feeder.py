# ============================================================
# Data Feeder - Universe = Blofin Futures USDT (wszystkie pary)
# Cross-check: Binance + Bybit + CoinGecko
# ============================================================

import requests
import time
from typing import List, Dict, Optional
import config
import disk_cache
import binance_ws
from binance_feed import BinanceFeed
from bybit_feed import BybitFeed
from blofin_feed import BlofinFeed
from market_context import MarketContext

STABLECOINS = {
    "USDT", "USDC", "DAI", "USDE", "USD1", "BFUSD", "TUSD", "FDUSD",
    "BUSD", "USDD", "GUSD", "USDP", "FRAX", "LUSD", "PYUSD", "USDJ",
    "CUSD", "SUSD", "USTC", "EURC", "EUROC", "XAUT", "PAXG"
}


class DataFeeder:
    def __init__(self):
        self.cg_url = "https://api.coingecko.com/api/v3"
        self.cg_key = config.COINGECKO_API_KEY
        self.cache = []
        self.ohlc_cache = {}
        self.last_fetch = 0
        self.last_successful_fetch = 0
        self.btc_price = None
        self.rate_limit_hits = 0
        self.binance = BinanceFeed()
        self.bybit = BybitFeed()
        self.blofin = BlofinFeed()
        self.market_ctx = MarketContext(cg_key=self.cg_key)
        self.last_market_context = {}
        self.last_errors = []
        self.instruments_cache = []
        self.instruments_ts = 0
        self.instruments_fail_ts = 0.0
        self.instruments_fail_streak = 0
        # Seed z dysku - przestarzala lista jest lepsza niz pusta zaraz po
        # restarcie (unika burst zapytan "od zera", ktory wywolywal
        # rate-limit spiral 19-20.08). Normalna logika swiezosci (600s) w
        # fetch_blofin_usdt_instruments() i tak zdecyduje, czy to jeszcze
        # aktualne, czy trzeba odswiezyc.
        _disk_hit = disk_cache.load("blofin_instruments")
        if _disk_hit and isinstance(_disk_hit.get("data"), list) and _disk_hit["data"]:
            self.instruments_cache = _disk_hit["data"]
            self.instruments_ts = _disk_hit["ts"]
            print(f"[DataFeeder] Wczytano {len(self.instruments_cache)} instrumentów z cache na dysku (sprzed {time.time()-_disk_hit['ts']:.0f}s)")
        self.cg_map_cache = {}  # symbol -> cg market data
        self.cg_map_ts = 0
        self.cg_map_fail_ts = 0.0
        self.cg_map_fail_streak = 0

    def _get_coingecko(self, endpoint: str, params: dict = None) -> Optional[dict]:
        if params is None:
            params = {}
        if self.cg_key:
            params["x_cg_demo_api_key"] = self.cg_key
        url = f"{self.cg_url}/{endpoint}"
        try:
            r = requests.get(url, params=params, timeout=12)
            if r.status_code == 429:
                self.rate_limit_hits += 1
                wait = 25 + (self.rate_limit_hits * 8)
                print(f"[DataFeeder] CoinGecko 429 – czekam {wait}s...")
                time.sleep(wait)
                r = requests.get(url, params=params, timeout=12)
            if r.status_code != 200:
                self.last_errors.append(f"CG:{r.status_code}")
                return None
            self.rate_limit_hits = max(0, self.rate_limit_hits - 1)
            return r.json()
        except Exception as e:
            self.last_errors.append(f"CG:{str(e)[:40]}")
            return None

    def fetch_blofin_usdt_instruments(self) -> List[Dict]:
        """Lista live SWAP USDT z Blofin (odswiezana co 10 min)."""
        if time.time() - self.instruments_ts < 600 and self.instruments_cache:
            return self.instruments_cache
        # Po niepowodzeniu: nie mloc API co cykl (~13s) - to samo tylko
        # przedluza rate-limit zamiast pozwolic mu wygasnac. Backoff
        # eskalujacy (45s, 90s, 180s, 360s, sufit 600s) zamiast stalego 45s -
        # w praktyce jeden cykl 45s bywa za krotki (Blofin realnie blokuje
        # dluzej), wiec kolejne niepowodzenia czekaja coraz dluzej zamiast
        # w kolko odpalac dokladnie ten sam, wciaz nieudany, 45s cykl.
        cooldown = min(45.0 * (2 ** max(0, self.instruments_fail_streak - 1)), 600.0)
        if self.instruments_fail_ts and time.time() - self.instruments_fail_ts < cooldown:
            wait_left = cooldown - (time.time() - self.instruments_fail_ts)
            print(f"[DataFeeder] Blofin instrumenty: cooldown po błędzie (próba {self.instruments_fail_streak}), ponowię za {wait_left:.0f}s")
            return self.instruments_cache

        data = self.blofin._get("market/instruments")
        if not data:
            self.instruments_fail_ts = time.time()
            self.instruments_fail_streak += 1
            return self.instruments_cache

        result = []
        for x in data.get("data", []):
            if x.get("state") != "live":
                continue
            if x.get("quoteCurrency") != "USDT":
                continue
            if x.get("instType") not in ("SWAP", "FUTURES", None, ""):
                # akceptuj SWAP / linear
                if x.get("contractType") != "linear":
                    continue
            base = (x.get("baseCurrency") or "").upper()
            if not base or base in STABLECOINS:
                continue
            result.append({
                "instId": x.get("instId"),
                "symbol": base,
                "max_leverage": x.get("maxLeverage"),
                "min_size": x.get("minSize"),
                "tick_size": x.get("tickSize"),
                "contract_value": x.get("contractValue"),
            })

        if result:
            self.instruments_cache = result
            self.instruments_ts = time.time()
            self.instruments_fail_ts = 0.0
            self.instruments_fail_streak = 0
            disk_cache.save("blofin_instruments", result)
            print(f"[DataFeeder] Blofin USDT futures: {len(result)} par")
        else:
            self.instruments_fail_ts = time.time()
            self.instruments_fail_streak += 1
        return self.instruments_cache

    def _refresh_coingecko_top(self) -> Dict[str, Dict]:
        """CG top ~250 do cross-check (symbol -> dane). Cache 2 min."""
        if time.time() - self.cg_map_ts < 120 and self.cg_map_cache:
            return self.cg_map_cache
        # Ten sam wzorzec co dla instrumentow Blofin: po porazce nie mloc API
        # co cykl (co 2 min i tak juz rzadko, ale CoinGecko jest historycznie
        # najbardziej rate-limitowanym zrodlem w tym projekcie) - eskalujacy
        # backoff zamiast wiecznego probowania na tym samym, stalym rytmie.
        cooldown = min(120.0 * (2 ** max(0, self.cg_map_fail_streak - 1)), 1800.0)
        if self.cg_map_fail_ts and time.time() - self.cg_map_fail_ts < cooldown:
            return self.cg_map_cache

        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "1h,24h,7d,30d"
        }
        data = self._get_coingecko("coins/markets", params)
        if not data:
            self.cg_map_fail_ts = time.time()
            self.cg_map_fail_streak += 1
            return self.cg_map_cache

        mapping = {}
        for c in data:
            sym = (c.get("symbol") or "").upper()
            if not sym or sym in STABLECOINS:
                continue
            # przy duplikatach symbolu bierz pierwszy (wyzszy mcap)
            if sym in mapping:
                continue
            mapping[sym] = {
                "id": c["id"],
                "name": c.get("name", sym),
                "price": c.get("current_price"),
                "market_cap": c.get("market_cap") or 0,
                "volume_24h": c.get("total_volume") or 0,
                "change_1h": c.get("price_change_percentage_1h_in_currency") or 0,
                "change_24h": c.get("price_change_percentage_24h") or 0,
                "change_7d": c.get("price_change_percentage_7d_in_currency") or 0,
                "change_30d": c.get("price_change_percentage_30d_in_currency") or 0,
                "high_24h": c.get("high_24h"),
                "low_24h": c.get("low_24h"),
            }
        if mapping:
            self.cg_map_cache = mapping
            self.cg_map_ts = time.time()
            self.cg_map_fail_ts = 0.0
            self.cg_map_fail_streak = 0
        else:
            self.cg_map_fail_ts = time.time()
            self.cg_map_fail_streak += 1
        return self.cg_map_cache

    def fetch_top_coins(self) -> List[Dict]:
        """
        Universe = wszystkie Blofin Futures USDT.
        Wzbogacenie danymi Binance / Bybit / CoinGecko gdzie dostepne.
        """
        self.last_errors = []
        instruments = self.fetch_blofin_usdt_instruments()
        if not instruments:
            print("[DataFeeder] Brak instrumentow Blofin – cache")
            return self.cache

        # Tickery ze wszystkich zrodel
        try:
            bf_tickers = self.blofin.fetch_all_tickers()
        except Exception as e:
            bf_tickers = {}
            self.last_errors.append(f"BF:{str(e)[:30]}")

        try:
            bn_tickers = self.binance.fetch_all_tickers()
        except Exception as e:
            bn_tickers = {}
            self.last_errors.append(f"BN:{str(e)[:30]}")

        # WS Binance dla BTC/ETH/majors - potwierdzenie ceny bez dodatkowych
        # zapytan REST (patrz binance_ws.py). Start jest idempotentny (no-op
        # jesli juz polaczony); brak websocket-client albo bledu polaczenia
        # po prostu skutkuje brakiem danych w cache, bn_tickers zostaje
        # pelnym fallbackiem jak dotychczas.
        major_symbols = list(getattr(config, "BINANCE_WS_MAJOR_SYMBOLS", []) or [])
        if major_symbols:
            try:
                binance_ws.PUBLIC_WS.start(major_symbols)
            except Exception:
                pass

        try:
            by_tickers = self.bybit.fetch_all_tickers()
        except Exception as e:
            by_tickers = {}
            self.last_errors.append(f"BY:{str(e)[:30]}")

        try:
            cg_map = self._refresh_coingecko_top()
        except Exception as e:
            cg_map = {}
            self.last_errors.append(f"CG:{str(e)[:30]}")

        coins = []
        diag_no_blofin_ticker = 0      # instrument jest "live" na liście, ale bez tickera z Blofin
        diag_rescued_by_fallback = 0   # brak tickera Blofin, ale cena znaleziona w BN/Bybit/CG
        diag_dropped_no_price = 0      # zaden z 4 zrodel nie dal ceny -> instrument znika calkowicie
        for inst in instruments:
            sym = inst["symbol"]
            bf = bf_tickers.get(sym) or {}
            bn = bn_tickers.get(sym) or {}
            by = by_tickers.get(sym) or {}
            cg = cg_map.get(sym) or {}
            if major_symbols and sym in major_symbols:
                # Dla majors: swiezsze potwierdzenie z WS (jesli polaczony i
                # ma dane <5s) nadpisuje wolniejszy bulk REST Binance.
                ws_price = binance_ws.PUBLIC_WS.get_price(sym, max_age_s=5.0)
                if ws_price is not None:
                    bn = {**bn, "binance_price": ws_price}

            if not bf.get("blofin_price"):
                diag_no_blofin_ticker += 1

            # cena priorytet: Blofin > Binance > Bybit > CG
            price = (
                bf.get("blofin_price")
                or bn.get("binance_price")
                or by.get("bybit_price")
                or cg.get("price")
            )
            if not price or price <= 0:
                if not bf.get("blofin_price"):
                    diag_dropped_no_price += 1
                continue
            if not bf.get("blofin_price"):
                diag_rescued_by_fallback += 1

            # Osobne pola per źródło — bez nadpisywania
            bf_ch24 = bf.get("blofin_change_24h")
            bn_ch24 = bn.get("binance_change_24h")
            by_ch24 = by.get("bybit_change_24h")
            cg_ch24 = cg.get("change_24h")
            # Trading change = BloFin primary
            change_24h = (
                bf_ch24 if bf_ch24 is not None
                else bn_ch24 if bn_ch24 is not None
                else by_ch24 if by_ch24 is not None
                else cg_ch24 if cg_ch24 is not None
                else 0
            )
            change_1h = cg.get("change_1h") or 0
            change_7d = cg.get("change_7d") or 0
            change_30d = cg.get("change_30d") or 0

            bf_vol = bf.get("blofin_volume")
            bf_base_vol = bf.get("blofin_base_volume")
            bf_quote_vol = bf.get("blofin_quote_volume") or bf_vol
            bn_vol = bn.get("binance_volume")
            by_vol = by.get("bybit_volume")
            cg_vol = cg.get("volume_24h")
            # Liquidity risk = TYLKO BloFin; brak = 0 (UNKNOWN), nie BN fallback
            volume = bf_vol if bf_vol else 0

            high = (
                bf.get("blofin_high") or bn.get("binance_high") or by.get("bybit_high")
                or cg.get("high_24h") or price
            )
            low = (
                bf.get("blofin_low") or bn.get("binance_low") or by.get("bybit_low")
                or cg.get("low_24h") or price
            )

            coin = {
                "id": cg.get("id") or sym.lower(),
                "symbol": sym,
                "name": cg.get("name") or sym,
                "instId": inst.get("instId"),
                "price": float(price),
                "price_source": (
                    "blofin" if bf.get("blofin_price") else
                    "binance" if bn.get("binance_price") else
                    "bybit" if by.get("bybit_price") else "coingecko"
                ),
                "market_cap": cg.get("market_cap") or 0,
                "volume_24h": float(volume) if volume else 0,
                "blofin_volume_24h": float(bf_vol) if bf_vol else 0,
                "blofin_base_volume_24h": float(bf_base_vol) if bf_base_vol else 0,
                "blofin_quote_volume_24h": float(bf_quote_vol) if bf_quote_vol else 0,
                "binance_volume_24h": float(bn_vol) if bn_vol else 0,
                "bybit_volume_24h": float(by_vol) if by_vol else 0,
                "coingecko_volume_24h": float(cg_vol) if cg_vol else 0,
                "change_1h": float(change_1h) if change_1h else 0,
                "change_24h": float(change_24h) if change_24h else 0,
                "blofin_change_24h": float(bf_ch24) if bf_ch24 is not None else None,
                "binance_change_24h": float(bn_ch24) if bn_ch24 is not None else None,
                "bybit_change_24h": float(by_ch24) if by_ch24 is not None else None,
                "coingecko_change_24h": float(cg_ch24) if cg_ch24 is not None else None,
                "change_7d": float(change_7d) if change_7d else 0,
                "change_30d": float(change_30d) if change_30d else 0,
                "high_24h": float(high) if high else float(price),
                "low_24h": float(low) if low else float(price),
                "price_diff_pct": 0,
                "max_leverage": inst.get("max_leverage"),
                "blofin_only": not bool(bn or by or cg),
                "sources_count": sum(1 for x in (bf, bn, by, cg) if x),
            }
            # doklej raw ze zrodel (korelacja)
            if bf:
                coin.update(bf)
            if bn:
                coin.update(bn)
            if by:
                coin.update(by)
            if cg.get("price"):
                # zachowaj CG price osobno jesli jest – analyze uzywa coin['price'] jako glownej
                # ale correlation czyta price jako CG – ustawmy flagi
                coin["coingecko_price"] = cg["price"]
                # dla correlation.py uzywa coin.get("price") jako CG – 
                # nadpiszemy w correlation albo dodamy alias
            coins.append(coin)

            if sym == "BTC":
                self.btc_price = coin["price"]

        # Opcjonalny filtr volume – tylko gdy mamy volume z zewnetrznego zrodla
        # NIE filtrujemy par tylko-blofin (moga nie miec volume z BN/CG)
        min_vol = getattr(config, "MIN_VOLUME_24H_USD", 0)
        if min_vol and min_vol > 0:
            # nie odrzucaj gdy volume=0 (brak danych), tylko gdy volume jest i jest za niski
            filtered = []
            for c in coins:
                v = c.get("volume_24h") or 0
                if v == 0 or v >= min_vol:
                    filtered.append(c)
            coins = filtered

        # Trend dla kazdej monety + kontekst rynku
        try:
            self.last_market_context = self.market_ctx.fetch_all()
        except Exception as e:
            self.last_errors.append(f"MKT:{str(e)[:30]}")
            self.last_market_context = self.last_market_context or {}

        for coin in coins:
            try:
                self.market_ctx.enrich_coin(coin, fetch_categories=False)
            except Exception:
                coin.setdefault("trend", "SIDEWAYS")
                coin.setdefault("trend_score", 0)

        self.cache = coins
        self.last_fetch = time.time()
        self.last_successful_fetch = time.time()
        try:
            from market_data import STALE, normalize_symbol
            STALE.touch("ticker:universe")
            if any(c.get("binance_price") for c in coins):
                STALE.touch("ticker:binance")
            if any(c.get("blofin_price") or c.get("price") for c in coins):
                STALE.touch("ticker:blofin")
            # normalizacja symboli
            for c in coins:
                c["symbol"] = normalize_symbol(c.get("symbol") or "")
        except Exception:
            pass
        print(
            f"[DataFeeder] uniwersum: {len(instruments)} instrumentow Blofin -> "
            f"{diag_no_blofin_ticker} bez tickera Blofin ({diag_rescued_by_fallback} uratowanych "
            f"przez BN/Bybit/CG, {diag_dropped_no_price} odrzuconych bo zaden z 4 zrodel nie dal ceny) "
            f"-> {len(coins)} finalnie w uniwersum"
        )
        return coins

    def fetch_ohlc_closes(self, coin_id: str, days: int = 1) -> List[float]:
        cache_key = f"{coin_id}_{days}"
        if cache_key in self.ohlc_cache:
            ts, closes = self.ohlc_cache[cache_key]
            if time.time() - ts < 120:
                return closes
        params = {"vs_currency": "usd", "days": days}
        data = self._get_coingecko(f"coins/{coin_id}/ohlc", params)
        if not data or not isinstance(data, list):
            return []
        closes = [candle[4] for candle in data if len(candle) >= 5]
        self.ohlc_cache[cache_key] = (time.time(), closes)
        return closes

    def get_btc_change(self, coins: List[Dict]) -> float:
        for c in coins:
            if c["symbol"] == "BTC":
                return c.get("change_24h") or 0
        return 0.0

    def is_data_stale(self) -> bool:
        if self.last_successful_fetch == 0:
            return True
        if (time.time() - self.last_successful_fetch) > config.STALE_DATA_SECONDS:
            return True
        try:
            from market_data import STALE
            ok, _ = STALE.check_trade_allowed(
                require_keys=["ticker:universe"],
                max_age=float(config.STALE_DATA_SECONDS),
            )
            return not ok
        except Exception:
            return False

    def data_age_seconds(self) -> float:
        if self.last_successful_fetch == 0:
            return 9999
        age = time.time() - self.last_successful_fetch
        try:
            from market_data import STALE
            age = max(age, STALE.age("ticker:universe"))
        except Exception:
            pass
        return age

    def sources_status(self) -> str:
        parts = []
        parts.append(f"Universe:BlofinUSDT({len(self.cache)})")
        parts.append(self.binance.status())
        parts.append(self.bybit.status())
        parts.append(self.blofin.status())
        if self.cg_map_cache:
            parts.append(f"CG:OK({len(self.cg_map_cache)})")
        else:
            parts.append("CG:empty")
        # 21.08.2026: bez tego stan Public WS byl calkowicie niewidoczny -
        # brak pakietu websocket-client / brak polaczenia degraduje V2 do
        # limitu top-N kandydatow (DAYTRADING_V2_MAX_CANDIDATES) zamiast
        # pelnego uniwersum, a w logach nie bylo o tym ani jednej linii
        # (zero "[BlofinWS]" przez cala sesje) - wygladalo jak cichy,
        # trudny do zdiagnozowania spadek liczby kandydatow.
        try:
            from blofin_ws import PUBLIC_WS
            if not PUBLIC_WS.available:
                parts.append("WS:brak pakietu websocket-client")
            elif PUBLIC_WS.is_connected():
                parts.append("WS:OK")
            else:
                parts.append("WS:rozlaczony")
        except Exception:
            parts.append("WS:?")
        if self.last_errors:
            parts.append("errs:" + ",".join(self.last_errors[-3:]))
        return " | ".join(parts)

    def get_market_context(self) -> Dict:
        if self.last_market_context:
            return self.last_market_context
        try:
            self.last_market_context = self.market_ctx.fetch_all()
        except Exception:
            pass
        return self.last_market_context or {}
