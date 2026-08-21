# CryptoEdge v17.24 — Replay Universes

- Historical Replay obsługuje `MANUAL`, `LIQUID` oraz `ALL`.
- `LIQUID` rankuje aktywne perpetuale według bieżącego quote volume BloFin,
  odrzuca stablecoiny, nieprawidłowe spready i pary poniżej 5 mln USDT/24h.
- Domyślny limit Liquid Universe wynosi 30 par i można go zmienić w UI.
- `ALL` obejmuje wszystkie bieżące pary BloFin z dodatnim wolumenem oraz
  prawidłowym bid/ask.
- Każda para musi posiadać co najmniej 90% wymaganej historii wraz z warm-upem.
  Braki danych nie przerywają testu — symbol i powód trafiają do `skipped`.
- Raport zapisuje sposób utworzenia universe oraz jawne ostrzeżenie o
  survivorship bias: bieżące API nie odtwarza kontraktów wcześniej wycofanych.
- Wynik wielu par jest porządkowany chronologicznie według zamknięć transakcji.
  Nadal jest to agregacja niezależnych replayów i nie symuluje konkurencji o
  wspólne limity pozycji/ekspozycji; ograniczenie jest widoczne w UI i raporcie.
- CLI: `--universe manual|liquid|all` oraz `--liquid-limit N`.
