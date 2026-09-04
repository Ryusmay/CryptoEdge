# Kontekst operacyjny — jak się pracuje na tym repo

Ten plik jest wersjonowany razem z kodem, więc świeży klon niesie go ze sobą.
Ta sama treść żyje równolegle w dwóch miejscach poza repo: w projekcie
CryptoEdge na claude.ai (`claude/OPERATING-GUIDE.md`, `claude/CONNECTORS.md`)
i w agent-wiki `my-agent-wiki`. Gdy coś się rozjedzie, **repo jest źródłem
prawdy dla faktów o kodzie**, a wiki dla stanu prac.

## Maszyna i powłoka

Repo mieszka na `F:\CryptoEdge`, Windows, użytkownik `Wojtek`. Agent dostaje
się tu wyłącznie przez most do maszyny; środowisko chmurowe nie widzi dysku F:.

Pułapki, każda kosztowała czas:

- Domyślna powłoka to **PowerShell** i psuje `&&`, `2>NUL |`, `[...]`
  i cudzysłowy. Pisz `.bat` do `%TEMP%` i uruchamiaj `cmd /c <ścieżka>.bat`.
- Wywołanie procesu ma limit ~60 s. Dłuższe zadania puszczaj odłączone
  (`cmd /c start "" /b`) z przekierowaniem do logu i odpytuj log.
- `timeout /t N` zawodzi przy przekierowanym stdin — używaj
  `ping -n N 127.0.0.1 >NUL`.
- Git: `"C:\Program Files\Git\bin\git.exe"`.
- W `.bat`: `set PYTHONIOENCODING=utf-8`, uruchamiaj `python -X utf8 -u`.
  Bez `-u` Python buforuje wyjście i log wygląda na zawieszony.
- Wyjście na konsolę trzymaj w ASCII. Diakrytyki tylko w `docs/*.md`.
- **Nigdy** `git checkout -- <plik>`, gdy w drzewie są niezacommitowane zmiany.

Zmierzone czasy: pełne testy ~60 s; `tools/parity.py` (5 symboli × 30 d)
~400 s; `tools/cost_breakdown.py` ~8 min; `tools/outcome_dataset.py` ~120 s
na symbol na 30 d.

## Metoda

**Bramka przed ruchem.** Nie migruj kodu, którego nie pokrywa bramka
charakteryzacyjna. Refaktor, który nie zmienia strategii, musi dawać zero
różnic.

**Mierz, nie zakładaj.** Każde „zmierzone" w `docs/architecture/MIGRATION_PLAN.md`
zostało uruchomione. Twierdzenia z drugiej ręki weryfikuj u źródła.

**Sabotaż.** Bramka jest wiarygodna dopiero, gdy celowo zepsuty kod ją
zaczerwienił. Psuj logikę, nie treść komunikatu.

**Denylista, nie allowlista** w komparatorach — lista pól przepuszcza każdy
nowy wymiar pomiaru. Ten błąd wystąpił dwa razy: `exec_gate` i `parity`.

**„Nie wiem" bije „wiem źle".** `None` jest lepsze niż zmyślone zero.

**Narzędzie pomiarowe opisuje swoje wejście.** Cicha liczba z niespójnych
danych jest gorsza niż brak liczby.

**Zero, które może znaczyć „nie było czego liczyć", ma być tak oznaczone.**

**Zmierzone wartości trafiają do repo jako dane z proweniencją** — data,
źródło, venue, ograniczenia. Wzór: `docs/analysis/venue_microstructure_20260903.json`.
Nie jako nowe stałe w `config.py`, bo po pół roku będą nieodróżnialne od tych,
które właśnie obaliliśmy.

## Narzędzia pomiarowe i czego nie wolno

| narzędzie | co robi | zapisuje? |
|---|---|---|
| `tools/parity.py` | bramka regresji replayu + opis okien wejścia | tylko baseline, na żądanie |
| `tools/risk_overlay.py` | ile sygnałów odrzuca produkcyjna bramka ryzyka | nie |
| `tools/cost_breakdown.py` | rozkład `expected_net_r` na brutto i koszty | nie |
| `tools/tp_rates.py` | empiryczne p(TP1), p(TP2\|TP1) z raportów | **nie** |
| `tools/outcome_dataset.py` | czysty zbiór wyników, jedna konfiguracja | JSONL |
| `tools/calibrate_expectancy.py` | **UWAGA** — patrz niżej | **TAK** |

**`tools/calibrate_expectancy.py` przy samym uruchomieniu woła `record()` na
produkcyjnym kalibratorze** i zmienia stan, którego użyłby LIVE. Do pomiaru
służy `tools/tp_rates.py`, który nie zapisuje niczego.

**`expected_net_r` to produkcyjna logika decyzyjna**, nie narzędzie pomiarowe —
karmi `net_r_ok`, które bramkuje wejścia w `risk_manager`. Każda zmiana tam
zaczerwieni `risk_gate` (143 przypadki) i `entry_gate` (22) oraz przesunie
baseline parytetu. To sygnał pożądany, ale wymaga świadomej decyzji.

## Stan trybu handlu

`PAPER_TRADING = True`, `LIVE_EXECUTION_ENABLED = False`. Bot **nigdy nie złożył
zlecenia na giełdzie**. PAPER nie potrafi wyprodukować obserwacji poślizgu:
`paper_trader.py:1205` podaje jako cenę fillu własną cenę wejścia. Instalacja
do zapisu prawdziwych fillów istnieje (`blofin_executor.py`, `FillLedger`)
i **nie jest podłączona** — `FillLedger` ma zero produkcyjnych wołających.

## Źródła danych zewnętrznych

Pełny rejestr konektorów: projekt CryptoEdge na claude.ai,
`claude/CONNECTORS.md`, oraz `notes/connectors.md` w agent-wiki. Skrót tego,
co ma tu realne zastosowanie:

- **mikrostruktura rynku** (spread w bps, głębokość księgi) — do kalibracji
  członu kosztu; użyte w v20.53.0. Brak BloFina, więc liczby są dolnym
  ograniczeniem kosztu;
- **ranking wolumenu** — istotny, bo `_volume_rank` nie jest wypełniane
  w replayu i psuje profil symbolu;
- **stopy fundingu** — `funding_r` w replayu wychodzi zero z braku danych;
- **niezależna implementacja wskaźników** — do kontroli krzyżowej matematyki
  wskaźników liczonych w repo;
- **specyfikacja giełdy** (tabela opłat, wielkości ticków, kroki zleceń) —
  liczby, które dziś siedzą w `config.py` bez źródła.
