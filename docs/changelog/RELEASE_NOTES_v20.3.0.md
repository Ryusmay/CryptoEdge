# CryptoEdge v20.3.0

- Uniwersum ograniczone do aktywnych linear USDT perpetual BloFin; akcje, ETF-y,
  indeksy, metale i towary sa blokowane w feedzie, replayu i przed egzekucja.
- Daytrading V2 ma ochronny SL od chwili fill.
- Sizing V2 jest liczony z risk budget / dystans do SL, bez floor 5% equity.
- Soft/hard time stop daytradingu skrocone z 24/96 h do 6/10 h.
- Expected Net R, filtr SL-vs-cost, replay, PAPER close i STOP korzystaja z tego
  samego round-trip slippage; dynamiczny impact nie jest liczony podwojnie.
- STOP zamyka jako zyskowne tylko pozycje dodatnie po pelnym modelu kosztow.
- Dodano zdarzenia OPEN oraz rozbicie gross/fees/slippage/funding dla zamkniec.
- Stan pozycji jest zapisywany natychmiast po STOP i Close All.
- Interwal pelnego skanu zwiekszony do 30 s, powyzej zmierzonego czasu skanu.
