# CryptoEdge v17 — audyt quant/risk (17.08.2026)

## Werdykt

Projekt nadaje się wyłącznie do dalszych testów PAPER. Nie ma jeszcze podstaw do
uruchomienia LIVE ani do twierdzenia, że expectancy jest dodatnie. Baseline miał
72 zielone testy, lecz zawierał materialny look-ahead MTF, niespójne księgowanie
slippage w backteście, błędną segmentację wyników reversal/trend i nieskuteczny
walk-forward bez historii rozgrzewkowej.

Po poprawkach: 76/76 testów przechodzi oraz wszystkie zmienione moduły kompilują
się. Nie uruchamiano zleceń LIVE.

## Naprawione błędy krytyczne

1. **MTF look-ahead** — API zapisuje timestamp otwarcia świecy. Backtester
   dopuszczał pełny close/high/low bieżącej świecy 4h/1d od chwili jej otwarcia.
   Okna sygnałowe uwzględniają teraz świecę dopiero po `open + timeframe`, a
   decyzja z drive-bar powstaje na jego zamknięciu i jest wykonywana na kolejnym
   open.
2. **Walk-forward bez warm-up** — osobne TRAIN/VAL/TEST usuwały EMA200/ATR history.
   Dodano causal pre-roll per TF (80 barów 15m/1h, 220 barów 4h/1d) i jawny zakres,
   w którym wolno otwierać pozycje. Pre-roll służy tylko wskaźnikom.
3. **Koszty backtestu** — entry zawierał adverse slippage, po czym close odejmował
   ponownie dwustronny slippage (trzy nogi kosztu). Teraz entry i exit są osobno
   repriced po jednej stronie, a maker/taker fee jest księgowane niezależnie.
4. **Podwójny market impact** — VWAP impact był jednocześnie `slippage` i
   `market impact` w Expected Net R. Zmierzony/modelowany VWAP jest teraz jednym
   składnikiem; slippage pozostaje niezależnym buforem wykonania.
5. **Reversal raportowany jako trend** — `BTPosition` nie przechowywał pola
   `engine`, a trade log go nie emitował. Statystyki `by_engine` są teraz
   rozdzielane prawidłowo.
6. **Risk-after-fill i konsola** — ścieżka była podatna na brak pomocniczych metod
   w adapterze/test double oraz na wyjątek kodowania znaku strzałki. Dodano
   bezpieczne obliczenia zastępcze i logi ASCII, bez pomijania invariant check.
7. **Regresje** — dodano testy zamknięcia świec HTF, kosztu impact/slippage,
   zachowania `engine` i causal warm-up walk-forward.

## Ocena architektury rynkowej

- BloFin jest primary w głównym event backtesterze przy `--data-source blofin`;
  Binance pełni osobną rolę confirmation. Tryb `auto` może jednak fallbackować
  do Binance — raport musi wtedy jawnie oznaczać degradację i nie może być
  traktowany jako walidacja BloFin.
- Stary `backtest_exits.py`, oparty na Binance spot i uproszczonym sygnale,
  został usunięty, ponieważ nie testował aktualnego CryptoEdge ani parity LIVE.
- CoinGecko jest kontekstem przekrojowym, nie źródłem ceny egzekucyjnej — to
  właściwa separacja. Brakuje jednak historycznej rekonstrukcji tego kontekstu w
  OOS, więc część LIVE nie jest odtworzona.
- Fibonacci pozostaje confluence/planem poziomów, nie samodzielnym triggerem.
- PUMP_CHASE blokuje pogoń w kierunku ekstremum i tworzy watch kierunku
  przeciwnego; samo wejście reversal nadal wymaga potwierdzenia.
- PANIC ogranicza trend i pozostawia reversal aktywny z niższym sizingiem.
- Registry BloFin blokuje instrumenty o stanie innym niż tradable, ale ryzyko
  suspension po otwarciu wymaga ćwiczenia operacyjnego i monitoringu alarmów.

## Ryzyka nadal niewalidowane / nieusuwalne samą recenzją kodu

1. **Expected R nie jest jeszcze estymatorem statystycznym.** Domyślna krzywa
   strength→R ma `n=0`; reversal buduje gross R z planu TP, nie z empirycznych
   prawdopodobieństw dojścia do TP/SL/trail. Filtr kosztowy jest matematycznie
   spójniejszy, ale edge musi pochodzić z OOS/PAPER.
2. **LIVE↔BT nie jest pełnym lustrem.** Event BT upraszcza pełny Signal Engine,
   Soft Pass, stan Reversal Shadow/Confirmation, kolejkę zleceń, partial fills,
   amend/cancel latency, CoinGecko history i historyczny L2.
3. **Intrabar ambiguity.** OHLC nie odtwarza kolejności high/low. SL-first jest
   konserwatywne, lecz potrzebny test na 1m/tick replay dla barów dotykających
   jednocześnie SL i TP/trailing oraz gap-through-stop.
4. **Funding.** Historia jest ograniczona limitem API; brak eventu nie powinien
   być interpretowany jako zerowy koszt. Zweryfikować interwały per instrument i
   sign convention na realnych settlementach BloFin.
5. **Maker/taker mix.** Model zakłada taker. To konserwatywne dla limit-maker,
   lecz wymagane są realne fill ratios, cancel rates i adverse selection.
6. **L2/impact.** Snapshot book nie mierzy resiliency ani kolejki. Model bar-volume
   musi być skalibrowany na zapisanych snapshotach BloFin i realnych fillach.
7. **Margin/liquidation.** Fallback jest przybliżeniem isolated, podczas gdy
   konfiguracja używa cross. Do PAPER potrzebny stress całego portfela na mark
   price, maintenance tiers, funding i skorelowany gap; exchange liquidation
   price ma pierwszeństwo po otwarciu.
8. **Risk of Ruin.** Monte Carlo bazuje na iid/bootstrap trade R i stałym typowym
   risk%. Wymagany block bootstrap po reżimach, wspólne szoki korelacyjne,
   zmienne koszty i co najmniej kilkaset niezależnych OOS/PAPER transakcji.
9. **Overfitting.** Nie stroić progów na foldach TEST. Zamrozić konfigurację,
   prowadzić rejestr eksperymentów i użyć purged/embargoed walk-forward z
   korektą multiple testing (np. Deflated Sharpe/PBO).
10. **Black swan/operations.** Przetestować stale feed, rozjazd mark/index/last,
    BloFin outage, Binance-only anomaly, flash gap, ADL, partial protection,
    delisting/suspension i restart w trakcie fill/cancel.

## Minimalny protokół PAPER przed LIVE

- Zamrozić parametry i wersję; co najmniej 8–12 tygodni obejmujących różne reżimy.
- Zapisywać decision timestamp, wersję danych, zamknięte świece, oba venue,
  book snapshot, planowany/wykonany notional, fee, funding i każdy reject.
- Raportować oddzielnie Trend Primary, Trend Soft, Reversal confirmed i Shadow;
  symbol, reżim, long/short i bucket płynności.
- Kryteria muszą być ustalone z góry: dodatni OOS net expectancy z przedziałem
  ufności, stabilność per fold/reżim, limity DD/RoR oraz brak krytycznych driftów.
- LIVE dopiero po kontrolowanym shadow→micro-size rollout i testach kill switch.

## Weryfikacja

- Baseline: 72/72 testów.
- Po zmianach: 76/76 testów.
- `py_compile`: event_backtester, walk_forward, expected_net_r, paper_trader — OK.
- Sieciowego walk-forward na danych BloFin nie uruchomiono w tym audycie; wynik
  statystyczny pozostaje obowiązkowym kolejnym etapem, nie założeniem.
