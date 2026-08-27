"""Ile ryzyka wolno wziac na jeden trade.

Pierwszy kawalek `calculate_position_size()` wyciagniety do modulu. Wybrane
celowo te fragmenty, ktore sa **czyste**: pasmo ryzyka per silnik, skalowanie
ryzyka sila sygnalu i mnozniki rezimu/jakosci danych. Kazdy z nich dostaje
jawne argumenty i niczego nie zapisuje.

Dlaczego akurat "niczego nie zapisuje" jest tu sednem. Cztery kolejne bledy
(v20.23.0 - v20.26.0) mialy wspolna przyczyne: `calculate_position_size()`
i `can_open_position()` mutuja ten sam slownik na przemian, a kolejnosc tych
mutacji nie byla nigdzie zapisana ani wymuszona. Mnoznik nakladany po
policzeniu rozmiaru nie robil nic; sila normalizowana po sizingu dawala
pozycji rozmiar z jednej liczby i zapis z drugiej.

`risk_multipliers()` zwraca wiec **parę**: mnoznik oraz slownik znacznikow do
wbicia na sygnal. Wywolujacy decyduje, kiedy je wbic - i widac to w jednej
linii, zamiast byc rozsypane po dwustu.

Progi i brzmienie sa kontraktem: te liczby steruja wielkoscia pozycji.
"""
from __future__ import annotations


def _config(cfg=None):
    if cfg is not None:
        return cfg
    import config as _cfg
    return _cfg


def _f(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def risk_band(is_day: bool, is_rev: bool, cfg=None) -> tuple:
    """(min, max, domyslny) udzial equity w ryzyku - osobny na silnik.

    Kolejnosc sprawdzania ma znaczenie: sygnal jednoczesnie `daytrading`
    i `reversal_confirmed` bierze pasmo daytradingu. Zachowane jak bylo.
    """
    cfg = _config(cfg)
    if is_day:
        return (_f(getattr(cfg, "DAYTRADING_RISK_PCT_MIN", 0.0035), 0.0035),
                _f(getattr(cfg, "DAYTRADING_RISK_PCT_MAX", 0.0060), 0.0060),
                _f(getattr(cfg, "DAYTRADING_RISK_PCT_DEFAULT", 0.0045), 0.0045))
    if is_rev:
        return (_f(getattr(cfg, "REVERSAL_RISK_PCT_MIN", 0.0025), 0.0025),
                _f(getattr(cfg, "REVERSAL_RISK_PCT_MAX", 0.0050), 0.0050),
                _f(getattr(cfg, "REVERSAL_RISK_PCT_DEFAULT", 0.0035), 0.0035))
    return (_f(getattr(cfg, "RISK_PCT_MIN", 0.0050), 0.0050),
            _f(getattr(cfg, "RISK_PCT_MAX", 0.0075), 0.0075),
            _f(getattr(cfg, "RISK_PCT_DEFAULT", 0.0060), 0.0060))


def strength_floor(is_rev: bool, cfg=None) -> float:
    """Dolny koniec skali sily. Reversal ma wlasny, nizszy prog."""
    cfg = _config(cfg)
    if is_rev:
        return _f(getattr(cfg, "REVERSAL_MIN_STRENGTH", 0.48), 0.48)
    return _f(getattr(cfg, "SIZE_STRENGTH_FLOOR",
                      getattr(cfg, "MIN_SIGNAL_STRENGTH", 0.48)), 0.48)


def scale_by_strength(score: float, is_rev: bool, band: tuple, cfg=None) -> float:
    """Interpolacja ryzyka miedzy dolnym a gornym koncem pasma.

    `hi <= lo` w skali sily daje pelne ryzyko (t=1.0) - tak bylo i tak
    zostaje; przy domyslnym configu ta galaz jest nieosiagalna.
    """
    cfg = _config(cfg)
    risk_lo, risk_hi, _ = band
    lo = strength_floor(is_rev, cfg)
    hi = _f(getattr(cfg, "SIZE_STRENGTH_CAP", 1.0), 1.0)
    if hi > lo:
        t = max(0.0, min(1.0, (float(score) - lo) / (hi - lo)))
    else:
        t = 1.0
    return risk_lo + t * (risk_hi - risk_lo)


def risk_multipliers(signal, regime: str, *, is_rev: bool, is_day: bool,
                     is_v2: bool, last_regime_detail=None, cfg=None) -> tuple:
    """(mnoznik, znaczniki) - redukcje ryzyka za rezim i jakosc danych.

    Zwraca iloczyn wszystkich redukcji oraz slownik pol, ktore wywolujacy ma
    wbic na sygnal. Modul niczego nie zapisuje - patrz naglowek.

    UWAGA, ktora warto miec przed oczami: w trybie `capital_pct` (V2 oraz
    reversal z REVERSAL_SIZE_CAPITAL_PCT) notional bierze sie z udzialu
    marginu, a `risk_pct` jest odrzucane w calosci. Wszystkie ponizsze
    redukcje sa wtedy **bezczynne** - polowa rozmiaru w RANGE, kara za
    proxy_4h, za zdegradowane 1D i za ekstremalna zmiennosc po prostu nie
    dzialaja. Znacznik `_uncalibrated_expected_r` jest mimo to wbijany, jakby
    kara zostala nalozona. To zastane zachowanie, nie zmieniamy go tutaj.
    """
    cfg = _config(cfg)
    signal = signal or {}
    mult = 1.0
    stamps: dict = {}

    if regime == "RANGE":
        mult *= _f(getattr(cfg, "REGIME_RANGE_SIZE_MULT", 0.50), 0.50)
    elif regime == "PANIC" and not is_rev and not is_day and not is_v2:
        panic = getattr(cfg, "REGIME_PANIC_TREND_SIZE_MULT", None)
        if panic is None:
            panic = getattr(cfg, "REGIME_PANIC_SIZE_MULT", 1.0)
        mult *= _f(panic, 1.0)

    if signal.get("ohlcv_source") == "proxy_4h" or signal.get("proxy_4h"):
        mult *= _f(getattr(cfg, "PROXY_4H_RISK_MULT", 0.70), 0.70)

    # Bez klamry i bez configu - wartosc idzie wprost z sygnalu. Zachowane
    # jak bylo, ale warto wiedziec, ze 5.0 zwiekszy tu ryzyko pieciokrotnie.
    if signal.get("cross_market_risk_mult"):
        mult *= float(signal["cross_market_risk_mult"])

    if signal.get("degraded_1d"):
        mult *= _f(getattr(cfg, "DEGRADED_1D_RISK_MULT", 0.75), 0.75)

    # Krzywa prior bez obserwacji to nie jest zmierzona oczekiwana wartosc.
    # PAPER moze dalej zbierac dane, ale mniejszym kapitalem.
    er_status = str(signal.get("expected_r_status") or "").upper()
    if er_status in ("PRIOR_ONLY", "LOW_SAMPLE") and not is_day:
        mult *= _f(getattr(cfg, "UNCALIBRATED_EXPECTED_R_SIZE_MULT", 0.65), 0.65)
        stamps["_uncalibrated_expected_r"] = True

    # Ekstremalna zmiennosc. Cala sekcja pod bare exceptem w oryginale -
    # zle dane po prostu nie nakladaly kary. Zachowane.
    try:
        pctile = signal.get("atr_percentile")
        if pctile is None:
            detail = signal.get("market_regime_detail") or last_regime_detail or {}
            if isinstance(detail, dict):
                pctile = detail.get("atr_percentile")
        threshold = _f(getattr(cfg, "EXTREME_VOL_ATR_PCTILE", 85.0), 85.0)
        if pctile is not None and float(pctile) >= threshold:
            mult *= _f(getattr(cfg, "EXTREME_VOL_RISK_MULT", 0.50), 0.50)
    except (TypeError, ValueError):
        pass

    return mult, stamps
