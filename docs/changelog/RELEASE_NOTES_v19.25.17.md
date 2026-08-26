# CryptoEdge v19.25.17

Zdjete kary czasowe V2. Zostaje seria przegranych.

- Po TP/SL/trail: **od razu** można zagrać ten sam setup (1 pozycja naraz).
- **5 przegranych z rzędu na jednej parze** → `V2_LOSS_STREAK_PAUSE` **15 min**.
- Zysk zeruje serię. ETH nie karze BTC.
- PnL z `paper_trader.close_position` idzie do `notify_exit`; bez PnL strata = reason `sl`.

Stare `COOLDOWN_AFTER_EXIT/SL/INVALIDATION` = 0.
