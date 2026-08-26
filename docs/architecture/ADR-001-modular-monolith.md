# ADR-001: Modularny monolit CryptoEdge

- Status: zaakceptowany
- Data: 2026-08-25
- Zakres: runtime, replay, API i interfejs użytkownika

## Kontekst

CryptoEdge wyrósł z zestawu współpracujących plików umieszczonych w katalogu
głównym. Strategia, pobieranie danych, ryzyko, egzekucja, księgowość, replay,
telemetria i UI są obecnie połączone importami oraz współdzielonym stanem. To
utrudnia ustalenie właściciela danych, izolowanie awarii i zagwarantowanie, że
runtime i replay podejmują decyzje według identycznych reguł.

Mikroserwisy nie rozwiązują tego problemu. Dodałyby sieć, serializację,
wdrożenia i awarie rozproszone do aplikacji, która nadal działa na jednej
maszynie i wymaga atomowej kontroli ryzyka.

## Decyzja

CryptoEdge będzie rozwijany jako **modularny monolit**. Pozostaje jedną
aplikacją Python z osobnym frontendem React/Tauri, lecz kod Python otrzymuje
jawne granice:

```text
cryptoedge/
  apps/             # composition roots: runtime, replay, API
  domain/           # czyste typy i niezmienniki biznesowe
  market_data/      # źródła, walidacja i budowa snapshotu
  strategy/         # sygnały i setupy; bez egzekucji
  risk/             # zgoda, sizing i limity portfela
  execution/        # lifecycle zleceń, fill i reconciliation
  portfolio/        # pozycje, ekspozycja i księgowość
  replay/           # zegar historyczny i modele fill
  telemetry/        # zdarzenia, metryki i diagnostyka
  services/         # przypadki użycia i orkiestracja
  infrastructure/   # adaptery plików, konfiguracji i giełd
frontend/           # React/TypeScript/Tauri; klient lokalnego API
```

Runtime i replay mają oddzielne composition roots, lecz importują ten sam
`cryptoedge.services.decision_pipeline.DecisionPipeline`. Źródło danych, zegar
i executor są wstrzykiwanymi adapterami. Strategia i ryzyko nie rozpoznają,
czy działają w LIVE, PAPER czy replay.

## Reguły zależności

1. `domain` nie importuje konfiguracji, sieci, giełd, UI ani pozostałych
   warstw CryptoEdge.
2. `strategy` zależy od `domain`, a nie od feedów, executorów lub UI.
3. `risk` zależy od modeli domenowych i portfela przekazanego jako snapshot.
4. `execution` realizuje `OrderIntent`; nie tworzy sygnałów.
5. `portfolio` aktualizuje stan wyłącznie z filli i zdarzeń księgowych.
6. `services` orkiestruje moduły, ale nie zawiera matematyki strategii.
7. `infrastructure` implementuje porty zdefiniowane wewnątrz aplikacji.
8. `apps` są jedynymi composition roots i miejscem składania zależności.
9. Frontend komunikuje się wyłącznie przez lokalne API i nie importuje kodu
   Python.

Do komunikacji między modułami używamy typowanych obiektów (`MarketSnapshot`,
`StrategyDecision`, `RiskDecision`, `OrderIntent`, `Fill`, `PositionSnapshot`),
a nie słowników o zależnym od ścieżki zestawie kluczy.

## Własność stanu

- market data: świece, quote, order book i świeżość danych;
- strategy: brak stanu rachunku; dopuszczalny wyłącznie jawny stan setupu;
- risk: limity, cooldown i stan ACTIVE/REDUCE_ONLY/HALTED;
- execution: zlecenia, protective orders, partial fills i reconciliation;
- portfolio: pozycje, PnL, opłaty, funding, MFE/MAE i ekspozycja;
- telemetry: append-only zapis przebiegu decyzji;
- UI: wyłącznie projekcja stanu, nigdy źródło prawdy.

## Konsekwencje

### Korzyści

- jedna ścieżka decyzyjna runtime/replay;
- testy modułów bez sieci i bez uruchamiania UI;
- awaria UI nie wpływa na handel;
- łatwiejsze wskazanie właściciela błędu i stanu;
- możliwość wymiany BloFin/PAPER/replay executora bez zmiany strategii.

### Koszty i ryzyka

- przez kilka wydań będą istnieć adaptery zgodności ze starymi modułami;
- samo przenoszenie plików może stworzyć pozorną modularność, dlatego granice
  są sprawdzane automatycznie;
- migracja stanu pozycji i zleceń wymaga testów restart/reconciliation oraz
  porównania PAPER/replay przed usunięciem starej ścieżki.

## Odrzucone alternatywy

- **Przepisanie od zera** — za duże ryzyko utraty zabezpieczeń i parytetu.
- **Mikroserwisy** — nieuzasadniony koszt operacyjny i nowe tryby awarii.
- **Tylko nowe katalogi** — nie usuwa współdzielonego stanu ani złych importów.
- **Osobny silnik replay** — uniemożliwia wiarygodne testowanie runtime.

## Kryterium zakończenia

Migrację uznajemy za zakończoną dopiero, gdy wszystkie composition roots używają
nowych modułów, test parytetu runtime/replay przechodzi na identycznym strumieniu
snapshotów, a stare pliki są adapterami bez logiki lub zostały usunięte po co
najmniej jednym stabilnym wydaniu PAPER.
