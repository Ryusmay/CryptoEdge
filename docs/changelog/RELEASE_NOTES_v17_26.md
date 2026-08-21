# CryptoEdge v17.26 — Trader-friendly UI language

- Dodano centralne tłumaczenie statusów i powodów decyzji na czytelny język.
- `NO_TRADE` jest prezentowane jako `Brak wejścia`, `NEUTRAL` jako
  `Obserwacja`, a oczekujące i zaakceptowane etapy mają jednoznaczne nazwy.
- Najczęstsze filtry Daytrading Engine opisują faktyczny brakujący warunek,
  np. konflikt 1h/4h, słaby ADX, chaos rynku, brak potwierdzenia 5m, barierę
  cenową, płynność albo brak danych.
- Parametry diagnostyczne, np. wartość ADX/CHOP, pozostają widoczne w nawiasie.
- Czytelne opisy zastosowano w Scannerze, Opportunities, Execution Queue,
  Analysis Workspace, Risk oraz Control Center.
- Surowe kody nie zostały usunięte z logów i telemetrii, dzięki czemu audyt i
  analiza statystyczna pozostają w pełni kompatybilne.
