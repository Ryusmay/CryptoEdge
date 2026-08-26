# CryptoEdge v19.25.7

Diagnostyka padu BloFin public API (sesja 22.08: 0 par, 0 sygnałów, log tylko „brak instrumentów”).

## Co było nie tak
`GET /api/v1/market/instruments` padał, ale `_get()` chował timeout / SSL / HTTP 403 w `last_error` i **nie drukował**. Feeder pisał tylko cooldown. Przyczyna niewidoczna.

Sam endpoint jest zdrowy (public, bez klucza). Problem jest lokalny: sieć / SSL antywirusa / DNS / WAF.

## Co widać teraz
Na starcie: `[Blofin] probe OK N instrumentów` albo `probe FAIL … err=timeout|SSL|HTTP 403|connection` + krótka rada (SSL / VPN / firewall).
Przy każdym padzie listy par: `[Blofin] GET market/instruments FAIL: …` i `[DataFeeder] Blofin instrumenty FAIL: …`.
`InstrumentRegistry: 0 par — <ten sam błąd>`.

Po starcie 19.25.7 wklej pierwsze ~30 linii `logs/console.log` — będzie widać dokładny err.
