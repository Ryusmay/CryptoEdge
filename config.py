# ============================================================
# CryptoEdge Bot - Konfiguracja
# ============================================================

import os
from pathlib import Path

# Ladowanie .env (proste, bez zewnetrznej biblioteki)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

# --- API KEYS (z .env) ---
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY", "")
BLOFIN_API_KEY = os.getenv("BLOFIN_API_KEY", "")
BLOFIN_API_SECRET = os.getenv("BLOFIN_API_SECRET", "")
BLOFIN_API_PASSPHRASE = os.getenv("BLOFIN_API_PASSPHRASE", "")
# LIVE: PAPER_TRADING=False + klucze Blofin → kapital z konta futures
LIVE_SYNC_BALANCE = True          # przy live odswiezaj equity z Blofin co cykl
LIVE_BALANCE_CACHE_SECONDS = 15

# --- Kapital i ryzyko ---
STARTING_CAPITAL = 100.0
LEVERAGE = 10
MAX_POSITIONS = 10
STRATEGY_MODE = "DAYTRADING_V2"  # V2 glowny; SWING/V1 poza UI / A/B replay
RISK_PER_TRADE = 0.22          # legacy; sizing = kapital / MAX_POSITIONS
DAILY_LOSS_LIMIT = 0.04          # 4% daily loss → halt nowych (x10: było 15% za luźno)

# --- Circuit breaker / heat ---
CONSECUTIVE_LOSS_LIMIT = 5       # N strat z rzędu → pauza
CONSECUTIVE_LOSS_PAUSE_MIN = 45  # minut pauzy po serii strat
MAX_SAME_DIRECTION_PCT = 0.65    # max 65% slotów w tym samym kierunku (LONG lub SHORT)

# --- Regime tiered (zamiast binarnego BLOCK) ---
REGIME_RANGE_SIZE_MULT = 0.50    # half-size w RANGE
REGIME_RANGE_MAX_POSITIONS = 10  # twardo 10, też w RANGE
REGIME_PANIC_SIZE_MULT = 0.0     # brak nowych wejść w PANIC

# --- Cross-sectional z-score (strength boost) ---
ZSCORE_ENABLED = True
ZSCORE_MIN_ABS = 1.5             # |z| powyżej tego daje bonus
ZSCORE_STRENGTH_BONUS = 0.08     # max bonus do strength przy wysokim |z|

# --- Sygnaly ---
MIN_SIGNAL_STRENGTH = 0.48  # było 0.55 – scoring rzadko dobijałTOP_N_COINS = 50              # legacy (nie limituje universe Blofin)
TOP_N_FETCH = 250
UNIVERSE_MODE = "blofin_usdt_futures"  # wszystkie pary USDT futures na Blofin

# --- Anomalie ---
PRICE_JUMP_THRESHOLD = 0.035
VOLUME_SPIKE_MULTIPLIER = 2.5    # wyzszy = latwiej SPIKE (progow change_24h nizej)

# --- Interwal ---
LOOP_INTERVAL_SECONDS = 1
# Pelny skan (fetch_top_coins + generate_signals na calym uniwersum - dziesiatki
# zapytan REST po swiece 4 interwalow per kandydat) nie musi sie powtarzac co
# iteracje petli. Miedzy pelnymi skanami leci tylko "szybki tick": swieza cena
# dla OTWARTYCH pozycji (tanie - 1 zbiorcze zapytanie, nie cale uniwersum) +
# trailing/SL/emergency/kill-switch, bo to musi reagowac natychmiast (co ~1s).
# Realny powod wprowadzenia (19-20.08): bot regularnie wyczerpywal limit
# zapytan Blofin (500/min -> 5 min ban, 1500/5min -> 1h ban), bo pelny skan
# probowal isc w rytmie petli 1s zamiast osobnym, wolniejszym rytmem.
FULL_SCAN_INTERVAL_SECONDS = 20

# Punkt 9 planu: event bus (zdarzenia cyklu/odrzucen do "laboratorium").
# Domyslnie wylaczony - nie kazdy ma/chce Redis; wlacz jesli masz gdzie to
# konsumowac. Patrz event_bus.py.
EVENT_BUS_ENABLED = False
EVENT_BUS_REDIS_URL = "redis://localhost:6379/0"

# Punkt 9 planu: gRPC jako drugi interfejs (obok HTTP). Domyslnie wylaczony -
# to opcjonalny dodatek dla zewnetrznych klientow/narzedzi. Patrz grpc_service.py.
GRPC_SERVICE_ENABLED = False
GRPC_SERVICE_PORT = 50061

# ============================================================
# UI DESK/SCAN/LAB (21.08.2026, domyslne od 19.9.0) - przebudowa interfejsu
# na 4 strony wg specyfikacji (theme.py, DataAdapter.candidates()/
# why_no_trade()). Shell+DESK, SCAN (QTableView) i LAB (Analysis Workspace)
# sa kompletne - nowy layout jest teraz glownym interfejsem. Stary,
# 7-zakladkowy shell pozostaje w kodzie w pelni nietkniety i osiagalny
# przez reczne ustawienie UI_DESK_V2 = False (np. do porownan/rollbacku).
# ============================================================
UI_DESK_V2 = True

# ============================================================
# DAYTRADING V2 - hierarchia timeframe (1D bias -> 4h confirm -> 1h setup
# mapa -> 15m trigger -> 5m opcjonalne potwierdzenie). Za przelacznikiem
# STRATEGY_MODE="DAYTRADING_V2" - silnik V1 (DAYTRADING) pozostaje
# nietkniety, oba wspoldzialaja obok siebie do czystego A/B na tym samym
# oknie replay. Patrz daytrading_engine_v2.py.
# ============================================================
DAYTRADING_V2_ENABLED = True  # V2 glowny silnik
STRATEGY_MODE = "DAYTRADING_V2"

# 21.08.2026: telemetria z realnej sesji PAPER pokazala, ze bias 1D/4h
# wymagal jednomyslnosci wszystkich 3 sygnalow (price>EMA200, EMA50>EMA200,
# SuperTrend) i to samo w sobie odrzucalo V2_1D_NO_BIAS ~63% ocenianych
# kandydatow, nawet przy silnym ruchu BTC (+8% 24h) - pojedynczy spozniony
# wskaznik (najczesciej SuperTrend, z natury wolniejszy) blokowal caly
# sygnal. Teraz wystarcza 2 z 3 (wiekszosc). Ustaw z powrotem na 3, zeby
# wrocic do starego, w pelni jednomyslnego zachowania.
DAYTRADING_V2_BIAS_MIN_AGREE = 2

# 21.08.2026: nowsze/mniejsze pary (np. swiezo dopiero co wylistowane na
# Blofin futures) nie maja 200+ dziennych swiec potrzebnych do EMA200 na
# 1D, wiec kazdorazowo odrzucalo je V2_1D_DATA_NA/V2_INDICATORS_NA, zanim
# w ogole dostaly szanse na analize - byly calkowicie wykluczone z handlu.
# Gdy 1D nie ma wystarczajacej historii, silnik uzywa 4h jako kotwicy
# kierunku (np. "4h ma trend spadkowy -> rozwaz shorta") - jednomyslnosc z
# 1h (swing, 15m trigger, 5m weto) i tak jest wymagana dalej w tym samym
# funnelu, wiec ryzyko nie jest wieksze niz na sciezce standardowej, tylko
# kotwica jest plytsza (4h zamiast 1D). Ustaw na False, zeby wrocic do
# twardego odrzucania par bez pelnej historii 1D.
DAYTRADING_V2_ALLOW_4H_ANCHOR_WITHOUT_1D = True

WARMUP_ENABLED = True
WARMUP_SECONDS = 300
# 21.08.2026: domyslnie False - rozruch ma trwac PELNE WARMUP_SECONDS (300s),
# zeby Blofin nie dostal nawalu zapytan zaraz po cold-starcie. Wczesniejsze
# wyjscie (po samych 60s, jesli ready_n/feed/bucket sa juz OK) bylo
# domyslnie WLACZONE, przez co realny czas rozruchu wynosil ~60-90s zamiast
# 300s - zawor bezpieczenstwa dzialal krocej niz nazwa/log sugerowaly.
WARMUP_ALLOW_EARLY_READY = False
WARMUP_MIN_PAIRS_READY = 20
WARMUP_CANDLES_1H = 180
WARMUP_CANDLES_15M = 120
WARMUP_NEED_1H = 80
WARMUP_NEED_15M = 40
WARMUP_MIN_BUCKET = 0.35
BACKFILL_MAX_JOBS_PER_DRAIN = 8

# Punkt 6: swing 1h - filtr rozmiaru (x ATR) i czasu (min. swiec), bez
# look-ahead (pivot potwierdzony dopiero right_confirm swiec po nim).
DAYTRADING_V2_SWING_MIN_MOVE_ATR = 1.5
DAYTRADING_V2_SWING_MIN_BARS = 3
DAYTRADING_V2_SWING_RIGHT_CONFIRM = 2

# Punkt 13: SL = swing 1h +/- bufor ATR.
DAYTRADING_V2_SL_ATR_BUFFER = 0.5

# Punkt 15-16: TP1 = min(1R, najblizszy poziom 1h); TP2 = extension 1.272-1.618 albo 2R.
DAYTRADING_V2_TP1_R = 1.0
DAYTRADING_V2_TP2_EXTENSION_RATIO = 1.618  # jesli brak sensownej extension, fallback do TP2_R
DAYTRADING_V2_TP2_R_FALLBACK = 2.0

# Punkt 18: min. R:R do TP1, ponizej ktorego "no trade" (SL zbyt szeroki
# wzgledem TP1).
DAYTRADING_V2_MIN_TP1_R_RATIO = 0.6

# Punkt 19: SL musi byc >= N x koszt round-trip (liczony z realnego configu
# COMMISSION_RATE/SLIPPAGE, nie ze sztywnych "18 bps" - to sie zmienia).
DAYTRADING_V2_MIN_SL_VS_COST_MULT = 3.5

# Punkt 20: ryzyko % kapitalu na trade (odleglosc SL 1h decyduje o wielkosci
# pozycji, strength/quality tylko mnozy w gore/dol wokol tego).
DAYTRADING_V2_RISK_PCT_OF_CAPITAL = 0.5  # legacy; V2 size = % kapitalu (margin)
DAYTRADING_V2_SIZE_MODE = "capital_pct"  # capital_pct | risk_sl
DAYTRADING_V2_MARGIN_PCT_MIN = 5.0       # 5% equity na wejscie (margin)
DAYTRADING_V2_MARGIN_PCT_MAX = 10.0      # 10% equity na najmocniejsze
# 21.08.2026: margin ma byc NIEZALEZNY od strength (spec: "margin moze byc
# niezalezny od sily") - domyslnie stala wartosc w przedziale [MIN,MAX],
# nie interpolowana na podstawie signal["strength"]. Skalowanie strength
# zostaje jako opcja (DAYTRADING_V2_MARGIN_STRENGTH_SCALED=True), nie
# usuniete - tylko wylaczone domyslnie.
DAYTRADING_V2_MARGIN_STRENGTH_SCALED = False
DAYTRADING_V2_MARGIN_PCT_FIXED = 7.5     # uzywane gdy MARGIN_STRENGTH_SCALED=False - stale w [MIN,MAX]
# 21.08.2026: znaleziono w telemetrii sesji (decision_telemetry.jsonl), ze
# WSZYSTKIE (6/6) sygnaly V2, ktore przeszly wszystkie bramki silnika (1D/4H/
# 1H/15m/5m), zostaly odrzucone przez risk_manager (PORTFOLIO_RISK/CORR_RISK)
# - mimo ZERO otwartych pozycji (open_positions=[] w bot_state.json). Przyczyna:
# sizing V2 (DAYTRADING_V2_SIZE_MODE="capital_pct") liczy notional wylacznie z
# % marginu * dzwignia (7.5% * 10x = 75% equity jako notional), CALKOWICIE
# niezaleznie od odleglosci SL - podczas gdy _portfolio_open_risk_ok() liczy
# dolarowe ryzyko jako notional * sl_dist i porownuje je do twardego limitu
# MAX_PORTFOLIO_OPEN_RISK (2.5% equity). Przy typowym SL 1h w warunkach
# podwyzszonej zmiennosci (ATR ratio >1.5x, jak w przegladanej sesji) samo
# JEDNO wejscie generowalo ~2.9-3.1% ryzyka - powyzej limitu calego portfela,
# wiec KAZDY sygnal V2 byl gwarantowanym odrzuceniem, niezaleznie od tego czy
# cokolwiek innego bylo otwarte. To osobny, strukturalny bug od bramek 1D/4H
# (te juz naprawione w 19.11.0) - sizing i limit ryzyka portfela nie byly ze
# soba spojne. Fix: dodatkowy, mniejszy "sufit" na ryzyko pojedynczego trade'a
# V2 (notional * sl_dist), stosowany PRZED limitem portfela - w razie potrzeby
# zmniejsza notional (nie odrzuca od razu), tak by pojedyncze wejscie mialo
# margines do limitu 2.5% zamiast go od razu wyczerpywac. Ustawione wyraznie
# PONIZEJ MAX_PORTFOLIO_OPEN_RISK, zeby zostawic miejsce na 2+ rownoczesne
# pozycje. Ustaw na bardzo duza wartosc (np. 100.0), zeby wylaczyc ten sufit
# i wrocic do czystego sizingu margin-based bez zadnej korekty pod SL.
DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE = 1.0
# 21.08.2026: powrot do "jedno wejscie na jeden impuls" (plan hierarchii
# timeframe, punkt 8) - addon/pyramiding na ten sam swing wylaczony domyslnie.
DAYTRADING_V2_MAX_ENTRIES_PER_SWING = 1
DAYTRADING_V2_ALLOW_ADDON = False

# Punkt 21-23: hamulce czestotliwosci (cooldown w minutach).
DAYTRADING_V2_COOLDOWN_AFTER_EXIT_MIN = 60       # kazde wyjscie (pkt 21, srodek zakresu 45-90)
DAYTRADING_V2_COOLDOWN_AFTER_SL_SAME_SIDE_MIN = 240  # po SL w te sama strone (pkt 22, do zamkniecia nastepnej 4h)
DAYTRADING_V2_COOLDOWN_AFTER_INVALIDATION_MIN = 180  # po invalidation, jesli cokolwiek zostalo (pkt 23)
DAYTRADING_V2_MIN_REENTRY_GAP_MIN = 10  # pkt 26: zakaz re-entry tym samym kierunkiem w <=10 min

# Punkt 29: symbole wylaczone z daytradingu V2 (niska beta / inna klasa
# aktywow) - out albo osobny, rzadszy profil pozniej.
DAYTRADING_V2_EXCLUDED_SYMBOLS = ["XAU", "TRX"]
# 21.08.2026, druga iteracja (patrz generate() w daytrading_engine_v2.py):
# uzytkownik jawnie odrzucil plaski sufit "WS-connected -> stala liczba
# kandydatow" (byl 60, wczesniej None/brak limitu - oba dawaly cold-start
# burst proporcjonalny do calego uniwersum, patrz historia ponizej) na rzecz
# INNEGO mechanizmu bezpieczenstwa: bez wzgledu na to, ile kandydatow
# ostatecznie wchodzi w gre (45 przy WS-down, CALE uniwersum przy
# WS-connected - patrz DAYTRADING_V2_COLD_START_BATCH_SIZE nizej), tylko
# ograniczona PACJA nowych, nigdy niepobieranych symboli dostaje pelna
# kaskade 5 interwalow w danym cyklu. Juz "cieple" (raz pobrane) symbole sa
# oceniane co cykl tanio, bo TTL+WS-merge (_KLINE_CACHE_TTL_S_WS_CONNECTED w
# blofin_feed.py) trzyma ich cache swiezym bez powtarzania REST. Dzieki temu
# limit liczby kandydatow przestaje byc jedynym hamulcem na REST - hamulcem
# jest tempo rozgrzewania nowych symboli, wiec nie trzeba go juz sztywno
# ograniczac do 60 (patrz test_ws_ramp_up_targets_full_universe_no_hard_cap
# i test_cold_start_batch_pacing_stays_safe_regardless_of_universe_size).
#
# Historia kalibracji DAYTRADING_V2_MAX_CANDIDATES (limit REST-only,
# WS-down - to jest INNY parametr niz opisany wyzej, patrz nizej):
# 20.08.2026: 30->45->60->30. Cofniete po realnym 429 na Cyklu #1 (cold
# start) - PUBLIC_BUCKET obnizony z 5 do 3 req/s (byl kalibrowany na, nie
# ponizej, praktycznego limitu Blofin). Przy budzecie 3req/s i limicie 30
# daje to 52.2% w gorszym przypadku (WS padl).
# 21.08.2026: prosba byla podniesc 30->45 - ALE 45 NIE miesci sie bezpiecznie
# w budzecie STEADY-STATE (nie tylko cold-startu): to jest limit dla
# scenariusza "WS padl", wiec kazdy z tych kandydatow jest odswiezany
# WIECZNIE w krotkich TTL z _KLINE_CACHE_TTL_S (bez WS-merge, ktory
# ratowalby sytuacje) - to nie jest jednorazowy koszt rozgrzewki tylko trwaly
# budzet. Przy 45: 45*sum(1/ttl)/PUBLIC_BUCKET.refill_per_sec = 78.75%
# (przekracza próg 70% - patrz
# test_budget_stays_safe_at_configured_max_candidates_even_if_ws_is_down,
# ktory to wylapal natychmiast przy probie ustawienia 45). Dawkowanie partiami
# (DAYTRADING_V2_COLD_START_BATCH_SIZE) chroni tylko PIERWSZE rozgrzanie
# nowych symboli - nie zmniejsza kosztu WIECZNEGO odswiezania juz cieplych.
# 39 to najwyzsza wartosc calkowita, ktora wciaz miesci sie pod 70% (68.25%)
# - najblizej 45 jak sie bezpiecznie da bez ruszania progu 70% marginesu ani
# TTL (oba to swiadome decyzje z realnych incydentow, nie do zmiany przy
# okazji tego jednego parametru).
DAYTRADING_V2_MAX_CANDIDATES = 39
# Ile NOWYCH (nigdy wczesniej niepobieranych w tej instancji silnika)
# symboli dostaje pelna kaskade 5 interwalow (1D/4H/1H/15m/5m) REST w
# JEDNYM cyklu generate() - dotyczy TAKZE listy 45 kandydatow REST-only
# (WS padl), nie tylko ramp-upu przy WS-connected ("rest nie pobiera
# wszystkiego na raz, w partiach" - wprost z prosby uzytkownika 21.08.2026).
# 8 nowych symboli x 5 interwalow = 40 zapytan REST/cykl w najgorszym razie
# (wszystkie 8 realnie zimne) => 40/3 ~= 13.3s burst przy PUBLIC_BUCKET
# 3 req/s - rzedu wielkosci mniejszy niz dotychczasowe bezpieczne cold-starty
# (30*5/3=50s), i co najwazniejsze NIEZALEZNY od wielkosci calego uniwersum
# (nie rosnie nawet jesli WS-connected target obejmuje setki symboli - patrz
# test_cold_start_batch_pacing_stays_safe_regardless_of_universe_size). 8 =
# ta sama wartosc co BACKFILL_MAX_JOBS_PER_DRAIN (spojna konwencja pacingu
# w calym projekcie).
DAYTRADING_V2_COLD_START_BATCH_SIZE = 8
DAYTRADING_V2_TP1_FRAC = 0.50          # partial na TP1, reszta trail
DAYTRADING_V2_TP2_FRAC = 0.50          # 50% POZOSTALEJ po TP1; reszta trail
DAYTRADING_V2_TIME_STOP_HOURS = 24.0   # zamykaj tylko gdy < min R
DAYTRADING_V2_TIME_STOP_MIN_R = 0.35
DAYTRADING_V2_HARD_TIME_STOP_HOURS = 48.0
STALE_DATA_SECONDS = 45          # odmowa handlu gdy dane starsze niz 45s

# 21.08.2026: realny incydent - Blofin zwrocil na publicznym endpoincie
# naglowek Retry-After: 3600 (godzina). Uzytkownik potwierdzil z wlasnej
# wiedzy o Blofin: to REALNY, godzinny ban za zbyt czeste odpytywanie
# limitu, nie przypadkowo zawyzona wartosc naglowka - wiec dalsze zapytania
# w trakcie tego okna moga ban tylko przedluzyc/pogorszyc (typowe dla
# anti-abuse). blofin_feed.py._get() rozroznia teraz dwa przypadki wg tego
# progu: Retry-After <= BLOFIN_RATE_LIMIT_SHORT_RETRY_MAX_S -> typowy,
# chwilowy throttle, obslugiwany jak dotychczas (krotki blokujacy sleep +
# jedna ponowna proba). Retry-After > tego progu -> traktowany jako realny
# ban: wchodzimy w NIEBLOKUJACY cooldown (_rate_limited_until) na CALY
# zadany przez serwer czas (nie skracany!) - przez ten czas _get() zwraca
# None natychmiast, bez wysylania ANI JEDNEGO kolejnego zapytania, wiec
# watek skanujacy (bot_loop, osobny od UI) sie nie zamraza, a serwer nie
# dostaje wiecej ruchu podczas bana. 30s to bezpieczna granica - wszystkie
# dotychczasowe, realnie zaobserwowane throttle'e byly rzedu kilkunastu
# sekund (patrz PUBLIC_BUCKET), a 3600s z tego incydentu jest o dwa rzedy
# wielkosci wyzej - miedzy nimi nie ma dwuznacznosci.
BLOFIN_RATE_LIMIT_SHORT_RETRY_MAX_S = 30.0

# --- Paper Trading ---
PAPER_TRADING = True                 # True = DEMO (paper), False = LIVE
LIVE_EXECUTION_ENABLED = False      # True dopiero gdy podłączymy realne zlecenia Blofin

# --- Execution foundation (Etap 1) ---
# Isolated = strata ograniczona do marginu pozycji (jak w tradebocie).
# Kwota z risk managera to MARGIN; notional = margin * leverage.
BLOFIN_MARGIN_MODE = "isolated"     # isolated | cross
BLOFIN_POSITION_SIDE = "net"        # net (one-way) | long/short (hedge)
ORDER_REQUEST_TIMEOUT = 12          # s – POST place/cancel
ORDER_POLL_TIMEOUT = 8              # s – GET status
ORDER_WAIT_FILL_SECONDS = 3.0       # po market – krótki poll fill
RECONCILE_EVERY_CYCLES = 30         # co N cykli porównaj lokalne vs giełda (gdy LIVE)

# --- Protection (Etap 2) ---
EXCHANGE_SL_ENABLED = True          # TPSL po stronie Blofin (gdy LIVE_EXECUTION)
EXCHANGE_TP_ENABLED = False         # TP na giełdzie OFF (ride trend / trailing lokalny)
EXCHANGE_TP_MIN_DISTANCE_PCT = 0.3
TPSL_TRIGGER_TYPE = "last"          # last | mark | index
LOCAL_SL_ALWAYS = True              # lokalny SL nawet gdy exchange SL OK
LOCAL_TP_BACKUP = False
RECOVERY_REATTACH_EXCHANGE_SL = True
RECOVERY_WARN_ORPHANS = True
RECOVERY_RECONCILE_IN_PAPER = True  # paper: porównuj lokalny stan vs planowane SL

# --- Market-data correctness (Etap 3) ---
CLOSED_CANDLES_STRICT = True
FILTER_UNIVERSE_BY_REGISTRY = False
STALE_KLINES_SECONDS = 600


COMMISSION_RATE = 0.0006
SLIPPAGE = 0.0008

# --- Take Profit / Stop Loss ---
TAKE_PROFIT_PCT = 35.0           # max soft TP (z dzwignia) – realny TP z analizy ma priorytet
STOP_LOSS_PCT = -22.0            # % PnL pozycji; przy x10 ≈ -2.2% ceny

# --- Trailing Stop ---
TRAILING_STOP_ENABLED = True
TRAILING_STOP_ACTIVATION_PCT = 10.0  # trail dopiero po +10% PnL (mniej whipsaw)
TRAILING_STOP_DISTANCE_PCT = 9.0     # szerszy trail – ride the trend
TRAILING_TIGHTEN = False             # nie zaciskaj trail agresywnie
CLOSE_ONLY_MAX_TP = True
RIDE_TREND = True                    # przy silnym trendzie nie zamykaj na TP – tylko trailing
RIDE_TREND_MIN_STRENGTH = 0.55       # min sila sygnalu zeby "jechac"
NO_HARD_TP = True                    # wylacz twarde TP – realizacja tylko trail/SL
STALE_POSITION_MINUTES = 0           # 0 = wylaczone (nie zamykaj za brak ruchu)
STALE_POSITION_MIN_PNL_PCT = 1.5     # legacy, nieuzywane gdy minutes=0


# --- Pliki ---
LOG_FILE = "logs/bot_log.csv"
STATE_FILE = "logs/bot_state.json"
SIGNALS_FILE = "logs/signals_history.csv"

# --- Filtry ---
# MIN_VOLUME_24H_USD ustawione nizej w sekcji ulepszen

# --- Margin call / likwidacja (paper) ---
MARGIN_CALL_ENABLED = True
MARGIN_CALL_THRESHOLD = 0.80      # strata >= 80% marginu → MARGIN CALL (zamknij)

# --- Drawdown / fail-safe ---
MAX_DRAWDOWN_PCT = 0.15          # 15% od peak equity → close-all + halt
CLOSE_ALL_ON_DAILY_LIMIT = True  # przy daily loss zamknij tez otwarte
CLOSE_ALL_ON_DRAWDOWN = True

# --- Kontrola reczna (pliki w folderze bota) ---
# utworz pusty plik PAUSE aby wstrzymac nowe wejscia
# utworz pusty plik CLOSE_ALL aby zamknac wszystkie pozycje
# utworz pusty plik RESUME aby wznowic po PAUSE
CONTROL_PAUSE_FILE = "PAUSE"
CONTROL_CLOSE_ALL_FILE = "CLOSE_ALL"
CONTROL_RESUME_FILE = "RESUME"

# --- Funding ---
FUNDING_ENABLED = True
FUNDING_EXTREME = 0.001          # |funding| > 0.1% = ekstremum (ostrzezenie w scoringu)
SRC_DIVERGENCE_SCORE_MULT = 0.95 # kara sily przy rozjezdzie zrodel (bylo 0.85)
PERP_CONTEXT_ENABLED = True      # funding + OI + F&G -> size, NIE trigger 15m
FNG_EXTREME_GREED = 75
FNG_EXTREME_FEAR = 25
FNG_GREED_LONG_SIZE = 0.70
FNG_FEAR_SHORT_SIZE = 0.70
FUNDING_CROWD_SIZE = 0.75
OI_SPIKE_PCT = 12.0
OI_SPIKE_SIZE = 0.80
FUNDING_PERIOD_HOURS = 8.0       # okno settlement funding
ACCOUNTING_DECIMAL = True        # Decimal w size/PnL/fee

# ============================================================
# Ulepszenia jakosci sygnalow / ryzyka (2026-08)
# ============================================================

# Tryb agresywny = pozwala wejsc bez STRAT_1H_OK (domyslnie OFF)
AGGRESSIVE_MODE = False
REQUIRE_STRATEGY_1H = True       # legacy name; realnie = REQUIRE primary TF (4h)

# Rezerwa kapitalu – nigdy nie zajmij 100%
CAPITAL_RESERVE_PCT = 0.20       # 20% depo zawsze wolne

# Plynnosc / spread / multi-source
MIN_VOLUME_24H_USD = 100_000     # min wolumen 24h USD (bylo 500k – za ostro)
MAX_SPREAD_PCT = 0.25            # max spread order book %
MAX_SOURCE_DIVERGENCE_PCT = 3.0  # max rozjazd cen miedzy zrodlami
# REQUIRE_MULTI_SOURCE – patrz sekcja Sizing / cache / multi-source
MIN_ORDERBOOK_DEPTH = 0          # 0 = wylaczone (gdy brak danych OB)

# ATR exits
USE_ATR_STOPS = True
ATR_SL_MULTIPLIER = 3.2          # trend mode: jeszcze szerszy SL
ATR_TRAIL_MULTIPLIER = 1.5       # trailing distance w ATR
VOLATILITY_SIZE_SCALE = True     # zmniejsz size przy wysokim ATR%

# Proxy HTF gdy brak natywnych 4h
STRATEGY_1H_PROXY = True         # 1h jako proxy primary gdy 4h NA
STRATEGY_AGG_4H_FROM_1H = True   # agreguj 1h → syntetyczne 4h

# Early loss cut (miękki time-stop)
EARLY_LOSS_CUT_ENABLED = True
EARLY_LOSS_CUT_MINUTES = 60      # min. wiek pozycji
EARLY_LOSS_CUT_PNL_PCT = -8.0    # PnL% poniżej tego
EARLY_LOSS_CUT_REQUIRE_MTF = True  # wymagaj odwrócenia MTF

# Candle-gate trailing: nie zaciskaj trail gdy LTF nadal zgodne
TRAILING_CANDLE_GATE = True

# Paper realism
TAKER_FEE = 0.0006
MAKER_FEE = 0.0002
USE_ORDERBOOK_SPREAD = True      # half-spread jako koszt wejscia
ENTRY_DELAY_SECONDS = 0          # opoznienie (0 w paperze szybkim)
FUNDING_ACCRUAL = True           # naliczaj funding co cykl przytrzymania

# Multi-timeframe
MTF_ENABLED = True
MTF_TIMEFRAMES = ["15m", "1h", "4h", "1d"]
MTF_REQUIRE_ALIGN = 3            # trend: min 3 TF zgodne (z 4)

# --- Cooldown / BTC correlation ---
SYMBOL_COOLDOWN_MINUTES = 40     # po zamknięciu: 40 min bez re-entry na ten symbol
BTC_CORRELATION_FILTER = True
BTC_CORRELATION_HARD = False     # False = kara do strength (nie twardy blok)
BTC_STRENGTH_PENALTY = 0.18      # o ile obcinac strength przy niekorzystnym BTC
BTC_DUMP_THRESHOLD = -2.0        # BTC 24h <= -2% → kara / blok LONG na altach
BTC_PUMP_THRESHOLD = 2.0         # BTC 24h >= +2% → kara / blok SHORT na altach
MAX_REJECT_LOG = 30              # ile odrzucen trzymac w UI

# --- Partial TP / scale-out ---
# Po osiągnięciu +50% PnL (z dźwignią) zamknij 50% pozycji;
# reszta jedzie wyłącznie na trailing stop.
PARTIAL_TP_ENABLED = True
PARTIAL_TP_PCT = 0.50              # ile pozycji zamknąć (50%)
PARTIAL_TP_TRIGGER_PCT = 50.0      # próg PnL% pozycji (z dźwignią), nie % ceny

# --- Alerty systemowe ---
ALERTS_ENABLED = True
ALERT_ON_OPEN = False
ALERT_ON_CLOSE = True
ALERT_ON_HALT = True
ALERT_ON_MARGIN_CALL = True
ALERT_ON_FEED_FAIL = True
ALERT_SOUND = True               # dźwięk Windows
ALERT_PUSH = True                # toast / balloon Windows

# --- Sizing / cache / multi-source ---
SIZE_BY_STRENGTH = True          # wiekszy strength → wieksza pozycja
SIZE_STRENGTH_FLOOR = 0.48       # = MIN_SIGNAL_STRENGTH; skala size od progu wejscia
SIZE_STRENGTH_CAP = 1.0
SIZE_MIN_FRACTION = 0.45         # najslabszy kandydat = 45% normalnego size
SIZE_MAX_FRACTION = 1.25         # najsilniejszy = 125% normalnego size
ADAPTIVE_SIZE_ENABLED = True     # jedna warstwa adaptacji notional
ADAPT_MULT_FLOOR = 0.20
ADAPT_MULT_CEIL = 1.25
ADAPT_VOL_ELEV_MULT = 0.75
ADAPT_VOL_HIGH_MULT = 0.50
ADAPT_LOSS_STREAK_MULTS = (1.0, 0.85, 0.65, 0.45)  # 0,1,2,3+ strat
ADAPT_DD_SOFT_PCT = 6.0
ADAPT_DD_SOFT_MULT = 0.70
ADAPT_DD_HARD_PCT = 12.0
ADAPT_DD_HARD_MULT = 0.40
ADAPT_DAILY_HALFWAY_MULT = 0.70
ADAPT_DAILY_NEAR_LIMIT_MULT = 0.35

INDICATOR_CACHE_SECONDS = 12     # RSI/MACD cache (bylo hardcode 90s)

# Multi-source: "off" | "all" | "majors"
# majors = wymagaj 2+ zrodel gdy volume_24h >= MULTI_SOURCE_MAJOR_VOLUME
REQUIRE_MULTI_SOURCE = False
MULTI_SOURCE_MODE = "majors"     # off / all / majors
MULTI_SOURCE_MAJOR_VOLUME = 5_000_000  # USD 24h – powyzej tego wymagaj multi-source
# Symbole, dla ktorych utrzymujemy WebSocket Binance jako potwierdzenie ceny
# (BTC/ETH + wieksze altcoiny) - patrz binance_ws.py.
BINANCE_WS_MAJOR_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "TON"]

# --- Rezim rynku / volume breakout / OB depth ---
REGIME_ENABLED = True
REGIME_ATR_PERIOD = 14
REGIME_ATR_MA = 50              # ATR vs srednia ATR
REGIME_PANIC_ATR_MULT = 1.8     # ATR > 1.8x sredniej = PANIC
REGIME_TREND_ADX_PROXY = 0.8    # |BTC 24h| duzy + ATR umiarkowany = trend
REGIME_RANGE_BTC_MAX = 1.2      # |BTC 24h| < 1.2% i niski ATR = RANGE

VOLUME_MA_PERIOD = 20
VOLUME_BREAKOUT_MULT = 1.75     # vol swiecy > 1.75x MA = potwierdzenie
VOLUME_BREAKOUT_TF = "15m"      # TF do volume vs MA

OB_DEPTH_BAND_PCT = 0.5         # ±0.5% od mid
OB_MIN_DEPTH_USD = 3500.0       # min depth USD – odrzucaj cieńsze booki przy x10
OB_THIN_STRENGTH_PENALTY = 0.12

# --- Trend-following mode (4h + 1d) ---
STRATEGY_PRIMARY_TF = "4h"       # glowny sygnal
STRATEGY_FILTER_TF = "1d"        # filtr kierunku (musi sie zgadzac)
REQUIRE_PRIMARY_STRATEGY = True  # twardy wymog pass na 4h (gdy jest wynik)
REQUIRE_DAILY_ALIGN = True       # 1d direction = sygnal
BLOCK_RANGE_REGIME = False       # False = tylko kara strength (nie twardy blok całego RANGE)
BLOCK_STRAT_NA_IN_RANGE = True   # STRAT_PRIMARY_NA + RANGE → wymagaj MTF lub wysokiej siły
MTF_MIN_VOTES_FALLBACK = 2       # bez 4h: min 2 zgodne TF
STRAT_NA_RANGE_MIN_STRENGTH = 0.68  # w RANGE bez 4h: wpuść tylko silne (≥0.68)
RANGE_STRENGTH_PENALTY = 0.10
EXIT_ON_SUPERTREND_FLIP = True   # zamknij gdy ST 4h sie odwroci
EXIT_ON_HTF_OPPOSITE = True      # zamknij przy silnym przeciwnym 1d/4h
MIN_SIGNAL_STRENGTH_TREND = 0.58

COUNTER_TREND_MIN_RS = 5.0   # |RS| vs BTC min do counter-trend (soft)

# --- Anti-VELVET: pump/dump chase + thin book ---
BLOCK_OB_THIN = True             # twardy reject cienkiego order booka
BLOCK_PUMP_CHASE_PCT = 22.0      # |24h| >= 22% bez STRAT_PRIMARY_OK → reject
REQUIRE_STRAT_FOR_COUNTER = True # counter-trend soft tylko z pass 4h
COUNTER_TREND_BLOCK_IF_NA = True # STRAT_PRIMARY_NA + counter-trend → reject

# --- Log retention ---
LOG_RETENTION_DAYS = 3           # trzymaj wpisy CSV z ostatnich N dni
LOG_MAX_LINES = 50000            # twardy limit linii na plik (po trim dni)
LOG_CLEANUP_EVERY_CYCLES = 100   # co ile cykli robic cleanup
LOG_CLEANUP_ENABLED = True       # automatyczne czyszczenie logs/ przy starcie i co N cykli
LOG_DIR_MAX_MB = 80              # gdy folder logs/ wiekszy — kasuj najstarsze archiwa
# Konsola (logs/console.log) — osobna rotacja po rozmiarze
CONSOLE_LOG_MAX_MB = 5           # po przekroczeniu: console_YYYYMMDD_HHMMSS.log
CONSOLE_LOG_KEEP = 8             # ile zrotowanych console_*.log zostawic
# 22.08.2026: caly output konsoli i tak leci do logs/console.log (patrz
# console_capture.py), wiec samo okno cmd/powershell obok natywnego UI
# PySide6 jest zbedne - dwa otwarte okna zamiast jednego. Po starcie UI
# okno konsoli jest ukrywane (Windows, ShowWindow SW_HIDE) - nic sie nie
# gubi, log nadal pelny na dysku. False = zostaw okno konsoli widoczne
# (np. do zywego podgladu bez zagladania do pliku).
HIDE_CONSOLE_ON_UI_START = True

STOP_ENGINE_TP_PCT = 5.0   # przy STOP: TP dla pozycji nie-na-plusie (% PnL z dzwignia)
STOP_ENGINE_MAX_PRICE_AGE_S = 60.0  # jesli last_price_map starszy niz to (sek.), nie decyduj o zamknieciu na "plusie" wg tych cen - zbyt ryzykowne


# --- Portfolio risk (Etap 5) ---
PORTFOLIO_RISK_ENABLED = True
MAX_GROSS_EXPOSURE_MULT = 3.0      # gross notional ≤ 5× equity
MAX_NET_EXPOSURE_MULT = 3.5        # |net| ≤ 3.5× equity
MAX_EFFECTIVE_LEVERAGE = 8.0       # gross/equity
MAX_CLUSTER_EXPOSURE_MULT = 1.2    # jeden klaster ≤ 2.5× equity
MAX_CLUSTER_POSITIONS = 4          # max pozycji w jednym klastrze

# --- Hardening (post etap 1-6) ---
RECONCILE_SIZE_TOLERANCE_PCT = 5.0
RECONCILE_SIZE_TOLERANCE_ABS = 0.01
BLOCK_ENTRIES_ON_RECONCILE_DRIFT = True
REQUIRE_LEVERAGE_SET = True
PROTECTION_REQUIRE_FILL_SIZE = True
EMERGENCY_CLOSE_CONFIRM_WAIT = 0.8
BLOFIN_POSITION_MODE = "one_way"   # one_way | hedge
SIZE_ON_EQUITY = True              # sizing na capital + UPL
PAPER_USE_ACTUAL_NOTIONAL = True   # paper size = actual po lot size

# --- 4H / 1D klines ---
MIN_BARS_4H_1D = 225           # minimum zamkniętych świec native 4H/1D
FETCH_LIMIT_4H_1D = 250        # request limit (zapas nad min)
PROXY_1H_LIMIT_FOR_4H = 1000   # paginowane 1H → agregacja 4H

# --- Risk-based sizing (zamiast równego % margin) ---
RISK_PCT_MIN = 0.0060            # Trend: 0.60% equity (słaby strength)
RISK_PCT_MAX = 0.0090            # Trend: 0.90% equity max / trade
RISK_PCT_DEFAULT = 0.0075        # Trend BASE ~0.75%
# Reversal — bardziej konserwatywny (łapanie noża)
REVERSAL_RISK_PCT_MIN = 0.0030   # 0.30% equity
REVERSAL_RISK_PCT_MAX = 0.0055   # 0.55% equity max
REVERSAL_RISK_PCT_DEFAULT = 0.0040  # 0.40% base
DAYTRADING_RISK_PCT_MIN = 0.010     # 1.0% equity
DAYTRADING_RISK_PCT_MAX = 0.018     # 1.8% equity
DAYTRADING_RISK_PCT_DEFAULT = 0.014
MAX_POSITION_MARGIN_EQUITY_FRAC = 0.12  # room for 10% V2 + buffer
MAX_NOTIONAL_EQUITY_FRAC = 2.0   # hard cap notional ≤ 2× equity
MIN_NOTIONAL_USD = 20.0             # nie otwieraj nóg groszowych ($0.44)

# --- Daytrading Engine (4H/1H bias, 15m setup, 5m timing) ---
# 20.08.2026: 30->45->60->30. Podnieslismy do 60, potem znalezlismy realny
# 429 na Cyklu #1 (cold start) - PUBLIC_BUCKET obnizony z 5 do 3 req/s
# (byl kalibrowany DOKLADNIE na niedokumentowanym praktycznym limicie
# Blofin, nie wyraznie ponizej niego). Przy nowym, mniejszym budzecie 60
# kandydatow przekraczaloby 100% budzetu w stanie ustalonym (nie tylko przy
# cold-starcie) - 30 daje 52.2%, bezpieczny margines. Patrz
# test_budget_stays_safe_at_configured_max_candidates_even_if_ws_is_down
# (teraz liczy wprost wzgledem PUBLIC_BUCKET.refill_per_sec, nie wobec
# zaszytej na sztywno liczby - ten sam blad kalibracji sie nie powtorzy
# w milczeniu).
DAYTRADING_MAX_CANDIDATES = 30  # szerszy liquid funnel; nie zmienia progów jakości setupu
# Adaptacyjnie: gdy WS jest polaczony, budzet REST na kandydata spada do
# ~0.0048 req/s (>1000 kandydatow zmiescilo by sie w 100% budzetu PUBLIC_BUCKET)
# - REST przestaje byc realnym ograniczeniem. None = brak limitu (caly
# wazny/plynny universe dostaje pelna kaskade). DAYTRADING_MAX_CANDIDATES
# powyzej pozostaje bezpiecznym sufitem na wypadek, gdyby WS akurat padl.
# UWAGA: to przesuwa cale ryzyko na czas obliczen wskaznikow (nie mierzony
# wprost w tym srodowisku) wzgledem FULL_SCAN_INTERVAL_SECONDS=20 - stad
# monitoring realnego czasu skanu (patrz app.py, ostrzega w logu, jesli
# skan przekroczy ten budzet czasowy).
DAYTRADING_MAX_CANDIDATES_WS_CONNECTED = None
DAYTRADING_SETUP_CHOP_MAX = 61.8
DAYTRADING_ADX_MIN = 15.0  # twarda podloga dla ADX 15m; ponizej brak wiarygodnego trendu intraday
DAYTRADING_ADX_QUALITY_MIN = 18.0  # od tego poziomu brak kary do strength

# HTF bias (4h/1h): 90-dniowy replay na 10 liquid symbolach (osobna galaz kodu,
# przed wprowadzeniem audit_relax) dal 7 transakcji lacznie - 7/10 symboli,
# w tym BTC/ETH/SOL/XRP, mialo ZERO transakcji. DAY_HTF_CONFLICT byl
# najczestszym powodem odrzucenia (~42-44% wszystkich decyzji, stabilne
# niezaleznie od reżimu). Twardy AND(4h, 1h) blokowal tez normalny lag miedzy
# interwalami, nie tylko realny konflikt kierunkow.
# DAYTRADING_HTF_SOFT_MODE=True: pelna zgoda obu TF nadal daje pelna sile; gdy
# 4h ma wyrazny kierunek a 1h jest neutralny/w trakcie przejscia (NIE
# przeciwny), sygnal przechodzi dalej ze zredukowana sila zamiast twardego
# rejectu. Realny konflikt (4h i 1h wprost przeciwne) nadal blokuje - to nie
# jest lag, to prawdziwa niezgoda. To dziala jako nowe DOMYSLNE zachowanie,
# niezalezne od `audit_relax` (ktory zostaje jako osobne narzedzie do
# mierzenia jeszcze bardziej agresywnej polityki: pelne przejscie na kierunek
# 1h nawet przy realnym konflikcie 4h).
# Ustaw False, zeby wrocic do starego, twardego AND.
DAYTRADING_HTF_SOFT_MODE = True
DAYTRADING_HTF_PARTIAL_STRENGTH_MULT = 0.55  # kara za czesciowe (nie pelne) potwierdzenie 1h

# DAY_15M_NOT_ALIGNED byl #2 blockerem (34-45% odrzucen), drugi twardy AND w
# lejku (obok HTF): setup_align = ema_15m_zgodna_z_kierunkiem AND NIE_bb_extreme.
# RADYKALNE (18.08): rozdzielone na dwa osobne warunki zamiast jednego AND -
# 1) kierunek EMA na 15m WCIAZ musi sie zgadzac (to prawdziwy sygnal "zly
#    kierunek", zostaje twardym blokiem)
# 2) "cena juz przy skraju Bollingera" (mozliwe przegrzanie) - teraz TYLKO
#    kara do strength, nie blokada. W silnym trendzie "przegrzanie" bywa
#    kontynuacja, nie odwrocenie.
# Cel: zebranie realnej probki transakcji do oceny czy strategia w ogole ma
# przewage - przy 0 transakcji nie da sie tego ocenic. Do rewizji po zebraniu
# kilkudziesieciu+ zamknietych pozycji (patrz METHODOLOGY.md).
DAYTRADING_SETUP_SOFT_MODE = True
DAYTRADING_SETUP_PARTIAL_STRENGTH_MULT = 0.6  # kara gdy EMA ok, ale cena przy skraju Bollingera
DAYTRADING_SL_ATR_MULT = 2.0        # 5m noise; struktura 15m ma pierwszeństwo
DAYTRADING_TIMING_REQUIRE_ST = True    # 15m SuperTrend (5m za szumne)
DAYTRADING_TIMING_TF = "15m"
DAYTRADING_TIMING_REQUIRE_MACD = False # MACD tylko w quality, nie twarda bramka
# TYMCZASOWO poluzowane celem zebrania probki transakcji do oceny strategii -
# rewizja obowiazkowa po zebraniu realnych danych PnL (patrz notatka w
# DAYTRADING_SETUP_SOFT_MODE nizej - ten sam powod).
DAYTRADING_RSI_LONG_EXTREME = 78.0  # twardy blok tylko skrajność przeciwko LONG
DAYTRADING_RSI_SHORT_EXTREME = 22.0
DAYTRADING_TP1_R = 1.5
DAYTRADING_TP2_R = 2.2
DAYTRADING_TP1_FRAC = 0.50
DAYTRADING_TP2_FRAC = 0.25       # 50% z pozostałych po TP1; reszta trail
DAYTRADING_BREAK_EVEN_R = 1.0
DAYTRADING_BREAK_EVEN_BUFFER_PCT = 0.18
DAYTRADING_TRAIL_ACTIVATION_R = 1.5
DAYTRADING_TRAIL_ATR_MULT = 1.10
DAYTRADING_TIME_STOP_HOURS = 6.0
DAYTRADING_TIME_STOP_MIN_R = 0.50
DAYTRADING_HARD_TIME_STOP_HOURS = 10.0
DAYTRADING_INVALIDATION_BARS = 2
DAYTRADING_MIN_EXPECTED_NET_R = 0.05
DAYTRADING_QUALITY_MIN = 0.55
DAYTRADING_MIN_GATE_VOTES = 3
DAYTRADING_SIZE_R_TARGET = 0.40
DAYTRADING_SIGNAL_COOLDOWN_MINUTES = 40
DAYTRADING_EXPECTED_HOLD_HOURS = 6.0
# Jawny, konserwatywny prior do czasu uzyskania >= EXPECTED_R_MIN_CALIBRATION_OBS
# niezależnych transakcji OOS. To są hipotezy PAPER, nie deklarowany edge.
DAYTRADING_PRIOR_P_TP1 = 0.55
DAYTRADING_PRIOR_P_TP2_GIVEN_TP1 = 0.45
DAYTRADING_UNCALIBRATED_SIZE_MULT = 0.35
DAYTRADING_NET_R_MIN_SAMPLE = 20   # twardy NET_R dopiero od tej próbki
DAYTRADING_CHASE_LONG_PCT = 22.0   # nie LONG po +22% / 24h
DAYTRADING_CHASE_SHORT_PCT = 20.0  # nie SHORT po -20% / 24h
DAYTRADING_MIN_SL_PCT = 0.40       # min odległość SL od ceny (%)
EMERGENCY_SL_BUFFER_PCT = 0.15     # failsafe dalej niż structural SL
DAYTRADING_STRUCTURE_ATR_BUFFER = 0.15
DAYTRADING_MIN_BARRIER_R = 1.20          # cel TP1; ponizej = soft (cap TP), nie twardy blok
DAYTRADING_BARRIER_HARD_R = 0.60         # twardy blok tylko gdy miejsce do bariery < 0.6R ATR-SL
DAYTRADING_BARRIER_IGNORE_ATR = 0.25     # poziomy blizej niz 0.25 ATR = szum, nie sciana
DAYTRADING_USE_VIPER_LEVELS = False      # Viper wylaczony z decyzji
DAYTRADING_MAX_STRUCTURAL_SL_ATR = 2.50
DAYTRADING_PANIC_MIN_STRENGTH = 0.75
DAYTRADING_PANIC_SIZE_MULT = 0.25
DAYTRADING_WF_PURGE_BARS = 12       # 1h drive: 12h separation
DAYTRADING_WF_EMBARGO_BARS = 12

# --- Likwidacja vs SL ---
REQUIRE_LIQ_BEYOND_SL = True
LIQ_SL_BUFFER_MULT = 1.5         # liq_dist > 1.5 × sl_dist
MAINTENANCE_MARGIN_RATE = 0.005  # ~0.5% MMR USDT-M
LIQ_ATR_BUFFER = True            # dodaj k*ATR do wymaganego dystansu
LIQ_ATR_BUFFER_MULT = 0.25

# --- Order book impact simulator ---
OB_IMPACT_FILTER = True
OB_MAX_IMPACT_PCT = 0.35         # reject gdy VWAP impact > 0.35% vs mid
OB_MIN_FILL_RATIO = 0.95         # min 95% notional musi się zmieścić w booku
OB_IMPACT_REQUIRE_BOOK = False   # True = brak OB → blokada
# PRIORYTET 13 — size z płynności (nie tylko OB_THIN bool)
OB_SIZE_FROM_LIQUIDITY = True    # cap notional do max_safe przy impact ≤ max
OB_DEPTH_SIZE_FRAC = 0.35        # nie bierz >35% widocznej głębokości booka
DECISION_TELEMETRY_ENABLED = True
DECISION_TELEMETRY_PATH = "logs/decision_telemetry.jsonl"
DECISION_TELEMETRY_DEDUPE_SECONDS = 300
DECISION_TELEMETRY_SKIP_REASONS = ("DAY_NOT_IN_LIQUID_TOP",)
ENGINE_COOLDOWN_BASE_MINUTES = 20
ENGINE_COOLDOWN_ESCALATED_MINUTES = 75
ENGINE_COOLDOWN_ESCALATION_LOSSES = 3
CROSS_ENGINE_SYMBOL_COOLDOWN = False  # strata trend nie blokuje potwierdzonego reversalu
WATCHDOG_MAX_CYCLE_AGE_SEC = 180
ENTRY_RESERVATION_TTL_SEC = 30.0

# Trend Engine — Fibonacci pullback (impulse → 0.5–0.618 → continuation)
TREND_FIB_PULLBACK = True        # bonus w strefie, kara na szczycie impulsu

# --- Dynamic spread (zamiast hard MAX_SPREAD) ---
DYNAMIC_SPREAD_FILTER = True
SPREAD_K_VOL = 0.15
SPREAD_K_BASIS = 0.5
SPREAD_ZSCORE_MAX = 2.5
MAX_SPREAD_PCT_HARD = 0.50
MAX_EXEC_COST_PCT = 0.25

# --- Regime model 10–11 ---
REGIME_HYSTERESIS = 3            # consecutive raw confirmations
REGIME_ATR_PERCENTILE_MIN_SAMPLES = 30
# Sam percentyl nie definiuje krachu: wymaga jednoczesnego wzrostu ATR i realized vol.
REGIME_PANIC_PERCENTILE_MIN_ATR_RATIO = 1.50
REGIME_PANIC_PERCENTILE_MIN_RVOL = 2.00
REGIME_ADX_TREND = 22.0
REGIME_ADX_RANGE = 18.0
REGIME_PANIC_RVOL = 8.0
REGIME_DISP_TREND = 6.0

# --- Strength calibration 12 ---
MIN_EXPECTED_R = 0.55            # strength 0.80 ≠ auto very strong
USE_EXPECTED_R_FILTER = False    # True = can_open wymaga expected_r ≥ MIN

# --- Docelowy Risk Engine (x10) ---
# BASE 0.50% / trade (RISK_PCT_DEFAULT)
# MAX  0.75% / trade (RISK_PCT_MAX)
MAX_PORTFOLIO_OPEN_RISK = 0.025   # 2.5% equity = suma risk$ otwartych
MAX_CORRELATED_RISK = 0.010       # 1.0% equity w jednym klastrze
EXTREME_VOL_RISK_MULT = 0.50      # ATR percentile wysoki → risk × 0.5
EXTREME_VOL_ATR_PCTILE = 85.0     # próg percentile ATR
# PANIC: Trend ograniczony, Reversal AKTYWNY (nie wyłącza systemu)
REGIME_PANIC_TREND_MIN_STRENGTH = 0.72   # trend w PANIC prawie wyłączony
# ILLIQUID / SLIPPAGE / LIQ BUFFER → filtry OB_IMPACT + REQUIRE_LIQ_BEYOND_SL

# --- P1: proxy 4H / degraded 1D ---
PROXY_4H_RISK_MULT = 0.70
PROXY_4H_STRENGTH_PENALTY = 0.04
DEGRADED_1D_RISK_MULT = 0.75
DEGRADED_1D_STRENGTH_PENALTY = 0.08
BLOCK_ON_DEGRADED_1D = False      # True = twardy blok gdy brak 1D

# --- P2: dynamic corr + expected net R ---
DYN_CORR_FILTER = True
DYN_CORR_MAX = 0.75
DYN_CORR_WINDOW = 48
DYN_CORR_MIN_OBS = 20
USE_EXPECTED_NET_R_FILTER = True
MIN_EXPECTED_NET_R = 0.05  # było 0.25 – przy SL%~2.2 ceny net_r rzadko >0.25
EXPECTED_HOLD_HOURS = 24.0
DEFAULT_SLIPPAGE = 0.0003
EXPECTED_R_MIN_CALIBRATION_OBS = 30
UNCALIBRATED_EXPECTED_R_SIZE_MULT = 0.65

# --- Data hierarchy (PRIORYTET 11) ---
# BloFin  = PRIMARY   (cena, orderbook, liquidity, spread, execution, funding)
# Binance = CONFIRM   (trend/momentum reference, anomaly, cross-market)
# CoinGecko = CONTEXT (market cap, meta, sanity check)
BLOCK_ON_BLOFIN_OHLCV_FAIL = True
REQUIRE_BLOFIN_VOLUME = False
REJECT_ON_CROSS_DIVERGE = False
REJECT_ON_1H_DIVERGE = True
CROSS_DIVERGE_RISK_MULT = 0.50   # soft BN↔BF → size ×0.5
BN_BF_DIVERGENCE_SOFT_PCT = 1.0  # ≥1% → kara + smaller size
BN_BF_DIVERGENCE_HARD_PCT = 3.0  # ≥3% → NO TRADE
REQUIRE_BN_BF_DIVERGENCE = False  # True = brak BN ceny = blokada
BN_CONFIRMATION_REQUIRED = True      # brak Binance = UNKNOWN, nigdy bonus za confirmation
CROSS_MARKET_MAX_SKEW_SECONDS = 20.0
REGIME_PANIC_MIN_STRENGTH = 0.62
REGIME_PANIC_MAX_POSITIONS = 10  # limit pozycji ma byc rowny 10 w kazdej sytuacji, rowniez w PANIC
AUTO_UNPAUSE_ON_START = True  # paper: odpauzuj przy starcie app

# --- Exhaustion Lens (druga soczewka – Trend Engine bez zmian) ---
EXHAUSTION_FILTER = True
EXHAUSTION_24H_PCT = 18.0          # |24h| >= 18% → strefa wyczerpania
EXHAUSTION_1H_EXTENSION_PCT = 3.5  # 1h dalej z impulsem = chase
EXHAUSTION_RSI_LONG = 68.0
EXHAUSTION_RSI_SHORT = 32.0
BLOCK_EXHAUSTION_CHASE = True      # twardy reject kontynuacji na ekstremum
REVERSAL_SCOUT_ENABLED = True      # tag REVERSAL_SCOUT_* w reasons (UI/analiza)
# Pełne auto-odwrócenie kierunku na razie OFF (najpierw scout w logach)
REVERSAL_AUTO_FLIP = False
REVERSAL_AUTO_FLIP_MIN_SCORE = 0.70

# --- REVERSAL ENGINE (niezależny od Trend Engine) ---
REVERSAL_ENGINE_ENABLED = True
# TP2: Fibonacci extension (mapa, nie „zawsze 1.618”)
REVERSAL_FIB_TP_EXT = 1.618   # 1.0 / 1.272 / 1.618 / 2.0 / 2.618
# Divergence + Fib + Structure > RSI alone
REVERSAL_REQUIRE_QUALITY_TRIAD = False  # True = twardo ≥2/3
# Asymetria: 0.75R reward vs risk → NO TRADE (nawet ładne wskaźniki)
REVERSAL_MIN_TP1_R = 1.0
REVERSAL_MIN_TP2_R = 1.5
REVERSAL_MIN_REWARD_R = 1.5
# Obiektywny swing (anti-overfit)
# valid_swing = move >= X×ATR AND duration >= N candles AND fractal pivot
FIB_PIVOT_LEFT = 2
FIB_PIVOT_RIGHT = 2
FIB_SWING_MIN_BARS = 4
FIB_SWING_MIN_ATR_MULT = 1.5
FIB_SWING_MIN_PCT = 2.5
FIB_SWING_MAX_LOOKBACK = 80

# ============================================================
# METODOLOGIA / ANTY-OVERFIT (obowiązkowe)
# ------------------------------------------------------------
# H (+32%) i COW (−20%) to PRZYKŁADY problemu, nie targety.
# Nie wolno stroić progów pod konkretne monety ani pojedyncze dni.
#
# Kolejność:
#   1) hipoteza
#   2) reguły ogólne (pipeline EXTREME → EXHAUSTION → CONFIRM)
#   3) shadow test (REVERSAL_SHADOW_ONLY=True)
#   4) dane (n setek kandydatów, reżimy, MFE/MAE)
#   5) walidacja (OOS / walk-forward)
#   6) dopiero potem ostrożne strojenie
#
# Progi poniżej = PRIORY (hipoteza), nie optimum z backtestu na H/COW.
# Zmiana progu wymaga uzasadnienia na populacji, nie na 1–2 case'ach.
# ============================================================
REVERSAL_MIN_24H_PCT = 18.0
REVERSAL_EMA_DEV_PCT = 6.0      # min |cena-EMA|% jako stretch (priory, nie z H/COW)
REVERSAL_1H_STALL_PCT = 2.0
REVERSAL_RSI_LONG_MAX = 38.0
REVERSAL_RSI_SHORT_MIN = 62.0
REVERSAL_MIN_STRENGTH = 0.48
REVERSAL_MAX_CANDIDATES = 12
REVERSAL_SL_PCT = 0.035
REVERSAL_TP_PCT = 0.055

# --- Trend Engine continuation structure ---
TREND_CONTINUATION_FILTER = True
CONT_IMPULSE_MIN_24H = 6.0     # min impuls do uznania pullback-setup
CONT_IMPULSE_MAX_24H = 18.0    # powyżej = late / oddaj Reversal Engine
CONT_PULLBACK_1H_PCT = 1.2     # 1h wyhamowanie = strefa retest
BLOCK_CONT_CHASE_EXT = True    # blokuj continuation przy 1h extension
REVERSAL_MIN_CONFIRMATIONS = 1   # paper: 1 potwierdzenie wystarczy
REVERSAL_CONF_SCORE_BYPASS = 0.0   # brak bypassu: ENTRY wymaga jawnego confirmation
REVERSAL_ZSCORE_EXTREME = 2.2
REVERSAL_ATR_MULT = 2.5
REVERSAL_MAX_SRC_DIFF = 1.5
REVERSAL_EXHAUST_MIN_SCORE = 0.35
# Reversal SL: structural + ATR buffer (nie stałe -3%)
REVERSAL_ATR_SL_BUFFER = 0.6
REVERSAL_ATR_FALLBACK_PCT = 0.012
REVERSAL_SWING_LOOKBACK = 12
REVERSAL_TP_R_MULT = 1.6
REVERSAL_MAX_SL_PCT = 0.08
REVERSAL_MULTI_TP = True
REVERSAL_TP1_FRAC = 0.25   # 25% @ 1R
REVERSAL_TP2_FRAC = 0.35   # 35% @ 2R
REVERSAL_TP3_FRAC = 0.40   # 40% trailing
# Expected Net R — wspólny filtr
# MIN_EXPECTED_NET_R już jest (trend)
REVERSAL_MIN_EXPECTED_NET_R = 0.35   # wyższy próg dla reversal
REVERSAL_EXPECTED_HOLD_HOURS = 12.0
DEFAULT_SPREAD_FRAC = 0.0004
DEFAULT_IMPACT_FRAC = 0.0002
REVERSAL_DEFAULT_IMPACT_FRAC = 0.0008
IMPACT_K = 0.015
# Shadow Mode — Reversal obserwuje, Trend handluje
REVERSAL_SHADOW_ONLY = False      # PAPER: reversal realnie otwiera
REVERSAL_SHADOW_LOG_ALWAYS = True
REVERSAL_SHADOW_MAX_HOURS = 48.0  # timeout kandydata
REVERSAL_PAPER_EXECUTION_ENABLED = True
REVERSAL_SIZE_CAPITAL_PCT = True
REVERSAL_SIZE_MULT = 0.70          # 70% pasa 5-10% margin
REVERSAL_LIVE_EXECUTION_ENABLED = False
REVERSAL_PAPER_MIN_CONFIRMATIONS = 1
REVERSAL_PAPER_REQUIRE_NET_R = False
ATR_SL_MAX_ATR_PCT = 12.0   # clamp ATR% gdy śmieciowe OHLC
ATR_SL_MAX_DIST_PCT = 10.0  # max dystans SL od entry (%)
REVERSAL_SHADOW_WATCH_MIN_24H = 15.0  # shadow watch od |24h|>=15%
