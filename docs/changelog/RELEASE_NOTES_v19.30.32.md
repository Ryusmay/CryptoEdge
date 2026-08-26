# CryptoEdge v19.30.32

Patch bezpieczeństwa i wiarygodności testów:

- trailing V2 po TP2 nie może cofnąć ochrony poniżej break-even,
- PAPER używa przyczynowego `slip_rt` z sygnału albo deterministycznego profilu,
- twardy sanity check BloFin–Binance ponownie blokuje rozjazd >= 3%,
- `max_age_s <= 0` jawnie odrzuca dane WebSocket,
- logowanie rozpoznaje `DAYTRADING_V2`,
- testy fundingu, profilu XAU i reconciliation odpowiadają aktualnej specyfikacji,
- domyślny runner izoluje moduły od globalnego stanu rynku i cache.
- paczka ZIP pomija `.env`, zaszyfrowane sekrety i lokalne `.venv`.

Strategiczne progi wejścia, w tym twardy trigger 15m, nie zostały poluzowane.
