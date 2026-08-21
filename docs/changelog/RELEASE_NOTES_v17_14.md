# CryptoEdge v17.14 — Safe Account Mode Transition

- Przełączenie LIVE / DEMO wymusza świeży odczyt pozycji BloFin.
- Nieaktualny cache pozycji nie blokuje już zmiany trybu.
- Przy przejściu LIVE → DEMO lokalne pozycje PAPER nie są błędnie traktowane jako blocker.
- Rzeczywiste pozycje BloFin nadal bezpiecznie blokują opuszczenie LIVE.
- Brak możliwości potwierdzenia stanu giełdy blokuje zmianę zamiast zakładać, że rachunek jest pusty.
- Komunikat wskazuje źródło, liczbę i symbole pozycji oraz właściwą metodę ich zamknięcia.

