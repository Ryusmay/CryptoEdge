# CryptoEdge v17.11 — UI, Analysis Flow, PANIC i Reversal Routing

Zmiany od v17.10:

- wyróżniony przełącznik DEMO/PAPER ↔ LIVE/BloFin na głównym Dashboardzie;
- stały, kolorowy wskaźnik faktycznie aktywnego trybu i stanu egzekucji LIVE;
- przełącznik korzysta z zabezpieczeń Control Center (zatrzymany silnik, brak pozycji, gotowe API);
- przycisk ANALIZA natychmiast wybudza cykl danych, ale nie włącza handlu;
- START BOT pozostaje osobną, wymaganą akcją włączającą nowe wejścia;
- Analysis Workspace automatycznie wybiera najlepszy instrument po pierwszym cyklu;
- dodano wybór instrumentu bezpośrednio w Analysis Workspace;
- dane scanner_assets, analysis_board i signals są scalane po symbolu;
- usunięto duplikaty Signal Lifecycle, Readiness, Why No Trade, Position Protection,
  Expected vs Actual i Entry Reservations z ekranów pomocniczych;
- Control Center jest jedynym pełnym ekranem diagnostyki operacyjnej;
- percentyl ATR jest liczony z historycznych ATR świec, a nie z powtarzanych cykli;
- sam ATR percentile nie uruchamia PANIC bez potwierdzenia ATR ratio i realized volatility;
- potwierdzony reversal nie może zostać nadpisany odrzuconym lub neutralnym trendem;
- Expected Net R reversal jest liczony przed scaleniem silników;
- stan aplikacji zapisuje engine, setup, confirmation i kosztowy Expected Net R;
- dodano testy regresyjne dla rozdzielenia ANALIZA/HANDEL, UI, PANIC i reversal routing.

Nie obniżono progów jakości sygnałów, minimalnego Expected Net R ani zabezpieczeń LIVE.

Status: 112/112 testów regresyjnych zakończonych powodzeniem.
