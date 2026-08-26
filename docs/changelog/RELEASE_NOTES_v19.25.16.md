# CryptoEdge v19.25.16

Re-entry po zysku, nie pyramiding.

BTC LONG zamknięty na TP → **30 min** → jeśli 4h/1h/15m nadal grają LONG → otwiera znowu. Jedna pozycja naraz.

- `MAX_ENTRIES_PER_SWING = 1`, `ALLOW_ADDON = False` (cofnięty addon 5 — klaster i tak ucinał na 2.)
- `notify_exit` **zwalnia slot swingu** (wcześniej impuls był spalony do końca życia).
- `COOLDOWN_AFTER_EXIT_MIN = 30` (było 60).
- Po SL nadal 240 min w tę samą stronę.
- Trailing-stop zmapowany jako `trailing`, nie `sl` — zyskowny trail nie dostaje 4h kary.

Klaster bez zmian (1.6×). Tu nie potrzeba 5 równoległych BTC.
