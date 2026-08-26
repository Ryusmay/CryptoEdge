# CryptoEdge 19.33.0

- Runtime/PAPER i replay używają wspólnej, przyczynowej polityki Daytrading V2.
- Ujednolicono pump/dump chase, range, orderbook, LIMIT touch i timeout.
- Replay wylicza zmianę 24h wyłącznie z dostępnych w danym momencie świec.
- Replay portfelowy stosuje limit koncentracji pozycji w jednym kierunku jak Risk Engine.
- Raport replay zapisuje jawny zakres parity i dane dostępne wyłącznie w runtime.
- Fingerprint replay obejmuje silnik V2, wspólną politykę i ustawienia bramek, więc stare cache nie maskują zmian strategii.
