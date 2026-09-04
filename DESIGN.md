---
version: alpha
name: "CryptoEdge Control Room"
description: "Gęsty, spokojny pulpit operacyjny do bezpiecznej obserwacji swing/intraday tradingu."
colors:
  primary: "#40c9ff"
  background: "#070a0e"
  rail: "#090d12"
  panel: "#0e1319"
  panel-raised: "#121922"
  border: "#222b35"
  text: "#edf2f7"
  muted: "#8391a2"
  success: "#33dfa0"
  danger: "#ff6474"
  warning: "#f5b942"
  info: "#40c9ff"
typography:
  sans:
    fontFamily: "Inter, Segoe UI, system-ui, sans-serif"
  mono:
    fontFamily: "JetBrains Mono, Cascadia Code, ui-monospace, monospace"
rounded:
  DEFAULT: "4px"
  dialog: "8px"
  pill: "999px"
spacing:
  control-gap: "0.55rem"
  panel-gap: "0.75rem"
  page-inline: "1.25rem"
components:
  button: {}
  card: {}
  dialog: {}
  table: {}
  chart: {}
---

# CryptoEdge Control Room Design System

## Overview

### Creative North Star

Interfejs ma przypominać profesjonalne stanowisko kontroli ryzyka: czytelne warstwy, ciasna typografia danych i jednoznaczne lampki stanu. Nie jest terminalem HFT ani konsumenckim portfelem.

### Product context and register

- **Audience and primary job:** operator CryptoEdge monitorujący rynek, decyzje, PAPER positions, ryzyko i recovery.
- **Target market and locale:** globalne rynki krypto; interfejs `pl-PL`, liczby i waluty formatowane jawnie.
- **Usage scene:** Windows desktop, długie sesje, częste skanowanie wielu paneli; produktowy, gęsty register.
- **Memorable signature:** pasek operacyjny i workspace’y pokazujące świeżość danych oraz stan zabezpieczeń bez otwierania dodatkowych ekranów.
- **Restraint:** animacja nie może konkurować z alarmami; kolor semantyczny nie służy dekoracji.
- **Anti-references:** neonowa „giełdowa dyskoteka”, marketingowy landing page, imitacja Bloomberg Terminal bez uzasadnienia.
- **Token ownership/runtime mapping:** istniejący plik `frontend/src/styles.css` pozostaje źródłem runtime; ten dokument odwzorowuje zaakceptowane wartości. Zmiany systemowe muszą aktualizować oba miejsca.

## Colors

Warstwy tła są prawie czarne, a hierarchię tworzą powierzchnie i cienkie obramowania. Zielony oznacza stan zdrowy lub dodatni, czerwony błąd/ryzyko/destrukcję, bursztyn ostrzeżenie, cyjan informację i fokus. Znaczenie zawsze ma tekst lub ikonę.

## Typography

Tekst interfejsu używa stosu sans, a ceny, PnL, wersje i tabele stosu mono. Nagłówki paneli są krótkie; ważne komunikaty i błędy nie są ucinane.

## Layout

Stały desktopowy rail i pasek statusu otaczają niezależnie przewijany obszar główny. Workspace ma grid z zapisywanym układem; każda operacja przesunięcia/rozmiaru ma alternatywę klawiaturową. Tabele posiadają własnego właściciela scrolla.

## Elevation & Depth

Podstawowa hierarchia używa tonu i obramowania. Cień jest zarezerwowany dla dialogu, toastu i command palette. Statyczne karty pozostają płaskie.

## Shapes

Panele i kontrolki używają małego promienia 4 px, dialog 8 px, a krótkie statusy formy pill. Nie stosujemy losowych promieni per ekran.

## Components

### Foundational visual states

Każda akcja ma hover, widoczny focus, disabled oraz stabilny busy. Stale/disconnected/frozen są trwałymi stanami z tekstem, nie chwilowym toastem.

### Buttons and actions

Zielony przycisk służy bezpiecznemu uruchomieniu PAPER, czerwony zatrzymaniu lub destrukcji. Akcja LIVE nie może pojawić się jako aktywna bez zweryfikowanej zgody backendu.

### Navigation and data display

Lucide jest jedyną rodziną ikon. Tabele używają TanStack i wirtualizacji przy długich zbiorach. Główny wykres ceny należy do Lightweight Charts; ENTRY/SL/TP są opisanymi poziomami.

### Forms and overlays

Sekrety są domyślnie maskowane z jawnym przełącznikiem. Formularze wyłączają natywne dymki walidacji. Destrukcja używa aplikacyjnego dialogu z początkowym fokusem na anulowaniu.

### Motion

Ruch wyłącznie komunikuje trwającą pracę, 150–200 ms, i znika przy `prefers-reduced-motion`.

### Content and data visualization

Komunikaty są krótkie, techniczne i po polsku. Czas, waluta i znak wartości muszą być jawne; brak danych pokazujemy jako brak, nigdy jako zmyślone zero.

## Do's and Don'ts

- **Do:** eksponuj świeżość danych, tryb PAPER i stan risk gates.
- **Do:** używaj wspólnych tokenów, komponentów i jednego kontraktu transportu.
- **Don't:** sugeruj, że LIVE działa, jeśli backend go nie dopuścił.
- **Don't:** zastępuj informacji o ryzyku samym kolorem lub animacją.
