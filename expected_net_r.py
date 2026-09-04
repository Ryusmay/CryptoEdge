# ============================================================
# Expected Net R — wspolny filtr obu silnikow (Trend + Reversal)
# ============================================================
#
# Gross Expected R
#      |
#  - fees
#  - slippage
#  - funding
#  - spread
#  - estimated market impact
#      |
# Expected Net R
#      |
#  Net R > minimum  ->  trade
# ============================================================

from __future__ import annotations
import math
from typing import Dict, Any, Tuple


def _f(x, default=0.0) -> float:
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def _is_reversal(signal: dict) -> bool:
    return (
        signal.get("engine") == "reversal"
        or signal.get("setup") == "reversal_confirmed"
    )


def _is_daytrading(signal: dict) -> bool:
    if _is_reversal(signal):
        return False
    engine = str(signal.get("engine") or "").lower()
    mode = str(signal.get("strategy_mode") or "").upper()
    return engine in ("daytrading", "daytrading_v2") or mode in ("DAYTRADING", "DAYTRADING_V2")


def _gross_expected_r(signal: dict) -> float:
    import config
    if _is_daytrading(signal):
        plan = signal.get("tp_plan") or {}
        f1 = max(0.0, min(1.0, _f(plan.get("frac_tp1"), 0.50)))
        remainder = max(0.0, 1.0 - f1)
        r1 = max(0.0, _f(plan.get("tp1_r") or signal.get("rr_tp1"), 1.5))
        r2 = max(r1, _f(plan.get("tp2_r") or signal.get("rr_tp2"), 2.2))
        calibration = signal.get("day_expectancy_calibration") or {}
        n = int(_f(calibration.get("n"), 0))
        min_n = int(getattr(config, "EXPECTED_R_MIN_CALIBRATION_OBS", 30))
        if n >= min_n:
            p1 = _f(calibration.get("p_tp1"), 0.0)
            p2_given = _f(calibration.get("p_tp2_given_tp1"), 0.0)
            status = "EMPIRICAL_OOS"
        else:
            p1 = _f(getattr(config, "DAYTRADING_PRIOR_P_TP1", 0.55), 0.55)
            p2_given = _f(getattr(config, "DAYTRADING_PRIOR_P_TP2_GIVEN_TP1", 0.45), 0.45)
            status = "PRIOR_ONLY"
        p1 = max(0.0, min(1.0, p1))
        p2_given = max(0.0, min(1.0, p2_given))
        base_p1, base_p2_given = p1, p2_given
        quality_mult = max(0.65, min(1.10, _f(signal.get("setup_probability_multiplier"), 1.0)))
        p1 = max(0.02, min(0.98, p1 * quality_mult))
        # Dalszy target dostaje lagodniejsza korekte; nie wolno podwojnie
        # nagradzac tego samego candle/RSI confluence.
        p2_given = max(0.02, min(0.98, p2_given * (quality_mult ** 0.5)))
        # Mutually exclusive lifecycle: loss before TP1, then realised TP1
        # fraction plus a TP2-or-break-even remainder.
        gross = -(1.0 - p1) + p1 * (f1 * r1 + remainder * p2_given * r2)
        signal["expected_r_status"] = status
        signal["expected_r_probabilities"] = {
            "p_tp1": p1, "p_tp2_given_tp1": p2_given,
            "base_p_tp1": base_p1, "base_p_tp2_given_tp1": base_p2_given,
            "setup_quality_multiplier": quality_mult, "n": n,
        }
        return gross
    elif _is_reversal(signal):
        plan = signal.get("tp_plan") or {}
        f1 = _f(plan.get("frac_tp1"), _f(getattr(config, "REVERSAL_TP1_FRAC", 0.25)))
        f2 = _f(plan.get("frac_tp2"), _f(getattr(config, "REVERSAL_TP2_FRAC", 0.35)))
        f3 = _f(plan.get("frac_trail"), _f(getattr(config, "REVERSAL_TP3_FRAC", 0.40)))
        # Realne R z poziomów (asymetria setupu), nie stałe 1R/2R
        r1 = _f(signal.get("rr_tp1") or plan.get("tp1_r"), 1.0)
        r2 = _f(signal.get("rr_tp2") or plan.get("tp2_r"), 2.0)
        if r1 < 0.5 and r2 < 0.5:
            # brak sensownej asymetrii
            return 0.1
        score = _f(signal.get("reversal_score") or signal.get("strength"), 0.5)
        t = max(0.0, min(1.0, (score - 0.48) / 0.42))
        trail_r = max(r2, 1.2 + 0.8 * t)
        gross = f1 * r1 + f2 * r2 + f3 * trail_r
        gross *= 0.85 + 0.15 * t
        return max(0.15, gross)
    strength = _f(signal.get("strength"), 0)
    gross = signal.get("expected_r")
    if gross is None:
        try:
            from strength_calibration import get_calibrator
            gross = get_calibrator().expected_r(strength)
        except Exception:
            gross = 0.5
    return _f(gross, 0.5)


def _sl_distance_pct(signal: dict, risk_manager=None) -> float:
    if risk_manager is not None:
        try:
            d = risk_manager._sl_distance_pct(signal)
            if d and d > 0:
                return float(d)
        except Exception:
            pass
    entry = _f(signal.get("price"))
    sl = _f(signal.get("sl_price"))
    if entry > 0 and sl > 0:
        return abs(entry - sl) / entry
    if signal.get("sl_dist_pct"):
        return _f(signal["sl_dist_pct"])
    return 0.022


def _spread_cost_frac(signal: dict) -> float:
    """Spread jako ulamek ceny. Trzy zrodla, w kolejnosci wiarygodnosci.

    1. Zywa ksiazka zlecen z sygnalu - najlepsze, bo dotyczy tej chwili.
    2. Zmierzony spread symbolu (`venue_microstructure`) - snapshot z gieldy
       proxy, uzywany gdy ksiazki nie ma. Replay nie ma jej NIGDY
       (`AsOfBlofinFeed.fetch_order_book` zwraca {}), wiec to jest sciezka
       calego backtestu.
    3. Stala DEFAULT_SPREAD_FRAC - dopiero gdy symbolu nie zmierzono.

    Zmierzone: realne spready ida od 0.0133 bps (BTC) do 4.48 bps (TRUMP),
    czyli 337-krotnie. Stala 4 bps lezy przy gornym koncu tego rozkladu -
    zawyza dla 18 symboli z 19 i zaniza dla jednego. Jedna liczba nie moze
    opisywac wielkosci zmieniajacej sie o dwa i pol rzedu.

    Zrodlo zapisujemy do sygnalu, zeby zero albo mala liczba nie udawaly
    pomiaru tam, gdzie ich nie bylo.
    """
    import config
    ob = signal.get("order_book") or {}
    sp = ob.get("ob_spread_pct")
    if sp is not None:
        signal["_spread_source"] = "order_book"
        return max(0.0, _f(sp) / 100.0)
    try:
        import venue_microstructure
        measured = venue_microstructure.spread_frac(signal.get("symbol"))
    except Exception:
        measured = None
    if measured is not None:
        signal["_spread_source"] = "measured"
        return max(0.0, float(measured))
    signal["_spread_source"] = "default_const"
    return _f(getattr(config, "DEFAULT_SPREAD_FRAC", 0.0004))


def _slippage_frac(signal: dict) -> float:
    import config
    # V2 przekazuje calkowity round-trip slip z tego samego modelu co fill.
    if signal.get("slip_rt") is not None:
        return max(0.0, _f(signal.get("slip_rt")))
    # Pure execution uncertainty only. A VWAP-vs-mid estimate belongs to
    # market impact below; treating it as both charged the same book walk twice.
    return max(0.0, _f(getattr(config, "DEFAULT_SLIPPAGE", 0.0003)))


def _market_impact_frac(signal: dict) -> float:
    import config
    measured = signal.get("_ob_impact") or {}
    if measured.get("impact_pct") is not None:
        return max(0.0, _f(measured["impact_pct"]) / 100.0)
    ob = signal.get("order_book") or {}
    depth = _f(ob.get("ob_depth_usd"))
    notional = _f(signal.get("_planned_notional") or signal.get("size_usd"))
    if depth <= 0:
        if _is_daytrading(signal):
            risk_pct = _f(getattr(config, "DAYTRADING_RISK_PCT_DEFAULT", 0.0045))
        elif _is_reversal(signal):
            return _f(getattr(config, "REVERSAL_DEFAULT_IMPACT_FRAC", 0.0008))
        return _f(getattr(config, "DEFAULT_IMPACT_FRAC", 0.0002))
    if notional <= 0:
        notional = 50.0
    ratio = notional / max(depth, 1.0)
    k = _f(getattr(config, "IMPACT_K", 0.015))
    impact = k * (ratio ** 0.5)
    return min(0.02, max(0.0, impact))


def _funding_cost_frac(signal: dict) -> float:
    import config
    fund = signal.get("funding") or {}
    fr = _f(fund.get("funding_rate"))
    interval_h = _f(fund.get("funding_interval_h"), 8.0) or 8.0
    if _is_daytrading(signal):
        hold_h = _f(getattr(config, "DAYTRADING_EXPECTED_HOLD_HOURS", 6.0), 6.0)
    elif _is_reversal(signal):
        hold_h = _f(getattr(config, "REVERSAL_EXPECTED_HOLD_HOURS", 12.0), 12.0)
    else:
        hold_h = _f(getattr(config, "EXPECTED_HOLD_HOURS", 24.0), 24.0)
    periods = hold_h / interval_h
    direction = (signal.get("direction") or "LONG").upper()
    if direction == "LONG":
        return fr * periods
    return -fr * periods


def expected_net_r(signal: dict, risk_manager=None) -> Dict[str, Any]:
    import config
    gross = _gross_expected_r(signal)
    sl_dist = _sl_distance_pct(signal, risk_manager)
    if sl_dist <= 0:
        sl_dist = 0.022

    fee = _f(getattr(config, "TAKER_FEE", getattr(config, "COMMISSION_RATE", 0.0006)))
    # Wejscie limitem placi MAKERA, nie takera. Do v20.60.0 bylo tu bezwarunkowe
    # fee * 2.0, czyli taker po obu stronach - a to nie zgadzalo sie z reszta
    # systemu:
    #   daytrading_backtester.py:849  (mf + tf) if kind == "limit" else 2*tf
    #   replay_execution.py:133,145   etykietuje fill jako "maker"/"taker"
    # Przy DAYTRADING_V2_LIMIT_IN_ZONE=True silnik wstawia limit_price
    # (daytrading_engine_v2.py:686), a resolve_v2_fill ma tylko dwa wyjscia:
    # "limit" albo "expired" - setup, ktory nie doszedl do strefy, jest
    # ANULOWANY, nigdy nie zamieniany w zlecenie rynkowe. Czyli kazda
    # transakcja, ktora faktycznie powstaje, jest wejsciem limitem.
    # Bramka przeplacala 0.0012 zamiast 0.0008, czyli o 50%, na jedynym
    # koszcie, ktory po naprawie spreadu i poslizgu jeszcze cokolwiek wazy.
    # Straznik: tests/test_fee_parity_gate_vs_replay.py
    maker = _f(getattr(config, "MAKER_FEE", 0.0002))
    fee_rt = (maker + fee) if signal.get("limit_price") is not None else (fee * 2.0)
    spread = _spread_cost_frac(signal)
    slip = _slippage_frac(signal)
    impact = _market_impact_frac(signal)
    fund = _funding_cost_frac(signal)

    fee_r = fee_rt / sl_dist
    has_rt_slip = signal.get("slip_rt") is not None
    # Czy slip_rt zawiera juz spread. Zmierzona sciezka V2 zwraca round-trip
    # rowny JEDNEMU pelnemu spreadowi (zlecenie miesci sie na szczycie ksiegi,
    # wiec calym kosztem jest przejscie spreadu). Doliczanie spread_r obok
    # byloby policzeniem go dwa razy. Stara sciezka zwraca stala slip_one_way,
    # ktora spreadu NIE zawiera - tam spread_r zostaje.
    spread_in_slip = False
    if has_rt_slip:
        try:
            import v2_profiles
            spread_in_slip = bool(v2_profiles.slip_includes_spread(signal.get("symbol")))
        except Exception:
            spread_in_slip = False
    if spread_in_slip:
        signal["_spread_source"] = "in_slip_rt"
    spread_r = (0.0 if spread_in_slip else spread) / sl_dist
    slip_r = (slip if has_rt_slip else slip * 2.0) / sl_dist
    # slip_rt V2 zawiera estymowany impact z wolumenu swiecy; nie licz go drugi raz.
    impact_r = (0.0 if has_rt_slip else impact) / sl_dist
    fund_r = fund / sl_dist

    net = gross - fee_r - spread_r - slip_r - impact_r
    if fund_r > 0:
        net -= fund_r
    else:
        net -= fund_r  # rebate increases net

    # Ekstremalna niepłynność: dodatkowa kara (typowa dla reversal na climax)
    liq_cost_r = spread_r + impact_r
    if liq_cost_r >= 0.12:
        net -= 0.15
    elif liq_cost_r >= 0.06:
        net -= 0.06

    risk_pct = _f(signal.get("_risk_pct"))
    if risk_pct <= 0:
        if _is_reversal(signal):
            risk_pct = _f(getattr(config, "REVERSAL_RISK_PCT_DEFAULT", 0.0035))
        else:
            risk_pct = _f(getattr(config, "RISK_PCT_DEFAULT", 0.006))

    # Zaokraglenie do 6, nie 4. Przy 4 miejscach zmierzony spread BTC
    # (0.0000666 R) stawal sie 0.0001 - piecdziesiat procent w gore - a
    # cokolwiek ponizej 0.00005 R zaokraglalo sie DO ZERA i realny koszt
    # znikal. Skoro spready sa zmierzone do trzech cyfr znaczacych, nie wolno
    # ich gubic w wyniku.
    out = {
        "gross_r": round(gross, 6),
        "fee_r": round(fee_r, 6),
        "spread_r": round(spread_r, 6),
        "slip_r": round(slip_r, 6),
        "impact_r": round(impact_r, 6),
        "funding_r": round(fund_r, 6),
        "net_r": round(net, 6),
        "sl_dist": round(sl_dist, 5),
        "risk_pct": risk_pct,
        # Czy spread byl ZMIERZONY, czy wziety ze stalej. Bez tego mala liczba
        # w spread_r wyglada tak samo niezaleznie od tego, skad pochodzi.
        "spread_source": signal.get("_spread_source") or "unknown",
        "engine": "daytrading" if _is_daytrading(signal) else ("reversal" if _is_reversal(signal) else "trend"),
        "calibration_status": signal.get("expected_r_status") or "UNKNOWN",
        "probabilities": signal.get("expected_r_probabilities") or {},
        "calibration_n": (signal.get("expected_r_calibration") or {}).get("n"),
    }
    signal["expected_gross_r"] = out["gross_r"]
    signal["expected_net_r"] = out["net_r"]
    signal["expected_r_breakdown"] = out
    return out


def net_r_ok(signal: dict, risk_manager=None) -> Tuple[bool, str]:
    import config
    if not bool(getattr(config, "USE_EXPECTED_NET_R_FILTER", True)):
        return True, "OK"
    br = expected_net_r(signal, risk_manager)
    # Niezależnie od jakości kalibracji transakcja o niedodatnim expectancy
    # nie może zwiększać ekspozycji. LOW_SAMPLE/PRIOR_ONLY może jedynie
    # zmniejszać sizing dodatniego setupu, nigdy legalizować ujemny edge.
    try:
        net_r = float(br["net_r"])
    except (KeyError, TypeError, ValueError):
        return False, "EXPECTED_NET_R_INVALID"
    if not math.isfinite(net_r):
        return False, "EXPECTED_NET_R_INVALID"
    if net_r <= 0.0:
        return False, f"NON_POSITIVE_NET_R({net_r:.2f}<=0)"
    if _is_daytrading(signal):
        min_net = float(getattr(config, "DAYTRADING_MIN_EXPECTED_NET_R", 0.20))
    elif _is_reversal(signal):
        min_net = float(getattr(config, "REVERSAL_MIN_EXPECTED_NET_R", 0.20))
    else:
        min_net = float(getattr(config, "MIN_EXPECTED_NET_R", 0.05))
    status = str(signal.get("expected_r_status") or br.get("calibration_status") or "")
    calib_n = 0
    try:
        calib_n = int(br.get("calibration_n") or (signal.get("day_expectancy_calibration") or {}).get("n") or 0)
    except (TypeError, ValueError):
        calib_n = 0
    min_sample = int(getattr(config, "DAYTRADING_NET_R_MIN_SAMPLE", 20) or 20)
    if _is_daytrading(signal) and (status in ("PRIOR_ONLY", "LOW_SAMPLE") or calib_n < min_sample):
        prior_floor = float(getattr(
            config, "DAYTRADING_PRIOR_ONLY_MIN_EXPECTED_NET_R", min_net,
        ))
        prior_floor = max(0.0, prior_floor)
        if net_r < prior_floor:
            return False, (
                f"DAY_PRIOR_NET_R_LOW({net_r:.2f}<{prior_floor:.2f}"
                f"|n={calib_n}|{status})"
            )
        return True, f"NET_R_SOFT({net_r:.2f}|n={calib_n}|{status})"
    if _is_reversal(signal) and status in ("PRIOR_ONLY", "LOW_SAMPLE"):
        prior_floor = float(getattr(config, "REVERSAL_PRIOR_ONLY_MIN_EXPECTED_NET_R", min_net))
        if br["net_r"] < prior_floor:
            return False, f"REV_PRIOR_NET_R_LOW({br['net_r']:.2f}<{prior_floor:.2f}|{status})"
    if br["net_r"] < min_net:
        return False, (
            f"LOW_NET_R({br['net_r']:.2f}<{min_net}|{br.get('engine')}"
            f"|g={br['gross_r']:.2f}"
            f" fee={br['fee_r']:.2f}"
            f" spr={br['spread_r']:.2f}"
            f" slip={br['slip_r']:.2f}"
            f" imp={br['impact_r']:.2f}"
            f" fund={br['funding_r']:.2f})"
        )
    return True, "OK"
