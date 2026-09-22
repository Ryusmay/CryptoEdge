# Audyt: jev-trader jako implementacja referencyjna dla CryptoEdge

Stan: 2026-09-22, CryptoEdge v20.73.0 (`2dc1baa`), jev-trader `main` (klon płytki, ~1,2 tys. linii TS w `src/`).

jev-trader jest tu **źródłem pomysłów, nie wzorcem do kopiowania**. Nie dodajemy go jako zależności i nie przenosimy stacku (Bun/TS, Monad, Kuru). Każde twierdzenie niżej ma plik i linię albo jest oznaczone jako UNKNOWN. Cytaty z jev-trader odnoszą się do jego repozytorium (`src/*.ts`); cytaty z CryptoEdge — do tego repo.

Co to jest jev-trader: demo pod tweet. Jeden rynek (MON-USDC na on-chainowym orderbooku Kuru), jedna decyzja buy/sell co blok Monad (~300 ms) i co blok jedno zlecenie post-only limit tick wewnątrz spreadu (`README.md`, `CLAUDE.md` „core message”). Horyzont to sekundy, a CryptoEdge to swing/intraday na świecach 15m/1H dla ~300 symboli. **Większość mechaniki mikrosekundowej jest tu NOT_APPLICABLE.** Wartość jev-trader leży w kilku czystych wzorcach granic, nie w wydajności.

---

## Część 1A — Hot path / cold path

### jev-trader: jak jest naprawdę (kod, nie README)

Pętla to `Trader.onBlock` (`src/trader.ts:85-131`).

| Krok | Na ścieżce? | Dowód |
|---|---|---|
| potwierdzenia wcześniejszych tx (receipts) | **poza** — `.then`, bez `await` | `trader.ts:87,134-138`; `market.ts:155-173` |
| odświeżenie fee/margin/vault co 200 bloków | **poza** — bez `await` | `trader.ts:88` |
| blok przyszedł, gdy poprzedni trwa → `late`, `hold`, pomiń | ochrona przed zatorem | `trader.ts:89-93` |
| odczyt orderbooka (1× `eth_call`) | **hot** (`await`) | `trader.ts:97`; `market.ts:112-114` |
| poll printów/fili (`eth_getLogs`) | **poza** — bez `await` | `trader.ts:102`; `trades.ts:361-392` |
| `model.decide(state)` | **hot** (`await`) | `trader.ts:104` |
| limit pozycji (z resting + in-flight) | hot, lokalnie | `trader.ts:108,202-217` |
| podpis + `eth_sendRawTransaction`, powrót po hashu | **hot**; bez estimateGas/nonce RPC | `market.ts:135-152,176-182` |
| zapis zdarzenia `appendFileSync("data/events.jsonl")` | **hot — synchroniczny I/O w pętli** | `trader.ts:291` (wołane z `emit`, `trader.ts:125`, przed `busy=false`) |
| broadcast SSE + `console.log` | **hot — synchronicznie** w callbacku `onEvent` | `index.ts:434-441`; `server.ts:529` |
| fille, PnL, gas | cold — aplikowane przy kolejnych blokach | `trader.ts:140-169,245-262` |

Block feed zawsze składa burst nagłówków do **najnowszego** bloku i nigdy nie przetwarza nieaktualnego (`chain.ts:463-471`). To dobry wzorzec.

**Werdykt:** separacja jest realna dla potwierdzeń, filli i odświeżeń. README jej nie przecenia, ale **sam jev-trader ją łamie**: synchroniczny zapis do pliku, broadcast i `console.log` wykonują się wewnątrz pętli, przed zwolnieniem `busy`. Nie ma kolejki ani backpressure. Jest też mutacja historii: spóźnione fill/quote nadpisują wcześniejsze zdarzenia w miejscu (`trader.ts:146-147,165-166`).

### CryptoEdge: jak jest naprawdę

- Jeden wątek `bot` (`app.py:1039` → `bot_loop` `app.py:391`) robi wszystko: pełny skan co 30 s (`config.py:73`) i „fast tick” co 1 s dla otwartych pozycji (`app.py:443-474`). Nie ma asyncio.
- Wątki WS (`blofin_ws.py:179`, `binance_ws.py:81`) tylko wypełniają cache (`market_store.py`). Decyzje są sterowane zegarem, nie zdarzeniem.
- UI działa w trybie pull: REST `/api/status` (`frontend/src/api.ts`, `useEngine.ts:16-18`) plus WS `market_stream_server.py`, który co 1 s sam przebudowuje payload (`market_stream_server.py:99-110`). **Wątek `bot` nigdy nie czeka na UI.** To jest BETTER_THAN_JEV: tam broadcast leży na ścieżce pętli.

**Co blokuje ścieżkę decyzji i ochrony w CryptoEdge.** Kolejność od najgroźniejszej; skróty jak w raporcie agenta: PD = per decyzja, PC = raz na pełny skan.

1. **Ochrona pozycji dzieli wątek ze skanem.** SL/TP/trailing w paper są sprawdzane w fast ticku *między* pełnymi skanami (`app.py:443-474`). Pełny skan to sekwencyjna pętla po universum z REST (`daytrading_engine_v2.py:805-891`), wolna od:
   - token bucketu do 8 s (`rate_limiter.py:53-69`),
   - `time.sleep` do 30 s przy krótkim 429 (`blofin_feed.py:1091`),
   - uśpień CoinGecko 25+8n s (`data_feeder.py:66-72`) i `market_context` 20 s (`market_context.py:64-67`).

   Na ten czas zarządzanie pozycjami stoi. Rzeczywisty czas trwania skanu: **UNKNOWN**, instrumentowany jest tylko łączny czas (`app.py:564-578`). *To jest prawdziwy „hot path” CryptoEdge — wyjścia, nie wejścia.*
2. **Wątek UI mutuje stan ryzyka.**
   - `engine_api._candidates` woła `risk.can_open_position(dict(row))` dla do 50 wierszy przy każdej delcie streamu co 1 s (`engine_api.py:202,403`).
   - `can_open_position` robi `_check_new_day()` (`risk_manager.py:214`) i **dopisuje ceny do silnika korelacji** (`eng.update_price`, `risk_manager.py:441`). Może też wołać REST `fetch_position_tiers` na wspólnym `PUBLIC_BUCKET` (`risk_manager.py:48-55,664`).

   Otwarte okno desktopu zmienia więc dane, na których bot potem decyduje, i zużywa limit REST bota.
3. **Synchroniczne zapisy plików w wątku `bot`:**
   - `decision_telemetry._append` (open 'a' pod globalnym lockiem, `decision_telemetry.py:28-46`) — PD;
   - CSV loggera (`logger.py:181,201,229`);
   - `save_state` dużego JSON z retry i `sleep(0.15)` (`logger.py:271-320`);
   - `ProtectionManager.save_state` wołane z trailing-SL **pod `trader.lock`** (`protection.py:284-294,453-467`; `paper_trader.py:1290`);
   - kalibracje przy każdym zamknięciu (`paper_trader.py:1676,1684`);
   - tee konsoli z flush pod lockiem dla każdego `print` (`console_capture.py:91-131`).

   To rząd milisekund, więc przy skanie REST jest drugorzędne, ale nie ma żadnego odcięcia.
4. **`event_bus.py`**: synchroniczny XADD z timeoutem 1 s w wątku `bot` (`event_bus.py:92`, wołany z `persist_cycle` `app.py:363-376`). Domyślnie wyłączony (`config.py:86`). Docstring twierdził „asynchronicznie” — **sprostowane w tym PR**.
5. ML/LLM: brak w kodzie (grep: 0). Baza danych: brak (0 sqlite/duckdb).

---

## Część 1B — MarketStateSnapshot

### jev-trader

`TradeState` (`model.ts:316-334`, budowany w `trader.ts:219-243`) jest mały, relatywny i czytelny. Zawiera:
- mid, `spreadBps`, `bookImbalance`, `depth` w pasmach 10/25/50 bps, top-5 poziomów,
- `returnsBps` 1/5/20/100,
- `trades` (count, buy/sell, **CVD**, VWAP, last side) i `recentTrades`,
- **`allowed: {buy, sell}`**, czyli ograniczenie ryzyka podane modelowi *na wejściu*.

Nie ma tam wersji schematu, timestampu stanu ani id.

### CryptoEdge

- `MarketSnapshot` (`cryptoedge/domain/models.py:56-115`) jest zamrożony, ma `snapshot_id`, `event_ts_ms`/`decision_ts_ms` i ochronę przed lookahead (`validate_closed_bars`). **BETTER_THAN_JEV jako kontrakt**, ale jest cienki: niesie tylko surowe `frames` + `ticker`.
- `order_book` i `funding` nie są wypełniane przez żaden adapter (`cryptoedge/market_data/legacy.py:24-42,67-73`).
- Wszystkie cechy są liczone ad-hoc w silnikach albo doklejane do dictu sygnału *po* decyzji. Przykłady: regime (`signal_engine.py:1384,1419`), perp_context OI/funding (`signal_engine.py:1443`), orderbook (`signal_engine.py:1764`).
- Istnieje prawie-duplikat `V2MarketSnapshot` (`v2_market_snapshot.py:41-60`).

Pokrycie sekcji z briefu:

| Sekcja | Status | Gdzie |
|---|---|---|
| OHLC, trend (EMA/ST/ADX), swingi, S/R, pivoty, Fibonacci | PARTIAL (ad-hoc) | `indicators_full.py`, `swing_structure.py:23-139` |
| RSI, MACD | PARTIAL | `indicators_full.py:31,194` |
| ROC, stochastic | MISSING | grep: 0 |
| ATR, realized vol, BB | PARTIAL | `indicators_full.py:226,269`; `regime_model.py:86-228` |
| relative volume, volume profile | PARTIAL | `indicators_full.py:433,596-600` |
| session VWAP | MISSING (jest tylko VWAP przejścia po książce) | `orderbook_impact.py:38` |
| spread, bid/ask, imbalance, depth | PARTIAL | `blofin_feed.py:1458-1515`; `venue_microstructure.py` |
| CVD, aggressive buys/sells, recent trades | MISSING | — (jev-trader ma: `trades.ts:413-426`) |
| OI, OI delta, funding | PARTIAL (tylko mnożnik rozmiaru po decyzji) | `perp_context.py:26-125`; `funding_model.py` |
| liquidations feed, L/S ratio | MISSING | — |
| BTC context, breadth, dispersion, korelacje, regime | PARTIAL (regime niewidoczny dla V2 `evaluate`) | `regime_model.py:120-374`; `dynamic_correlation.py` |
| portfolio/exposure | EXISTS poza snapshotem | `portfolio_risk.py:88-257` |
| execution conditions | PARTIAL, rozproszone | `config.py:555-556`; `expected_net_r.py`; `daytrading_engine_v2.py:670-675` |

### Proponowany minimalny stabilny kontrakt (NIE implementowany teraz)

Nie budować jednej wielkiej klasy. Proponuję trzy osobne wejścia decyzji:

1. **`MarketSnapshot`** (istnieje) + nowa opcjonalna sekcja `features: MarketFeatures` z `feature_schema_version` i podsekcjami (`price`, `momentum`, `volatility`, `volume`, `microstructure`, `derivatives`, `context`). Każde pole jest `Optional[float]`, bo brak ≠ 0 (AGENTS.md). Liczone **raz** w warstwie `market_data`, nie w strategii.
2. **`PortfolioSnapshot`**: osobny, bo zmienia się z innym rytmem i należy do risk. MODULE_BOUNDARIES już go nazywa, a kodu nie ma.
3. **`ExecutionAssumptions`**: osobny, **dodany w tym PR** (część 4).

Świadomie poza snapshotem rynku jest „strategy context” (głosy, konflikty, kandydaci). To *wyjście* strategii i wejście Brain, a nie stan rynku. Jego miejsce to lista `StrategyDecision`, którą Brain dostaje obok snapshotu.

Kolejność: najpierw `order_book`/`funding` w istniejącym snapshocie (adaptery już mają dane), potem `context.regime` przed `evaluate` (dziś liczony po). Każdy krok przez `tools/decision_parity.py`.

---

## Część 2 — Kontrakt decyzji

**jev-trader:** `Model.decide(TradeState) → Decision {action, probabilities, upIn10, latencyMs, inputTokens}` (`model.ts:336-347`). Ma zaletę: interfejs modelu to jedna metoda, a `MockModel` (deterministyczny, `model.ts:387-412`) i `JevModel` są wymienne. Wady:
- brak `decision_id`, wersji modelu i ważności;
- decyzja jest **mutowana po fakcie**: `decision.action = side`, gdy limit pozycji wymusi drugą stronę (`trader.ts:114`). Audyt „co model chciał, a co zrobiono” trzyma się tylko na fladze `capped` w quote.

**CryptoEdge:**
- `StrategyDecision` + `EntryCandidate` + `RiskDecision` + `OrderIntent` (`cryptoedge/domain/models.py`) są zamrożone, mają `decision_id`/`snapshot_id`/`expected_net_r`/`reasons`. **BETTER_THAN_JEV**.
- Produkcja nie używa ich jednak wcale. V2 zwraca dict (`daytrading_engine_v2.py:680-752`), a `DecisionPipeline._stamp_lineage` dokleja `decision_id`/`snapshot_id`/`decision_ts_ms` (`cryptoedge/services/decision_pipeline.py:39-61`).
- `strength` w V2 to stała 0.75 (`daytrading_engine_v2.py:712`). Nie ma pewności, wersji strategii ani ważności.

**Decyzja: NIE tworzyć `DecisionEnvelope`.** `StrategyDecision` jest tą kopertą. W tym PR rozszerzono go o opcjonalne pola:
- `strategy_version`, `model_id`, `confidence` (0–1),
- `valid_until_ms`, `market_version`,
- `execution: ExecutionAssumptions`.

Do tego doszła metoda `timing()` dla routera. Pola trafiają do legacy dict **tylko gdy są ustawione**, więc kształt sygnału i bramki bajtowe się nie zmieniają (`tests/test_execution_assumptions.py`).

Z listy briefu pozostałe pola już istnieją albo mają swoje miejsce:
- entry/SL/TP są w `EntryCandidate`;
- `evidence` to `reasons`;
- `expected_R` to `expected_net_r`;
- `risk_constraints` należy do `RiskDecision`;
- `market_regime` jest w `EntryCandidate.regime`;
- `decision_latency_ms` należy do zdarzenia EXECUTION, nie do decyzji.

`time_horizon` i `invalidation_conditions` zostawiam na później. Pierwsze da się wyrazić przez `valid_until_ms`, drugie przez `DecisionTiming.invalidation_price` (dziś SL).

Brain (osobne repo) produkuje `StrategyDecision` z `model_id` i `confidence`. Nie zna BloFin, bo kontrakt jest w `cryptoedge/domain`, który nie może importować giełdy (`tests/test_architecture_boundaries.py:25-42`).

---

## Część 3 — Ochrona przed spóźnioną/nieaktualną decyzją

**jev-trader:** flaga `busy` (`trader.ts:89-94`). Blok, który przyszedł w trakcie decyzji, jest pomijany jako `late` (hold). Decyzja już w toku zostanie jednak **wysłana** nawet po kilku blokach, z ceną z książki odczytanej przed decyzją (`trader.ts:97,116`; `market.ts:121-129`). Faktycznym zabezpieczeniem jest tam **post-only po stronie giełdy**: nieaktualne zlecenie, które przeszłoby przez spread, jest odrzucane (`reverted`), a nie wykonywane jako taker (README, „reverted”). To cenna lekcja — dla wejść limitem post-only jest naturalną, darmową bramką „stale”.

**CryptoEdge dziś:**
- `decision_fresh` to jedna decyzja na świecę 15m (`daytrading_engine_v2.py:864-891`); powtórki są wykluczane z wejść (`app.py:663-667`).
- Są bramki na nieaktualne świece (`daytrading_engine_v2.py:79-102`) i nieaktualny feed (`STALE_DATA_SECONDS`).
- Jest TTL limitu 900 s (`paper_trader.py:931-932,998-1008`).
- **Brak:** limitu czasu od zamknięcia świecy do wejścia, sprawdzenia dryfu ceny między oceną a wejściem i sprawdzenia przejścia przez SL przed wejściem. Paper wchodzi po `signal["price"]` z chwili skanu (`paper_trader.py:1028`), nawet jeśli skan trwał długo.

**Dodane w tym PR** (czyste, niewpięte):
- `cryptoedge/domain/validity.py`: `DecisionTiming`, `assess_validity(...) → DecisionValidity` ze statusem `VALID | LATE | STALE | INVALIDATED`, precedencja INVALIDATED > LATE > STALE;
- `valid_until_after_bars()`;
- `DecisionValidityStatus` w `enums.py`.

Zegar podaje wołający: runtime używa zegara ściennego, replay zegara zdarzeń, więc zachowanie jest to samo w obu torach. Brak danych nie daje „VALID” po cichu — pominięte testy trafiają do `checks_skipped`.

**Proponowane wpięcie (później, osobny PR z pomiarem):**
1. Tryb obserwacyjny: w `PaperTrader.open_position` policzyć `assess_validity` i zapisać `validity_status`/`decision_age_ms` w wierszu ACCEPT `decision_telemetry`. Nie blokować.
2. Zmierzyć rozkład wieku decyzji i dryfu na paper.
3. Włączyć blokadę za flagą, z bramką `decision_parity`.
4. Dla LIVE limitów: post-only.

---

## Część 4 — Założenia wykonania

**jev-trader — antywzorzec, który warto zapamiętać:**
- prompt modelu mówi „The order executes as an immediate-or-cancel market order in the next block” i „The trade crosses the spread”, a także „A decision is made every few blocks and held” (`model.ts:354-355`);
- wykonanie to **post-only limit wewnątrz spreadu, co blok** (`market.ts:185-190`, argument `true` = postOnly; `trader.ts:116`).

Model optymalizuje więc inną transakcję niż ta, która powstaje. Nic w kodzie tego nie wykrywa.

**CryptoEdge ma ten sam problem.** Zweryfikowane:

| Miejsce | Zakładana opłata RT dla wejścia limitem | Dowód |
|---|---|---|
| filtr kosztu V2 | 2×taker = 0.0012 | `daytrading_engine_v2.py:557-560` |
| `expected_net_r` | maker+taker = 0.0008 | `expected_net_r.py:234-235` |
| replay V2 | maker+taker = 0.0008 | `daytrading_backtester.py:510-512` |
| **paper** | **taker+taker = 0.0012** | `paper_trader.py:96` (`"limit"` ∉ {maker, resting_limit, limit_maker}) + `:997` (`fill_kind="limit"`) |

Dodatkowe rozbieżności:
- paper dolicza połowę spreadu do wejścia limitem (`paper_trader.py:1052-1064`) i jeszcze `slip_rt`, który w ścieżce „measured” już zawiera spread (`v2_profiles.py:136-158`);
- replay wycenia otwarcie świecy za limitem (zlecenie marketable) jako maker fill (`v2_parity_policy.py:93-109`);
- LIVE nie ma w ogóle implementacji wejścia limitem: `open_market`/`close_market` tylko (`blofin_executor.py:587-635`), a `LIVE_EXECUTION_ENABLED=False`.

**Dodane w tym PR:**
- `cryptoedge/domain/execution.py`: `ExecutionAssumptions` (order_type, post_only, entry/exit role, stawki, spread, slippage, latency, fill_probability, ttl), niezależne od giełdy, z `None` = nieznane;
- `from_signal()` z tą samą regułą co `expected_net_r`;
- `from_fill_kind()` z regułą paper;
- `execution_mismatches(assumed, realized)`.

Rozbieżność paper jest przypięta testem `@expectedFailure` (`tests/test_execution_assumptions.py`). **Sabotaż bramki:** tymczasowo dodano `"limit"` do zbioru maker w `paper_trader.py`, test dał „unexpected success”. Po przywróceniu wynik to znowu „expected failure”, a `paper_trader.py` ma pusty diff.

**Poprawka paper nie wchodzi w tym PR.** Zmienia PnL paper, a bramki replay (`tests/test_*_gate_baseline.py`) są czerwone już na HEAD z powodu fingerprintu konfiguracji `ac8946fc879ac185 → 406cdfb93778ea5e`, więc nie da się zmierzyć jej skutku (AGENTS.md: gate before move).

---

## Część 5 — Paper / masowa symulacja

**jev-trader (dry run),** `simFills` (`trader.ts:186-200`):
- zlecenie złożone w bloku N leży na książce od N+1;
- wypełnia je **prawdziwy print** takera po lub przez naszą cenę, do rozmiaru printu (częściowe fille przez `min`);
- zlecenie żyje 1 blok (`trader.ts:119-120`), cancel jest natychmiastowy;
- kolejka jest ignorowana (print *na* cenie zakłada, że jesteśmy pierwsi — optymistycznie);
- w dry run brak opłat (`market.ts:137`: `gasMon: 0`), a model opłat Kuru jest UNKNOWN.

**CryptoEdge:**
- replay to 5m OHLC z fillem „touch” (`v2_parity_policy.py:93-109`);
- brak partial fills, kolejki, likwidacji w replay i gap-through na SL (`daytrading_backtester.py:637-641`);
- optymizm świecy wejścia: fill i TP w tym samym barze bez kolejności wewnątrz baru (`daytrading_backtester.py:745-772`);
- latency jest zaokrąglana do pełnego baru 5m (`daytrading_backtester.py:969`);
- paper widzi tylko ceny z polla;
- istnieje bardziej realistyczny `ReplayExecutionEngine` (`replay_execution.py:108-128`), ale nikt go nie używa;
- nieużywane pokrętła: `EXECUTION_LATENCY_MS`, `REPLAY_INTRABAR_POLICY`, `REPLAY_REQUIRE_REAL_1M/L2` (`config.py:74-77`).

Masowo:
- jeden symbol na proces (`_run_v2_90d_par.py:146`), bez wspólnego portfela;
- config to mutowalny globalny moduł (`historical_replay.py:822-837`), więc **nie da się bezpiecznie uruchomić siatki konfiguracji w jednym procesie**;
- brak runnera eksperymentów.

Czego brakuje do realizmu, w kolejności wartości:
1. **Spójność opłat i ról** (tabela wyżej) — tani, duży wpływ, bo koszt to jedyny składnik, który „jeszcze coś waży” (komentarz w `expected_net_r.py:228-233`).
2. Kolejność wewnątrz baru dla świecy wejścia i SL (1m już jest opcjonalnie pobierane, `historical_replay.py:27-34`), gap-through na SL (fill po open, nie po poziomie), marketable limit = taker.
3. **Fill limitu po printach** (wzorzec jev-trader) zamiast „touch” — wymaga danych trade. Rekorder WS BloFin trades/L2 na VPS (M2) jest jedynym źródłem **zgodnym z venue** (AGENTS.md reguła 3: fille tylko BloFin).
4. Config jako niemutowalny obiekt parametrów + manifest eksperymentu (fingerprint już istnieje w `tests/baselines/`) → siatka konfiguracji równolegle, wyniki z lineage.
5. hftbacktest / Tardis: **hftbacktest** ma sens dopiero przy strategii sub-minutowej albo maker-heavy z modelem kolejki. Dla wejść na świecach 15m dominujący błąd pomiaru to p(TP1) i koszty (ROADMAP), nie kolejka. **Tardis.dev — pokrycie BloFin: UNKNOWN** (nie weryfikowano). Dane Binance/Bybit jako proxy fili łamałyby regułę venue. Ocena: IMPLEMENT LATER, po punktach 1–4.

---

## Część 6 — Eventy / telemetria

**jev-trader:**
- jedno zdarzenie `BlockEvent` na blok (decyzja + quote + fill + pozycja + totals, `trader.ts:7-23`);
- SSE `snapshot|block|quote|fill|ping` z historią 1000 (`server.ts`);
- dobre: jawne koszty decyzji (`jevUsd`, gas) w PnL i pomiar `readMs`/`loopMs` per blok (`trader.ts:125`);
- złe: zdarzenia mutowane wstecz (`trader.ts:146-147,165-166`) i zapis synchroniczny.

**CryptoEdge:**
- `DomainEvent` z pełnymi identyfikatorami korelacji (`cryptoedge/domain/events.py`), co jest **BETTER_THAN_JEV** jako schemat (append-only przez id) — ale **nigdy nie emitowany** poza testami;
- `event_bus.py`: 2 strumienie Redis, synchroniczny, domyślnie wyłączony;
- `decision_telemetry.jsonl` z DECISION/OUTCOME, synchroniczny;
- UI w trybie pull.

**Ocena `TradeEventBus`:** nie dodawać nowej szyny obok `event_bus.py` i `DomainEvent` — to byłby trzeci mechanizm. W tym PR:
- `EventType` rozszerzono o `ORDER_INTENT, EXECUTION, PNL, STRATEGY_HEALTH, MODEL, SIMULATION`;
- świadomie **bez** `MARKET`/`FEATURE` per tick (300+ symboli × tick to wolumen dla store, nie dla szyny; stan rynku = `SNAPSHOT`);
- **bez** `SIGNAL`, bo w CryptoEdge sygnał = `DECISION`;
- dodano `cryptoedge/telemetry/sink.py`: `NonBlockingEventSink` (ograniczona kolejka, jeden wątek zapisu, `emit()` nigdy nie czeka, odrzucone zdarzenia **liczone**, błędy writera nie wracają do handlu) i `JsonlWriter`.

Niewpięte. Kolejny krok: `decision_telemetry._append` przez sink za flagą, a potem emisja `DomainEvent` w `DecisionPipeline`, `open_position` i `close_position`.

UI (Tauri) zostaje przy pull przez `engine_api`/`market_stream_server` — **nie ruszać `frontend/`** (ROADMAP M3: „Do not touch frontend/ during stages 5–7”).

## Część 7 — Strategy Health / uczenie

Lineage już jest: `decision_id` + `snapshot_id` od `DecisionPipeline`, a wiersz OUTCOME przy zamknięciu (`decision_telemetry.py`, `paper_trader.py:1660`). Brakuje, żeby jeden strumień zasilał UI i dataset:
- `strategy_version`/fingerprint konfiguracji w każdym wierszu (dziś brak — nie da się oddzielić wyników dwóch wersji V2);
- `validity_status`/`decision_age_ms` na wejściu;
- `execution_assumptions` vs zrealizowane (rola, opłata, cena) na fillu;
- `market_regime` w replay (dziś `UNKNOWN` dla każdego trade'u — ROADMAP).

Najpierw poprawne dane, potem ML. Nic z ML nie jest proponowane.

## Część 8 — Engine V3

**UNKNOWN / nie istnieje w kodzie.** Grep `v3|EngineV3|engine_v3|Strategy Engine` zwraca tylko URL-e API (`binance_feed.py:16` itd.). Aktywny jest `DayTradingEngineV2`. Rekomendowane granice dla przyszłego V3 (spójne z ADR-001):
- `StrategyPort.evaluate(MarketSnapshot) → StrategyDecision | EntryCandidate` (typowane, dziś `→ Any`, `cryptoedge/strategy/ports.py`);
- zero `feeder`, `config`, `market_store`, `blofin_ws` w silniku (V2 importuje je wszystkie: `daytrading_engine_v2.py:22-24,191,206,214-259`);
- decyzja niesie `valid_until_ms`, `market_version`, `execution`;
- test granic rozszerzony na `cryptoedge/strategy` (dziś pilnuje tylko `domain`, `tests/test_architecture_boundaries.py:61`).

---

## Część 9 — Tabela porównawcza

| Element | jev-trader | CryptoEdge | Status | Gap | Priorytet |
|---|---|---|---|---|---|
| Rozdział hot/cold (potwierdzenia, fille poza pętlą) | tak, `trader.ts:87,102` | brak; wszystko w wątku `bot` | PARTIAL | wyjścia (SL/TP) czekają na pełny skan | P1 |
| Synchroniczny I/O w pętli | tak (`appendFileSync`) | tak (telemetria, CSV, state) | PARTIAL | brak sinka; dodany, niewpięty | P2 |
| UI odcięte od pętli | nie (broadcast inline) | tak (pull) | BETTER_THAN_JEV | ale wątek UI mutuje stan ryzyka | P0 |
| Koalescencja do najnowszego zdarzenia | tak, `chain.ts:463-471` | N/A (zegar 30 s) + `decision_fresh` | NOT_APPLICABLE | — | — |
| Kanoniczny stan rynku | `TradeState`, bez wersji | `MarketSnapshot` (cienki, zamrożony) | PARTIAL | brak typowanych cech, order_book/funding puste | P2 |
| Ryzyko jako wejście modelu (`allowed`) | tak, `model.ts:333` | ryzyko po decyzji | MISSING (świadomie) | rozważyć dla Brain | P3 |
| CVD / printy | tak, `trades.ts` | brak | MISSING | brak feedu trades | P2 |
| Kontrakt decyzji | `Decision` bez id, mutowany | `StrategyDecision` zamrożony, nieużywany | BETTER_THAN_JEV (projekt) / PARTIAL (użycie) | V2 zwraca dict | P1 |
| Wersja modelu/strategii w decyzji | tylko `model.name` | brak | MISSING → dodane pole | wypełnić w V2 | P1 |
| Late/stale protection | `busy` + post-only revert | `decision_fresh` per bar | PARTIAL | brak wieku/dryfu; evaluator dodany | P1 |
| Zgodność założeń wykonania | **łamana** (IOC w prompcie, post-only w kodzie) | **łamana** (maker vs taker w paper) | PARTIAL | kontrakt + test dodane, poprawka za bramką | P0 |
| Post-only dla limitów | tak | brak (LIVE nie ma limitów) | MISSING | przy LIVE limitach | P2 |
| Paper fill po printach | tak, `trader.ts:186-200` | touch na OHLC | MISSING | dane trades BloFin | P2 |
| Partial fills (sim) | tak (min z printem) | brak | MISSING | — | P2 |
| Kolejka | brak | brak | MISSING (oba) | niski priorytet dla 15m | P3 |
| TTL zlecenia | 1 blok | 900 s / 3–4 bary (niespójne) | PARTIAL | ujednolicić | P2 |
| Funding | N/A (spot) | tak | BETTER_THAN_JEV | partiale ignorowane w replay | P2 |
| Likwidacja | N/A | tylko paper, nie V2 | PARTIAL | — | P3 |
| Ograniczenia giełdy (tick/lot) | tak (tick math, `market.ts:121-129`) | registry w executorze; paper nie zaokrągla limitu | PARTIAL | — | P3 |
| Liczenie in-flight do limitu | tak, `trader.ts:202-207` | `EntryReservationBook` | EXISTS | — | — |
| Schemat zdarzeń z korelacją | brak id, mutacja wsteczna | `DomainEvent` (nieużywany) | BETTER_THAN_JEV (projekt) | emisja | P1 |
| Pomiar latencji per etap | `readMs`/`loopMs` | tylko łączny czas skanu | MISSING | instrumentacja | P1 |
| Koszt decyzji w PnL | tak (`jevUsd`, gas) | N/A dziś; ważne dla LLM Brain | NOT_APPLICABLE (dziś) | — | P3 |
| Deterministyczny mock modelu | `MockModel` | replay deterministyczny | EXISTS | — | — |
| Masowe eksperymenty | brak | proces/symbol, config globalny | PARTIAL | config jako obiekt, manifest | P1 |

## TOP 10 zmian

| # | Problem | Aktualny kod | Zmiana | Pliki | Zależności | Ryzyko regresji | Testy | Korzyść |
|---|---|---|---|---|---|---|---|---|
| 1 | Paper księguje limit jako taker; decyzja zakłada maker | `paper_trader.py:96,997` vs `expected_net_r.py:234-235` | dodać `"limit"` do ról maker **i** usunąć podwójny spread dla fillów limit | `paper_trader.py`, `accounting.py` | naprawione fingerprinty baseline'ów | średnie: zmienia PnL paper | zdjąć `@expectedFailure`; `test_fee_parity_gate_vs_replay`; `decision_parity`; gate'y | paper = replay = decyzja |
| 2 | Wątek UI mutuje ryzyko i zużywa REST | `engine_api.py:202`; `risk_manager.py:214,441` | UI czyta **wynik bramki policzony przez wątek bot** (cache w `rt`), zamiast wołać `can_open_position` | `engine_api.py`, `app.py`/`runtime.py` | — | niskie–średnie (tylko UI) | test: seria wywołań `_candidates` nie zmienia `DynamicCorrelation` | UI nie wpływa na decyzje |
| 3 | SL/TP paper czeka na koniec skanu | `app.py:443-474` | osobny wątek/tick ochrony (ceny WS) niezależny od skanu | `app.py`, `runtime.py`, `paper_trader.py` (lock) | pomiar czasu skanu (#7) | średnie (współbieżność na `trader.lock`) | test: długi skan nie opóźnia `check_exits` | realne wyjścia na czas |
| 4 | Brak wieku/dryfu decyzji przed wejściem | `paper_trader.py:1028` | `assess_validity` w `open_position`, najpierw obserwacja → telemetria | `paper_trader.py`, `decision_telemetry.py` | ten PR | niskie w trybie obserwacji | wiersz ACCEPT ma `decision_age_ms` | wiemy, jak stare są wejścia |
| 5 | Kontrakt założeń wykonania | brak | `ExecutionAssumptions` (**zrobione**) + zapis na DECISION i FILL | ten PR + `decision_telemetry.py` | — | niskie | `test_execution_assumptions` | wykrywalne „A vs B” |
| 6 | Telemetria synchroniczna | `decision_telemetry.py:28-46` | `_append` przez `NonBlockingEventSink` za flagą | `decision_telemetry.py`, `app.py` (flush przy stop) | ten PR | niskie (testy czytające plik → flush) | `test_decision_telemetry` + flush | mniej I/O w wątku bot |
| 7 | Brak pomiaru latencji etapów | `app.py:564-578` | czasy: fetch, evaluate per symbol, risk, entry → zdarzenie EXECUTION | `app.py`, `daytrading_engine_v2.py` (tylko pomiar) | #6 | niskie | test na obecność pól | dowód zamiast domysłu (UNKNOWN z raportu) |
| 8 | Wersja strategii niewidoczna w danych | brak | `strategy_version` + fingerprint config w DECISION/OUTCOME | `decision_telemetry.py`, `DecisionPipeline._stamp_lineage` | fingerprint z `tools/` | niskie (addytywne pola) | test pól | Strategy Health per wersja |
| 9 | Replay: gap-through i kolejność w barze wejścia | `daytrading_backtester.py:637-641,745-772` | SL po open przy luce; marketable limit = taker; 1m dla baru wejścia | `v2_trade_lifecycle.py`, `v2_parity_policy.py`, `daytrading_backtester.py` | #1, gate'y | **wysokie** (zmienia wyniki) | `decision_parity`, gate'y z nowymi baseline'ami | realizm zamiast optymizmu |
| 10 | Config globalny blokuje siatkę eksperymentów | `historical_replay.py:822-837` | obiekt parametrów + manifest eksperymentu + wyniki z fingerprintem | `historical_replay.py`, `_run_v2_*`, nowe `tools/experiments.py` | #8 | średnie | determinizm: 2 przebiegi = bajt w bajt | 300 × N konfiguracji |

## Czego NIE robić
- Nie kopiować jev-trader, nie dodawać zależności i nie przechodzić na Bun/TS.
- Żadnego kodu Monad/Kuru (nonce, gas, `batchUpdate`, `eth_getLogs`).
- Nie budować mikrosekundowego hot path. Horyzont 15m nie potrzebuje go, a przeszkadza tam REST i wspólny wątek, nie CPU.
- Nie mutować decyzji po fakcie (`trader.ts:114`) ani zdarzeń wstecz (`trader.ts:146`).
- Nie tworzyć `DecisionEnvelope`/`TradeEventBus` obok istniejących `StrategyDecision`/`DomainEvent`/`event_bus`.
- Nie ruszać `frontend/` (ROADMAP M3). Nie wprowadzać PySide6 (istniejący `pyside6_ui.py` zostaje nietknięty).
- Nie wiązać Brain z repo: Brain dostaje kontrakty z `cryptoedge/domain` i produkuje `StrategyDecision`.
- Nie używać danych Binance/Bybit do fili (AGENTS.md reguła 3).
- Nie retunować V2 przy okazji. Nie uruchamiać `tools/calibrate_expectancy.py`.

## Rekomendowana architektura docelowa

```
Market Data (WS/REST BloFin, recorder)  ──►  MarketSnapshot [+ MarketFeatures vN]
                                                   │
             PortfolioSnapshot ──┐                 ▼
                                 ├──►  Strategy Engine (V2 / V3 / Brain sidecar)
             ExecutionAssumptions┘        StrategyPort.evaluate → StrategyDecision
                                          (+ valid_until, market_version, execution,
                                             strategy_version / model_id, confidence)
                                                   │
                                     Decision Funnel (ranking, konflikty)
                                                   ▼
                                     Risk Engine → RiskDecision
                                                   ▼
                                     Execution Router: assess_validity → VALID?
                                        execution_mismatches(assumed, adapter.caps)
                                                   ▼
                              ExecutionPort: Paper | BloFin | (BloFinCopyTrading)
   każdy krok ──► DomainEvent ──► NonBlockingEventSink ──► JSONL/Redis ──► UI read models / dataset
```

Adapter copy tradingu to kolejna implementacja `ExecutionPort`. Brain widzi tylko `ExecutionAssumptions` (ekonomię), nigdy adapter.

## Kolejność wdrożenia
1. **Faza 1 — kontrakty** (ten PR): `ExecutionAssumptions`, rozszerzony `StrategyDecision`, `EventType`.
2. **Faza 2 — stale protection** (ten PR: evaluator). Wpięcie obserwacyjne w osobnym PR.
3. **Faza 3 — telemetria** (ten PR: sink). Wpięcie `decision_telemetry` za flagą w osobnym PR.
4. Najpierw naprawa fingerprintów baseline'ów gate'ów, żeby dało się mierzyć (warunek dla #1 i #9).
5. Faza 4: #2 (UI przestaje mutować ryzyko), #3 (osobny tick ochrony), #7 (pomiar).
6. Faza 5: #1, potem #9, potem #10; dane trades BloFin (M2).
7. Faza 6: V2 zwraca `StrategyDecision` przez adapter; test granic dla `strategy/`.
8. Faza 7: interfejs Brain = `StrategyPort` + kontrakty domeny, w trybie shadow (ROADMAP M4).

## Pliki do zmian (kolejne PR)
`paper_trader.py`, `accounting.py`, `engine_api.py`, `app.py`, `runtime.py`, `decision_telemetry.py`, `cryptoedge/services/decision_pipeline.py`, `cryptoedge/strategy/ports.py`, `cryptoedge/strategy/legacy.py`, `v2_parity_policy.py`, `v2_trade_lifecycle.py`, `daytrading_backtester.py`, `historical_replay.py`, `tests/test_architecture_boundaries.py`.

## Pliki, których NIE ruszać
`frontend/**`, `pyside6_ui.py`, logika wejścia/TP/SL w `daytrading_engine_v2.py` (poza czystym pomiarem), wartości w `config.py`, `tools/calibrate_expectancy.py`, `tests/baselines/**` (tylko przez procedurę bramek), `event_backtester.py` (V1 legacy), `blofin_executor.py` (LIVE wyłączone).

## Testy wymagane przed merge
- `python run_tests.py`: zbiór czerwonych przypadków ma być identyczny jak na HEAD.
- Nowe: `tests/test_execution_assumptions.py`, `tests/test_decision_validity.py`, `tests/test_telemetry_sink.py`.
- Bez regresji: `tests/test_domain_contracts.py`, `tests/test_architecture_boundaries.py`, `tests/test_event_bus.py`, `tests/test_modular_services.py`.
- Dla PR zmieniających zachowanie (#1, #3, #9): dodatkowo `tools/decision_parity.py` i gate'y z odświeżonymi baseline'ami, zmierzone przed i po.

## Wynik tego PR (zmierzony)
- `python run_tests.py`, HEAD `2dc1baa` w czystym worktree: 1378 testów, `FAILED (failures=17, errors=4, modules=12)`.
- Ta sama komenda po zmianach: 1404 testy (+26 nowych), `FAILED (failures=17, errors=4, modules=12)`.
- Lista `FAIL:`/`ERROR:` jest identyczna (porównana `diff`).

Czerwone moduły na HEAD i ich przyczyny:
- fingerprint konfiguracji w `tests/baselines` (gate'y entry/exec/exit/fill/restart/risk);
- brak bundli 90d w `data/replay` (`test_outcome_dataset`);
- brak PySide6 (`test_events_warmup_summary`);
- pozostałe (`test_ui_language`, `test_v2_funding`, `test_v2_runtime_replay_parity`, `test_venue_microstructure`): nie badane, nie dotknięte tym PR.

## Werdykt per koncepcja

| Koncepcja | Werdykt |
|---|---|
| `ExecutionAssumptions` + `execution_mismatches` | **IMPLEMENT NOW** (zrobione) |
| Rozszerzenie `StrategyDecision` zamiast `DecisionEnvelope` | **IMPLEMENT NOW** (zrobione) |
| Evaluator VALID/STALE/LATE/INVALIDATED | **IMPLEMENT NOW** (zrobione, niewpięte) |
| `EventType` rozszerzony, bez MARKET/FEATURE per tick | **IMPLEMENT NOW** (zrobione) |
| `NonBlockingEventSink` | **IMPLEMENT NOW** (zrobione, niewpięte) |
| Test przypinający rozbieżność fee paper (`expectedFailure`) | **IMPLEMENT NOW** (zrobione) |
| Sprostowanie docstringu `event_bus.py` | **IMPLEMENT NOW** (zrobione) |
| Naprawa roli maker w paper | IMPLEMENT LATER (po naprawie baseline'ów, z bramką) |
| UI czyta cache bramki zamiast wołać `can_open_position` | IMPLEMENT LATER (P0, następny PR) |
| Osobny tick ochrony pozycji | IMPLEMENT LATER (P1) |
| Wpięcie validity w `open_position` (obserwacja → blokada) | IMPLEMENT LATER (P1) |
| Pomiar latencji etapów | IMPLEMENT LATER (P1) |
| `strategy_version`/fingerprint w telemetrii | IMPLEMENT LATER (P1) |
| `MarketFeatures` (typowane sekcje) | IMPLEMENT LATER (P2) |
| Fill limitu po printach + recorder trades BloFin | IMPLEMENT LATER (P2, M2) |
| Post-only dla LIVE limitów | IMPLEMENT LATER (gdy LIVE limity) |
| Config jako obiekt + runner eksperymentów | IMPLEMENT LATER (P1–P2) |
| hftbacktest / L2-L3 / Tardis | IMPLEMENT LATER, warunkowo (po weryfikacji pokrycia BloFin; dziś UNKNOWN) |
| Ryzyko (`allowed`) jako wejście modelu | IMPLEMENT LATER (do rozważenia dla Brain) |
| Koszt inferencji w PnL | IMPLEMENT LATER (dopiero gdy Brain/LLM) |
| Nowy `TradeEventBus` obok istniejących | **REJECT** |
| Nowy `DecisionEnvelope` obok `StrategyDecision` | **REJECT** |
| Mutowanie decyzji/zdarzeń po fakcie (jev `trader.ts:114,146`) | **REJECT** |
| Hot path pod ~300 ms (fire-and-forget, lokalny nonce, bez estimateGas) | **REJECT** (NOT_APPLICABLE) |
| Decyzja co tick/blok | **REJECT** (sprzeczne z 15m swing/intraday) |
| Stack Bun/TS, Monad/Kuru, SSE zamiast istniejącego API | **REJECT** |
| Proxy fili z Binance/Bybit/Tardis innych venue | **REJECT** (AGENTS.md reguła 3) |
