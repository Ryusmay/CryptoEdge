# CryptoEdge v20.2.0 — React + TypeScript + Tauri

## Nowy główny interfejs

- niezależny klient React/TypeScript z sześcioma przestrzeniami: Pulpit, Skaner, Analiza, Replay, Historia i Ustawienia,
- natywne okno Windows dostarczane przez Tauri,
- duży panel otwartych pozycji oraz watchlista 2×2,
- uproszczone komunikaty statusu, ryzyka i zdarzeń,
- dostępność klawiaturowa, widoczny fokus i responsywny układ.

## Architektura i bezpieczeństwo

- Python pozostaje jedynym silnikiem strategii, ryzyka, PAPER/LIVE i egzekucji,
- Tauri korzysta z istniejącego lokalnego API, bez kopiowania logiki handlowej do UI,
- tryb `--web-ui` uruchamia silnik bez PySide6 i nie uruchamia automatycznie analizy ani handlu,
- CORS ograniczony do lokalnych originów CryptoEdge,
- zamknięcie klienta uruchomionego skryptem zatrzymuje uruchomiony przez niego proces silnika.

## Zgodność awaryjna

- `CryptoEdge.bat` i `URUCHOM.bat` uruchamiają nowy interfejs,
- `CryptoEdge_PySide6.bat` zachowuje dotychczasowy interfejs jako opcję awaryjną,
- strategia i dane pozostają wspólne dla obu klientów.

## Pakiety

- przenośny `CryptoEdge_Terminal.exe`,
- instalatory Windows NSIS i MSI,
- pełne źródła frontendu w katalogu `frontend`.
