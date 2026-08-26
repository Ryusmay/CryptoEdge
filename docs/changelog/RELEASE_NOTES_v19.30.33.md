# CryptoEdge v19.30.33

PAPER session reset:

- start aplikacji w PAPER nie odtwarza kapitału ani pozycji z `bot_state.json`,
- każda sesja PAPER zaczyna od `STARTING_CAPITAL`, pustego portfela i zerowego daily PnL,
- historyczne logi i telemetria pozostają na dysku wyłącznie do analizy,
- recovery kapitału i pozycji pozostaje dostępne dla LIVE.
