# CryptoEdge v17.10 — Reversal PAPER Gate

Najważniejsze zmiany:

- potwierdzone sygnały Reversal Engine mogą być wykonywane wyłącznie w trybie PAPER;
- ścieżka Reversal w LIVE pozostaje bezwarunkowo wyłączona;
- PAPER wymaga statusu `CONFIRMED`, co najmniej 2 potwierdzeń i dodatniego Expected Net R ponad istniejący próg;
- Expected Net R uwzględnia fee, spread, slippage, market impact i funding przed wejściem;
- shadow telemetry zapisuje składniki kosztów oraz wynik gross/net R;
- raport shadow rozdziela LONG i SHORT;
- pełny potwierdzony setup może zastąpić wcześniejszy miękki `REVERSAL_WATCH`;
- PANIC zapisuje dokładny wyzwalacz: ATR ratio, percentyl ATR i/lub realized volatility;
- identyczne odrzucenia są deduplikowane przez 5 minut, a ACCEPT/OUTCOME nigdy;
- blokady Reversal PAPER trafiają do decision telemetry z konkretnym powodem.

Nie zmieniono progów Trend Engine ani progu Expected Net R w celu sztucznego zwiększenia liczby transakcji.

Status testów: 105/105 testów regresyjnych zakończonych powodzeniem.
