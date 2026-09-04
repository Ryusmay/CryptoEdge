# UX Contract

## Product context

- Audience: operator CryptoEdge na Windows.
- Primary jobs: obserwacja PAPER tradingu, rynku, decyzji, ryzyka, recovery i badań.
- Active locale: `pl-PL`; timestamps źródłowe zachowują strefę, prezentacja ma ją ujawniać, gdy istotna.
- Accessibility target: WCAG 2.2 AA.

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| PAPER/LIVE i akcje silnika | `engine_api.py`, `config.py`, testy risk/live boundary | API/domain invariants | 2026-08-31 |
| Modułowa architektura | `docs/architecture/MODULE_BOUNDARIES.md` | architecture contract | 2026-08-31 |
| UI read models | `ui_read_models.py`, `ui_trade_projection.py` | projection contracts | 2026-08-31 |
| Sekrety BloFin | `secrets_store.py`, endpoint settings | security implementation | 2026-08-31 |

## Visual contract

- Project `DESIGN.md`: `DESIGN.md`.
- Token ownership: istniejący runtime canonical.
- Runtime source: `frontend/src/styles.css`; `DESIGN.md` odwzorowuje role.
- Supported theme: dark; forced-colors pozostaje systemowo operowalne.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | natywny select dla krótkiego wyboru workspace/replay | ten kontrakt | native, bo popup systemowy jest akceptowany | keyboard + browser |
| Form | widoki React + `api.ts` | API contract | credentials/replay | unit + build |
| Scrollbar | globalny `styles.css` | `DESIGN.md` | stable gutter per panel | browser |
| Toast | jeden `.toast` w `App.tsx` | ten kontrakt | status | live region |
| Destructive dialog | `ConfirmDialog` | ten kontrakt | danger | keyboard/focus test |

## Dataset navigation

- Skaner i historia używają TanStack Table/Virtual i renderują tylko widoczny zakres.
- Stan workspace, sortowania i wybranego symbolu jest lokalny dla aplikacji desktopowej; nie trafia do URL, bo Tauri nie ma kanonicznych route URLs.
- Loading, empty, stale, disconnected i error muszą różnić się tekstem i nie zmieniać nagle geometrii panelu.

## Flow ledger

| Operation | Trigger | Pending | Success feedback | Failure recovery | Focus outcome | Source ref |
|---|---|---|---|---|---|---|
| Start/stop engine | topbar/command palette | blokada duplikatu | wspólny status/toast | komunikat + ponowna próba | pozostaje na akcji | `engine_api.py` |
| Save/test keys | settings form | disabled/busy | inline status | wartości zachowane przy błędzie | formularz | settings API |
| Delete keys | aplikacyjny dialog | disabled/busy | inline status | dialog/form pozostaje bez utraty danych | wraca do triggera | settings API |
| Replay | replay form | jawny etap/progress | panel wyniku | błąd i ponowna próba | pozostaje w panelu | replay endpoints |

## Navigation and responsive behavior

- Tytuł dokumentu: `{Widok} — CryptoEdge`.
- Sidebar pozostaje widoczny na wspieranym desktopowym minimum 1120 px.
- Dense tables przewijają się we własnym panelu; ważne wartości nie zależą od hover.
- Command palette nie reaguje na skróty podczas wpisywania/IME i przywraca fokus po zamknięciu.

## Overlays and feedback

- Dialog: `ConfirmDialog`; Escape zamyka, anulowanie ma pierwszy fokus, po zamknięciu fokus wraca.
- Toast potwierdza; trwałe stale/offline/risk conditions są bannerem lub statusem.
- Layer order: dialog/command palette > toast > sticky content.

## Async and resilience

- Mutacje finansowe/security są pessimistic; brak optimistic LIVE actions.
- Market stream używa snapshot + delta, `session_id`, `sequence_id`, heartbeat, stale detection i REST resync.
- Retry ma bounded backoff; sequence gap wymusza canonical snapshot.
- Zamknięcie Tauri zatrzymuje tylko backend uruchomiony i posiadany przez tę instancję.

## Validation

- Formularze używają `noValidate`, blokują duplicate submit i pokazują błędy inline.
- Sekrety są maskowane, można je jawnie odsłonić; nie trafiają do URL, logów ani toastu.

## Permission and trading boundary

- UI nie jest autoryzacją. Backend zawsze egzekwuje PAPER/LIVE i confirmation gates.
- Panel manualnego zlecenia pozostaje nieaktywny do osobnego audytu execution boundary.

## Migration status

- Canonical frontend: `frontend/src`; usunięto nieużywaną kopię `frontend/engine_api.py`.
- PySide6 pozostaje awaryjnym UI, ale nowy rozwój workspace należy do React/Tauri.
- Rollback: checkpointy git; Tauri nie zgaduje ścieżki do Python i nie zabija obcego procesu.

## Verification

- Python pytest/unittest, Vitest, TypeScript/Vite build, Cargo test, premium strict audit.
- Reprezentatywny sibling: Workspace oraz Scanner korzystające ze wspólnych statusów/tabel.
