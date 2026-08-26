# CryptoEdge v19.26.0

Open Positions (DESK) bierze układ z v14:

- kolumny: SYM · SIDE · ENTRY · MARK · SL · ZYSK SL · PNL · PNL% · AGE
- SL przed PnL
- ikony ochrony: ↓ twardy SL, 🔒− breakeven, ▲ trailing podniesiony
- Zysk SL = PnL gdyby cena dobiła aktualny stop

Trailing:

- `LIVE_ATR_TRAILING_ENABLED` — dystans traila od aktualnego ATR pary, nie od ATR zamrożonego na wejściu (brak świeżego ATR = fail-open na wartość z sygnału)
- `TRAILING_MIN_UPDATE_INTERVAL_SEC` (5s) — pierwsza aktywacja od razu, kolejne zacieśnienia nie co tick
