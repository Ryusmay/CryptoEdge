# Plan migracji do modularnego monolitu

## Zasady prowadzenia migracji

1. Nie przepisujemy strategii i nie stroimy parametrów podczas refaktoryzacji.
2. Każdy etap ma test charakterystyki oraz porównanie wyniku przed/po.
3. Najpierw powstaje nowy kontrakt, potem adapter starego kodu, a dopiero na
   końcu przenoszona jest logika.
4. Stare publiczne importy działają przez re-export przez co najmniej jedno
   stabilne wydanie PAPER.
5. Nie usuwamy starej ścieżki, dopóki runtime i replay nie osiągną parytetu.

## Etap 0 — zamrożenie punktu odniesienia

- zapisać wersję, ustawienia oraz hash danych replay;
- uruchomić pełne testy i reprezentatywny replay;
- zapisać sygnały, odrzucenia, transakcje, fill, PnL i metryki kosztów;
- dodać test granic architektury.

**Warunek wyjścia:** powtarzalny baseline i zielone testy.

**Rollback:** brak zmian runtime; usunięcie samych nowych dokumentów/pakietów.

## Etap 1 — domena i kontrakty

- utworzyć modele `MarketSnapshot`, `StrategyDecision`, `RiskDecision`,
  `OrderIntent`, `OrderEvent`, `Fill`, `PositionSnapshot`;
- wprowadzić identyfikatory session/decision/order/position;
- zapewnić translatory stary słownik <-> model;
- re-exportować nowe typy ze starych modułów.

**Warunek wyjścia:** modele nie wykonują I/O; round-trip adapterów nie traci pól.

**Rollback:** przełącznik zgodności kieruje wszystkie wywołania do starych typów.

## Etap 2 — wspólny DecisionPipeline

- opakować istniejący DayTrading V2 w `StrategyPort`;
- opakować istniejący RiskManager w `RiskPort`;
- zbudować `services/decision_pipeline.py`;
- podłączyć runtime i replay w trybie shadow, zapisując obie decyzje;
- porównywać kod decyzji, kierunek, SL/TP, size i powód odrzucenia.

**Warunek wyjścia:** zero niewyjaśnionych różnic na baseline replay i PAPER shadow.

**Rollback:** composition roots wracają do starego routera jednym przełącznikiem.

## Etap 3 — market data

- wydzielić porty clock, candles, quote, orderbook i external confirmation;
- przenieść BloFin/Binance/CoinGecko do adapterów infrastructure;
- budować jeden snapshot z jawnie zamkniętych świec;
- usunąć bezpośrednie użycie globalnego `STORE` z pipeline;
- dodać fresh/stale/partial/missing status dla każdej warstwy danych.

**Warunek wyjścia:** runtime i replay produkują równoważny snapshot dla tych
samych świec; test look-ahead przechodzi.

**Rollback:** `LegacyMarketDataAdapter` deleguje do dotychczasowego feedera.

## Etap 4 — risk i portfolio

- przenieść approval, sizing, open-risk, cluster risk, projected daily loss i
  cooldown do `risk`;
- utworzyć jedną księgę pozycji aktualizowaną wyłącznie przez fille;
- ujednolicić partiale, fees, funding, realized/unrealized PnL i MFE/MAE;
- wprowadzić jawne stany ACTIVE/REDUCE_ONLY/HALTED/RECONCILIATION_REQUIRED.

**Warunek wyjścia:** zgodność księgowa per `position_id`, brak podwójnego OPEN,
brak przekroczenia budżetu po uwzględnieniu projected loss.

**Rollback:** mirror ledger porównuje nową księgę ze starym PaperTrader, który
pozostaje źródłem wykonawczym do zakończenia etapu.

## Etap 5 — execution

- wspólna maszyna decision -> submit -> accepted -> partial/full fill -> cancel;
- adaptery PAPER, replay i BloFin realizują ten sam `ExecutionPort`;
- ochronny exchange-side SL powstaje po fillu;
- wdrożyć idempotency, orphan cancellation i pełne startup reconciliation;
- rozdzielić strategy/decision/submitted/fill/mark price.

**Warunek wyjścia:** testy restartu z pozycją, orphan orderem, partialem i
brakującym SL; LIVE failure wymusza REDUCE_ONLY zamiast pustego stanu.

**Rollback:** BloFin adapter może delegować do starego executora; nowe wejścia
blokowane, ale redukcja ryzyka pozostaje dostępna.

## Etap 6 — replay, telemetry i API

- replay zmienia wyłącznie data source, clock i executor;
- wspólny event schema zasila telemetry oraz projekcje UI;
- API wystawia stabilne DTO i health per moduł;
- frontend przestaje czytać pliki logów i stan procesów bezpośrednio.

**Warunek wyjścia:** parytet decyzji runtime/replay, pełny lineage oraz UI
działające po restarcie niezależnie od silnika.

**Rollback:** stary endpoint API pozostaje pod wersją compatibility do następnego
wydania.

## Etap 7 — wyłączenie legacy

- przez minimum jedną dłuższą sesję PAPER zbierać różnice shadow;
- oznaczyć stare API i importy jako deprecated;
- przenieść narzędzia jednorazowe do `tools/`;
- usunąć logikę ze starych wrapperów, a następnie same wrappery;
- zaktualizować instalator, pakowanie i instrukcję operacyjną.

**Warunek wyjścia:** brak importów produkcyjnych ze starych modułów, pełne testy,
replay walk-forward, test restart/reconciliation i zaakceptowany raport PAPER.

## Bramy jakości po każdym etapie

- wszystkie dotychczasowe testy oraz test granic są zielone;
- brak zmiany liczby/decyzji transakcji, jeśli etap nie zmienia strategii;
- brak nowych importów w przeciwnym kierunku;
- brak nowego globalnego mutowalnego stanu;
- test awarii adaptera dowodzi fail-closed lub REDUCE_ONLY;
- changelog wskazuje migrację i sposób rollbacku.

## Procedura bezpiecznego rollbacku wydania

1. Zatrzymać tworzenie nowych zleceń i przejść do REDUCE_ONLY.
2. Wykonać reconciliation pozycji, zleceń i protective orders.
3. Zachować ledger i telemetry bieżącej sesji.
4. Przełączyć composition root na ostatni zgodny adapter; nie kopiować starego
   pliku stanu na ślepo.
5. Ponownie wykonać reconciliation przed ACTIVE.
6. Nie zamykać pozycji użytkownika ani nie anulować obcych zleceń.

Rollback kodu nie jest rollbackiem stanu giełdy. Stan venue pozostaje
autorytatywny i zawsze wymaga uzgodnienia.
