# CryptoEdge v19.25.10

Konfiguracja WAF i IPv6 — nie tylko hardcoded.

## IPv6
19.25.8 „IPv4-first” robiło `bind 0.0.0.0`. urllib3 i tak pytał o AAAA; na Windows blackhole zjadał connect timeout zanim poleciał A-rekord.

Teraz DNS+connect to **tylko AF_INET** (`BLOFIN_IPV4_ONLY=True`). Dual-stack tylko po `connection` retry albo gdy ustawisz `False`.
TLS probe też idzie na A-rekord, nie na AAAA.

## WAF
Retry 403 przy UA już Chrome był **martwy**. Teraz od startu: Chrome UA + Origin/Referer/Accept-Language (`BLOFIN_WAF_BROWSER_HEADERS=True`).
403 → druga próba z alt UA (Edge/Chrome 128) + te same nagłówki.

Ten sam transport: REST feed, executor, registry fallback, WebSocket (Origin + SSL context).

## Config (`config.py`, restart)
- `BLOFIN_IPV4_ONLY = True` — wyłącz tylko gdy sieć jest IPv6-only.
- `BLOFIN_WAF_BROWSER_HEADERS = True` — wyłącz (`False`) gdy API 403-uje właśnie na Origin.

Na starcie: `[Blofin] transport ipv4_only=True waf_headers=True connect=4s`
