# CryptoEdge v19.31.0

Unified bot start:

- jeden widoczny przycisk `START BOT` zastępuje osobne `Start analysis` i `Start trading`,
- kliknięcie uruchamia analizę oraz handel przez istniejącą, zabezpieczoną ścieżkę `start_trading()`,
- podczas pierwszego skanu UI nadal pokazuje ładowanie analizy,
- statusy ANALIZA i HANDEL pozostają osobne i czytelne,
- `STOP TRADING` nadal zatrzymuje tylko nowe wejścia, pozostawiając analizę aktywną,
- wewnętrzne `start_analysis()` pozostaje dostępne dla API i diagnostyki.
