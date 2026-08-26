# CryptoEdge v20.0.0

## Jeden kontrakt strategii DayTrading V2

- PAPER/LIVE i replay wywołują ten sam czysty reducer `v2_trade_lifecycle`.
- Wspólna kolejność decyzji: SL, odwrócenie HTF, TP1, TP2, miękki time-stop,
  hard time-stop, aktualizacja trailingu.
- Wspólne reguły break-even, aktywacji SL, MFE/unclog i kotwicy trailingu.
- Replay pozostaje adapterem danych historycznych i symulatorem rozliczeń, a
  PAPER/LIVE adapterem danych bieżących i egzekucji. Nie zawierają już osobnej
  logiki decyzyjnej V2.
- Odłączono automatyczny ratchet 15m wykonywany wyłącznie w runtime.
- Dodano testy golden parity dla kwotowania, świecy, priorytetu SL/TP i adaptera
  PAPER.

Zmiana kontraktu architektonicznego uzasadnia numer główny 20.0.0.
