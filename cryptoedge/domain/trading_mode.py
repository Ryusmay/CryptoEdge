"""Czy bot handluje papierem, czy prawdziwymi pieniedzmi.

To najwazniejsze pytanie w calym systemie i przed ta wersja odpowiadalo na
nie szesc niezaleznych kopii tej samej logiki, plus kilka miejsc, ktore
robily po prostu `bool(config.PAPER_TRADING)`.

Roznica nie jest kosmetyczna. `bool("false")` to `True`, wiec gdyby do
configu trafil string - a `settings_store.apply_settings()` wpisuje wartosc
z JSON-a bez konwersji - czesc systemu uznalaby stan za PAPER, a czesc za
LIVE. paper_trader odmowilby symulacji ("PAPER_TRADER_FORBIDDEN_IN_LIVE"),
a runtime pominalby okresowa rekoncyliacje, bo "przeciez to tylko papier".
Bot rozjechalby sie sam ze soba w kwestii tego, czy w grze sa realne
pieniadze.

Jeden wlasciciel pytania, jedna odpowiedz. Modul jest czysty i nie ma
zadnego stanu.
"""
from __future__ import annotations

# Wartosci uznawane za "papier". Historyczne warianty ("demo", "paper")
# zostaja, bo starsze pliki settings.json moga je zawierac.
PAPER_TOKENS = ("1", "true", "yes", "on", "demo", "paper")
LIVE_TOKENS = ("0", "false", "no", "off", "live", "real")


def coerce_paper_flag(value, default: bool = True) -> bool:
    """Zamienia surowa wartosc PAPER_TRADING na bool.

    Nierozpoznany string nie jest zgadywany - wraca `default`, ktory jest
    `True`, czyli papier. Przy niejasnym ustawieniu system ma udawac, ze
    handluje, a nie ryzykowac prawdziwe zlecenia.
    """
    if isinstance(value, str):
        token = value.strip().lower()
        if token in PAPER_TOKENS:
            return True
        if token in LIVE_TOKENS:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def is_paper(cfg) -> bool:
    """cfg jest wymagany.

    Domena nie ma prawa importowac `config` (pilnuje tego
    test_architecture_boundaries) i slusznie: modul, ktory sam siega po stan
    globalny, jest nietestowalny i niewidoczny w sygnaturze. Konfiguracje
    podaje wolajacy.
    """
    return coerce_paper_flag(getattr(cfg, "PAPER_TRADING", True))


def is_live(cfg) -> bool:
    return not is_paper(cfg)


def live_execution_armed(cfg) -> bool:
    """LIVE *i* wlaczona egzekucja. Dwa osobne przelaczniki, oba musza paść."""
    return is_live(cfg) and bool(getattr(cfg, "LIVE_EXECUTION_ENABLED", False))


def mode_label(cfg) -> str:
    return "PAPER" if is_paper(cfg) else "LIVE"
