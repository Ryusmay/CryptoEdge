# CryptoEdge v17.20 — Intraday Quant/Risk Hardening (PAPER)

## Zmiany wykonane

- Poprawiono jednostki orderbooka BloFin: rozmiar kontraktowy jest konwertowany
  przez `contractValue` do base asset przed obliczeniem depth, VWAP i impact.
- Daytrading otrzymał własną bramkę ryzyka; nie podlega już swingowemu
  `REQUIRE_PRIMARY_STRATEGY`/MTF, ale wymaga potwierdzonego setupu 4H/1H–15m–5m,
  natywnych danych BloFin i zamkniętych świec.
- Reżim rynku jest obliczany również w ścieżce daytrading i przekazywany do Risk
  Engine. W PANIC wymagane jest strength >= 0.75, a rozmiar jest ograniczony do 25%.
- Dynamiczna korelacja używa teraz szeregu log-returns z zamkniętych świec 5m,
  zamiast kolejnych snapshotów ceny podczas skanowania.
- Expected Net R daytradingu jest prawdopodobieństwowym expectancy cyklu
  TP1/TP2/BE, a nie średnią targetów. Funding używa horyzontu 6h.
- Do czasu kalibracji OOS model jest jawnie oznaczony `PRIOR_ONLY`, a pozycja ma
  mnożnik rozmiaru maksymalnie 0.50. Minimalny Net R 0.05 odpowiada nowej,
  prawidłowej skali expectancy po kosztach.
- Pivot points, potwierdzona struktura, Viper i Fibonacci działają jako confluence:
  struktura może wyznaczyć SL, bliska bariera blokuje trade, a TP1 nie jest
  ustawiany bez uwzględnienia oporu/wsparcia. Fibo nie tworzy kierunku sygnału.
- Dodano dwustopniową invalidation pozycji na dwóch różnych zamkniętych świecach
  5m, soft time-stop 6h oraz bezwarunkowy hard time-stop 10h.
- Dodano zdarzeniowy replay daytradingu: decyzja na zamknięciu t, fill na open
  t+1 i konserwatywne `stop-first`, gdy SL i TP występują w tej samej świecy.
- Dodano testy prefix-invariance (look-ahead), recursive stability oraz purged
  walk-forward z embargo.
- Usunięto sprzeczne duplikaty ustawień, w tym błędne 5% maintenance margin.
- LIVE execution pozostaje wyłączony (`LIVE_EXECUTION_ENABLED = False`).

## Walidacja wykonana

- 146/146 testów jednostkowych i regresyjnych: OK.
- Dodane regresje dla units contractValue, funding 6h, expectancy prior,
  invalidation, hard time-stop, purged split, look-ahead i next-open replay.

## Nadal wymaga danych statystycznych

- Priory `P(TP1)=0.55` i `P(TP2|TP1)=0.45` są hipotezami PAPER, nie wynikiem.
- Należy zebrać co najmniej 100–200 niezależnych transakcji na setup/score-bin;
  30 obserwacji jest wyłącznie minimalnym progiem technicznym.
- Wymagany jest purged walk-forward obejmujący trend, range, panic/flash-crash,
  różne poziomy funding i płynności oraz osobne wyniki per symbol/regime.
- Viper na OHLCV jest estymacją profilu wolumenu, nie rzeczywistą mapą zleceń
  oczekujących; nie wolno interpretować go jako pełnego orderbooka.
- Przed LIVE trzeba zweryfikować fee tier konta, realne fill/partial-fill,
  latencję, cancel/replace, liquidation tiers i zachowanie przy suspension/delisting.
