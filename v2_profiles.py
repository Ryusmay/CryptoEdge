"""V2 instrument profiles: major / alt / metal.

Uniwersum zostaje pełne. Różnią się gałki (ATR, RANGE, size, 4H, 5m).
BTC/ETH/SOL zawsze major. Rank ≤ TOP_N po quoteVolume też major. XAU/XAG = metal.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

import config


_volume_rank: Dict[str, int] = {}


def _sym(symbol: str) -> str:
    s = str(symbol or "").upper().replace("-USDT", "").replace("USDT", "")
    return s


def _set(name: str, default) -> set:
    raw = getattr(config, name, default)
    return {str(x).upper() for x in (raw or default or [])}


def refresh_volume_ranks(coins: Iterable[dict]) -> Dict[str, int]:
    """Raz na cykl generate(): rank 1 = największy quoteVolume."""
    global _volume_rank

    def vol(coin: dict) -> float:
        try:
            explicit = float(coin.get("blofin_quote_volume_24h") or 0)
        except (TypeError, ValueError):
            explicit = 0.0
        if explicit > 0:
            return explicit
        try:
            base = float(coin.get("blofin_base_volume_24h") or 0)
            px = float(coin.get("price") or 0)
        except (TypeError, ValueError):
            return 0.0
        if base > 0 and px > 0:
            return base * px
        try:
            return float(coin.get("blofin_volume_24h") or coin.get("volume_24h") or 0)
        except (TypeError, ValueError):
            return 0.0

    vols = {}
    for c in coins or []:
        s = _sym(c.get("symbol"))
        if s:
            vols[s] = vol(c)
    order = sorted(vols, key=lambda s: vols[s], reverse=True)
    _volume_rank = {s: i + 1 for i, s in enumerate(order)}
    return dict(_volume_rank)


def profile_for(symbol: str, coin: Optional[dict] = None) -> str:
    s = _sym(symbol)
    if s in _set("DAYTRADING_V2_PROFILE_METAL", ("XAU", "XAG")):
        return "metal"
    if s in _set("DAYTRADING_V2_PROFILE_MAJOR_ALWAYS", ("BTC", "ETH", "SOL")):
        return "major"
    top_n = int(getattr(config, "DAYTRADING_V2_PROFILE_MAJOR_TOP_N", 30) or 30)
    rank = _volume_rank.get(s)
    if rank is not None and rank <= top_n:
        return "major"
    return "alt"


def params_for(profile: str) -> dict:
    p = str(profile or "alt").lower()
    if p == "metal":
        return {
            "name": "metal",
            "swing_min_move_atr": float(getattr(config, "DAYTRADING_V2_METAL_SWING_MIN_MOVE_ATR", 2.0) or 2.0),
            "skip_range": True,
            "range_adx_max": float(getattr(config, "DAYTRADING_V2_ALT_RANGE_ADX_MAX", 18.0) or 18.0),
            "margin_pct": float(getattr(config, "DAYTRADING_V2_METAL_MARGIN_PCT", 5.0) or 5.0),
            "oppose_size_mult": 1.0,
            "skip_4h_oppose": True,
            "use_5m_veto": bool(getattr(config, "DAYTRADING_V2_METAL_USE_5M_VETO", False)),
            "use_4h_context": bool(getattr(config, "DAYTRADING_V2_METAL_USE_4H_CONTEXT", False)),
            "slip_one_way": float(getattr(config, "DAYTRADING_V2_SLIP_METAL", 0.0005) or 0.0005),
            "sl_atr_buffer": float(getattr(config, "DAYTRADING_V2_METAL_SL_ATR_BUFFER", 2.5) or 2.5),
            "trade": bool(getattr(config, "DAYTRADING_V2_METAL_TRADE", False)),
        }
    if p == "alt":
        return {
            "name": "alt",
            "swing_min_move_atr": float(getattr(config, "DAYTRADING_V2_ALT_SWING_MIN_MOVE_ATR", 2.8) or 2.8),
            "skip_range": bool(getattr(config, "DAYTRADING_V2_ALT_SKIP_RANGE", True)),
            "range_adx_max": float(getattr(config, "DAYTRADING_V2_ALT_RANGE_ADX_MAX", 18.0) or 18.0),
            "margin_pct": float(getattr(config, "DAYTRADING_V2_ALT_MARGIN_PCT", 5.0) or 5.0),
            "oppose_size_mult": float(getattr(config, "DAYTRADING_V2_ALT_4H_OPPOSE_SIZE_MULT", 0.50) or 0.50),
            "skip_4h_oppose": bool(getattr(config, "DAYTRADING_V2_ALT_SKIP_4H_OPPOSE", True)),
            "use_5m_veto": True,
            "use_4h_context": True,
            "slip_one_way": float(getattr(config, "DAYTRADING_V2_SLIP_ALT", 0.0015) or 0.0015),
            "sl_atr_buffer": float(getattr(config, "DAYTRADING_V2_SL_ATR_BUFFER", 1.5) or 1.5),
            "trade": True,
        }
    return {
        "name": "major",
        "swing_min_move_atr": float(getattr(config, "DAYTRADING_V2_SWING_MIN_MOVE_ATR", 2.0) or 2.0),
        "skip_range": False,
        "range_adx_max": 18.0,
        "margin_pct": float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 7.5) or 7.5),
        "oppose_size_mult": float(getattr(config, "DAYTRADING_V2_4H_OPPOSE_SIZE_MULT", 0.70) or 0.70),
        "skip_4h_oppose": False,
        "use_5m_veto": True,
        "use_4h_context": True,
        "slip_one_way": float(getattr(config, "DAYTRADING_V2_SLIP_MAJOR", 0.0003) or 0.0003),
        "sl_atr_buffer": float(getattr(config, "DAYTRADING_V2_SL_ATR_BUFFER", 1.5) or 1.5),
        "trade": True,
    }


def _planned_notional_usd(symbol: str) -> float:
    """Notional modelowanego zlecenia. Uwaga: przypiety do STARTING_CAPITAL,
    wiec powiekszenie konta 100x go NIE rusza. To osobna wada, tu tylko
    odtworzona wiernie, zeby porownanie ze stara sciezka bylo uczciwe."""
    p = params_for(profile_for(symbol))
    eq = float(getattr(config, "STARTING_CAPITAL", 100) or 100)
    lev = float(getattr(config, "LEVERAGE", 10) or 10)
    return eq * (float(p.get("margin_pct") or 5.0) / 100.0) * lev


def _measured_slip_round_trip(symbol: str) -> Optional[float]:
    """Round-trip slip ze zmierzonej mikrostruktury, albo None.

    None znaczy "nie umiem tego policzyc z pomiaru" - nie "zero". Wolajacy
    wraca wtedy do starego modelu i to jest swiadome, a nie ciche.
    """
    try:
        import venue_microstructure
    except Exception:
        return None
    spread = venue_microstructure.spread_frac(symbol)
    if spread is None:
        return None
    notional = _planned_notional_usd(symbol)
    # Bierzemy CIENSZA strone ksiegi - wejscie i wyjscie ida w przeciwne
    # strony, wiec liczy sie ta gorsza z dwoch.
    depths = [d for d in (venue_microstructure.top1_depth_usd(symbol, "ask"),
                          venue_microstructure.top1_depth_usd(symbol, "bid"))
              if d]
    if not depths:
        return None
    thinnest = min(depths)
    if notional > thinnest:
        # Zlecenie zjada ksiege glebiej niz pierwszy poziom. Nie mamy ksztaltu
        # ksiegi ponizej szczytu, wiec nie zmyslamy - stary model.
        return None
    # Miesci sie na szczycie: caly koszt to przejscie spreadu, raz w kazda
    # strone, czyli round-trip = jeden pelny spread. Impact zerowy.
    return max(0.0, min(0.02, float(spread)))


def slip_includes_spread(symbol: str) -> bool:
    """Czy `slip_rt` tego symbolu ZAWIERA juz spread.

    To nie jest kosmetyka. Zmierzona sciezka zwraca round-trip rowny jednemu
    pelnemu spreadowi, wiec `expected_net_r` nie moze doliczac `spread_r`
    obok - byloby to policzenie spreadu dwa razy. Stara sciezka natomiast
    zwraca stala `slip_one_way`, ktora spreadu NIE zawiera, wiec tam
    `spread_r` doliczyc trzeba.

    Bezwarunkowe zerowanie `spread_r` przy obecnym `slip_rt` odwrocilo by
    decyzje w przypadkach z szeroka ksiazka zlecen (wide_spread_*), gdzie
    spread jest glownym kosztem i ma zostac policzony.
    """
    return _measured_slip_round_trip(symbol) is not None


def replay_slip_round_trip(
    symbol: str,
    ohlcv: Optional[dict] = None,
    index: int = 0,
    price: float = 0.0,
    fallback: float = 0.0006,
) -> float:
    """Round-trip slip for V2 replay.

    ZMIERZONA SCIEZKA (gdy symbol jest w venue_microstructure). Dla zlecenia,
    ktore miesci sie na szczycie ksiegi, calym kosztem egzekucji wzgledem mid
    jest przejscie pol spreadu w kazda strone - czyli round-trip rowna sie
    JEDNEMU spreadowi. Market impact zaczyna sie dopiero, gdy zlecenie zjada
    ksiege, i wtedy wlasciwym mianownikiem jest GLEBOKOSC SZCZYTU, a nie obrot
    calej swiecy 5m.

    Zmierzone na 19 symbolach: modelowane zlecenie to od 0.016% szczytu ksiegi
    (BTC) do 16% (XMR). Nigdzie go nie przekracza, wiec impact jest dzis zerowy
    w calym uniwersum - ale prog realnie wiaze i przy wiekszym koncie zacznie
    dzialac.

    Stary model liczyl partycypacje wzgledem obrotu calej swiecy z twardo
    wpisanym k=0.08 i wykladnikiem 0.6, ktorych nie ma nawet w config.py.
    Dawal mediane 0.0901 R - wiecej niz caly medianowy edge (0.0476 R).

    STARA SCIEZKA zostaje dla symboli niezmierzonych ORAZ dla zlecen wiekszych
    niz szczyt ksiegi. Tego drugiego przypadku nie umiemy dzis policzyc lepiej
    (potrzebny byly by ksztalt ksiegi glebiej), wiec zamiast wymyslac kolejna
    stala - wracamy do modelu, ktory przynajmniej jest udokumentowany.
    """
    if not symbol:
        return float(fallback)

    measured = _measured_slip_round_trip(symbol)
    if measured is not None:
        return measured

    p = params_for(profile_for(symbol))
    base = float(p.get("slip_one_way") or 0.0003)
    eq = float(getattr(config, "STARTING_CAPITAL", 100) or 100)
    lev = float(getattr(config, "LEVERAGE", 10) or 10)
    notional = eq * (float(p.get("margin_pct") or 5.0) / 100.0) * lev
    vol = None
    vols = (ohlcv or {}).get("volumes") or []
    if 0 <= int(index) < len(vols):
        vol = vols[int(index)]
    try:
        from orderbook_impact import estimate_bar_slippage
        one = float(estimate_bar_slippage(
            notional, vol, price or None, base_slip=base,
        ).get("slip") or base)
    except Exception:
        one = base
    return min(0.02, max(2.0 * base, 2.0 * one))


def paper_slip_round_trip(symbol: str, signal: Optional[dict] = None) -> float:
    """Ten sam slip RT co replay — paper i WF liczą identycznie.

    Produkcyjny sygnał V2 niesie ``slip_rt`` policzony z tej samej zamkniętej
    świecy co decyzja. Gdy go brak (np. ręczny sygnał/test), używamy
    deterministycznego profilu symbolu. Nie sięgamy do globalnego STORE, bo
    mogłaby tam być już nowsza świeca niż ta, na której powstał sygnał.
    """
    if signal and signal.get("slip_rt") is not None:
        try:
            return min(0.02, max(0.0, float(signal.get("slip_rt"))))
        except (TypeError, ValueError):
            pass
    return replay_slip_round_trip(symbol)
