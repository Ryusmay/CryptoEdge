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

**Status warunku wyjścia: SPEŁNIONY** (v20.39.0 + v20.40.0).
`tools/restart_gate.py` — 18 przypadków na pełnym `RestartRecovery.run()`,
pinuje raport, stan ryzyka, każde `attach_protection` i operacje executora;
`tests/test_restart_gate_baseline.py` — 18 testów; 14 sabotaży zrodła, 14
wykrytych. LIVE failure (`ex_pos` / `reconcile`) ustawia
`risk_state = REDUCE_ONLY` z powodem `RECOVERY_UNCONFIRMED:*`, w PAPER nie.

Otwarte w zakresie etapu (nie w warunku wyjścia):
- księga oparta na fillach — `FillLedger`/`OrderLifecycle` mają **zero
  wywołań produkcyjnych**, autorytetem pozostaje `PaperTrader`; skutek
  uboczny widoczny w bramce restartu: po partialu księga PAPER trzyma 3.0,
  gdy na giełdzie jest 1.0, i nikt jej nie koryguje.

  **Warunek wstępny, zmierzony przez `tools/fill_gate.py` (v20.41.0–42.0):**
  fill dociera dziś wyłącznie do `Order.filled_size` w pamięci executora
  i tam umiera — ścieżka urywa się w trzech miejscach niezależnie
  (`paper_trader.py:1093` blokuje LIVE, `cryptoedge/apps/runtime.py:42`
  omija adapter giełdowy, `ExecutionResult` nie ma pola na wypełnioną
  ilość). Co ważniejsze: z migawki `order-detail` **ceny pojedynczego fillu
  odtworzyć się nie da**, bo wiersz niesie średnią całego zlecenia. Po
  v20.42.0 `refresh_order` bierze tę średnią wprost i jest w tym miejscu
  **dokładniejszy niż `FillLedger`**, który liczy VWAP z faktów per
  transakcja, a tych ta ścieżka nie dostarcza. Podmiana księgi wymaga więc
  albo prawdziwego strumienia transakcji (`trade_id`, cena i ilość per
  transakcja), albo przeniesienia autorytatywnej średniej zlecenia obok
  własnego agregatu. Do rozstrzygnięcia zostaje też rozjazd kontraktów przy
  przepełnieniu: `Order` zapisuje i oznacza, `OrderLifecycle` odmawia;
- exchange-side SL zakładany po fillu — **zmierzone (v20.43.0): brakuje
  ogniwa, nie logiki.** `protection.py` poprawnie odkłada giełdowy TPSL do
  czasu, aż pozna wypełnienie (`WAIT_FILL_SIZE`), i w międzyczasie uzbraja
  lokalny SL. Ale `attach_protection` ma w produkcji dokładnie dwóch
  wołających: `paper_trader` (zablokowany w LIVE) i `restart_recovery`
  (tylko przy starcie). **Nikt nie uzbraja ochrony po fillu** — odłożenie
  czeka na krok, który nie istnieje, a jedyne, co dziś by je podjęło, to
  restart bota. Port od v20.43.0 niesie już `filled_quantity`, więc to
  ogniwo ma z czego skorzystać;
- idempotencja — **zrobione (v20.45.0)**. `exec_gate` ma sekcję
  `kind: "idempotency"`, która zapisuje ślad identyfikatorów na drucie
  (znormalizowany do `cid#1`, `cid#2`… po pierwszym wystąpieniu, bo sama
  wartość niesie czas i losowy sufiks). Zmierzone: po TIMEOUT jeden POST
  i zapytanie po tym samym cid; anulowanie trafia w to samo zlecenie;
  narzucony przez wołającego cid idzie na giełdę niezmieniony; bez
  narzuconego — nowy przy każdym wywołaniu, bo **executor nie deduplikuje
  i idempotencję trzyma wołający**. `retried_submits` w meta musi być 0.
  Poza bramką zostaje TPSL w `protection.py` (własny prefiks `PT`, przy
  `TIMEOUT_TPSL` brak ponowienia i fallback na lokalny SL);
- rozdzielenie strategy / decision / submitted / fill / mark price —
  **częściowo zrobione (v20.44.0)**. Zmierzone: `strategy`, `decision`,
  `submitted` i `fill` są naprawdę rozdzielone i niosą różne wartości.
  `mark` i `index` **nie są podłączone**: `blofin_ws` odbiera kanał mark
  i trzyma go w `_prices[symbol]["mark_price"]`, ale do sygnału to nie
  trafia, a `blofin_mark` nie ma w repo ani jednego pisarza. `mark_pnl()`
  ma zero wywołań. Od v20.44.0 brak marka daje `None` zamiast podszywać się
  pod cenę strategii (co dawało `basis_pct` strukturalnie równe 0.0).
  **Podłączenie kanału to decyzja handlowa, nie refaktor**: `basis_pct`
  rozszerza limit spreadu (`SPREAD_K_BASIS`), a mark przychodzi wyłącznie
  po websockecie, którego w replayu nie ma — `parity` nie zmierzy skutku
  ani w jedną, ani w drugą stronę.

**Rollback:** BloFin adapter może delegować do starego executora; nowe wejścia
blokowane, ale redukcja ryzyka pozostaje dostępna.

## Etap 6 — replay, telemetry i API

- replay zmienia wyłącznie data source, clock i executor;
- wspólny event schema zasila telemetry oraz projekcje UI;
- API wystawia stabilne DTO i health per moduł;
- frontend przestaje czytać pliki logów i stan procesów bezpośrednio.

**Warunek wyjścia:** parytet decyzji runtime/replay, pełny lineage oraz UI
działające po restarcie niezależnie od silnika.

**Stan zmierzony (v20.46.0). Deklaracja „replay zmienia wyłącznie data
source, clock i executor" NIE jest spełniona.**

Wspólne dla obu ścieżek: ocena setupu V2 (`evaluate` przez
`LegacyV2StrategyAdapter`), bramki rynkowe (`v2_parity_policy.apply_market_gates`)
i reduktor wyjścia V2 (`decide_v2_lifecycle`). Rozjechane: bramka ryzyka,
sizing, księga pozycji, time-stopy i orkiestracja pętli.

Zweryfikowane wprost, nie z drugiej ręki:
- `can_open_position` ma **77 wystąpień w repo i ani jednego w plikach
  replayu** (`daytrading_backtester.py`, `historical_replay.py`,
  `tools/parity.py`, `run_historical_replay.py`). Lejek wejść replayu to
  `max_positions` + `max_same_direction`;
- `EventClock` (`v2_market_snapshot.py:63`, docstring: *„Ten sam monotoniczny
  zegar decyzja → submit → fill dla live/replay"*) ma **3 wystąpienia:
  definicję i dwie linie w jednym teście. Zero użyć produkcyjnych** —
  wstrzykiwanego zegara nie ma w żadnej ścieżce;
- `tools/parity.py` porównuje replay z **własnym** baseline'em. To bramka
  regresji i jako taka działa — jej docstring nigdy nie obiecywał parytetu
  runtime/replay. Mimo nazwy **nie mierzy** tego, czego wymaga warunek wyjścia.

**Konsekwencja wykraczająca poza etap 6** (`tools/risk_overlay.py`, 30d ×
5 symboli): z 1062 kandydatów kierunkowych produkcyjna bramka ryzyka
przepuszcza **25 (2,35%)** — i to jako *dolne ograniczenie*, bo stan konta
był przed każdym sprawdzeniem resetowany do maksymalnie pobłażliwego.
928 odrzuceń to `NON_POSITIVE_NET_R`. Odrzucenia nie są graniczne: mediana
`expected_net_r` to −0,0914, p10 −0,2204, dodatnich 134/1062.

Ta liczba jest **już w sygnale**: `daytrading_engine_v2.py:760` woła tę samą
funkcję `expected_net_r()`, co `risk_manager.py:458` (sprawdzone —
0 rozbieżności na 1062 sygnałach). Silnik wystawia więc setup, któremu sam
przypisał ujemną oczekiwaną wartość, a `parity.py` nie ma czym tego odsiać.
`historical_replay.py:814` ten filtr **stosuje** (`"final_gate": net_r_ok`).

Czytać ostrożnie: to **nie dowodzi**, że strategia z włączonym filtrem byłaby
rentowna. Dowodzi, że −5,8939R z `parity.py` opisuje konfigurację handlującą
setupami, które system sam ocenia na minus — więc nie jest werdyktem
o strategii.

**Wejście też trzeba opisać (v20.47.0).** Zamrożone bundle 30d nie pokrywają
się w czasie: XRP i ZEC startują **263 h (11 dni) wcześniej** niż BTC/ETH/SOL,
a `portfolio_replay_v2` chodzi po **wspólnym indeksie baru**, nie po zegarze —
`bar 5000` to dla BTC 19 sierpnia, a dla XRP 8 sierpnia. Sprawdzone wprost:
samych liczb to nie rusza (58 transakcji, −5,8939R, ten sam config hash), bo
limit slotów i limit kierunku przy `max_positions=10` i pięciu symbolach nigdy
nie wiążą — `rejected_for_slots` i `rejected_for_direction` są zerowe, a wynik
jest arytmetycznie sumą pięciu niezależnych przebiegów. Rozjazd psuje
**interpretację**: „portfel przez jedno okno 30d" to w rzeczywistości dwa okna
przesunięte o 11 dni, a indeksowy podział IS/OOS tnie każdy symbol w innej
dacie. Jest to też pułapka na przyszłość: gdy uniwersum urośnie albo
`max_same_direction` zostanie podpięte pod `MAX_SAME_DIRECTION_PCT`, limity
zaczną wiązać na pozycjach, które nigdy nie były otwarte jednocześnie.
`tools/parity.py` mierzy teraz ten rozjazd, drukuje go, zapisuje w baseline
(sekcja `windows`) i potrafi odmówić wyniku (`--require-aligned`). Porównanie
`meta` przeszło z allowlisty na denylistę — nowy wymiar pomiaru jest pilnowany
od pierwszej chwili, a nie dopiero gdy ktoś dopisze go do listy.

Osobno `--final-gate` uruchamia ten sam przebieg z filtrem `net_r_ok`, którego
`historical_replay.py:814` używa, a `parity.py` domyślnie nie. To jawny
eksperyment: `meta` dostaje pole `final_gate`, więc różnica krzyczy sama
z siebie, a zapis takiego przebiegu jako baseline jest zablokowany.

**Zmierzony skutek tego jednego filtra (v20.48.0).** Komenda:
`python tools/parity.py --final-gate` — te same zamrożone dane, ten sam
`config_hash 60c1af1e975f04f6`, jedyna zmiana to podanie `net_r_ok`.

| | bez `final_gate` (baseline) | z `final_gate` |
|---|---|---|
| transakcje | 58 (IS 41 / OOS 17) | **2 (IS 2 / OOS 0)** |
| net R | −5,8939 | **−0,2198** |
| OOS net R | +1,0628 | 0,0000 |

Odrzucenia przypisane wprost temu filtrowi: **1028** — 923 `NON_POSITIVE_NET_R`
i 105 `DAY_PRIOR_NET_R_LOW(<0.10)`. To niezależnie potwierdza pomiar
`tools/risk_overlay.py` (928 `NON_POSITIVE_NET_R` na 1062 kandydatów): dwie
różne drogi pomiarowe dają tę samą liczbę.

Trzy rzeczy, których ten wynik **nie** mówi:

Po pierwsze, to nie jest dowód rentowności. −0,2198R na **dwóch** transakcjach
to nie jest lepsza strategia, tylko system, który praktycznie nie handluje.
Zero wejść OOS oznacza, że nie ma czego walidować poza próbą.

Po drugie, próg jest tu surowszy niż w rozgrzanym LIVE. Replay wymusza pusty
kalibrator (`daytrading_backtester.py:126-128`), więc `expected_net_r` zawsze
wraca ze statusem `PRIOR_ONLY` i `net_r_ok` porównuje z
`DAYTRADING_PRIOR_ONLY_MIN_EXPECTED_NET_R = 0,10`, a nie z
`DAYTRADING_MIN_EXPECTED_NET_R = 0,05` (`expected_net_r.py:279-291` — ta gałąź
zwraca wcześnie, druga nigdy nie jest osiągana). LIVE z kalibratorem `n ≥ 30`
sądziłby łagodniej; LIVE po restarcie, z zimnym kalibratorem — tak samo surowo.

To zastrzeżenie zostało **zmierzone, a nie oszacowane** (v20.49.0):
`python tools/parity.py --final-gate --prior-floor 0.05` — ta sama komenda
z podmienionym samym progiem (`config_hash` przechodzi w `975650cde5938ee8`,
i to jest poprawne: to inna konfiguracja).

| | próg 0,10 (zimny) | próg 0,05 (rozgrzany) |
|---|---|---|
| transakcje | 2 (IS 2 / OOS 0) | **3 (IS 2 / OOS 1)** |
| net R | −0,2198 | −0,1520 |
| `DAY_PRIOR_NET_R_LOW` | 105 | 79 |
| `NON_POSITIVE_NET_R` | 923 | 920 |

Poluzowanie progu ratuje 26 kandydatów przed podłogą priora i daje **dokładnie
jedną** transakcję więcej. Confound jest realny, ale wielkości błędu
zaokrąglenia. Prawdziwy ciężar leży gdzie indziej: **92% odrzuceń (920 z 999)
to `NON_POSITIVE_NET_R`** — setupy, którym silnik sam przypisał ujemną wartość
oczekiwaną. Żadna zmiana progu ich nie dotyka, bo przepadają przy każdej
podłodze ≥ 0. Zimny kalibrator nie jest więc wytłumaczeniem tej liczby.

Po trzecie, `final_gate` **nie jest czystym post-filtrem**. Lejek V2 też się
przesunął (`V2_NO_15M_TRIGGER` 5158 → 6064, `V2_SL_TOO_TIGHT_VS_COST`
1816 → 2230): mniej wejść to inny stan silnika przez `notify_exit`, więc
strumień kandydatów jest inny. Nie wolno powiedzieć „te same 58 kandydatów
przefiltrowano do 2" — kandydaci też się zmienili.

**Setup czy koszt? Rozłożone (v20.50.0).** Skoro próg nie tłumaczy 920 odrzuceń
`NON_POSITIVE_NET_R`, zostały dwie możliwości prowadzące w przeciwne strony:
brutto jest ujemne (problem w detektorze setupów) albo brutto jest dodatnie,
a zjadają je koszty (problem w modelu kosztów i venue). `tools/cost_breakdown.py`
zbiera pełny rozkład, który `expected_net_r()` **już zwraca** — żadnego nowego
modelu, ta sama kadencja 15m i ten sam filtr kierunku co `risk_overlay.py`.

Wynik na 1062 kandydatach (`config 60c1af1e975f04f6`), zgodny co do sztuki
z `risk_overlay.py` (134/1062 dodatnich, mediana −0,0914 — dwa niezależne
przebiegi, ta sama liczba):

| | mediana | p10 | p90 | dodatnich |
|---|---|---|---|---|
| `gross_r` | **+0,0476** | −0,0046 | +0,1056 | **937/1062 (88,2%)** |
| `net_r` | **−0,0914** | −0,2204 | +0,0115 | **134/1062 (12,6%)** |
| koszt łącznie | **+0,1432** | +0,0588 | +0,2534 | — |

Odpowiedź jest jednoznaczna i jest to odpowiedź (b): **brutto jest dodatnie
dla 88% kandydatów, a mediana kosztu jest 3× większa niż mediana edge'u.**
Detektor setupów nie jest tu winowajcą.

Rozbicie kosztu i kontrfaktyczne „gdyby ten jeden koszt był zerowy":

| składnik | mediana R | netto dodatnich |
|---|---|---|
| `slip_r` | **0,0901** | 134 → **533** (+399) |
| `fee_r` | 0,0320 | 134 → 237 (+103) |
| `spread_r` | 0,0107 | 134 → 159 (+25) |
| `impact_r` | 0,0000 | bez zmian |
| `funding_r` | 0,0000 | bez zmian |
| wszystkie zerowe | — | 937 |

**Poślizg sam jeden (0,0901R) przewyższa cały medianowy edge (0,0476R).**
Moja hipoteza, że winny jest ciasny SL, okazała się fałszywa: `sl_dist` ma
medianę 0,0374 (3,7%), czyli stopy nie są ciasne.

Czytać z trzema zastrzeżeniami, bo to porównanie liczb **modelowanych**, nie
zmierzonych:

- `gross_r` to **prior z configu**, nie zmierzony edge — wszystkie 1062
  kandydatów mają status `PRIOR_ONLY` (`p_tp1 = 0,55` × mnożnik jakości).
- `slip_r` to estymata modelu V2 z wolumenu świecy (`slip_rt` obecny na
  wszystkich 1062 sygnałach), a nie odczyt z realnych fillów.
- `spread_r` to **stała** `DEFAULT_SPREAD_FRAC`: replay nie ma książki zleceń
  (`fetch_order_book` zwraca `{}`), więc ta liczba nie opisuje rynku.
- `impact_r` jest z definicji zerowany, gdy `slip_rt` jest obecny (żeby nie
  liczyć tego samego dwa razy), a `funding_r = 0` znaczy „nie było czego
  liczyć" — `parity` nie podaje fundingu. Narzędzie raportuje te dwa fakty
  osobno, żeby zero nie udawało pomiaru.

**Pytanie, które z tego wynika i jest teraz najdroższe:** czy model `slip_rt`
jest wobec czegokolwiek skalibrowany? Jeśli jest pesymistyczny dwukrotnie, cały
obraz się odwraca — a to jest sprawdzalne wobec realnych fillów z PAPER/LIVE,
w przeciwieństwie do reszty tej analizy.

**Czy `slip_rt` jest skalibrowany? Nie. Sprawdzone (v20.51.0).**

Sam model (`v2_profiles.replay_slip_round_trip` → `orderbook_impact.estimate_bar_slippage`)
liczy `min(0,02; max(2·base, 2·(base + k·participation^0,6)))`, gdzie
`participation = notional / (wolumen jednej świecy 5m × cena)`, a
`notional = STARTING_CAPITAL × margin_pct% × LEVERAGE`. Zweryfikowane wprost:

- `k = 0,08` i wykładnik `0,6` **nie istnieją w `config.py`** — są literałami
  w `getattr(config, "BT_IMPACT_K", 0.08)` (`orderbook_impact.py:263-264`),
  opisanymi w kodzie jako *„classic"*. To samo `BT_MAX_SLIPPAGE = 0,012`.
- `notional` jest przypięty do `STARTING_CAPITAL = 100` — 75 USD dla majora,
  50 dla alta. Powiększenie konta 100× nie rusza modelowanego poślizgu.
- **Nigdzie w repo nie ma porównania tego modelu z realnym fillem.** Jedyna
  telemetria, która na to wygląda (`edge_monitor.slippage_avg/p95`), karmi się
  `accounting` = `notional × slip_frac`, czyli `slip_rt` — model raportuje sam
  siebie i z definicji nie wykryje własnego błędu.

**Rozbieżność replay/produkcja na profilu symbolu.** `profile_for()` awansuje
symbol do „major" tylko przez `_volume_rank`, które wypełnia
`refresh_volume_ranks()` — wołane wyłącznie z `generate()`. Replay idzie przez
`pipeline.analyze()` i nigdy nie woła `generate()`, więc w świeżym procesie
`len(_volume_rank) == 0` i wszystko poza twardą listą `BTC/ETH/SOL` dostaje
profil „alt": floor 30 bps RT zamiast 6 bps. Ten sam symbol kosztuje w replayu
pięć razy więcej niż w produkcji.

**Ale to NIE jest wyjaśnienie liczby 0,0901R** — hipoteza padła w zderzeniu
z pomiarem. Rozbicie per symbol (`tools/cost_breakdown.py`):

| symbol | profil replayu | kandydatów | `slip_r` mediana |
|---|---|---|---|
| BTC | major (6 bps floor) | 251 | 0,0755 |
| ETH | major | 294 | 0,0851 |
| SOL | major | 266 | 0,0605 |
| XRP | alt (30 bps floor) | 83 | 0,1496 |
| ZEC | alt | 168 | 0,1224 |

BTC ma floor `0,0006`, więc przy `sl_dist ≈ 0,019` sam floor dałby
`slip_r ≈ 0,016`. Zmierzone 0,0755 to **ponad czterokrotność floora** — dla
majorów dominuje człon impaktu, nie stała. Przebieg z profilami jak w produkcji
(`--as-production-profiles`, wszystkie symbole „major") potwierdza:
`slip_r` XRP spada tylko 0,1496 → 0,1311, ZEC 0,1224 → 0,1160, a mediana
całości praktycznie stoi: 0,0901 → 0,0921. Udział netto dodatnich też stoi:
12,6% → 12,0%.

Zastrzeżenie do tego przebiegu, ważne: **to nie jest eksperyment jednej
zmiennej.** `params_for()` wiąże profil nie tylko z poślizgiem, ale też ze
`swing_min_move_atr`, `skip_range`, `use_5m_veto` i `skip_4h_oppose`, więc
zmienia się cały strumień kandydatów (1062 → 1298). Porównywać rozkłady
i udziały, nie liczby bezwzględne.

**Wniosek: poślizg jest wysoki nie przez błędną klasyfikację, tylko przez człon
partycypacji** — 75 USD zlecenia przeciwko wolumenowi jednej świecy 5 m, ze
skalą `k`, której nikt nigdy nie dopasował do danych.

**Czy mamy do czego przyłożyć linijkę? Dziś nie.** Sprawdzone: `PAPER_TRADING
= True`, `LIVE_EXECUTION_ENABLED = False`, bot nigdy nie wykonał zlecenia na
giełdzie. PAPER jest z definicji niezdolny do wyprodukowania obserwacji
poślizgu — `paper_trader.py:1205` woła
`pos.recalculate_after_fill(pos.entry_price, ...)`, czyli jako „cenę fillu"
podaje własną cenę wejścia; komentarz obok mówi to wprost: *„paper: entry
already = exec after spread"*. `logs/bot_log.csv` ma jedną kolumnę `price`,
bez pary zamierzona/rzeczywista. Cała instalacja do zapisu prawdziwych fillów
istnieje i nie jest podłączona: `blofin_executor.py:502-521` poprawnie parsuje
`averagePrice`/`filledSize`/`fee`/`liquidityRole`, a `FillLedger`
(`cryptoedge/execution/ledger.py`) ma `save_json()` i **zero produkcyjnych
wołających** — wszystko żyje w pamięci i ginie z procesem.

Żeby to kiedykolwiek zmierzyć, trzeba trwale zapisywać parę
`decision_price` → `avg_fill_price` z giełdy plus `sl_dist`, i usunąć dwie
przeszkody: `LOG_RETENTION_DAYS = 3` (`logger.py` przycina logi co 100 cykli,
więc próbka nie zdąży się zebrać) oraz brak wołającego `FillLedger`.

**Druga strona równania: prior brutto (v20.52.0).** Skoro koszt jest modelem
bez kalibracji, a brutto jest priorem bez kalibracji, to przed płaceniem
realnymi fillami za zmierzenie kosztu zmierzono stronę darmową.
`tools/tp_rates.py` czyta zapisane raporty replayu (nic nie zapisuje — inaczej
niż `tools/calibrate_expectancy.py`, które przy samym uruchomieniu woła
`record()` na **produkcyjnym** kalibratorze) i używa wyłącznie jawnych pól
`tp1`/`tp2`, bez zgadywania z progów R.

Próbka: 202 wiersze z 35 raportów, po deduplikacji **193** (klucz jawny
w narzędziu — raporty pochodzą z nakładających się przebiegów).

| | empiria | prior |
|---|---|---|
| p(TP1) | **0,015** [0,005–0,045] | 0,55 |
| p(TP2\|TP1) | 0,000 [0,000–0,561] | 0,45 |

Różnica 36-krotna jest zbyt duża, żeby ufać jednej fladze, więc kontrola
krzyżowa **innym polem** — maksymalnym korzystnym wychyleniem: mediana
`mfe_r` to 0,363R, a udział transakcji sięgających 1,5R wynosi **5,2%**.
Flaga mówi prawdę: TP1 praktycznie nie jest osiągane.

**Ale wniosek nie brzmi „prior jest za wysoki".** Formuła
`_gross_expected_r` zakłada dwa stany: TP1 albo pełna strata −1R. Zmierzone
na tych samych 193 transakcjach:

- pełną stratę (≤ −0,9R) bierze **7,8%**, nie 98,5%;
- w przedziale ±0,25R kończy **45,1%**;
- średnie `realised_r` = **−0,062**.

Brutto z formuły przy empirycznym p(TP1) to **−0,973** — szesnaście razy
dalej od rzeczywistego średniego wyniku niż prior. Czyli **kształt modelu jest
zły, nie tylko jego parametry**: człon zysku jest skrajnie optymistyczny
(0,55 wobec 0,015), a człon straty skrajnie pesymistyczny (zakłada −1R tam,
gdzie realnie wychodzi około zera). Te dwa duże błędy częściowo się znoszą
i dlatego liczba z priora (+0,0476 mediany brutto) trafia w okolicę
rzeczywistości przez przypadek, a nie przez poprawność.

Rozkład powodów wyjścia pokazuje, gdzie naprawdę jest wynik:

| powód | n | średnie R |
|---|---|---|
| `hard_time_stop` | 68 | **+0,352** |
| `window_end_mark` | 2 | +0,141 |
| `htf_reversal` | 5 | −0,201 |
| `time_stop` | 103 | −0,195 |
| `sl` | 15 | −1,001 |

Czytać ostrożnie: 193 transakcje pochodzą z raportów o **różnych wersjach,
konfiguracjach i zakresach dat**, więc to jest zgrubna próbka, nie czysty
pomiar jednej konfiguracji. Fille replayu są dodatkowo optymistyczne (limit
wypełnia się po cenie otwarcia świecy, gdy ta przeskoczy limit), więc
empiryczne p(TP1) jest **górnym** ograniczeniem — co tylko wzmacnia wniosek.

**Wniosek dla planu:** nie ma sensu kalibrować członu kosztu, dopóki człon
brutto ma zły kształt. Pierwszym zadaniem jest zastąpienie modelu
dwustanowego takim, który dopuszcza wyjście pośrednie — bo to ono opisuje
45% transakcji i całą dodatnią stronę wyniku (`hard_time_stop`, +0,352R).

**Koszt skonfrontowany z rynkiem (v20.53.0).** Poprzednio zapisano, że modelu
kosztu nie ma dziś czym zmierzyć, bo brak realnych fillów. To była prawda o
*naszych* fillach, ale nie o rynku. Dane mikrostruktury są dostępne bez
handlowania. Pomiar: `docs/analysis/venue_microstructure_20260903.json`.

| | zmierzony spread | tick / cena | model |
|---|---|---|---|
| BTC | 0,0133 bps | 0,10 / 77 733 = **0,0129 bps** | 4 bps |
| XRP | 0,7316 bps | 0,0001 / 1,3671 = **0,7314 bps** | 4 bps |
| ZEC | 0,1229 bps | 0,01 / 820,15 = **0,1219 bps** | 4 bps |

Kontrola wiarygodności: każdy z trzech instrumentów ma średni spread równy
**dokładnie jednemu tickowi** swojej siatki cen. Trzy niezależne instrumenty
trafiające każdy w swój własny tick to mocna przesłanka, że pomiar jest
prawdziwy, a księgi są ciasne. `DEFAULT_SPREAD_FRAC = 0,0004` jest więc
zawyżone **5,5× dla XRP, 32× dla ZEC i 300× dla BTC**.

Głębokość szczytu księgi: BTC 480–516 tys. USD, XRP 17–25 tys., ZEC 5–6,4 tys.
Modelowane zlecenie ma **75 USD**. To 0,016% szczytu księgi na BTC i 1,5% na
ZEC — najcieńszym z trójki. Zlecenie tej wielkości mieści się w całości na
pierwszym poziomie, więc jego market impact jest w praktyce zerowy.

**Stąd błąd strukturalny, nie parametryczny:** model liczy impact z
partycypacji względem obrotu **całej świecy 5 m**. Dla zlecenia 75 USD
właściwym mianownikiem jest głębokość szczytu księgi, a nie obrót świecy —
i to jest źródłem członu, który samodzielnie przewyższał cały edge.

Zastrzeżenia, bez których ta liczba wprowadza w błąd:

- **To Binance USDT-M, nie BloFin.** Bot handluje na BloFinie, który jest
  mniejszy, więc jego spread i głębokość będą gorsze. Te liczby są **dolnym
  ograniczeniem** kosztu, nie jego odwzorowaniem.
- To pojedynczy snapshot na żywo (2026-09-03 03:32 UTC), a nie okno replayu
  z sierpnia. Historyczny spread wymagałby danych tickowych.
- Nie zmienia to fee: 6 bps taker w jedną stronę to realny, nieredukowalny
  koszt i największa pozostała pozycja (`fee_r` mediana 0,0320).

**Co to razem znaczy.** Obie strony rachunku były zepsute, w przeciwnych
kierunkach: koszt zawyżony o rząd wielkości na dwóch z trzech członów, a model
brutto zepsuty w kształcie (v20.52.0). Dlatego **żadnej z liczb −5,8939R,
−0,2198R ani +0,0476R nie wolno czytać jako werdyktu o strategii** — to są
wyjścia z modelu, którego oba człony właśnie okazały się niezgodne z pomiarem.

**Człon kosztu naprawiony (v20.57.0).** Dwie zmiany, obie licencjonowane
pomiarem, każda ze świadomym przesunięciem baseline'u.

*Spread* — `expected_net_r._spread_cost_frac` ma trzy źródła w kolejności
wiarygodności: żywa książka zleceń → zmierzony spread symbolu → stała. Replay
nie ma książki nigdy, więc druga ścieżka obsługuje cały backtest.

*Poślizg* — `replay_slip_round_trip` liczy teraz round-trip jako **jeden pełny
spread**, gdy zlecenie mieści się na szczycie księgi (bo wtedy całym kosztem
egzekucji względem mid jest przejście spreadu), a impact zaczyna się dopiero
przy zjadaniu księgi — z **głębokością szczytu** jako mianownikiem zamiast
obrotu całej świecy 5 m. Zmierzone: zlecenie mieści się na szczycie u wszystkich
19 symboli, więc dziś impact jest wszędzie zerowy — ale próg realnie wiąże
(XMR: 16% pierwszego poziomu) i przy większym koncie zacznie działać.

Skutek na 1062 → 1426 kandydatach:

| | przed | po spreadzie | po poślizgu |
|---|---|---|---|
| mediana `net_r` | −0,0914 | −0,0809 | **+0,0003** |
| netto dodatnich | 134 (12,6%) | 158 (14,9%) | **718 (50,4%)** |
| mediana kosztu | 0,1432 | 0,1308 | **0,0415** |
| mediana `slip_r` | 0,0901 | 0,0901 | **0,0002** |

**Czego ten wynik NIE mówi — i to jest ważniejsze od samej liczby.**

Po pierwsze, **liczba kandydatów wzrosła z 1062 do 1426**, bo tańszy koszt
przepuszcza setupy, które wcześniej odpadały na `V2_SL_TOO_TIGHT_VS_COST`
(mediana `sl_dist` spadła 0,0374 → 0,0294). To **nie jest** porównanie
kandydat-w-kandydata; zmieniła się populacja.

Po drugie, mediana `net_r` = +0,0003 to **zero, nie zysk**. System przestał
oceniać własne setupy na minus — to nie to samo co rentowność.

Po trzecie i najważniejsze: **strona brutto wciąż jest fikcją.** Wszystkie 1426
kandydatów ma status `PRIOR_ONLY`, a model dwustanowy został obalony pomiarem
w v20.52.0 (zakłada TP1 albo −1R; naprawdę 7,8% bierze pełną stratę, a 45%
kończy w ±0,25R). Liczymy więc netto jako różnicę zmierzonego kosztu i
**niezmierzonego** brutto. Naprawiliśmy jedną stronę równania z dwóch.

**Co zostało z kosztu:** opłata taker. `fee_r` ma medianę 0,0408 i jest teraz
**całym** kosztem — wyzerowanie jej podniosłoby netto dodatnich z 718 na 1235.
To jest koszt realny i nieredukowalny inaczej niż przez zlecenia maker.

Kontrola: kontrfaktyczny „stary floor major" daje teraz **gorszy** wynik niż
pomiar (718 → 550), bo 6 bps to więcej niż zmierzony spread większości symboli.
Narzędzie mówi to wprost zamiast dalej udawać, że jest to górne ograniczenie
korekty.

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
