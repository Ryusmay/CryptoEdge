# ============================================================
# Automatyczna konwersja Blofin symbol → TradingView
# ============================================================

from __future__ import annotations

import json
import time
import webbrowser
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from urllib.parse import quote

BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "logs" / "tv_symbol_map.json"

# Ręczne nadpisania (Blofin ticker → preferowany TV symbol)
MANUAL_MAP = {
    "PEPE": "BINANCE:1000PEPEUSDT",
    "SHIB": "BINANCE:1000SHIBUSDT",
    "BONK": "BINANCE:1000BONKUSDT",
    "FLOKI": "BINANCE:1000FLOKIUSDT",
    "SATS": "BINANCE:1000SATSUSDT",
    "LUNC": "BINANCE:1000LUNCUSDT",
    "XEC": "BINANCE:1000XECUSDT",
    "RATS": "BINANCE:1000RATSUSDT",
    "CAT": "BINANCE:1000CATUSDT",
    "WHY": "BINANCE:1000WHYUSDT",
    "POL": "BINANCE:POLUSDT",  # ex-MATIC
    "MATIC": "BINANCE:POLUSDT",
}


def _load_cache() -> Dict:
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cache(data: Dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def normalize_base(symbol: str) -> str:
    sym = (symbol or "").upper().replace("-", "").replace("/", "").replace("_", "")
    for suf in ("USDT", "USD", "PERP", "SWAP"):
        if sym.endswith(suf) and len(sym) > len(suf):
            sym = sym[: -len(suf)]
    return sym


def _bases_from_pair(pair: str) -> str:
    p = (pair or "").upper().replace("-", "").replace("/", "")
    if p.endswith("USDT"):
        return p[:-4]
    return p


def collect_exchange_bases(feeder=None) -> Dict[str, set]:
    """
    Zwraca { 'binance': {BTC, ETH, ...}, 'bybit': {...}, 'blofin': {...} }
    z aktualnych cache tickerów.
    """
    out = {"binance": set(), "bybit": set(), "blofin": set()}
    if feeder is None:
        try:
            from runtime import BotRuntime
            feeder = BotRuntime.get().feeder
        except Exception:
            feeder = None
    if not feeder:
        return out
    try:
        bn = getattr(feeder, "binance", None)
        if bn and getattr(bn, "ticker_cache", None):
            for k in bn.ticker_cache.keys():
                out["binance"].add(_bases_from_pair(str(k)))
        if bn and getattr(bn, "_valid_symbols", None):
            for k in bn._valid_symbols:
                out["binance"].add(_bases_from_pair(str(k)))
    except Exception:
        pass
    try:
        by = getattr(feeder, "bybit", None)
        if by and getattr(by, "ticker_cache", None):
            for k in by.ticker_cache.keys():
                out["bybit"].add(_bases_from_pair(str(k)))
    except Exception:
        pass
    try:
        bf = getattr(feeder, "blofin", None)
        if bf and getattr(bf, "ticker_cache", None):
            for k in bf.ticker_cache.keys():
                out["blofin"].add(_bases_from_pair(str(k)))
    except Exception:
        pass
    return out


def resolve_tv_symbol(symbol: str, feeder=None) -> Tuple[str, str]:
    """
    Automatyczna konwersja.
    Returns: (tv_symbol, reason)
    """
    base = normalize_base(symbol)
    if not base:
        return "BINANCE:BTCUSDT", "fallback_btc"

    cache = _load_cache()
    if base in cache and cache[base].get("tv"):
        return cache[base]["tv"], cache[base].get("reason", "cache")

    if base in MANUAL_MAP:
        tv = MANUAL_MAP[base]
        cache[base] = {"tv": tv, "reason": "manual", "ts": time.time()}
        _save_cache(cache)
        return tv, "manual"

    ex = collect_exchange_bases(feeder)

    # warianty nazwy (1000PEPE vs PEPE)
    variants = [base]
    if base.startswith("1000") and len(base) > 4:
        variants.append(base[4:])
    else:
        variants.append("1000" + base)
    if base.startswith("1000000") and len(base) > 7:
        variants.append(base[7:])

    # 1) Binance spot
    for v in variants:
        if v in ex["binance"] or f"{v}USDT" in {x + "USDT" for x in ex["binance"]}:
            # check exact in ticker keys if possible
            tv = f"BINANCE:{v}USDT"
            cache[base] = {"tv": tv, "reason": f"binance:{v}", "ts": time.time()}
            _save_cache(cache)
            return tv, f"binance:{v}"

    # 2) Bybit linear
    for v in variants:
        if v in ex["bybit"]:
            tv = f"BYBIT:{v}USDT.P"
            cache[base] = {"tv": tv, "reason": f"bybit:{v}", "ts": time.time()}
            _save_cache(cache)
            return tv, f"bybit:{v}"

    # 3) Heurystyka bez cache giełd
    for v in variants:
        # typowe 1000* na Binance futures/spot
        if v.startswith("1000"):
            tv = f"BINANCE:{v}USDT"
            cache[base] = {"tv": tv, "reason": "heuristic_1000", "ts": time.time()}
            _save_cache(cache)
            return tv, "heuristic_1000"

    # 4) Domyślnie Binance + lista kandydatów
    tv = f"BINANCE:{base}USDT"
    cache[base] = {"tv": tv, "reason": "default_binance", "ts": time.time()}
    _save_cache(cache)
    return tv, "default_binance"


def candidate_tv_symbols(symbol: str, feeder=None) -> List[str]:
    primary, reason = resolve_tv_symbol(symbol, feeder=feeder)
    base = normalize_base(symbol)
    variants = [base]
    if not base.startswith("1000"):
        variants.append("1000" + base)
    elif len(base) > 4:
        variants.append(base[4:])

    cands = [primary]
    for v in variants:
        cands.extend([
            f"BINANCE:{v}USDT",
            f"BYBIT:{v}USDT.P",
            f"OKX:{v}USDT.P",
            f"BINANCE:{v}USDT.P",
            f"BYBIT:{v}USDT",
            f"{v}USDT",
        ])
    # dedupe keep order
    seen = set()
    out = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def tv_symbol(symbol: str, exchange: str = "AUTO") -> str:
    if (exchange or "AUTO").upper() != "AUTO":
        base = normalize_base(symbol)
        ex = exchange.upper()
        if ex == "BYBIT":
            return f"BYBIT:{base}USDT.P"
        if ex == "OKX":
            return f"OKX:{base}USDT.P"
        return f"BINANCE:{base}USDT"
    return resolve_tv_symbol(symbol)[0]


def chart_url(symbol: str, interval: str = "240", exchange: str = "AUTO") -> str:
    if (exchange or "AUTO").upper() == "AUTO":
        s, _ = resolve_tv_symbol(symbol)
    else:
        s = tv_symbol(symbol, exchange=exchange)
    return f"https://www.tradingview.com/chart/?symbol={quote(s)}&interval={interval}"


def search_url(symbol: str) -> str:
    q = normalize_base(symbol) + " USDT"
    return f"https://www.tradingview.com/symbols/search/?text={quote(q)}"


def widget_embed_html(symbol: str, interval: str = "240", height: int = 560) -> str:
    primary, reason = resolve_tv_symbol(symbol)
    cands = candidate_tv_symbols(symbol)
    options = "\n".join(
        f'<option value="{c}"{" selected" if c == primary else ""}>{c}</option>'
        for c in cands
    )
    base = normalize_base(symbol)
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>TV {base}</title>
  <style>
    html,body {{ margin:0; padding:0; background:#0b1220; color:#e8eef7; font-family:Segoe UI,sans-serif; height:100%; }}
    .bar {{ display:flex; gap:8px; align-items:center; padding:8px 12px; background:#121a27; border-bottom:1px solid #2a3548; flex-wrap:wrap; }}
    select,button,a {{ background:#182233; color:#e8eef7; border:1px solid #2a3548; border-radius:6px; padding:6px 10px; font-size:13px; cursor:pointer; text-decoration:none; }}
    button:hover,a:hover {{ border-color:#38bdf8; color:#38bdf8; }}
    #tv {{ height:calc(100% - 52px); width:100%; }}
    .hint {{ color:#8b9bb4; font-size:12px; }}
    .ok {{ color:#22c55e; }}
  </style>
</head>
<body>
  <div class="bar">
    <strong>{base}</strong>
    <span class="hint ok">auto: {primary} ({reason})</span>
    <label class="hint">Symbol:</label>
    <select id="sym">{options}</select>
    <label class="hint">TF:</label>
    <select id="tf">
      <option value="15">15m</option>
      <option value="60">1H</option>
      <option value="240" selected>4H</option>
      <option value="D">1D</option>
    </select>
    <button onclick="reloadWidget()">Załaduj</button>
    <a id="openFull" href="#" target="_blank">Pełny TV</a>
    <a href="{search_url(symbol)}" target="_blank">Szukaj</a>
  </div>
  <div id="tv"></div>
  <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
  <script>
    function reloadWidget() {{
      var s = document.getElementById('sym').value;
      var tf = document.getElementById('tf').value;
      document.getElementById('openFull').href =
        'https://www.tradingview.com/chart/?symbol=' + encodeURIComponent(s) + '&interval=' + tf;
      document.getElementById('tv').innerHTML = '';
      new TradingView.widget({{
        "container_id": "tv",
        "symbol": s,
        "interval": tf,
        "timezone": "Europe/Warsaw",
        "theme": "dark",
        "style": "1",
        "locale": "pl",
        "toolbar_bg": "#0b1220",
        "enable_publishing": false,
        "hide_side_toolbar": false,
        "allow_symbol_change": true,
        "studies": ["RSI@tv-basicstudies", "MACD@tv-basicstudies"],
        "autosize": true
      }});
    }}
    reloadWidget();
  </script>
</body>
</html>
"""


def open_chart(symbol: str, interval: str = "240", exchange: str = "AUTO") -> Optional[str]:
    """Otwiera bezpośrednio tradingview.com z zresolvowanym symbolem."""
    if not symbol:
        return None
    base = normalize_base(symbol)
    tv, reason = resolve_tv_symbol(symbol)
    print(f"[TV] {base} → {tv} ({reason})")
    url = f"https://www.tradingview.com/chart/?symbol={quote(tv)}&interval={interval}"
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[TV] open error: {e}")
    return url
