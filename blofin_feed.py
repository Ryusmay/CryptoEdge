# ============================================================
# Blofin Public Market Data (bez klucza API)
# ============================================================

import requests
import time
import hmac
import hashlib
import base64
import uuid
import socket
import ssl
from typing import Dict, List, Optional
import config
import disk_cache
from rate_limiter import PUBLIC_BUCKET, TRADING_BUCKET
from blofin_ws import PUBLIC_WS

from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.poolmanager import PoolManager
from urllib3.util.retry import Retry

BLOFIN_BASE = "https://openapi.blofin.com/api/v1"
BLOFIN_HOST = "openapi.blofin.com"
BLOFIN_ORIGIN = "https://blofin.com"

# Chrome UA: Cloudflare WAF 403 na "python-requests" / własnym UA.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_ALT_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
)
# connect krótki (IPv6 blackhole na Windows), read dłuższy (duża lista instrumentów)
_CONNECT_TIMEOUT_S = 4.0
_READ_TIMEOUT_S = 20.0
_DEFAULT_TIMEOUT = (_CONNECT_TIMEOUT_S, _READ_TIMEOUT_S)

try:
    import certifi
    _CERTIFI_CA = certifi.where()
except Exception:
    _CERTIFI_CA = None


def _ssl_context():
    """Systemowy magazyn CA (Windows + AV HTTPS-scan) PLUS certifi."""
    ctx = ssl.create_default_context()
    if _CERTIFI_CA:
        try:
            ctx.load_verify_locations(cafile=_CERTIFI_CA)
        except Exception:
            pass
    return ctx


def _waf_headers(ua: str = None) -> dict:
    """Nagłówki jak z przeglądarki — Cloudflare WAF na openapi.blofin.com."""
    return {
        "User-Agent": ua or _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Origin": BLOFIN_ORIGIN,
        "Referer": BLOFIN_ORIGIN + "/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def _cfg_ipv4_only() -> bool:
    return bool(getattr(config, "BLOFIN_IPV4_ONLY", True))


def _cfg_waf_headers() -> bool:
    return bool(getattr(config, "BLOFIN_WAF_BROWSER_HEADERS", True))


def _connect_timeout_sec(timeout) -> Optional[float]:
    if timeout is None:
        return _CONNECT_TIMEOUT_S
    if hasattr(timeout, "connect_timeout"):
        ct = timeout.connect_timeout
        try:
            return float(ct)
        except (TypeError, ValueError):
            return _CONNECT_TIMEOUT_S
    if isinstance(timeout, (int, float)):
        return float(timeout)
    return _CONNECT_TIMEOUT_S


def _connect_family(address, timeout, source_address, socket_options, family):
    """getaddrinfo + connect z wymuszoną rodziną (AF_INET = zero AAAA)."""
    host, port = address
    if str(host).startswith("["):
        host = str(host).strip("[]")
    err = None
    for res in socket.getaddrinfo(host, port, family, socket.SOCK_STREAM):
        af, socktype, proto, canonname, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            if socket_options:
                for opt in socket_options:
                    sock.setsockopt(*opt)
            to = _connect_timeout_sec(timeout)
            if to is not None:
                sock.settimeout(to)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock
        except OSError as e:
            err = e
            if sock is not None:
                sock.close()
    if err is not None:
        raise err
    raise OSError(f"getaddrinfo empty for {host}:{port} family={family}")


class _BlofinHTTPSConnection(HTTPSConnection):
    """HTTPS z DNS+connect tylko IPv4 (albo dual-stack gdy ipv4_only=False).

    urllib3 2.x: allowed_gai_family() = AF_UNSPEC gdy HAS_IPV6. Na Windows
    AAAA idzie pierwszy; blackhole zjada connect timeout zanim poleci A.
    `source_address=('0.0.0.0',0)` tego NIE naprawia — bind nie pomija AAAA.
    """
    ipv4_only = True

    def _new_conn(self):
        family = socket.AF_INET if getattr(self, "ipv4_only", True) else socket.AF_UNSPEC
        try:
            return _connect_family(
                (self._dns_host, self.port),
                self.timeout,
                self.source_address,
                self.socket_options,
                family,
            )
        except socket.timeout as e:
            raise OSError(
                f"Connection to {self.host} timed out (connect timeout={self.timeout})"
            ) from e
        except OSError:
            raise


class _BlofinAdapter(HTTPAdapter):
    """IPv4-first (prawdziwy AF_INET) + system SSL."""

    def __init__(self, ipv4_only: bool = True, ssl_context=None, **kwargs):
        self._ipv4_only = bool(ipv4_only)
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        if self._ssl_context is not None:
            pool_kwargs["ssl_context"] = self._ssl_context
        pool_kwargs.setdefault(
            "socket_options",
            [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        )
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize, block=block, **pool_kwargs
        )
        conn_cls = type(
            "_CE_HTTPSConn",
            (_BlofinHTTPSConnection,),
            {"ipv4_only": self._ipv4_only},
        )
        pool_cls = type(
            "_CE_HTTPSPool",
            (HTTPSConnectionPool,),
            {"ConnectionCls": conn_cls},
        )
        # pool_classes_by_scheme jest współdzieloną mapą modułu — kopia.
        self.poolmanager.pool_classes_by_scheme = dict(
            self.poolmanager.pool_classes_by_scheme
        )
        self.poolmanager.pool_classes_by_scheme["https"] = pool_cls


def _new_retry() -> Retry:
    # 429 obsługujemy sami (Retry-After / cooldown). Tu tylko dziury sieci.
    kwargs = dict(
        total=1,
        connect=1,
        read=0,
        backoff_factor=0.3,
        status_forcelist=(502, 503, 504),
        raise_on_status=False,
    )
    try:
        return Retry(allowed_methods=frozenset(["GET"]), **kwargs)
    except TypeError:
        try:
            return Retry(method_whitelist=frozenset(["GET"]), **kwargs)
        except TypeError:
            return Retry(total=1, connect=1, read=0)



def _mount_adapter(session: requests.Session, ipv4_only: bool = True, system_ssl: bool = True) -> None:
    ctx = _ssl_context() if system_ssl else None
    adapter = _BlofinAdapter(
        ipv4_only=ipv4_only,
        ssl_context=ctx,
        max_retries=_new_retry(),
        pool_connections=8,
        pool_maxsize=8,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)


def configure_blofin_session(
    session: requests.Session,
    ipv4_only: bool = None,
    waf_headers: bool = None,
    system_ssl: bool = True,
) -> requests.Session:
    """Wspólny transport: IPv4-only DNS + Chrome/WAF headers. Feed i executor."""
    if ipv4_only is None:
        ipv4_only = _cfg_ipv4_only()
    if waf_headers is None:
        waf_headers = _cfg_waf_headers()
    if waf_headers:
        session.headers.update(_waf_headers())
    else:
        session.headers["User-Agent"] = _BROWSER_UA
        session.headers.setdefault("Accept", "application/json")
        session.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    try:
        _mount_adapter(session, ipv4_only=bool(ipv4_only), system_ssl=system_ssl)
    except Exception as e:
        print(f"[Blofin] adapter: {e} — sesja bez IPv4/SSL-force")
    return session


def _timeout_of(timeout) -> tuple:
    if timeout is None:
        return _DEFAULT_TIMEOUT
    if isinstance(timeout, (int, float)):
        t = float(timeout)
        return (min(_CONNECT_TIMEOUT_S, t), t)
    if isinstance(timeout, (tuple, list)) and len(timeout) >= 2:
        return (float(timeout[0]), float(timeout[1]))
    return _DEFAULT_TIMEOUT


def _timeout_label(timeout) -> str:
    t = _timeout_of(timeout)
    return f"{t[0]:.0f}/{t[1]:.0f}"


# TTL cache swiec dopasowany do realnego czasu zycia bara (nie plaskie 60/120s
# dla wszystkiego - patrz komentarz przy uzyciu w fetch_klines_ohlcv).
_KLINE_CACHE_TTL_S = {
    # Dostrojone 20.08.2026 wg realnego zuzycia budzetu PUBLIC_BUCKET (token
    # bucket, 5 req/s) przy DAYTRADING_V2_MAX_CANDIDATES=30 kandydatach z
    # pelna kaskada 5 interwalow. To jest TTL "bezpieczny" - obowiazuje
    # gdy WS NIE jest polaczony (patrz _KLINE_CACHE_TTL_S_WS_CONNECTED
    # ponizej dla przypadku, gdy jest). Bez WS ten sam TTL co poprzednio -
    # jedyna zmiana ponizej dotyczy przypadku, gdy WS realnie dowozi
    # swiezosc, wiec REST moze odpoczac.
    "5m": 30,      # bar co 300s  (10x/bar)
    "15m": 90,     # bar co 900s  (10x/bar)
    "1H": 180,     # bar co 3600s (20x/bar)
    "4H": 600,     # bar co 14400s (24x/bar)
    "1D": 1800,    # bar co 86400s (48x/bar)
    "1W": 3600,    # bar co 604800s
}
# Gdy WS jest polaczony, dostarcza swiezosc samodzielnie (~1-2s od
# zamkniecia bara, patrz _merge_ws_closed_candle) - REST juz nie musi byc
# jedynym zrodlem swiezosci, tylko solidnym backbone'em/fallbackiem. To
# pozwala poluzowac TTL az do 1x czasu zycia bara - naturalny "sufit"
# bezpieczenstwa: REST resynchronizuje sie mniej wiecej raz na bar (lapiac
# subtelne bledy WS, ktorych sam znacznik czasu by nie wykryl), WS robi
# cala robote w miedzyczasie. Dalej nie ma sensu isc - ponizej tego REST
# przestalby pelnic jakakolwiek role weryfikujaca. Bezpieczne DOPIERO od
# momentu wprowadzenia wykrywania luk w _merge_ws_closed_candle (bez tego
# przerwa w WS + tak dlugi TTL zrobilaby cicha dziure w serii na caly bar).
_KLINE_CACHE_TTL_S_WS_CONNECTED = {
    "5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800,
}

# V2: timestamps = OPEN ostatniej ZAMKNIĘTEJ. Kolejna zamknięta dopiero
# po 1 barze, więc stale = 2*bar + STALE_KLINES_SECONDS (nie bar+slack —
# to wyłączało 4H na ~3h50m co cykl). Patrz klines_stale_reason.
_V2_STALE_BARS = {"15m": 900, "1H": 3600, "4H": 14400}


def _ohlcv_too_old_for_v2(data: dict, bar: str) -> bool:
    """True = serwowanie tego OHLCV daloby V2_STALE_KLINES (albo brak swiec)."""
    bar_s = _V2_STALE_BARS.get(bar)
    if bar_s is None:
        return False
    ts_list = (data or {}).get("timestamps") or []
    if not ts_list:
        return False
    try:
        t = float(ts_list[-1])
    except (TypeError, ValueError):
        return True
    if t > 1e16:
        t /= 1e9
    elif t > 1e11:
        t /= 1000.0
    slack = float(getattr(config, "STALE_KLINES_SECONDS", 600) or 600)
    return (time.time() - t) > (2 * bar_s + slack)
# Dlugie interwaly persystujemy na dysk (przetrwaja restart). 5m/1m
# faktycznie zmieniaja sie za szybko, zeby dysk cokolwiek dal po restarcie.
#
# 21.08.2026: 1H i 15m dopisane - to WLASNIE te dwa interwaly Warmup
# backfilluje na starcie (patrz warmup.py: WARMUP_CANDLES_1H=180,
# WARMUP_CANDLES_15M=120, do DAYTRADING_V2_MAX_CANDIDATES kandydatow), a
# do tej pory byly jedynymi wykluczonymi z dysku - kazdy restart bota
# odpytywal Blofin od zera po cala historie 1H/15m dla kazdego kandydata,
# mimo ze 180 barow 1H to 7,5 dnia danych, z ktorych po typowym przestoju
# (minuty-godziny) zdecydowana wiekszosc jest wciaz aktualna. To NIE
# eliminuje zapytan (TTL po restarcie i tak zwykle wygasl, wiec fetch
# leci od nowa na pelny `limit` - patrz uwaga w fetch_klines_ohlcv), ale
# Warmup od razu ma cos z dysku zamiast czekac w pustce na pierwszy live
# fetch. Prawdziwe ciecie wolumenu zapytan (doszywanie tylko delty od
# ostatniego timestampu z dysku) to osobny, wiekszy krok - do zrobienia
# pozniej.
_KLINE_DISK_PERSIST_BARS = ("1H", "15m", "4H", "1D", "1W")


def _publish_ohlcv(symbol: str, bar: str, data: dict) -> dict:
    """STORE jest gorącym buforem; ohlc_cache zostaje TTL-em REST."""
    if data and data.get("closes"):
        try:
            from market_store import STORE
            STORE.put_ohlcv(str(symbol).upper(), bar, data)
        except Exception:
            pass
    return data


def _synth_instruments_from_tickers(rows) -> list:
    """instId z tickerów → minimalny wiersz jak market/instruments (tylko USDT SWAP)."""
    out = []
    seen = set()
    for t in rows or []:
        inst = str(t.get("instId") or "").upper()
        if "-USDT" not in inst:
            continue
        base = inst.split("-")[0]
        if not base or base in seen:
            continue
        seen.add(base)
        out.append({
            "instId": f"{base}-USDT",
            "baseCurrency": base,
            "quoteCurrency": "USDT",
            "state": "live",
            "instType": "SWAP",
            "contractType": "linear",
        })
    return out


def _retry_after_seconds(headers, default: float = 12.0) -> tuple:
    """Parsuje naglowek Retry-After (RFC 7231): albo liczba sekund, albo data
    HTTP. Uzywamy dokladnego czasu od serwera zamiast zgadywac stalym sleep -
    jesli naglowek brakuje/jest niepoprawny, spadamy do `default`.
    Zwraca (sekundy, czy_odczytano_z_naglowka)."""
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return default, False
    try:
        return max(0.0, float(raw)), True
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds()), True
    except Exception:
        return default, False


def _bar_duration_ms(bar: str) -> Optional[int]:
    return {
        "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
        "1H": 3_600_000, "2H": 7_200_000, "4H": 14_400_000, "6H": 21_600_000,
        "12H": 43_200_000, "1D": 86_400_000, "1W": 604_800_000,
    }.get(bar)


def _merge_ws_closed_candle(symbol: str, bar: str, data: dict) -> tuple:
    """Nakleja ostatnia FAKTYCZNIE zamknieta swiece z WS (patrz
    BlofinPublicWebSocket.get_last_closed_candle) na koniec serii z REST/
    cache, jesli jest nowsza niz to, co juz mamy - skraca opoznienie
    wykrycia zamkniecia bara z rzedu TTL cache do ~1-2s (push WS). Nigdy
    nie dolacza wciaz-formujacej sie swiecy - tylko rzeczywiscie zamknietej
    (ta sama zasada co REST: "Never pass a known-open candle into
    indicators").

    Wykrywanie luk: jesli WS wraca po przerwie (np. krotkim rozlaczeniu) i
    przeskoczyl >1 bar, zwykle doklejenie zrobiloby CICHA dziure w serii -
    wskazniki (ATR, detekcja swingu) zobaczylyby ja jako ciagla. Zamiast
    tego zwracamy (data, gap_detected=True) - wolajacy MUSI wtedy wymusic
    prawdziwy fetch REST (zeby wypelnic luke), nie ufac samemu merge'owi.

    Zwraca (data_do_zwrocenia, gap_detected: bool)."""
    if not data.get("timestamps"):
        return data, False
    try:
        candle = PUBLIC_WS.get_last_closed_candle(symbol, bar)
    except Exception:
        return data, False
    if not candle:
        return data, False
    try:
        ws_ts = int(float(candle["ts"]))
        last_ts = int(data["timestamps"][-1])
    except (TypeError, ValueError, IndexError):
        return data, False
    if ws_ts <= last_ts:
        return data, False  # REST/cache juz ma ten bar (albo nowszy) - nic do zrobienia
    bar_ms = _bar_duration_ms(bar)
    if bar_ms and (ws_ts - last_ts) > int(bar_ms * 1.5):
        # Luka >1 bar (z marginesem 50% na jitter) - NIE doklejaj, to
        # zrobiloby cicha dziure w serii. Wolajacy musi wymusic realny fetch.
        return data, True
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in data.items()}
    out["timestamps"].append(ws_ts)
    out["opens"].append(candle["open"])
    out["highs"].append(candle["high"])
    out["lows"].append(candle["low"])
    out["closes"].append(candle["close"])
    if "volumes" in out:
        out["volumes"].append(candle.get("volume") or 0.0)
    if "quote_volumes" in out:
        out["quote_volumes"].append((candle.get("volume") or 0.0) * (candle.get("close") or 0))
    return out, False
    return out


def _parse_kline_rows(rows: list, bar: str, limit: int) -> dict:
    """Zamienia surowe wiersze Blofin (dowolna kolejnosc, moga sie
    powtarzac) na kolumnowy slownik OHLCV: dedupe po timestampie, odrzuca
    nie-domkniete swiece (flaga confirm na indeksie 8), sortuje rosnaco,
    przycina do `limit` najnowszych i na koniec odrzuca ostatnia
    potencjalnie wciaz-formujaca sie swiece przez drop_unclosed_candle.

    Wydzielone 21.08.2026 z fetch_klines_ohlcv (bylo tam inline) - ten sam
    kod obsluguje teraz zarowno pelny fetch calego okna, jak i parsowanie
    samej delty przy doszywaniu z dysku (patrz _fetch_delta_kline_rows)."""
    if not rows:
        return {}
    parsed = []
    seen = set()
    for row in rows:
        try:
            ts_v = int(float(row[0]))
        except (IndexError, ValueError, TypeError):
            continue
        if ts_v in seen:
            continue
        seen.add(ts_v)
        # Public candles include an explicit confirm flag at index 8.
        # Never pass a known-open candle into indicators.
        if len(row) > 8 and str(row[8]) == "0":
            continue
        parsed.append(row)
    parsed.sort(key=lambda r: int(float(r[0])))
    parsed = parsed[-limit:]

    opens, closes, highs, lows, volumes, quote_volumes, timestamps = [], [], [], [], [], [], []
    for row in parsed:
        try:
            item = (int(float(row[0])), float(row[1]), float(row[2]),
                    float(row[3]), float(row[4]))
            # row[5]=contracts, row[6]=base, row[7]=quote.
            # Indicators and volume profiles use base volume so a contract
            # specification change cannot silently rescale history.
            base_vol = float(row[6]) if len(row) > 6 else float(row[5]) if len(row) > 5 else 0.0
            quote_vol = float(row[7]) if len(row) > 7 else base_vol * item[4]
        except (IndexError, ValueError, TypeError):
            continue
        timestamps.append(item[0])
        opens.append(item[1])
        highs.append(item[2])
        lows.append(item[3])
        closes.append(item[4])
        volumes.append(base_vol)
        quote_volumes.append(quote_vol)
    data = {
        "opens": opens, "closes": closes, "highs": highs, "lows": lows,
        "volumes": volumes, "quote_volumes": quote_volumes,
        "timestamps": timestamps, "candles_confirmed": True,
    }
    try:
        from market_data import drop_unclosed_candle
        iv = {"1m": "1m", "5m": "5m", "15m": "15m", "1H": "1h", "4H": "4h", "1D": "1d"}.get(bar, bar)
        data = drop_unclosed_candle(data, iv)
    except Exception:
        if closes:
            data = {k: (v[:-1] if isinstance(v, list) else v) for k, v in data.items()}
    return data


def _merge_parsed_klines(old: dict, new: dict, limit: int) -> dict:
    """Laczy dwa juz sparsowane, kolumnowe slowniki swiec (stara historia -
    zwykle z dysku/pamieci - plus swieza delta) po timestampie: nowe
    wygrywaja przy kolizji (ostatnia swieca w starych danych mogla nie byc
    jeszcze w pelni domknieta w momencie zapisu). Sortuje rosnaco i
    przycina do `limit` najnowszych."""
    fields = ("opens", "highs", "lows", "closes", "volumes", "quote_volumes")
    by_ts: dict = {}
    for source in (old, new):
        ts_list = list((source or {}).get("timestamps") or [])
        cols = {f: list((source or {}).get(f) or []) for f in fields}
        for i, ts_v in enumerate(ts_list):
            by_ts[ts_v] = tuple(cols[f][i] if i < len(cols[f]) else None for f in fields)
    if not by_ts:
        return {}
    ordered = sorted(by_ts.items())[-int(limit):]
    result = {"timestamps": [t for t, _ in ordered], "candles_confirmed": True}
    for idx, f in enumerate(fields):
        result[f] = [row[idx] for _, row in ordered]
    return result


class BlofinFeed:
    def __init__(self):
        self.session = requests.Session()
        self._ipv4_only = _cfg_ipv4_only()
        self._waf = _cfg_waf_headers()
        self._system_ssl = True
        configure_blofin_session(
            self.session,
            ipv4_only=self._ipv4_only,
            waf_headers=self._waf,
            system_ssl=True,
        )
        self.ticker_cache: Dict[str, Dict] = {}
        self.ticker_cache_ts = 0
        self.ohlc_cache = {}
        self.last_error = None
        self.available = True
        self.fail_count = 0
        self._instrument_registry = None
        self._fail_log_ts = {}
        # 21.08.2026: nieblokujacy cooldown po dlugim (realnym) banie Blofin
        # za zbyt czeste odpytywanie limitu - patrz _get(). 0.0 = brak
        # aktywnego cooldownu.
        self._rate_limited_until = 0.0
        self._instruments_error = None

    def _contract_value(self, symbol: str) -> float:
        """Return base-asset value of one BloFin contract.

        BloFin book sizes are contract counts, not base-asset quantities.  The
        rest of CryptoEdge works in base quantity/USD, so the conversion has to
        happen once, at the market-data boundary.
        """
        try:
            if self._instrument_registry is None:
                from instrument_registry import InstrumentRegistry
                self._instrument_registry = InstrumentRegistry(feeder=self)
            spec = self._instrument_registry.get(symbol)
            value = float(getattr(spec, "contract_value", 0.0) or 0.0)
            return value if value > 0 else 1.0
        except Exception:
            return 1.0


    def _has_auth(self) -> bool:
        return bool(getattr(config, "BLOFIN_API_KEY", "") and getattr(config, "BLOFIN_API_SECRET", "")
                    and getattr(config, "BLOFIN_API_PASSPHRASE", ""))

    def _sign_headers(self, method: str, path_with_query: str, body: str = "") -> dict:
        """Naglowki prywatnego API Blofin (HMAC-SHA256 → hex → Base64)."""
        ts = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        prehash = f"{path_with_query}{method.upper()}{ts}{nonce}{body}"
        secret = config.BLOFIN_API_SECRET
        hex_sig = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()
        sign = base64.b64encode(hex_sig.encode()).decode()
        return {
            "ACCESS-KEY": config.BLOFIN_API_KEY,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-NONCE": nonce,
            "ACCESS-PASSPHRASE": config.BLOFIN_API_PASSPHRASE,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def fetch_futures_balance(self) -> Optional[dict]:
        """
        Equity konta futures USDT.
        Zwraca: {equity, available, currency, raw} lub None.
        """
        if not self._has_auth():
            self.last_error = "brak BLOFIN_API_KEY/SECRET/PASSPHRASE w .env"
            return None
        # Preferowane endpointy (kolejnosc fallback)
        candidates = [
            ("/api/v1/account/balance", {"accountType": "futures"}),
            ("/api/v1/asset/balances", {"accountType": "futures"}),
            ("/api/v1/account/balance", None),
        ]
        for path, params in candidates:
            if not TRADING_BUCKET.acquire():
                self.last_error = "local rate limit (trading bucket)"
                continue
            try:
                q = ""
                if params:
                    q = "?" + "&".join(f"{k}={v}" for k, v in params.items())
                full_path = path + q
                headers = self._sign_headers("GET", full_path, "")
                url = "https://openapi.blofin.com" + full_path
                r = self.session.get(url, headers=headers, timeout=12)
                if r.status_code != 200:
                    self.last_error = f"balance HTTP {r.status_code}: {r.text[:120]}"
                    continue
                data = r.json()
                if str(data.get("code")) not in ("0", "success") and data.get("code") != 0:
                    self.last_error = f"balance code={data.get('code')} {data.get('msg')}"
                    continue
                payload = data.get("data") or data.get("details") or data
                equity = available = None
                currency = "USDT"
                # rozne formaty odpowiedzi
                if isinstance(payload, dict):
                    equity = payload.get("totalEquity") or payload.get("equity") or payload.get("balance")
                    available = payload.get("available") or payload.get("availableBalance") or payload.get("availEq")
                    details = payload.get("details") or payload.get("data")
                    if details and isinstance(details, list):
                        for d in details:
                            ccy = (d.get("currency") or d.get("ccy") or "").upper()
                            if ccy in ("USDT", "USD"):
                                equity = d.get("equity") or d.get("balance") or d.get("totalEquity") or equity
                                available = d.get("available") or d.get("availEq") or available
                                currency = ccy
                                break
                elif isinstance(payload, list):
                    for d in payload:
                        ccy = (d.get("currency") or d.get("ccy") or "").upper()
                        if ccy in ("USDT", "USD"):
                            equity = d.get("equity") or d.get("balance") or d.get("totalEquity")
                            available = d.get("available") or d.get("availEq")
                            currency = ccy
                            break
                if equity is None and available is None:
                    self.last_error = "balance: nie znaleziono USDT equity"
                    continue
                eq = float(equity if equity is not None else available or 0)
                av = float(available if available is not None else equity or 0)
                return {
                    "equity": eq,
                    "available": av,
                    "currency": currency,
                    "raw": payload,
                }
            except Exception as e:
                self.last_error = f"balance err: {e}"
                continue
        return None

    def fetch_api_key_permissions(self) -> Optional[list]:
        """Best-effort: probuje odczytac liste uprawnien skonfigurowanego
        klucza (READ/TRADE/TRANSFER), zeby dac konkretniejsze ostrzezenie niz
        ogolna wskazowke, jesli klucz ma wiecej niz READ w trybie PAPER.

        UWAGA: nie udalo sie potwierdzic w dokumentacji dokladnej sciezki/
        schematu tego endpointu (sekcja "User -> GET API Key Info" byla
        obcinana przy kazdej probie pobrania). Sciezka ponizej to najlepsze
        wnioskowanie ze wzorca innych endpointow w tym API, NIE potwierdzony
        fakt. Dlatego funkcja jest maksymalnie defensywna: kazdy niepasujacy
        ksztalt odpowiedzi, kazdy blad, kazdy status != 200 po prostu zwraca
        None (nieznane), nigdy nie zglasza falszywej pewnosci co do uprawnien.
        Wolajacy MUSI traktowac None jako "nie da sie zweryfikowac", nie jako
        "brak dodatkowych uprawnien"."""
        if not self._has_auth():
            return None
        if not TRADING_BUCKET.acquire():
            return None
        try:
            path = "/api/v1/user/api-key"
            headers = self._sign_headers("GET", path, "")
            url = "https://openapi.blofin.com" + path
            r = self.session.get(url, headers=headers, timeout=8)
            if r.status_code != 200:
                return None
            data = r.json()
            payload = data.get("data")
            if isinstance(payload, list) and payload:
                payload = payload[0]
            if not isinstance(payload, dict):
                return None
            raw = payload.get("permissions") or payload.get("perm") or payload.get("permission")
            if raw is None:
                return None
            if isinstance(raw, str):
                raw = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
            if not isinstance(raw, list):
                return None
            return [str(p).upper() for p in raw]
        except Exception:
            return None

    def _private_get(self, path: str, params: dict = None, timeout: int = 12) -> Optional[dict]:
        """Tylko GET – read-only. Żadnych POST/PUT (handel zabroniony w kodzie)."""
        if not self._has_auth():
            self.last_error = "brak kluczy Blofin"
            return None
        if not TRADING_BUCKET.acquire():
            self.last_error = "local rate limit (trading bucket)"
            return None
        params = params or {}
        q = ""
        if params:
            q = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        full_path = path + q
        try:
            headers = self._sign_headers("GET", full_path, "")
            url = "https://openapi.blofin.com" + full_path
            r = self.session.get(url, headers=headers, timeout=timeout)
            if r.status_code != 200:
                self.last_error = f"HTTP {r.status_code}: {r.text[:160]}"
                return None
            data = r.json()
            code = data.get("code")
            if str(code) not in ("0", "success") and code != 0:
                self.last_error = f"code={code} {data.get('msg')}"
                return None
            return data
        except Exception as e:
            self.last_error = str(e)
            return None

    def fetch_open_positions(self) -> List[dict]:
        """
        Otwarte pozycje futures (READ-ONLY).
        GET /api/v1/account/positions
        Zwraca listę znormalizowanych dictów – bez możliwości modyfikacji.
        """
        if not self._has_auth():
            self.last_error = "brak kluczy Blofin"
            return []

        # oficjalny endpoint + fallback
        candidates = [
            ("/api/v1/account/positions", None),
            ("/api/v1/account/positions", {"instType": "SWAP"}),
            ("/api/v1/trade/positions", None),
        ]
        raw_list = None
        for path, params in candidates:
            data = self._private_get(path, params)
            if not data:
                continue
            payload = data.get("data")
            if payload is None:
                continue
            if isinstance(payload, list):
                raw_list = payload
                break
            if isinstance(payload, dict):
                raw_list = payload.get("positions") or payload.get("details") or [payload]
                break
        if not raw_list:
            return []

        out: List[dict] = []
        for p in raw_list:
            try:
                size_raw = p.get("positions") or p.get("pos") or p.get("size") or "0"
                size = float(size_raw)
                if abs(size) < 1e-12:
                    continue
                side = (p.get("positionSide") or p.get("posSide") or p.get("side") or "net").lower()
                # net mode: size > 0 long, < 0 short
                if side in ("net", ""):
                    direction = "LONG" if size > 0 else "SHORT"
                    size = abs(size)
                elif side in ("long", "buy"):
                    direction = "LONG"
                    size = abs(size)
                else:
                    direction = "SHORT"
                    size = abs(size)

                inst = p.get("instId") or p.get("symbol") or "?"
                symbol = str(inst).replace("-USDT", "").replace("USDT", "").replace("-", "")
                entry = float(p.get("averagePrice") or p.get("avgPx") or p.get("avgPrice") or 0)
                mark = float(p.get("markPrice") or p.get("markPx") or 0)
                upnl = float(p.get("unrealizedPnl") or p.get("upl") or p.get("unrealized_pnl") or 0)
                margin = float(p.get("margin") or p.get("marginBalance") or 0)
                lev = p.get("leverage") or p.get("lever") or ""
                try:
                    lev_f = float(lev) if lev not in ("", None) else None
                except Exception:
                    lev_f = None
                liq = p.get("liquidationPrice") or p.get("liqPx") or ""
                try:
                    liq_f = float(liq) if liq not in ("", None) else None
                except Exception:
                    liq_f = None
                mmode = p.get("marginMode") or p.get("mgnMode") or ""
                out.append({
                    "symbol": symbol,
                    "inst_id": inst,
                    "direction": direction,
                    "size": size,
                    "entry": entry,
                    "mark": mark,
                    "pnl": upnl,
                    "margin": margin,
                    "leverage": lev_f,
                    "liquidation": liq_f,
                    "margin_mode": mmode,
                    "position_id": p.get("positionId") or p.get("posId"),
                    "source": "blofin",
                    "read_only": True,
                })
            except Exception:
                continue
        return out


    def _log_fail(self, path: str, err: str, force: bool = False) -> None:
        """Jedna linia na pad GET. Dedup 20s, żeby kline fail nie zalał logu."""
        now = time.time()
        key = f"{path}|{err}"
        prev = float((self._fail_log_ts or {}).get(key) or 0.0)
        if not force and now - prev < 20.0:
            return
        if not hasattr(self, "_fail_log_ts") or self._fail_log_ts is None:
            self._fail_log_ts = {}
        self._fail_log_ts[key] = now
        print(f"[Blofin] GET {path} FAIL: {err}")

    @staticmethod
    def _body_snip(resp, n: int = 160) -> str:
        try:
            return (getattr(resp, "text", None) or "").replace("\n", " ").strip()[:n]
        except Exception:
            return ""

    def _tcp_probe(self, ips, port: int = 443, per_ip_s: float = 3.0) -> list:
        """Szybki TCP :443 per IP (max 3). Zwraca listę IP które przyjmują SYN."""
        ok = []
        for ip in list(ips or [])[:3]:
            family = socket.AF_INET6 if ":" in str(ip) else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(per_ip_s)
            try:
                if family == socket.AF_INET6:
                    sock.connect((ip, port, 0, 0))
                else:
                    sock.connect((ip, port))
                ok.append(ip)
                print(f"[Blofin] TCP {ip}:{port} OK")
            except Exception as e:
                print(f"[Blofin] TCP {ip}:{port} FAIL {type(e).__name__}: {e}")
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        return ok

    def _tls_probe(self, host: str, timeout: float = 5.0) -> str:
        """Surowe TLS (SNI) — odróżnia firewall od AV-MITM zanim poleci requests.
        IPv4-only: connect na A-rekord, nie na AAAA (blackhole)."""
        try:
            ctx = _ssl_context()
            family = socket.AF_INET if self._ipv4_only else socket.AF_UNSPEC
            infos = socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)
            if not infos:
                return "getaddrinfo empty"
            af, _socktype, proto, _canon, sa = infos[0]
            raw = socket.socket(af, socket.SOCK_STREAM, proto)
            raw.settimeout(timeout)
            raw.connect(sa)
            try:
                tls = ctx.wrap_socket(raw, server_hostname=host)
                ver = tls.version() or "?"
                cipher = (tls.cipher() or ("?", "", 0))[0]
                print(f"[Blofin] TLS OK {ver} {cipher}")
                try:
                    tls.close()
                except Exception:
                    pass
                return f"OK {ver}"
            finally:
                try:
                    raw.close()
                except Exception:
                    pass
        except ssl.SSLError as e:
            print(f"[Blofin] TLS FAIL SSL {e}")
            return f"SSL {e}"
        except Exception as e:
            print(f"[Blofin] TLS FAIL {type(e).__name__}: {e}")
            return f"{type(e).__name__}: {e}"

    def _remount(self, ipv4_only: bool, system_ssl: bool, why: str) -> None:
        """Przepina adapter na tej samej Session (testy mockują session.get)."""
        self._ipv4_only = ipv4_only
        self._system_ssl = system_ssl
        try:
            _mount_adapter(self.session, ipv4_only=ipv4_only, system_ssl=system_ssl)
            print(f"[Blofin] retry transport: {why} (ipv4={ipv4_only} system_ssl={system_ssl})")
        except Exception as e:
            print(f"[Blofin] remount FAIL: {e}")

    def _universe_from_tickers(self, instruments_err: str = "") -> Optional[dict]:
        """Gdy market/instruments pada: uniwersum z market/tickers (te same instId USDT)."""
        tick = self._get("market/tickers")
        synth = _synth_instruments_from_tickers((tick or {}).get("data") or [])
        if not synth:
            return None
        why = instruments_err or "brak odpowiedzi"
        print(f"[Blofin] instruments pad ({why}) — universe z tickers: {len(synth)} par")
        self.last_error = None
        self.available = True
        return {"code": "0", "msg": "tickers_fallback", "data": synth}

    def fetch_instruments(self) -> dict:
        """Lista instrumentów. Gdy GET market/instruments pada (Windows SSL/IPv6/WAF),
        buduje uniwersum z market/tickers (te same instId)."""
        data = self._get("market/instruments", params={"instType": "SWAP"})
        rows = (data or {}).get("data") or []
        if rows:
            self._instruments_error = None
            return data
        err = self.last_error or "brak odpowiedzi"
        self._instruments_error = err
        fb = self._universe_from_tickers(err)
        if fb:
            return fb
        return data or {}

    def _print_probe_hints(self, error: str, report: dict) -> None:
        err_l = (error or "").lower()
        tls_l = (report.get("tls") or "").lower()
        if "ssl" in err_l or "certificate" in err_l or "ssl" in tls_l:
            print("[Blofin] SSL: antywirus (HTTPS scan) na Windows często psuje certyfikat. Wyłącz skan HTTPS dla Pythona albo dodaj CA antywirusa do zaufanych.")
        elif not report.get("tcp"):
            print("[Blofin] TCP: żaden IP nie przyjął :443 — firewall/VPN/DNS. Sprawdź w przeglądarce https://openapi.blofin.com/api/v1/market/instruments")
        elif "timeout" in err_l or "timed out" in err_l:
            print("[Blofin] timeout: sieć/VPN/IPv6. Domyślnie BLOFIN_IPV4_ONLY=True (tylko A-rekord). Jeśli dalej pada — filtr/ISP.")
        elif "403" in err_l or "451" in err_l or "blocked" in err_l:
            print("[Blofin] HTTP 403/451: WAF/geo. Bot wysyła Chrome UA + Origin. VPN albo BLOFIN_WAF_BROWSER_HEADERS=False (bez Origin) zwykle pomaga.")
        elif "connection" in err_l or "connect" in err_l:
            print("[Blofin] connection: firewall/DNS. Czy openapi.blofin.com w ogóle wychodzi na 443?")

    def probe_public(self) -> dict:
        """Start-up: DNS + TCP + TLS + GET market/instruments (tickers fallback). Drukuje etap."""
        host = BLOFIN_HOST
        report = {
            "ok": False, "n": 0, "error": None, "dns": [], "tcp": [],
            "tls": None, "elapsed_s": 0.0, "source": None,
        }
        t0 = time.time()
        print(
            f"[Blofin] transport ipv4_only={self._ipv4_only} "
            f"waf_headers={self._waf} connect={_CONNECT_TIMEOUT_S:.0f}s"
        )
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            report["dns"] = sorted({str(i[4][0]) for i in infos})
            fam = sorted({getattr(i[0], "name", str(i[0])) for i in infos})
            print(f"[Blofin] DNS {host} -> {','.join(report['dns'][:8])} ({','.join(fam)})")
        except Exception as e:
            report["error"] = f"DNS {type(e).__name__}: {e}"
            report["elapsed_s"] = round(time.time() - t0, 2)
            print(f"[Blofin] probe FAIL DNS {host}: {e}")
            return report
        report["tcp"] = self._tcp_probe(report["dns"])
        report["tls"] = self._tls_probe(host)
        payload = self._get("market/instruments", params={"instType": "SWAP"})
        report["elapsed_s"] = round(time.time() - t0, 2)
        rows = (payload or {}).get("data") or []
        dns_s = ",".join(report["dns"][:6])
        if rows:
            report["ok"] = True
            report["n"] = len(rows)
            report["error"] = None
            report["source"] = "instruments"
            self._instruments_error = None
            print(f"[Blofin] probe OK {report['n']} instrumentów w {report['elapsed_s']:.2f}s dns={dns_s}")
            return report
        inst_err = self.last_error or "pusta lista"
        self._instruments_error = inst_err
        report["error"] = inst_err
        report["source"] = "instruments"
        print(
            f"[Blofin] probe FAIL n=0 {report['elapsed_s']:.2f}s dns={dns_s} "
            f"tcp={len(report['tcp'])}/{len(report['dns'])} tls={report['tls']} err={inst_err}"
        )
        self._print_probe_hints(inst_err, report)
        fb = self._universe_from_tickers(inst_err)
        fb_rows = (fb or {}).get("data") or []
        if fb_rows:
            report["ok"] = True
            report["n"] = len(fb_rows)
            report["source"] = "tickers"
            report["error"] = None
            report["elapsed_s"] = round(time.time() - t0, 2)
            print(f"[Blofin] probe recovered via tickers: {report['n']} par")
        return report

    def _get(self, path: str, params: dict = None, timeout=None, _attempt: int = 0) -> Optional[dict]:
        # 21.08.2026: realny incydent - Blofin zwrocil Retry-After: 3600
        # (godzina) na publicznym endpoincie. Uzytkownik potwierdzil (z
        # wlasnej wiedzy o Blofin): to nie przypadkowo zawyzony naglowek,
        # tylko REALNY, godzinny ban za zbyt czeste odpytywanie limitu.
        # Konsekwencja: podczas takiego bana NIE WOLNO probowac dalej co
        # chwile - kazde kolejne zapytanie moze ban tylko przedluzyc/pogorszyc
        # (typowe dla anti-abuse na gieldach). Dlatego DLUGIE oczekiwania
        # (powyzej BLOFIN_RATE_LIMIT_SHORT_RETRY_MAX_S) NIE sa obslugiwane
        # blokujacym time.sleep() + retry - zamiast tego wchodzimy w
        # nieblokujacy cooldown (_rate_limited_until): przez caly zadany
        # przez serwer czas _get() zwraca None natychmiast, BEZ wysylania
        # jakiegokolwiek zapytania, wiec watek skanujacy (bot_loop, osobny od
        # UI) nie zamraza sie (dalej robi swoje: warmup/backfill/UI zostaje
        # responsywne), a serwer nie dostaje ANI JEDNEGO zapytania podczas
        # bana. Krotkie throttle'e (typowy, chwilowy 429) dalej sa
        # obslugiwane od razu, blokujacym sleep+retry jak dotychczas - to
        # tania, bezpieczna sciezka dla normalnego, drobnego przypadku.
        now = time.time()
        if now < self._rate_limited_until:
            self.last_error = (
                f"429 rate limit - Blofin cooldown jeszcze {self._rate_limited_until - now:.0f}s"
            )
            return None
        # Proaktywnie: czekaj na token PRZED wyslaniem zapytania, zamiast
        # odpalac je i dostawac 429 po fakcie (za pozno - zapytanie juz
        # zuzylo budzet limitu po stronie Blofin).
        if not PUBLIC_BUCKET.acquire():
            self.last_error = "local rate limit (token bucket)"
            return None
        url = f"{BLOFIN_BASE}/{path}"
        req_timeout = _timeout_of(timeout)
        try:
            r = self.session.get(url, params=params or {}, timeout=req_timeout)
            if r.status_code == 429:
                self.last_error = "429 rate limit"
                self.fail_count += 1
                # Retry-After z naglowka gdy dostepny (dokladny czas, ktory
                # serwer sam podaje), fallback 12s tylko gdy brak naglowka.
                wait_s, from_header = _retry_after_seconds(r.headers, default=12.0)
                try:
                    short_max = float(getattr(config, "BLOFIN_RATE_LIMIT_SHORT_RETRY_MAX_S", 30.0))
                except (TypeError, ValueError):
                    short_max = 30.0
                if from_header and wait_s > short_max:
                    # Dlugi, prawdopodobnie realny ban - wchodzimy w
                    # nieblokujacy cooldown, ZERO kolejnych zapytan przez
                    # caly ten czas (patrz komentarz nad funkcja).
                    self._rate_limited_until = now + wait_s
                    print(
                        f"[Blofin] Rate limit – Blofin prosi o {wait_s:.0f}s (Retry-After), "
                        f"wstrzymuje WSZYSTKIE zapytania publiczne do tego czasu bez ponawiania "
                        f"i bez blokowania watku (realny ban, nie przypadkowy naglowek)"
                    )
                    try:
                        from feed_log import note
                        note("Blofin", f"429 cooldown {wait_s:.0f}s Retry-After {path}")
                    except Exception:
                        pass
                    return None
                print(f"[Blofin] Rate limit – czekam {wait_s:.0f}s" + (" (Retry-After)" if from_header else ""))
                try:
                    from feed_log import note
                    note("Blofin", f"429 wait {wait_s:.0f}s {path}")
                except Exception:
                    pass
                time.sleep(wait_s)
                PUBLIC_BUCKET.acquire()
                r = self.session.get(url, params=params or {}, timeout=req_timeout)
            if r.status_code in (403, 451) and _attempt == 0:
                # UA już Chrome — stary retry był martwy. Druga próba: alt UA + pełne WAF.
                self.session.headers.update(_waf_headers(_ALT_BROWSER_UA))
                print(f"[Blofin] HTTP {r.status_code} — retry z WAF headers + alt UA")
                PUBLIC_BUCKET.acquire()
                r = self.session.get(url, params=params or {}, timeout=req_timeout)

            if r.status_code in (403, 451):
                snip = self._body_snip(r)
                self.last_error = f"{r.status_code} blocked" + (f" {snip}" if snip else "")
                self.fail_count += 1
                if self.fail_count >= 8:
                    self.available = False
                self._log_fail(path, self.last_error, force=True)
                try:
                    from feed_log import note
                    note("Blofin", self.last_error)
                except Exception:
                    pass
                return None
            if r.status_code != 200:
                snip = self._body_snip(r)
                self.last_error = f"HTTP {r.status_code}" + (f" {snip}" if snip else "")
                self.fail_count += 1
                self._log_fail(path, self.last_error, force=True)
                return None
            data = r.json()
            if str(data.get("code")) not in ("0", "success") and data.get("code") != 0:
                self.last_error = f"code={data.get('code')} {data.get('msg')}"
                self.fail_count += 1
                self._log_fail(path, self.last_error, force=True)
                return None
            self.fail_count = max(0, self.fail_count - 1)
            self.available = True
            self.last_error = None
            return data
        except requests.Timeout:
            self.last_error = f"timeout {_timeout_label(timeout)}s GET {path}"
            if _attempt == 0:
                self._remount(ipv4_only=True, system_ssl=True, why="timeout → IPv4 + krótki connect")
                return self._get(path, params=params, timeout=timeout, _attempt=1)
            self.fail_count += 1
            self._log_fail(path, self.last_error, force=True)
            return None
        except requests.exceptions.SSLError as e:
            self.last_error = f"SSL {e}"
            if _attempt == 0:
                self._remount(ipv4_only=True, system_ssl=True, why="SSL → magazyn certyfikatów Windows/systemu")
                return self._get(path, params=params, timeout=timeout, _attempt=1)
            self.fail_count += 1
            self._log_fail(path, self.last_error, force=True)
            return None
        except requests.exceptions.ConnectionError as e:
            self.last_error = f"connection {e}"
            if _attempt == 0:
                # IPv4-only mogło zablokować jedyny działający stos — spróbuj dual-stack.
                self._remount(ipv4_only=False, system_ssl=True, why="connection → dual-stack")
                return self._get(path, params=params, timeout=timeout, _attempt=1)
            self.fail_count += 1
            self._log_fail(path, self.last_error, force=True)
            return None
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            self.fail_count += 1
            self._log_fail(path, self.last_error, force=True)
            try:
                from feed_log import note
                note("Blofin", f"GET {path}", e)
            except Exception:
                pass
            return None

    def fetch_all_tickers(self) -> Dict[str, Dict]:
        """Tickery – mapa BASE -> dane (preferuj USDT)."""
        if time.time() - self.ticker_cache_ts < 10 and self.ticker_cache:
            return self.ticker_cache
        if not self.available and self.fail_count >= 3:
            if time.time() - self.ticker_cache_ts < 120:
                return self.ticker_cache

        data = self._get("market/tickers")
        if not data:
            return self.ticker_cache

        result = {}
        for t in data.get("data", []):
            inst = t.get("instId", "")
            # BTC-USDT / ETH-USDT
            if "-USDT" not in inst:
                continue
            base = inst.split("-")[0].upper()
            try:
                last = float(t.get("last") or 0)
                if last <= 0:
                    continue
                if base in result:
                    continue
                open24 = float(t["open24h"]) if t.get("open24h") else None
                high24 = float(t["high24h"]) if t.get("high24h") else None
                low24 = float(t["low24h"]) if t.get("low24h") else None
                vol = float(t["volCurrency24h"]) if t.get("volCurrency24h") else (
                    float(t["vol24h"]) if t.get("vol24h") else None
                )
                # volCurrency24h jest w walucie bazowej; ranking wymaga USDT.
                quote_vol = vol * last if vol is not None else None
                chg = None
                if open24 and open24 > 0:
                    chg = (last - open24) / open24 * 100.0
                result[base] = {
                    "blofin_price": last,
                    "blofin_change_24h": chg,
                    "blofin_high": high24,
                    "blofin_low": low24,
                    "blofin_volume": quote_vol,
                    "blofin_base_volume": vol,
                    "blofin_quote_volume": quote_vol,
                    "blofin_bid": float(t["bidPrice"]) if t.get("bidPrice") else None,
                    "blofin_ask": float(t["askPrice"]) if t.get("askPrice") else None,
                    "blofin_only_ready": True,
                    "blofin_ts_ms": int(float(t.get("ts") or time.time() * 1000)),
                }
            except (KeyError, ValueError, TypeError):
                continue

        if result:
            self.ticker_cache = result
            self.ticker_cache_ts = time.time()
        return self.ticker_cache

    def fetch_last_prices(self, symbols) -> Dict[str, float]:
        """Ceny ochronne dla otwartych pozycji - najpierw z WebSocketa (jesli
        polaczony i ma swieze dane), REST tylko dla brakujacych symboli.
        To najczestsza, najbardziej krytyczna sciezka w calym systemie (fast
        tick co ~1s) - stad priorytet WS tutaj, nie gdzie indziej."""
        wanted = {str(s).upper() for s in (symbols or []) if s}
        if not wanted:
            return {}
        out: Dict[str, float] = {}
        if PUBLIC_WS.available:
            PUBLIC_WS.start(list(wanted))
            PUBLIC_WS.subscribe(list(wanted))
            for sym in list(wanted):
                px = PUBLIC_WS.get_price(sym, max_age_s=5.0)
                if px is not None:
                    out[sym] = px
        missing = wanted - set(out)
        if not missing:
            return out
        data = self._get("market/tickers")
        for row in (data or {}).get("data") or []:
            base = str(row.get("instId") or "").split("-")[0].upper()
            if base not in missing:
                continue
            try:
                price = float(row.get("last") or 0)
                if price > 0:
                    out[base] = price
            except (TypeError, ValueError):
                continue
        return out

    def fetch_klines_closes(self, symbol: str, bar: str = "1H", limit: int = 50) -> List[float]:
        """Tylko zamknięte świece (przez fetch_klines_ohlcv + drop_unclosed)."""
        data = self.fetch_klines_ohlcv(symbol, bar=bar, limit=max(int(limit) + 1, 2))
        closes = list((data or {}).get("closes") or [])
        return closes[-int(limit):] if closes else []

        data = self._get("market/candles", {
            "instId": inst,
            "bar": bar,
            "limit": str(limit)
        })
        if not data:
            return []

        rows = data.get("data", [])
        # Blofin: [ts, o, h, l, c, vol, ...] od najnowszych
        closes = []
        for row in reversed(rows):
            try:
                closes.append(float(row[4]))
            except (IndexError, ValueError, TypeError):
                continue

        if closes:
            self.ohlc_cache[cache_key] = (time.time(), closes)
        return closes

    
    def _fetch_delta_kline_rows(self, inst: str, bar: str, since_ts: int, cap: int) -> list:
        """Doszywa TYLKO swiece nowsze niz since_ts (Blofin: `before` w
        market/candles = rekordy nowsze niz podany ts, w przeciwienstwie do
        `after` uzywanego w pelnym fetchu ponizej - zweryfikowane w
        dokumentacji Blofin 21.08.2026), paginujac w przod az do `cap`
        wierszy. Uzywane, zeby po restarcie NIE fetchowac calego okna od
        zera, gdy dysk/pamiec ma juz wiekszosc historii - patrz uwaga przy
        _KLINE_DISK_PERSIST_BARS.

        Zwraca liste surowych wierszy - moze byc pusta ([]), co jest
        POTWIERDZONYM "zero nowych swiec, cache byl juz aktualny". Zwraca
        None TYLKO gdy pierwsze zapytanie nie powiodlo sie (siec/API) i nie
        zdobylismy jeszcze zadnych wierszy - to sygnal dla wolajacego, zeby
        spasc do pelnego fetchu ponizej, a NIE zakladac bezpodstawnie, ze
        cache jest aktualny. Blad na kolejnej (nie pierwszej) stronie przy
        wielostronicowej delcie zwraca to, co juz zdobyto - czesciowa delta
        wciaz jest lepsza niz nic."""
        max_per_req = 300
        all_rows: list = []
        cursor = int(since_ts)
        while len(all_rows) < cap:
            batch = min(cap - len(all_rows), max_per_req)
            params = {"instId": inst, "bar": bar, "limit": str(batch), "before": str(cursor)}
            raw = self._get("market/candles", params)
            if raw is None:
                return None if not all_rows else all_rows
            chunk = list(raw.get("data") or [])
            if not chunk:
                break  # potwierdzone: nic nowego od cursor
            all_rows.extend(chunk)
            try:
                newest = max(int(float(r[0])) for r in chunk if r)
            except (ValueError, TypeError):
                break
            if newest <= cursor:
                break  # brak postepu - unikamy niekonczacej sie petli
            cursor = newest
            if len(chunk) < batch:
                break
        return all_rows

    def fetch_klines_ohlcv(self, symbol: str, bar: str = "1H", limit: int = 120) -> dict:
        """
        Paginacja candles Blofin (max ~300/req) – after/before ts.
        """
        inst = f"{symbol.upper()}-USDT"
        limit = int(max(1, limit))
        cache_key = f"ohlcv_{inst}_{bar}_{limit}"
        if PUBLIC_WS.available:
            try:
                PUBLIC_WS.start([symbol])
                PUBLIC_WS.subscribe_candles(symbol, [bar])
            except Exception:
                pass
        if cache_key not in self.ohlc_cache and bar in _KLINE_DISK_PERSIST_BARS:
            # Nic w pamieci (typowo zaraz po restarcie) - zanim odpalimy
            # zapytanie, sprawdz dysk. Przestarzale dane z dysku sa lepsze
            # niz brak danych, dopoki nie zdazymy odswiezyc na zywo.
            disk_hit = disk_cache.load(cache_key)
            if disk_hit and isinstance(disk_hit.get("data"), dict):
                self.ohlc_cache[cache_key] = (disk_hit["ts"], disk_hit["data"])
        if cache_key in self.ohlc_cache:
            ts, data = self.ohlc_cache[cache_key]
            # TTL dopasowany do realnego czasu zycia bara, nie plaskie 60/120s
            # dla wszystkiego. Poprzednio: 1h/4h/1D odswiezaly sie niemal tak
            # czesto jak 5m, mimo ze ich swieca zmienia sie dziesiatki razy
            # rzadziej - to byl jeden z glownych zrodel nadmiarowych zapytan.
            #
            # Adaptacyjnie: jesli WS jest polaczony, sam dowozi swiezosc
            # (~1-2s od zamkniecia bara przez _merge_ws_closed_candle) -
            # REST moze poluzowac TTL (mniejsze zuzycie budzetu). Jesli WS
            # akurat nie zyje, wracamy do ciasniejszego TTL - bez tego
            # dostalibysmy dlugi TTL BEZ zadnego zrodla swiezosci w tym oknie.
            ttl_table = _KLINE_CACHE_TTL_S_WS_CONNECTED if PUBLIC_WS.is_connected() else _KLINE_CACHE_TTL_S
            ttl = ttl_table.get(bar, 60)
            if isinstance(data, dict) and time.time() - ts < ttl:
                merged, gap = _merge_ws_closed_candle(symbol, bar, data)
                if not gap and not _ohlcv_too_old_for_v2(merged, bar):
                    return _publish_ohlcv(symbol, bar, merged)
                if gap:
                    print(f"[Blofin] Luka w świecach WS {inst} {bar} - wymuszam odświeżenie REST")
                elif _ohlcv_too_old_for_v2(merged, bar):
                    print(f"[Blofin] Cache {inst} {bar} za stary na V2 - wymuszam REST")
            # Wiadro nisko (<20%) - swiece sa najbardziej dyskrecjonalnym
            # zapytaniem (najwieksza objetosc: 4 interwaly x N kandydatow co
            # skan). Lepiej oddac stare dane niz pogorszyc sytuacje kolejnym
            # zapytaniem - rezerwujemy pozostaly budzet na tickery/ceny
            # ochronne dla otwartych pozycji.
            if isinstance(data, dict) and PUBLIC_BUCKET.level() < 0.20:
                if _ohlcv_too_old_for_v2(data, bar):
                    print(
                        f"[Blofin] Wiadro <20% ale {inst} {bar} za stare na V2 "
                        f"- wymuszam REST"
                    )
                else:
                    print(f"[Blofin] Wiadro <20% - oddaje stare świece {inst} {bar} (sprzed {time.time()-ts:.0f}s)")
                    merged, gap = _merge_ws_closed_candle(symbol, bar, data)
                    # Przy niskim budzecie NIE wymuszamy fetchu nawet gdy jest
                    # luka (to zaprzeczyloby calemu celowi tej galezi) - po
                    # prostu oddajemy dane bez (potencjalnie dziurawego) merge'u.
                    return _publish_ohlcv(symbol, bar, data if gap else merged)

            # 21.08.2026: TTL wygasl i wiadro ma budzet (>=20%, inaczej
            # zwrocilibysmy sie juz wyzej) - zanim zrobimy PELNY re-fetch
            # calego `limit`, sprobuj doszyc tylko delte od ostatniej
            # znanej swiecy (dysk lub pamiec). To wlasciwe ciecie wolumenu
            # zapytan przy rozruchu: poprzedni krok (dopisanie 1H/15m do
            # _KLINE_DISK_PERSIST_BARS) dawal dysk tylko jako "cos lepsze
            # niz nic" - po wygasnieciu TTL (regula po kazdym realnym
            # restarcie) i tak lecial pelny fetch. Tu fetchujemy realnie
            # tylko brakujacy ogon.
            if bar in _KLINE_DISK_PERSIST_BARS and isinstance(data, dict) and data.get("timestamps"):
                last_ts = data["timestamps"][-1]
                try:
                    delta_rows = self._fetch_delta_kline_rows(inst, bar, last_ts, limit)
                except Exception as exc:
                    print(f"[Blofin] Delta fetch {inst} {bar} nieudany ({exc}) - pelny re-fetch")
                    delta_rows = None
                if delta_rows is not None:
                    fresh = _parse_kline_rows(delta_rows, bar, limit) if delta_rows else {}
                    merged_data = _merge_parsed_klines(data, fresh, limit)
                    if merged_data.get("closes"):
                        self.ohlc_cache[cache_key] = (time.time(), merged_data)
                        disk_cache.save(cache_key, merged_data)
                        result, _gap = _merge_ws_closed_candle(symbol, bar, merged_data)
                        return _publish_ohlcv(symbol, bar, result)
                # delta_rows is None (pierwsze zapytanie nieudane) albo
                # merge nie dal uzytecznych danych - spadamy do pelnego
                # fetchu ponizej jako bezpieczny fallback.

        max_per_req = 300  # bezpieczny limit Blofin
        all_rows = []
        remaining = limit
        after = None  # BloFin: `after` = rekordy starsze niż timestamp
        while remaining > 0:
            batch = min(remaining, max_per_req)
            params = {"instId": inst, "bar": bar, "limit": str(batch)}
            if after is not None:
                params["after"] = str(after)
            raw = self._get("market/candles", params)
            if not raw:
                break
            chunk = list(raw.get("data") or [])
            if not chunk:
                break
            # Blofin zwraca zwykle newest-first
            all_rows = chunk + all_rows
            try:
                # najstarszy ts w paczce
                oldest = min(int(float(r[0])) for r in chunk if r)
                after = oldest
            except (ValueError, TypeError):
                break
            if len(chunk) < batch:
                break
            remaining = limit - len(all_rows)
            if remaining <= 0:
                break

        if not all_rows:
            return {}
        # 21.08.2026: parsowanie (dedupe/sort/trim/drop_unclosed_candle)
        # wydzielone do _parse_kline_rows - ta sama logika obsluguje teraz
        # i pelny fetch (tutaj), i doszywanie delty (wyzej).
        data = _parse_kline_rows(all_rows, bar, limit)
        if data.get("closes"):
            self.ohlc_cache[cache_key] = (time.time(), data)
            if bar in _KLINE_DISK_PERSIST_BARS:
                disk_cache.save(cache_key, data)
        merged, _gap = _merge_ws_closed_candle(symbol, bar, data)
        return _publish_ohlcv(symbol, bar, merged)


    def fetch_order_book(self, symbol: str, size: int = 15) -> dict:
        """
        Order book Blofin. Zwraca imbalance, spread, depth.
        imbalance > 0 → wiecej bidow (presja kupna)
        """
        inst = f"{symbol.upper()}-USDT"
        data = self._get("market/books", {"instId": inst, "size": str(size)})
        if not data:
            return {}
        rows = data.get("data") or []
        if not rows:
            return {}
        book = rows[0]
        try:
            contract_value = self._contract_value(symbol)
            bids_contracts = [(float(p), float(s)) for p, s in book.get("bids") or []]
            asks_contracts = [(float(p), float(s)) for p, s in book.get("asks") or []]
            bids = [(p, contracts * contract_value) for p, contracts in bids_contracts]
            asks = [(p, contracts * contract_value) for p, contracts in asks_contracts]
        except (ValueError, TypeError):
            return {}
        if not bids or not asks:
            return {}
        bid_vol = sum(s for _, s in bids)
        ask_vol = sum(s for _, s in asks)
        total = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total if total else 0.0
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid = (best_bid + best_ask) / 2
        spread_pct = (best_ask - best_bid) / mid * 100 if mid else 0.0
        band = 0.5
        min_depth = 3000.0
        try:
            import config as _cfg
            band = float(getattr(_cfg, "OB_DEPTH_BAND_PCT", 0.5) or 0.5)
            min_depth = float(getattr(_cfg, "OB_MIN_DEPTH_USD", 3000) or 3000)
        except Exception:
            pass
        lo = mid * (1 - band / 100.0)
        hi = mid * (1 + band / 100.0)
        depth_bid = sum(p * s for p, s in bids if p >= lo)
        depth_ask = sum(p * s for p, s in asks if p <= hi)
        depth_total = depth_bid + depth_ask
        return {
            "ob_bid_vol": bid_vol,
            "ob_ask_vol": ask_vol,
            "ob_imbalance": round(imbalance, 4),
            "ob_spread_pct": round(spread_pct, 5),
            "ob_best_bid": best_bid,
            "ob_best_ask": best_ask,
            "ob_mid": mid,
            "ob_depth_bid_usd": round(depth_bid, 2),
            "ob_depth_ask_usd": round(depth_ask, 2),
            "ob_depth_usd": round(depth_total, 2),
            "ob_depth_band_pct": band,
            "ob_bias": "buy" if imbalance > 0.15 else ("sell" if imbalance < -0.15 else "neutral"),
            "ob_thin": depth_total < min_depth,
            # Poziomy znormalizowane do base asset dla impact simulatora.
            "bids": bids,
            "asks": asks,
            "bids_contracts": bids_contracts,
            "asks_contracts": asks_contracts,
            "contract_value": contract_value,
            "size_unit": "base_asset",
        }


    def fetch_funding_rate(self, symbol: str) -> dict:
        inst = f"{symbol.upper()}-USDT"
        data = self._get("market/funding-rate", {"instId": inst})
        if not data:
            return {}
        rows = data.get("data") or []
        if not rows:
            return {}
        row = rows[0]
        try:
            rate = float(row.get("fundingRate") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        raw = {
            "funding_rate": rate,
            "funding_rate_pct": round(rate * 100, 5),
            "funding_time": row.get("fundingTime") or row.get("fundingRateTimestamp"),
            "funding_interval": row.get("fundingInterval") or row.get("interval"),
            "next_funding_time": row.get("nextFundingTime") or row.get("nextFundingRateTimestamp"),
        }
        try:
            from funding_model import enrich_funding
            return enrich_funding(raw)
        except Exception:
            return raw

    def fetch_open_interest(self, symbol: str) -> dict:
        """GET /api/v1/market/open-interest — cache w perp_context, nie tutaj."""
        inst = f"{symbol.upper()}-USDT"
        data = self._get("market/open-interest", {"instId": inst})
        if not data:
            return {}
        rows = data.get("data") or []
        if not rows:
            return {}
        row = rows[0] if isinstance(rows, list) else rows
        if not isinstance(row, dict):
            return {}
        try:
            oi = float(row.get("oi") or row.get("openInterest") or row.get("holdVol") or 0)
        except (TypeError, ValueError):
            oi = 0.0
        try:
            oi_usd = float(row.get("oiUsd") or row.get("openInterestUsd") or 0)
        except (TypeError, ValueError):
            oi_usd = 0.0
        return {
            "open_interest": oi,
            "open_interest_usd": oi_usd,
            "oi_raw": row,
        }

    def fetch_position_tiers(self, symbol: str, margin_mode: str = "isolated") -> list:
        """Venue maintenance-margin tiers used by liquidation checks."""
        inst = f"{symbol.upper()}-USDT"
        data = self._get("market/position-tiers", {"instId": inst, "marginMode": margin_mode})
        out = []
        for row in (data or {}).get("data") or []:
            try:
                out.append({
                    "min_size": float(row.get("minSize") or 0),
                    "max_size": float(row.get("maxSize") or float("inf")),
                    "maintenance_margin_rate": float(row.get("maintenanceMarginRate") or 0),
                    "max_leverage": float(row.get("maxLeverage") or 0),
                    "margin_mode": str(row.get("marginMode") or margin_mode),
                })
            except (TypeError, ValueError):
                continue
        return sorted(out, key=lambda x: x["min_size"])


    def fetch_funding_rate_history(self, symbol: str, limit: int = 100, after: str = None) -> list:
        """
        GET /api/v1/market/funding-rate-history
        Zwraca listę {ts_ms, rate} rosnąco po czasie.
        Paginacja: after = fundingTime starszego rekordu.
        """
        inst = f"{symbol.upper()}-USDT"
        all_rows = []
        remaining = int(max(1, limit))
        after_ts = after
        while remaining > 0:
            batch = min(remaining, 100)
            params = {"instId": inst, "limit": str(batch)}
            if after_ts:
                params["after"] = str(after_ts)
            data = self._get("market/funding-rate-history", params)
            if not data:
                break
            chunk = list(data.get("data") or [])
            if not chunk:
                break
            all_rows.extend(chunk)
            try:
                oldest = min(int(float(r.get("fundingTime") or 0)) for r in chunk)
                after_ts = str(oldest)
            except (TypeError, ValueError):
                break
            if len(chunk) < batch:
                break
            remaining = limit - len(all_rows)
            if remaining <= 0:
                break
        out = []
        seen = set()
        for r in all_rows:
            try:
                ts = int(float(r.get("fundingTime") or 0))
                rate = float(r.get("fundingRate") or r.get("realizedRate") or 0)
            except (TypeError, ValueError):
                continue
            if ts in seen:
                continue
            seen.add(ts)
            out.append({"ts_ms": ts, "rate": rate})
        out.sort(key=lambda x: x["ts_ms"])
        return out[-limit:] if limit else out

    def status(self) -> str:
        if self.ticker_cache:
            extra = f" last={self.last_error}" if self.last_error else ""
            return f"Blofin: OK ({len(self.ticker_cache)} pairs){extra}"
        if self.last_error:
            return f"Blofin: ERROR ({self.last_error})"
        return "Blofin: no data"
