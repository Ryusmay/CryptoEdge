# CryptoEdge v17.23 — Historical Replay

- Dodano ekran `Historical Replay` dla produkcyjnego Daytrading Engine.
- Domyślny test: 90 dni, BTC/ETH/SOL, 30% chronologicznego out-of-sample.
- Sygnał korzysta tylko z zamkniętych świec, a wejście następuje na otwarciu
  kolejnej. Gdy SL i TP są w tym samym OHLC, replay przyjmuje SL jako pierwszy.
- Dane 5m/15m/1h/4h i funding pochodzą z publicznego API BloFin. Cache trafia
  do `data/replay`, a raporty do `reports/replay`.
- Koszty obejmują round-trip fee, modelowany slippage i dostępny funding.
- IS oraz OOS oddziela 12 zamkniętych świec purge; OOS nie stroi progów.
- Replay działa w osobnym wątku i nie uruchamia handlu ani zleceń LIVE.
- Dostępny jest też terminal: `python run_historical_replay.py`.

## Ograniczenie

Bez pełnego historycznego orderbooka L2 slippage i market impact są modelowane,
a nie odtwarzane tick po ticku. Wynik trzeba potwierdzić ciągłym testem PAPER.
