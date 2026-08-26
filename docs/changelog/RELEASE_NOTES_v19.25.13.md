# CryptoEdge v19.25.13

V2: impuls 1h zamiast ostatniego swingu, TP1 bez ścinania szumem S/R, trigger 15m w paśmie 0.5–0.618.

Na cache BloFin (BTC 10d) funnel 15m przed/po:

| reject / PASS | 19.25.12 | 19.25.13 |
|---|---|---|
| PASS | 0.1% | **2.8%** (~18 unikalnych / 10d) |
| `SWING_DIRECTION_MISMATCH` | 40% | **0** (zastąpione impulsem) |
| `TP1_TOO_SMALL_VS_RISK` | 33% | **0** |
| `V2_NO_15M_TRIGGER` | 0.8% | **17%** (wreszcie bramka) |
| `V2_SL_TOO_TIGHT_VS_COST` | 27% | 18% (zostawione celowo) |
| `V2_SWING_ALREADY_TRADED` | ~0 | 51% (ten sam impuls, max 1 wejście — OK) |

## 1. Impuls, nie korekta
`find_last_confirmed_swing(..., prefer_direction=)` — Fibo/SL z ostatniego swingu **zgodnego z 4h**. Nowszy swing przeciwny = retest, nie reject.
Brak impulsu → `V2_NO_IMPULSE_SWING`. Cena poza startem → `V2_IMPULSE_BROKEN`. Koniec starszy niż `DAYTRADING_V2_IMPULSE_MAX_AGE_BARS` (36) → `V2_IMPULSE_TOO_OLD`.

## 2. TP1
Najbliższy S/R tylko gdy ≥ 0.6R od ceny. Blższy szum ignorowany, TP1 = 1R.
SL musi być po właściwej stronie ceny (`abs()` już nie przepuszcza). TP2 zawsze za TP1 (inaczej fallback 2R).

## 3. 15m
Pasmo 0.5–0.618 naprawdę używane (`zone_far` nie jest martwe). Reclaim nadal po stronie `zone_near`.

Bez zmian: 4h min_agree, 1D off, cooldowny, 1 wejście/swing, filtr kosztów SL 3.5×.
