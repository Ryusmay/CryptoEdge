# CryptoEdge v19.28.0

Lokalne HTTP API obok PySide6 (krok 1+2 pod Tauri):

- `GET /api/status` — ten sam stan co DESK (pozycje v14, sesja, skaner, eventy)
- `POST /api/engine/*` — start/pause/resume/stop/close_all (mutating wymaga `{"confirm": true}`)
- bind tylko `127.0.0.1` (bez tokena nie wolno 0.0.0.0)
- HTML Control Room: http://127.0.0.1:47821/
- Qt bez zmian
