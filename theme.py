"""Jeden plik stylu dla nowego UI (DESK/SCAN/LAB, za flaga config.UI_DESK_V2).

Zgodnie z wytycznymi: "Nie 2500 linii inline. theme.py." - zamiast rozrzucac
kolory po calym pyside6_ui.py, wszystko zyje tutaj. Stary interfejs (7-zakladkowy,
za UI_DESK_V2=False) nadal korzysta z wlasnego, niezmienionego slownika C w
pyside6_ui.py - nie ryzykujemy dzialajacego kodu przy tej zmianie.
"""

from __future__ import annotations

BG = "#07090C"
SIDE = "#0A0D12"
PANEL = "#0C0F14"
PANEL2 = "#10141B"
LINE = "#1C2330"
LINE2 = "#26303F"
TEXT = "#D6DEEA"
MUTED = "#7C8A9C"

LONG = "#3DDC97"
SHORT = "#F07178"
WAIT = "#E6B84F"
CYAN = "#2BC4FF"
PURPLE = "#A889FF"

MONO = "JetBrains Mono"
SANS = "Segoe UI"

# GATE (OPEN/WAIT/BLOCK) -> (kolor tekstu, tlo pigulki, obwodka)
GATE_COLORS = {
    "OPEN": (LONG, "#0D2B22", "#1E6A4C"),
    "WAIT": (WAIT, "#332711", "#6B4D17"),
    "BLOCK": (SHORT, "#33131A", "#6B2634"),
}

SIDE_COLORS = {
    "LONG": LONG, "SHORT": SHORT, "WAIT": WAIT,
}

REGIME_COLORS = {
    "TREND_UP": LONG, "TREND_DOWN": SHORT, "TREND": CYAN,
    "RANGE": WAIT, "PANIC": SHORT, "UNKNOWN": MUTED,
}

# Etykieta pokazywana userowi != wewnetrzny klucz rezimu. "PANIC" w regime_model.py
# to tylko nazwa progu (ATR/RVOL wybite ponad prog "extreme vol" - patrz
# regime_model.py._classify) - nie oznacza faktycznej paniki/strachu na rynku,
# tylko gwaltowny, silny ruch ceny. Pokazywanie surowego "PANIC" w UI myli
# tradera co do przyczyny. Klucz w config/regime_model/telemetrii zostaje
# bez zmian ("PANIC") - zmieniamy wylacznie to, co widzi user.
REGIME_LABELS = {
    "PANIC": "STRONG MOVE",
}


def gate_tone(gate: str) -> tuple:
    """(text_color, bg, border) dla danego GATE - domyslnie WAIT jesli nieznany."""
    return GATE_COLORS.get(str(gate or "").upper(), GATE_COLORS["WAIT"])


def side_color(side: str) -> str:
    return SIDE_COLORS.get(str(side or "").upper(), MUTED)


def regime_color(regime: str) -> str:
    return REGIME_COLORS.get(str(regime or "").upper(), MUTED)


def regime_label(regime: str) -> str:
    """Tekst do pokazania userowi - patrz REGIME_LABELS. Nieznane/bez etykiety
    -> surowy klucz (uppercase), tak jak dotychczas."""
    key = str(regime or "").upper()
    return REGIME_LABELS.get(key, key)


def qss() -> str:
    """QSS wspolny dla DESK/SCAN/LAB - 2px radius, hairline border, zero cieni."""
    return f"""
    QWidget#DeskV2Root, QWidget#ScanV2Root, QWidget#LabV2Root {{
        background:{BG}; color:{TEXT};
    }}
    QFrame#V2Card {{
        background:{PANEL}; border:1px solid {LINE}; border-radius:2px;
    }}
    QLabel#V2CardTitle {{
        color:{MUTED}; font-size:10px; font-weight:700; letter-spacing:1px;
    }}
    QLabel#V2Mono {{ font-family:'{MONO}'; }}
    QFrame#V2TopBar {{
        background:{SIDE}; border-bottom:1px solid {LINE};
    }}
    QToolButton#V2Nav {{
        color:{MUTED}; background:transparent; border:0;
        border-bottom:2px solid transparent; padding:9px 16px; font-weight:700;
    }}
    QToolButton#V2Nav:checked {{
        color:{TEXT}; border-bottom:2px solid {CYAN};
    }}
    QToolButton#V2Nav:hover {{ color:{TEXT}; }}
    QLabel#V2StatePill {{
        border:1px solid {LINE2}; border-radius:2px; padding:3px 8px; font-weight:700;
    }}
    QLabel#V2StatePill[tone='on'] {{ color:{LONG}; border-color:#1E6A4C; background:#0D2B22; }}
    QLabel#V2StatePill[tone='off'] {{ color:{MUTED}; }}
    QLabel#V2StatePill[tone='loading'] {{ color:{CYAN}; border-color:#145678; background:#0C2B3D; }}
    QLabel#V2StatePill[tone='paused'] {{ color:{WAIT}; border-color:#6B4D17; background:#332711; }}
    QLabel#V2StatePill[tone='demo'] {{ color:{LONG}; border-color:#1E6A4C; background:#0D2B22; }}
    QLabel#V2StatePill[tone='live'] {{ color:{SHORT}; border-color:#6B2634; background:#33131A; }}
    QLabel#V2RegimePill {{
        border:1px solid {LINE2}; border-radius:2px; padding:4px 10px; font-weight:700;
    }}
    QPushButton#V2CloseAll {{
        color:{SHORT}; background:{PANEL2}; border:1px solid #6B2634; border-radius:2px;
        padding:8px; font-weight:700;
    }}
    QPushButton#V2CloseAll:hover {{ background:#33131A; }}
    QTableView#V2Table, QTableWidget#V2Table {{
        background:{PANEL}; border:0; gridline-color:{LINE};
        selection-background-color:#143448; alternate-background-color:{PANEL2};
    }}
    QHeaderView::section {{
        background:{SIDE}; color:{MUTED}; border:0; border-bottom:1px solid {LINE};
        padding:6px; font-size:10px; font-weight:700;
    }}

    /* 21.08.2026: rozszerzenie na LAB/REPLAY/SET (+ nowa zakladka HISTORY) -
    do teraz te trzy zakladki uzywaly self.page() (duzy naglowek + duzy
    QScrollArea) i renderowaly sie starym stylem (#Card z self.styles(),
    10px radius) - patrz komentarz w pyside6_ui.py przy Card. Poniższe
    reguly stosuja sie do TYCH SAMYCH klas widgetow (Card/StatePill/Pill/
    przyciski/pola formularzy), ktorych DESK/SCAN juz uzywaja - wiec cala
    apka (stary i nowy layout obu tych stron) dostaje jeden, spojny,
    plaski jezyk wizualny (2px radius, hairline border) zamiast dwoch
    rozjezdzajacych sie stylow. Kolejnosc w build_v2() to
    self.styles() + theme.qss() - te reguly (pozniejsze w lancuchu) wygrywaja
    z odpowiednikami w self.styles() dla tych samych selektorow. */
    QLabel#PageTitle {{
        color:{TEXT}; font-family:'{SANS}'; font-size:18px; font-weight:700;
        letter-spacing:0.4px;
    }}
    QLabel#PageContext {{
        color:{CYAN}; background:#0C2B3D; border:1px solid #175B7C;
        border-radius:2px; padding:5px 9px; font-size:10px; font-weight:700;
        letter-spacing:0.6px;
    }}
    QFrame#Card {{
        background:{PANEL}; border:1px solid {LINE}; border-radius:2px;
    }}
    QLabel#CardTitle {{
        color:{MUTED}; font-size:10px; font-weight:700; letter-spacing:1px;
    }}
    QLabel#Pill {{
        border:1px solid {LINE2}; border-radius:2px; padding:3px 8px; font-weight:700;
    }}
    QLabel#Pill[tone='green'] {{ color:{LONG}; border-color:#1E6A4C; background:#0D2B22; }}
    QLabel#Pill[tone='red'] {{ color:{SHORT}; border-color:#6B2634; background:#33131A; }}
    QLabel#Pill[tone='amber'] {{ color:{WAIT}; border-color:#6B4D17; background:#332711; }}
    QLabel#Pill[tone='blue'] {{ color:{CYAN}; border-color:#145678; background:#0C2B3D; }}
    QLabel#StatusBanner {{
        border:1px solid {LINE2}; border-radius:2px; padding:12px 16px; font-size:14px; font-weight:700;
    }}
    QLabel#StatusBanner[tone='green'] {{ color:{LONG}; background:#0D2B22; border-color:#1E6A4C; }}
    QLabel#StatusBanner[tone='red'] {{ color:{SHORT}; background:#33131A; border-color:#6B2634; }}
    QLabel#StatusBanner[tone='amber'] {{ color:{WAIT}; background:#332711; border-color:#6B4D17; }}
    QLabel#StatusBanner[tone='muted'] {{ color:{MUTED}; background:{PANEL2}; border-color:{LINE}; }}
    QLabel#AnalysisSection {{ color:{CYAN}; font-size:11px; font-weight:800; padding:12px 2px 4px 2px; }}
    QLabel#AnalysisValue {{ color:{TEXT}; padding:2px; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background:{PANEL2}; border:1px solid {LINE}; border-radius:2px; padding:6px 8px; color:{TEXT};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color:{CYAN}; }}
    QCheckBox {{ spacing:8px; color:{TEXT}; }}
    QCheckBox::indicator {{ width:16px; height:16px; }}
    QPushButton {{
        color:{TEXT}; background:{PANEL2}; border:1px solid {LINE}; border-radius:2px;
        padding:7px 12px; font-weight:600;
    }}
    QPushButton:hover {{ border-color:{CYAN}; }}
    QPushButton:disabled {{ color:{MUTED}; border-color:{LINE}; }}
    QPushButton#Good {{ color:{LONG}; background:#0D2B22; border-color:#1E6A4C; }}
    QPushButton#Danger {{ color:{SHORT}; background:#33131A; border-color:#6B2634; }}
    QPushButton#Primary {{ color:{CYAN}; background:#0C2B3D; border-color:#175B7C; }}
    QPushButton#ModeDemo, QPushButton#ModeLive {{ min-width:110px; padding:9px 14px; font-size:12px; }}
    QPushButton#ModeDemo:checked {{ color:{LONG}; background:#0D2B22; border:2px solid {LONG}; }}
    QPushButton#ModeLive:checked {{ color:#ffffff; background:#33131A; border:2px solid {SHORT}; }}
    QTableWidget {{
        background:{PANEL}; border:0; gridline-color:{LINE};
        selection-background-color:#143448; alternate-background-color:{PANEL2};
    }}

    /* 22.08.2026: WatchlistPanel/WatchlistTile (DESK) - realne ceny + live
    sparkline dla BTC/ETH/SOL/XRP, patrz pyside6_ui.py. Reuzywa te same
    tokeny (LONG/SHORT/MUTED/PANEL2/LINE) co reszta panelu, nie nowa paleta. */
    QFrame#WLTile {{
        background:{PANEL2}; border:1px solid {LINE}; border-radius:2px;
    }}
    QLabel#WLSym {{ color:{MUTED}; font-size:11px; font-weight:700; letter-spacing:0.5px; }}
    QLabel#WLPrice {{ font-family:'{MONO}'; color:{TEXT}; font-size:14px; font-weight:600; }}
    QLabel#WLChg {{ font-family:'{MONO}'; font-size:11px; font-weight:700; color:{MUTED}; }}
    QLabel#WLChg[tone='up'] {{ color:{LONG}; }}
    QLabel#WLChg[tone='down'] {{ color:{SHORT}; }}

    QPushButton#V2Chip {{
        color:{MUTED}; background:{PANEL2}; border:1px solid {LINE};
        border-radius:2px; padding:5px 10px; font-weight:700; font-size:11px;
    }}
    QPushButton#V2Chip:checked {{
        color:{TEXT}; background:#0C2B3D; border:1px solid {CYAN};
    }}
    QPushButton#V2Chip:hover {{ border-color:{CYAN}; color:{TEXT}; }}
    QFrame#ScanStat {{
        background:{PANEL2}; border:1px solid {LINE}; border-radius:2px;
    }}
    QLabel#ScanStatValue {{
        font-family:'{MONO}'; font-size:20px; font-weight:700; color:{TEXT};
    }}
    QLabel#KPI {{
        font-family:'{MONO}'; font-size:22px; font-weight:700;
    }}
    """
