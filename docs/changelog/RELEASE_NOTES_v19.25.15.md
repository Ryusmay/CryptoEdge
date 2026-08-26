# CryptoEdge v19.25.15

Addon V2: do **5 wejść na ten sam impuls 1h**.

- `DAYTRADING_V2_MAX_ENTRIES_PER_SWING = 5`
- `DAYTRADING_V2_ALLOW_ADDON = True` (bez 10-min gap między dodaniami)

Silnik liczy wejścia per `(symbol, swing_end)`. Paper pozwoli na 5 pozycji w tę samą stronę na parze; przeciwny kierunek nadal blokuje.

Cooldown po **zamknięciu** zostaje (60 min / 240 po SL). Addon działa, dopóki impuls żyje i nie ma exit.

Uwaga: 5 × 7.5% margin = 37.5% equity na jednej parze (x10). Limit portfela/klastra może uciąć wcześniej — to zamierzone.
