# CryptoEdge 19.31.1

- PAPER V2 nie przesuwa już terminu ważności zlecenia LIMIT przy każdym skanie.
- Kolejka PAPER zachowuje pierwszy sygnał aż do dotknięcia limitu lub market fallback po timeout.
- Wypełnienia z kolejki są liczone i zapisywane jako zdarzenia OPEN.
- Stan aplikacji publikuje `pending_orders` dla dashboardu i diagnostyki.
- Oczekujące zlecenie jest raportowane jako `LIMIT_PENDING`, nie `OPEN_NONE`.
