# CryptoEdge v20.4.0 — safety, replay parity and edge monitoring

- Globalny stan ryzyka REDUCE_ONLY z API on/off.
- Pelny lancuch cen: strategy, decision, submitted, fill i mark.
- Startup reconciliation rozszerzone o working orders i protective orders.
- Automatyczne anulowanie osieroconych zlecen CryptoEdge (client ID CE*).
- Wspolny V2MarketSnapshot i EventClock z kontrola zamknietych swiec i latency.
- Tryby replay 1m/L2 wymagaja prawdziwych danych; brak zwraca DATA_UNAVAILABLE.
- Research Gate: wiele symboli/okien, koncentracja PnL, koszty x1.5,
  sasiedztwo parametrow, DSR i PAPER/replay parity.
- Edge-decay telemetry w API: expectancy 20/50/100, replay parity,
  maker ratio, avg/P95 slippage, reject share, regime/profile performance,
  profit concentration i risk budget.
