# CryptoEdge v17.21 — Data, Replay and Statistical Integrity (PAPER)

## Naprawione

- Paginacja BloFin używa `after` do starszych świec; Viper otrzymuje pełne 365+ barów.
- Jawne `confirm=0` jest odrzucane, a OHLCV przechowuje base i quote volume osobno.
- Backtester BloFin nie usuwa już drugi raz ostatniej zamkniętej świecy.
- Day replay używa stałego initial R po break-even, księguje mark-to-market na hard
  time-stop, obsługuje TP1, BE, trailing, TP2 i dwubarową invalidation.
- Dodano causal as-of feeder uruchamiający produkcyjny DayTradingEngine w replayu.
- Główny walk-forward stosuje purge i embargo, nie tylko nieużywany helper.
- Dodano production prefix audit, recursive stability oraz Deflated/Probabilistic Sharpe.
- Day expectancy zapisuje empiryczne trafienia TP1 i TP2 po zamknięciu PAPER i może
  przejść z `PRIOR_ONLY` do kalibracji po wymaganej liczbie obserwacji.
- Funding PAPER jest księgowany wyłącznie na settlement i używa interwału instrumentu.
- Korelacje 5m są wyrównane po wspólnych timestampach.
- PANIC daytrading nie dostaje już potrójnej redukcji ryzyka; obowiązuje jawny cap 25%.
- Liquidation fallback pobiera maintenance margin z BloFin position tiers.
- Cross-market sanity check używa zsynchronizowanego basis BloFin–Binance i blokuje
  ekstremalny rozjazd; stare 24h zmiany pozostają tylko miękkim kontekstem.
- Otwarte pozycje PAPER dostają niecache'owany snapshot ceny BloFin co cykl ochronny.
- Fibonacci wymaga uporządkowanego impulsu, a Viper jest jawnie opisany jako
  historyczny volume profile, nie mapa aktualnych zleceń.

## Walidacja

- 153/153 testów: OK.
- Pełna kompilacja modułów: OK.
- LIVE execution pozostaje wyłączony.

## Ograniczenia pozostające do walidacji na danych

- Kalibracja empiryczna zaczyna się od zera i potrzebuje co najmniej 100–200
  niezależnych transakcji OOS do decyzji kapitałowej.
- Historyczny orderbook L2 BloFin nie jest dostępny w obecnej paczce, dlatego replay
  impact używa konserwatywnego modelu, a nie prawdziwych kolejek i partial fills.
- Przed LIVE wymagany jest osobny test WebSocket/private fills, stop-on-exchange,
  cancel/replace, suspension oraz recovery po utracie połączenia.
