# Granice modułów CryptoEdge

## Kierunek zależności

```text
apps ──> services ──> strategy ──> domain
  │          │            │
  │          ├──> risk ───┤
  │          ├──> execution ─────> domain
  │          └──> portfolio ─────> domain
  ├──> infrastructure ── implementuje porty
  └──> telemetry

market_data ──> domain
replay ──> domain + porty execution/market_data
frontend ──HTTP/WS──> apps/api
```

Zależność może wskazywać do środka, nigdy z domeny na zewnątrz. Adapter zna
port, ale port nie zna adaptera.

## Kontrakty

| Moduł | Odpowiada za | Przyjmuje | Zwraca/emituje | Nie może |
|---|---|---|---|---|
| `domain` | typy, identyfikatory, niezmienniki | wartości | niemutowalne modele | czytać config, plików, sieci lub UI |
| `market_data` | dane BloFin/Binance/CoinGecko, closed bars, walidację | porty feedów i zegar | `MarketSnapshot` | składać zleceń lub oceniać setupów |
| `strategy` | trend, reversal, confluence, timing i score | `MarketSnapshot` | `StrategyDecision` | znać saldo, tryb LIVE i executor |
| `risk` | Expected Net R, sizing, open-risk, cluster/daily limits | kandydat i snapshot portfela | `RiskDecision` | zmieniać pozycję lub zlecenie |
| `execution` | submit/accept/fill/cancel, SL, reduce-only, reconcile | `OrderIntent` | zdarzenia zleceń i `Fill` | generować sygnałów lub księgować PnL |
| `portfolio` | lifecycle pozycji, PnL, fees/funding, ekspozycję | `Fill`, mark/funding event | `PortfolioSnapshot` | pobierać danych z giełdy samodzielnie |
| `replay` | historyczny zegar i model egzekucji | historyczne dane | te same zdarzenia co runtime | mieć kopii strategii albo risk engine |
| `telemetry` | append-only audit i health | zdarzenia domenowe | logi, metryki, projekcje UI | sterować handlem |
| `services` | kolejność przypadków użycia | porty i moduły | wynik operacji/zdarzenia | zawierać wskaźników i reguł sizingu |
| `infrastructure` | REST/WS, config, pliki, persistence | porty aplikacji | implementacje adapterów | przenosić decyzji biznesowych |
| `apps` | składanie zależności i lifecycle procesu | ustawienia startowe | uruchomiona aplikacja | zawierać strategii |
| `frontend` | prezentacja i komendy użytkownika | DTO API | intencje użytkownika | importować Python lub być źródłem stanu |

## Wspólny pipeline

`DecisionPipeline.evaluate(snapshot, portfolio_snapshot)` jest jedynym publicznym
wejściem do decyzji handlowej. Kolejność jest stała:

```text
walidacja snapshotu
  -> StrategyDecision
  -> Expected Net R i RiskDecision
  -> OrderIntent albo jawne odrzucenie
  -> telemetry event
```

`apps/runtime.py` i `apps/replay.py` muszą importować dokładnie ten sam
`DecisionPipeline`. Różnią się tylko implementacją `Clock`, `MarketDataPort` i
`ExecutionPort`.

## Mapowanie obecnego kodu

| Obecny plik/obszar | Docelowy moduł | Uwagi migracyjne |
|---|---|---|
| `v2_market_snapshot.py`, `price_layers.py`, `order_models.py` | `domain/` | najpierw stabilne typy; stare importy przez re-export |
| `data_feeder.py`, `blofin_feed.py`, `blofin_ws.py`, `binance_feed.py`, `binance_ws.py`, `market_store.py`, `warmup.py` | `market_data/` + `infrastructure/exchanges/` | oddzielić port od klienta REST/WS i usunąć globalny STORE z pipeline |
| `market_context.py`, `external_confirmation.py`, `perp_context.py` | `market_data/context/` | wynik w snapshot, nie boczny globalny odczyt |
| `daytrading_engine_v2.py`, `reversal_engine.py`, `trend_continuation.py`, `setup_quality.py`, `expected_net_r.py`, wskaźniki | `strategy/` | czyste wejście snapshot -> decyzja; swing zachować jako nieaktywny plugin |
| `risk_manager.py`, `portfolio_risk.py`, `adaptive_size.py`, `entry_reservations.py` | `risk/` | jeden właściciel approval, sizing i projected loss |
| `blofin_executor.py`, `replay_execution.py`, `protection.py`, `position_reconciler.py`, `restart_recovery.py` | `execution/` | wspólna maszyna stanów i idempotency key |
| `paper_trader.py`, `accounting.py`, `performance_metrics.py`, `funding_model.py` | `portfolio/` i adapter `execution/paper/` | rozdzielić fill simulation od księgi pozycji |
| `historical_replay.py`, `walk_forward*.py`, `daytrading_backtester.py` | `replay/` | runner bez kopii reguł strategii |
| `decision_telemetry` w logice, `logger.py`, `event_bus.py`, `edge_monitor.py`, raporty | `telemetry/` | korelacja session/decision/order/position ID |
| `runtime.py`, `engine_router.py`, `control_center.py` | `services/` + `apps/runtime.py` | runtime zostaje composition root, router traci reguły strategii |
| `engine_api.py`, `grpc_service.py` | `apps/api.py` + `infrastructure/api/` | API publikuje projekcję, nie obiekty wewnętrzne |
| `config.py`, `settings_store.py`, `secrets_store.py`, `instrument_registry.py` | `infrastructure/` | config wstrzykiwany jako typowane settings |
| `pyside6_ui.py`, `native_ui.py` | warstwa legacy UI | zamrożona awaryjnie, bez importów z domeny |
| `frontend/` | pozostaje osobnym frontendem | wyłącznie wygenerowane DTO i HTTP/WS |

## Reguły importów sprawdzane automatycznie

- kod w `cryptoedge/domain` nie importuje `config`, klientów sieciowych,
  PySide/Tk ani żadnego innego pakietu `cryptoedge` poza `domain`;
- kod Python nie importuje `frontend`;
- frontend nie odwołuje się do plików `.py` ani ścieżek `cryptoedge/`;
- runtime i replay używają wspólnego `DecisionPipeline`.

Test celowo obejmuje nową przestrzeń `cryptoedge/`. Stare moduły głównego
katalogu są długiem migracyjnym, ale nie mogą być kopiowane do nowej struktury
z zachowaniem dawnych złych zależności.
