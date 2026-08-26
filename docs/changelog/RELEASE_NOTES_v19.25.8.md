# CryptoEdge v19.25.8

Naprawa transportu BloFin (sesja 22.08: 0 par, API żywe, Windows milczy).

## Co było nie tak
`GET /api/v1/market/instruments` padał lokalnie (nie sam endpoint). 19.25.7 tylko **pokazywał** błąd. Typowe przyczyny na Windows:

1. **IPv6 blackhole** — Cloudflare ma AAAA, Windows bierze IPv6 pierwszy, connect wisi aż timeout zjada całe zapytanie zanim urllib3 spróbuje IPv4.
2. **Antywirus HTTPS-scan** — requests ufa tylko certifi, nie magazynowi Windows, więc certyfikat AV = `SSL: CERTIFICATE_VERIFY_FAILED`.
3. **WAF 403** na nietypowym User-Agent.

## Co robi 19.25.8
- Sesja REST: **IPv4-first** + **systemowy magazyn CA** (Windows/AV) + certifi.
- Connect 4s / read 20s zamiast jednego 12s.
- Timeout / SSL / connection: **jedno retry** z przełączeniem transportu (IPv4 / certy / dual-stack).
- User-Agent jak Chrome.
- Probe na starcie: DNS → TCP :443 per IP → TLS → GET. Widać który etap pada.

PAPER / V2 / 4h jako najwyższy TF / bez SRC — bez zmian.
