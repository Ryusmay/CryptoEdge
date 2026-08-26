# CryptoEdge v19.25.9

BloFin: puste uniwersum + leftover Close-All, które w PAPER blokowało start.

## Co było nie tak (sesja 19.25.6)
`GET /api/v1/market/instruments` padał lokalnie (SSL / IPv6 / WAF). Endpoint żyje (~485 SWAP). Bot zostawiał 0 par i wchodził w backoff 45/90/180s. UI Close-All (`manual_close_all`) zostawiał KILL SWITCH w `protection_state.json` + pliku — PAPER po restarcie nie otwierał nic (`[Paper] BLOKADA: KILL SWITCH aktywny`).

19.25.7/8 pokazywały błąd i łatały transport. To nie wystarcza, gdy instruments nadal pada, a tickers przechodzą.

## Co robi 19.25.9
- Gdy `market/instruments` padnie: uniwersum z `market/tickers` (te same instId USDT). Feeder + InstrumentRegistry korzystają z tej samej ścieżki.
- Probe na starcie: FAIL instruments + hint (SSL/TCP/timeout/403), potem `probe recovered via tickers: N par` albo nadal FAIL z `last_error`.
- PAPER: leftover Close-All (`manual_close_all`) zdejmowany przy recovery. Operator kill i LIVE — bez zmian.

PAPER / V2 / 4h jako najwyższy TF / bez SRC — bez zmian.
