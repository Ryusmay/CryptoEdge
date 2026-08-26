# CryptoEdge v19.25.14

V2 częściej — setup dnia, nie tygodnia. Bez drugiego wejścia na ten sam impuls.

## Co się zmienia
- Touch 15m: pasmo **0.382–0.618** (było 0.5–0.618). Płytsza korekta też jest retestem.
- Reclaim nadal przez **0.5** — nie wchodzimy w spadający nóż.
- Lookback 15m: **12** barów (3h), było 8 (2h).
- Impuls ważny **48h** (było 36h).

Config: `DAYTRADING_V2_FIB_ZONE_NEAR/FAR/RECLAIM`, `DAYTRADING_V2_15M_LOOKBACK`, `IMPULSE_MAX_AGE_BARS=48`.

## Czego nie ruszamy
`MAX_ENTRIES_PER_SWING=1`, cooldowny, SL vs koszt 3.5×, 4h min_agree=2.
Sufit strategii i tak to ~1 impuls 1h / kilka godzin na parę — 10 wejść/dzień na BTC wymagałoby innego silnika.

Cel: major ~1+/dzień, portfel (top płynnych) regularnie w ciągu dnia, nie cisza przez tydzień.
