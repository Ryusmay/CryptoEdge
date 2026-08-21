# ============================================================
# Kontekst rynku: Fear&Greed, dominacje, mcap, kategorie, trend
# ============================================================

import requests
import time
from typing import Dict, List, Optional, Any

CG_BASE = "https://api.coingecko.com/api/v3"
FNG_URL = "https://api.alternative.me/fng/"

SECTOR_RULES = [
    ("L1", ["layer 1", "layer-1", "smart contract platform", "proof of work", "proof of stake"]),
    ("L2", ["layer 2", "layer-2", "rollups", "optimistic", "zero knowledge", "zk ", "scaling"]),
    ("DeFi", ["decentralized finance", "defi", "dex", "lending", "yield", "amm", "liquid staking"]),
    ("Gaming", ["gaming", "play to earn", "gamefi", "metaverse", "nft"]),
    ("Privacy", ["privacy", "mixer", "anonymous"]),
    ("Meme", ["meme", "dog-themed", "frog"]),
    ("AI", ["artificial intelligence", "ai ", "ai-"]),
    ("RWA", ["real world assets", "rwa", "tokenization"]),
    ("Infrastructure", ["infrastructure", "oracle", "data availability", "interoperability", "bridge", "storage"]),
    ("Payments", ["payments", "remittance"]),
    ("Exchange", ["centralized exchange", "exchange-based", "cex"]),
    ("Depin", ["depin", "iot", "wireless", "compute"]),
]


def normalize_sectors(categories: list) -> list:
    if not categories:
        return []
    joined = " | ".join(str(c).lower() for c in categories)
    tags = []
    for tag, keys in SECTOR_RULES:
        if any(k in joined for k in keys):
            tags.append(tag)
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out



class MarketContext:
    def __init__(self, cg_key: str = ""):
        self.cg_key = cg_key
        self.session = requests.Session()
        self.global_cache: Dict = {}
        self.global_ts = 0
        self.fng_cache: Dict = {}
        self.fng_ts = 0
        self.coin_meta_cache: Dict[str, Dict] = {}  # id -> categories etc
        self.last_error = None

    def _cg_params(self, params: dict = None) -> dict:
        p = dict(params or {})
        if self.cg_key:
            p["x_cg_demo_api_key"] = self.cg_key
        return p

    def _get(self, url: str, params: dict = None, timeout: int = 12) -> Optional[Any]:
        try:
            r = self.session.get(url, params=params or {}, timeout=timeout)
            if r.status_code == 429:
                time.sleep(20)
                r = self.session.get(url, params=params or {}, timeout=timeout)
            if r.status_code != 200:
                self.last_error = f"HTTP {r.status_code}"
                return None
            return r.json()
        except Exception as e:
            self.last_error = str(e)[:80]
            return None

    def fetch_fear_greed(self) -> Dict:
        if time.time() - self.fng_ts < 300 and self.fng_cache:
            return self.fng_cache
        data = self._get(FNG_URL, {"limit": 1})
        if not data or not data.get("data"):
            return self.fng_cache or {}
        row = data["data"][0]
        result = {
            "value": int(row.get("value", 0)),
            "label": row.get("value_classification", ""),
            "timestamp": row.get("timestamp"),
        }
        self.fng_cache = result
        self.fng_ts = time.time()
        return result

    def fetch_global(self) -> Dict:
        if time.time() - self.global_ts < 120 and self.global_cache:
            return self.global_cache
        data = self._get(f"{CG_BASE}/global", self._cg_params())
        if not data or "data" not in data:
            return self.global_cache or {}
        g = data["data"]
        mcap = g.get("total_market_cap") or {}
        vol = g.get("total_volume") or {}
        dom = g.get("market_cap_percentage") or {}

        total_usd = float(mcap.get("usd") or 0)
        btc_dom = float(dom.get("btc") or 0)
        eth_dom = float(dom.get("eth") or 0)
        usdt_dom = float(dom.get("usdt") or 0)
        # Alt dominance ≈ 100 - btc (uproszczenie rynkowe)
        alt_dom = max(0.0, 100.0 - btc_dom)
        # Alt market cap ≈ total * (1 - btc_dom/100)
        alt_mcap = total_usd * (1 - btc_dom / 100.0) if total_usd else 0

        result = {
            "total_market_cap_usd": total_usd,
            "total_volume_usd": float(vol.get("usd") or 0),
            "btc_dominance": round(btc_dom, 2),
            "eth_dominance": round(eth_dom, 2),
            "usdt_dominance": round(usdt_dom, 2),
            "altcoin_dominance": round(alt_dom, 2),
            "altcoin_market_cap_usd": alt_mcap,
            "market_cap_change_24h_pct": float(g.get("market_cap_change_percentage_24h_usd") or 0),
            "active_cryptocurrencies": g.get("active_cryptocurrencies"),
        }
        self.global_cache = result
        self.global_ts = time.time()
        return result

    def fetch_all(self) -> Dict:
        fng = self.fetch_fear_greed()
        glob = self.fetch_global()
        return {"fear_greed": fng, "global": glob}

    def get_coin_categories(self, coin_id: str) -> List[str]:
        return self.get_coin_meta(coin_id).get("categories") or []

    def get_coin_sectors(self, coin_id: str) -> List[str]:
        return self.get_coin_meta(coin_id).get("sectors") or []

    def get_coin_meta(self, coin_id: str) -> Dict:
        if not coin_id:
            return {"categories": [], "sectors": []}
        if coin_id in self.coin_meta_cache:
            entry = self.coin_meta_cache[coin_id]
            if time.time() - entry.get("ts", 0) < 3600:
                return entry

        data = self._get(
            f"{CG_BASE}/coins/{coin_id}",
            self._cg_params({
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
            }),
        )
        cats = []
        if data:
            cats = [c for c in (data.get("categories") or []) if c]
        sectors = normalize_sectors(cats)
        entry = {"categories": cats, "sectors": sectors, "ts": time.time()}
        self.coin_meta_cache[coin_id] = entry
        return entry

    @staticmethod
    def classify_trend(coin: Dict) -> Dict:
        """
        Trend monety na bazie zmian 1h/24h/7d/30d.
        """
        c1 = float(coin.get("change_1h") or 0)
        c24 = float(coin.get("change_24h") or 0)
        c7 = float(coin.get("change_7d") or 0)
        c30 = float(coin.get("change_30d") or 0)

        score = 0
        if c1 > 1: score += 1
        elif c1 < -1: score -= 1
        if c24 > 3: score += 2
        elif c24 < -3: score -= 2
        if c7 > 8: score += 2
        elif c7 < -8: score -= 2
        if c30 > 15: score += 1
        elif c30 < -15: score -= 1

        if score >= 4:
            label = "STRONG_UP"
        elif score >= 2:
            label = "UP"
        elif score <= -4:
            label = "STRONG_DOWN"
        elif score <= -2:
            label = "DOWN"
        else:
            label = "SIDEWAYS"

        return {
            "trend": label,
            "trend_score": score,
            "change_1h": round(c1, 2),
            "change_24h": round(c24, 2),
            "change_7d": round(c7, 2),
            "change_30d": round(c30, 2),
        }

    def enrich_coin(self, coin: Dict, fetch_categories: bool = False) -> Dict:
        trend = self.classify_trend(coin)
        coin["trend"] = trend["trend"]
        coin["trend_score"] = trend["trend_score"]
        coin.setdefault("categories", [])
        coin.setdefault("sectors", [])
        if fetch_categories:
            cid = coin.get("id")
            sym = (coin.get("symbol") or "").lower()
            if cid and cid != sym:
                meta = self.get_coin_meta(cid)
                coin["categories"] = (meta.get("categories") or [])[:8]
                coin["sectors"] = meta.get("sectors") or []
        return coin


    def status_line(self, ctx: Dict = None) -> str:
        ctx = ctx or self.fetch_all()
        fng = ctx.get("fear_greed") or {}
        g = ctx.get("global") or {}
        def mcap_fmt(v):
            if not v:
                return "—"
            if v >= 1e12:
                return f"${v/1e12:.2f}T"
            if v >= 1e9:
                return f"${v/1e9:.1f}B"
            return f"${v/1e6:.0f}M"
        return (
            f"F&G {fng.get('value', '—')} ({fng.get('label', '—')}) | "
            f"BTC.D {g.get('btc_dominance', '—')}% | "
            f"ALT.D {g.get('altcoin_dominance', '—')}% | "
            f"USDT.D {g.get('usdt_dominance', '—')}% | "
            f"MCap {mcap_fmt(g.get('total_market_cap_usd'))} | "
            f"AltMCap {mcap_fmt(g.get('altcoin_market_cap_usd'))}"
        )
