"""Filtr strategii primary (4h) i fallback MTF.

Pyta o jedno: czy sygnal bez potwierdzenia z wyzszego interwalu ma prawo
wejsc. Do v20.21.0 ta galaz nie byla objeta zadna bramka - korpus jechal na
DAYTRADING_V2, ktory omija ja w calosci. Najpierw powstalo pokrycie
(120 przypadkow, powody STRAT_* i DAY_*), dopiero potem te przenosiny.

Czego tu NIE ma: galezi ustawiajacych `signal["_size_mult"]` na 0.6 i 0.5.
Mutuja sygnal w miejscu i zostaja w `can_open_position()`, dopoki mutacja nie
zostanie zrobiona jawna. Ich wartosci sa juz przypiete w baseline, wiec ten
krok bedzie dowodliwy.

Zachowany swiadomie: kierunek czytamy surowo (`signal.get("direction")`), bez
`.upper()`. Walidacja ksztaltu normalizuje kierunek tylko na potrzeby wlasnej
kontroli, a heat limit i MTF od zawsze porownuja wartosc surowa - sygnal
"long" pisany mala litera nie dostanie tu wiekszosci MTF. To niespojnosc,
ale realna i widoczna w telemetrii; naprawa jest osobna decyzja, nie skutkiem
ubocznym refaktoru.

Brzmienie powodow jest kontraktem - trafiaja do reject_log i na ekran.
"""
from __future__ import annotations

# Markery miekkiego przejscia dopisywane przez silnik do `reasons`.
SOFT_ALIGN_MARKERS = ("PRIMARY_MTF_FALLBACK", "PRIMARY_SOFT_PASS", "STRAT_SOFT_ALIGN")

V2_ENGINES = ("daytrading_v2", "daytradingv2")

CONFIRMED_DAY_SETUPS = (
    "intraday_5m_confirmed",
    "intraday_15m_confirmed",
    "intraday_confirmed",
)

NATIVE_SIGNAL_SOURCE = "BLOFIN_NATIVE"


def _config(cfg=None):
    if cfg is not None:
        return cfg
    import config as _cfg
    return _cfg


def min_votes(cfg=None) -> int:
    cfg = _config(cfg)
    return int(getattr(cfg, "MTF_MIN_VOTES_FALLBACK", 2) or 2)


def votes_of(signal) -> tuple:
    """(long, short). Brak sekcji mtf to zero glosow, nie blad."""
    mtf = (signal or {}).get("mtf") or {}
    return int(mtf.get("long_votes") or 0), int(mtf.get("short_votes") or 0)


def mtf_majority(direction, signal, cfg=None) -> bool:
    """Czy wyzsze interwaly glosuja za tym kierunkiem.

    Kierunek porownywany surowo - patrz uwaga w naglowku modulu.
    """
    long_votes, short_votes = votes_of(signal)
    minimum = min_votes(cfg)
    return (
        (direction == "LONG" and long_votes >= minimum)
        or (direction == "SHORT" and short_votes >= minimum)
    )


def has_soft_align(reasons) -> bool:
    """Silnik zglosil miekkie potwierdzenie (ADX + SuperTrend)."""
    joined = " ".join(str(r) for r in (reasons or []))
    return any(marker in joined for marker in SOFT_ALIGN_MARKERS)


def is_v2(signal) -> bool:
    signal = signal or {}
    engine = str(signal.get("engine") or "").lower()
    return engine in V2_ENGINES or signal.get("strategy_mode") == "DAYTRADING_V2"


def is_daytrading(signal) -> bool:
    signal = signal or {}
    engine = str(signal.get("engine") or "").lower()
    return (engine == "daytrading" or signal.get("strategy_mode") == "DAYTRADING") and not is_v2(signal)


def primary_filter_applies(signal, cfg=None) -> bool:
    """V2 i DAYTRADING maja wlasne kontrole w silniku i omijaja ten filtr."""
    cfg = _config(cfg)
    require = getattr(cfg, "REQUIRE_PRIMARY_STRATEGY",
                      getattr(cfg, "REQUIRE_STRATEGY_1H", True))
    aggressive = getattr(cfg, "AGGRESSIVE_MODE", False)
    return bool(require) and not aggressive and not is_daytrading(signal) and not is_v2(signal)


def day_setup_ok(signal) -> tuple:
    """Galaz DAYTRADING: setup musi byc potwierdzony i ze zrodla natywnego."""
    signal = signal or {}
    if str(signal.get("setup") or "") not in CONFIRMED_DAY_SETUPS:
        return False, "DAY_SETUP_NOT_CONFIRMED"
    if str(signal.get("signal_source") or "") != NATIVE_SIGNAL_SOURCE:
        return False, "DAY_NON_NATIVE_SOURCE"
    return True, "OK"


def strat_na_verdict(strength, regime, mtf_ok, long_votes, short_votes, cfg=None) -> tuple:
    """Brak oceny 4h (STRAT_PRIMARY_NA) - wpuscic czy nie.

    Uwaga na ksztalt: to nie jest zwykly lancuch if/elif. Kontrola
    `strength < min_str` na koncu jest BEZWARUNKOWA i dziala takze wtedy,
    gdy wiekszosc MTF przepuscila sygnal wyzej. Dzieki temu STRAT_NA_WEAK
    lapie sygnaly reversal, ktore przeszly wlasny, nizszy prog sily
    (REVERSAL_MIN_STRENGTH=0.32) - dla trendu ta linia jest nieosiagalna,
    bo bramka sily per silnik odrzuca wczesniej.
    """
    cfg = _config(cfg)
    strength = float(strength or 0)
    min_str = float(cfg.MIN_SIGNAL_STRENGTH)
    range_min = float(getattr(cfg, "STRAT_NA_RANGE_MIN_STRENGTH", 0.68))
    block_na_range = getattr(cfg, "BLOCK_STRAT_NA_IN_RANGE", True)

    if mtf_ok:
        pass
    elif regime == "RANGE" and block_na_range:
        if strength < range_min:
            return False, f"STRAT_NA_RANGE_WEAK({strength:.2f}<{range_min})"
    elif strength < (min_str + 0.08):
        return False, f"STRAT_NA_NO_MTF(L{long_votes}/S{short_votes}<{min_votes(cfg)})"
    if strength < min_str:
        return False, "STRAT_NA_WEAK"
    return True, "OK"
