"""Dlaczego expected_net_r jest ujemne: setup czy koszt?

POWOD. Zmierzone wczesniej: z 1062 kandydatow kierunkowych produkcyjna bramka
przepuszcza 25, a 920+ odrzucen to NON_POSITIVE_NET_R - silnik wystawia setup,
ktoremu sam przypisal ujemna wartosc oczekiwana. Proba poluzowania progu
(`parity.py --final-gate --prior-floor 0.05`) dala JEDNA transakcje wiecej,
wiec prog nie jest wytlumaczeniem.

Zostaja dwie mozliwosci i trzeba je rozdzielic, bo prowadza w przeciwne strony:

  (a) BRUTTO jest ujemne - detektor setupow wystawia uklady, ktore same z
      siebie nie maja edge. Wtedy poprawianie kosztow nic nie da.
  (b) BRUTTO jest dodatnie, a zjadaja je KOSZTY. Wtedy pytanie jest o model
      kosztow, dystans SL i venue - nie o strategie.

METODA. Zaden nowy model. `expected_net_r()` juz zwraca pelny rozklad
(gross_r, fee_r, spread_r, slip_r, impact_r, funding_r, sl_dist), wiec ten
skrypt tylko go ZBIERA dla tego samego strumienia kandydatow, co
`tools/risk_overlay.py` i `tools/parity.py` - ta sama kadencja 15m, ten sam
filtr kierunku. Liczby pochodza z produkcyjnej funkcji, nie z jej repliki.

Kazdy koszt jest dzielony przez `sl_dist`, wiec ciasny stop mnozy KAZDY
skladnik. Dlatego rozklad `sl_dist` jest tu raportowany na rowni z kosztami -
to podejrzany numer jeden.

Kontrfaktyczne "co gdyby": dla kazdego skladnika liczone jest, ilu kandydatow
przeszloby na plus, gdyby ten jeden koszt byl zerowy (net + skladnik > 0).
Zerowanie WSZYSTKICH kosztow daje liczbe kandydatow o dodatnim brutto - czyli
czysta jakosc setupu, bez venue.

UWAGA o --as-production-profiles. Flaga wypelnia `_volume_rank`, ktore w
replayu zostaje puste (bo `refresh_volume_ranks()` wola tylko `generate()`,
a replay idzie przez `pipeline.analyze()`). To NIE jest czysty eksperyment
jednej zmiennej: `params_for()` wiaze profil nie tylko z poslizgiem, ale tez
ze `swing_min_move_atr`, `skip_range`, `use_5m_veto` i `skip_4h_oppose`, wiec
zmienia sie caly strumien kandydatow (zmierzone: 1062 -> 1298). Porownuj
rozklady i udzialy, nie liczby bezwzgledne.

    python tools/cost_breakdown.py
    python tools/cost_breakdown.py --symbols BTC --days 30 --json out.json
    python tools/cost_breakdown.py --as-production-profiles
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from parity import (  # noqa: E402
    DEFAULT_DAYS, DEFAULT_SYMBOLS, SKIP_BARS, load_bundles, config_fingerprint,
)

# Skladniki kosztu tak, jak nazywa je expected_net_r(). "other_r" nie jest
# jego polem - to reszta (kara za skrajna niepynnosc), liczona jako
# gross - net - suma nazwanych skladnikow. Gdyby kiedys doszedl nowy koszt,
# pojawi sie tutaj sam, zamiast zniknac po cichu.
COST_FIELDS = ("fee_r", "spread_r", "slip_r", "impact_r", "funding_r")


def _distribution(values: list) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)

    def pct(p):
        return round(ordered[min(n - 1, max(0, int(round(p * (n - 1)))))], 4)

    return {
        "n": n, "min": round(ordered[0], 4), "p10": pct(0.10),
        "median": pct(0.50), "p90": pct(0.90), "max": round(ordered[-1], 4),
        "mean": round(sum(ordered) / n, 4),
        "above_zero": sum(1 for v in ordered if v > 0),
    }


def breakdown_row(symbol: str, br: dict) -> dict:
    """Jeden wiersz z tego, co zwrocilo expected_net_r().

    `other_r` to RESZTA, nie kolejne nazwane pole: gross - net minus suma
    nazwanych skladnikow. Dzieki temu koszt, ktorego nikt nie dopisal do
    COST_FIELDS, pojawia sie tu sam, zamiast zniknac po cichu.
    """
    gross = float(br.get("gross_r") or 0.0)
    net = float(br.get("net_r") or 0.0)
    named = {f: float(br.get(f) or 0.0) for f in COST_FIELDS}
    row = {
        "symbol": symbol,
        "gross_r": gross,
        "net_r": net,
        "sl_dist": float(br.get("sl_dist") or 0.0),
        "total_cost_r": round(gross - net, 4),
        "other_r": round(gross - net - sum(named.values()), 4),
    }
    row.update(named)
    return row


def major_floor_rows(rows: list) -> list:
    """Co by bylo, gdyby replay klasyfikowal symbole tak jak produkcja.

    SPRAWDZONE, nie zalozone: `v2_profiles.profile_for()` awansuje symbol do
    "major" tylko przez `_volume_rank`, ktore wypelnia `refresh_volume_ranks()`.
    Jedyny produkcyjny wolajacy to `daytrading_engine_v2.generate()`, a sciezka
    replayu wola `pipeline.analyze()` i nigdy `generate()`. W swiezym procesie
    `len(_volume_rank) == 0`, wiec wszystko poza BTC/ETH/SOL (twarda lista
    DAYTRADING_V2_PROFILE_MAJOR_ALWAYS) dostaje profil "alt" i floor
    2*DAYTRADING_V2_SLIP_ALT = 30 bps zamiast 2*..._MAJOR = 6 bps. Piec razy
    wiecej, na czlonie, ktory sam jeden przewyzsza caly edge.

    To jest GORNE ograniczenie korekty: zaklada, ze kazdy symbol zmiescilby sie
    w produkcyjnym top-N. Czy ZEC by sie zmiescil - nie wiadomo i ten skrypt
    tego nie rozstrzyga.
    """
    import config as _cfg
    major_rt = 2.0 * float(getattr(_cfg, "DAYTRADING_V2_SLIP_MAJOR", 0.0003) or 0.0003)
    out = []
    for r in rows:
        sl = r.get("sl_dist") or 0.0
        if sl <= 0:
            out.append(dict(r))
            continue
        new_slip_r = major_rt / sl
        row = dict(r)
        # net = gross - ... - slip_r - ...; podmieniamy tylko ten jeden czlon.
        row["net_r"] = round(r["net_r"] + r["slip_r"] - new_slip_r, 4)
        row["slip_r"] = round(new_slip_r, 4)
        out.append(row)
    return out


def counterfactual_net_positive(rows: list) -> dict:
    """Ilu kandydatow przeszloby na plus, gdyby ten JEDEN koszt byl zerowy.

    Kazdy skladnik zostal od net odjety, wiec "bez niego" to net + skladnik.
    """
    return {
        field: sum(1 for r in rows if (r["net_r"] + r.get(field, 0.0)) > 0)
        for field in COST_FIELDS + ("other_r",)
    }


def measure(symbols, days: int) -> dict:
    from daytrading_backtester import production_signal_provider_v2
    from expected_net_r import expected_net_r as _enr

    bundles = load_bundles(symbols, days)
    bar_count = min(len(b["5m"]["opens"]) for b in bundles.values())

    rows = []
    per_symbol = {}
    statuses: Counter = Counter()
    wiring: Counter = Counter()

    for symbol, bundle in bundles.items():
        signal_at, _engine = production_signal_provider_v2(symbol, bundle)
        ts5 = bundle["5m"]["timestamps"]
        seen = 0
        for index in range(SKIP_BARS, bar_count):
            # Ta sama kadencja decyzji co parity.py i risk_overlay.py.
            if int(ts5[index]) % 900_000 != 0:
                continue
            signal = signal_at(index)
            if not signal:
                continue
            # Wiersz bez kierunku to "tu nie ma setupu", nie proba wejscia.
            if str(signal.get("direction") or "").upper() not in ("LONG", "SHORT"):
                continue
            seen += 1
            try:
                br = _enr(dict(signal))
            except Exception:
                continue
            rows.append(breakdown_row(symbol, br))
            statuses[str(br.get("calibration_status") or "UNKNOWN")] += 1
            # Skad wzial sie spread: order_book / measured / default_const.
            # Bez tego nie widac, ile kandydatow faktycznie skorzystalo
            # z pomiaru, a ilu wciaz jedzie na stalej.
            wiring["spread_" + str(br.get("spread_source") or "unknown")] += 1
            # Zero w koszcie moze znaczyc dwie rozne rzeczy: "policzone i
            # wyszlo zero" albo "nie ma czego liczyc". Bez tego licznika
            # funding_r = 0 wygladalby jak pomiar, a moze byc brakiem danych.
            fund = signal.get("funding") or {}
            if fund:
                wiring["funding_dict"] += 1
                try:
                    if float(fund.get("funding_rate") or 0.0) != 0.0:
                        wiring["funding_rate_nonzero"] += 1
                except (TypeError, ValueError):
                    pass
            if (signal.get("order_book") or {}).get("ob_spread_pct") is not None:
                wiring["orderbook_spread"] += 1
            if signal.get("slip_rt") is not None:
                # Gdy V2 podaje slip round-trip, impact_r jest z definicji
                # zerowany, zeby nie liczyc tego samego dwa razy.
                wiring["slip_rt_present"] += 1
        per_symbol[symbol] = {"candidates": seen}

    return {
        "meta": {"symbols": list(symbols), "days": days, "bars": bar_count,
                 "skip_bars": SKIP_BARS},
        "config": config_fingerprint(),
        "totals": {
            "candidates": len(rows),
            "gross_positive": sum(1 for r in rows if r["gross_r"] > 0),
            "net_positive": sum(1 for r in rows if r["net_r"] > 0),
        },
        "calibration_status": dict(statuses.most_common()),
        # Czy skladnik w ogole mial z czego powstac.
        "wiring": dict(wiring),
        "gross_r": _distribution([r["gross_r"] for r in rows]),
        "net_r": _distribution([r["net_r"] for r in rows]),
        "total_cost_r": _distribution([r["total_cost_r"] for r in rows]),
        "sl_dist": _distribution([r["sl_dist"] for r in rows]),
        "components": {
            f: _distribution([r[f] for r in rows])
            for f in COST_FIELDS + ("other_r",)
        },
        "counterfactual_net_positive": counterfactual_net_positive(rows),
        "per_symbol": {
            symbol: {
                "candidates": per_symbol.get(symbol, {}).get("candidates", 0),
                "rows": sum(1 for r in rows if r["symbol"] == symbol),
                "net_positive": sum(1 for r in rows
                                    if r["symbol"] == symbol and r["net_r"] > 0),
                "gross_r": _distribution([r["gross_r"] for r in rows
                                          if r["symbol"] == symbol]),
                "net_r": _distribution([r["net_r"] for r in rows
                                        if r["symbol"] == symbol]),
                "slip_r": _distribution([r["slip_r"] for r in rows
                                         if r["symbol"] == symbol]),
            }
            for symbol in sorted(per_symbol)
        },
        # Rozbieznosc replay/produkcja na profilu symbolu - patrz major_floor_rows.
        "major_floor": _major_floor_summary(rows),
    }


def _major_floor_summary(rows: list) -> dict:
    fixed = major_floor_rows(rows)
    return {
        "net_positive": sum(1 for r in fixed if r["net_r"] > 0),
        "net_r": _distribution([r["net_r"] for r in fixed]),
        "slip_r": _distribution([r["slip_r"] for r in fixed]),
    }


def _line(name: str, dist: dict) -> str:
    if not dist:
        return f"    {name:<14} (brak danych)"
    return (f"    {name:<14} mediana {dist['median']:>8.4f} | srednia {dist['mean']:>8.4f}"
            f" | p10 {dist['p10']:>8.4f} | p90 {dist['p90']:>8.4f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Rozklad expected_net_r na brutto i skladniki kosztu")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--as-production-profiles", action="store_true",
                    help="EKSPERYMENT: wypelnij _volume_rank tak, jak robi to "
                         "generate() w produkcji, zanim zaczniesz mierzyc")
    args = ap.parse_args(argv)

    if args.as_production_profiles:
        # Replay nigdy nie wola generate(), wiec _volume_rank zostaje puste
        # i wszystko poza BTC/ETH/SOL dostaje profil "alt". Tu wypelniamy je
        # recznie, zeby model policzyl major base + PRAWDZIWY impact, zamiast
        # przyblizenia. Zalozenie: kazdy z tych symboli miesci sie w
        # produkcyjnym top-N. Dla ZEC to jest zalozenie, nie fakt.
        import v2_profiles as _vp
        _vp.refresh_volume_ranks([
            {"symbol": s, "blofin_volume_24h": 1e12 - i}
            for i, s in enumerate(args.symbols)
        ])
        print("[koszt] EKSPERYMENT: profile jak w produkcji ->",
              ", ".join(f"{s}={_vp.profile_for(s)}" for s in args.symbols))

    result = measure(args.symbols, args.days)
    result["meta"]["as_production_profiles"] = bool(args.as_production_profiles)
    t = result["totals"]
    n = t["candidates"]
    print(f"[koszt] kandydatow {n} | config {result['config'].get('hash')}")
    if not n:
        print("[koszt] brak kandydatow - nie ma czego rozkladac.")
        return 1

    print(f"[koszt] BRUTTO dodatnie: {t['gross_positive']}/{n}"
          f" ({100.0 * t['gross_positive'] / n:.1f}%)"
          f" | NETTO dodatnie: {t['net_positive']}/{n}"
          f" ({100.0 * t['net_positive'] / n:.1f}%)")
    print("[koszt] rozklady w R:")
    print(_line("gross_r", result["gross_r"]))
    print(_line("net_r", result["net_r"]))
    print(_line("total_cost_r", result["total_cost_r"]))
    print("[koszt] skladniki kosztu w R (kazdy = frakcja / sl_dist):")
    for field in COST_FIELDS + ("other_r",):
        print(_line(field, result["components"].get(field) or {}))

    sl = result.get("sl_dist") or {}
    if sl:
        print(f"[koszt] sl_dist (ulamek ceny): mediana {sl['median']:.5f}"
              f" | p10 {sl['p10']:.5f} | p90 {sl['p90']:.5f}")
        print("[koszt]   kazdy koszt jest dzielony przez te liczbe - ciasny stop"
              " mnozy WSZYSTKIE skladniki naraz.")

    print("[koszt] gdyby ten JEDEN koszt byl zerowy, netto dodatnich byloby:")
    base = t["net_positive"]
    for field, count in sorted(result["counterfactual_net_positive"].items(),
                               key=lambda kv: -kv[1]):
        print(f"    {field:<14} {count:>6}  ({count - base:+d} wzgledem {base})")
    print(f"    {'wszystkie=0':<14} {t['gross_positive']:>6}"
          f"  ({t['gross_positive'] - base:+d}) <- czysta jakosc setupu, bez venue")

    for status, count in (result.get("calibration_status") or {}).items():
        print(f"[koszt] status kalibracji {status}: {count}")
    wiring = result.get("wiring") or {}
    print(f"[koszt] czy skladnik mial z czego powstac (na {n} kandydatow):")
    for key in ("funding_dict", "funding_rate_nonzero", "orderbook_spread",
                "slip_rt_present", "spread_measured", "spread_in_slip_rt",
                "spread_default_const", "spread_order_book"):
        print(f"    {key:<22} {wiring.get(key, 0):>6}")
    if wiring.get("spread_default_const"):
        print(f"[koszt]   UWAGA: {wiring['spread_default_const']} kandydatow"
              " nadal jedzie na STALEJ DEFAULT_SPREAD_FRAC - te symbole nie sa"
              " zmierzone i ich spread_r nie jest pomiarem rynku.")
    if wiring.get("spread_measured") and not wiring.get("orderbook_spread"):
        print("[koszt]   spread_r pochodzi ze snapshotu gieldy proxy (Binance),"
              " nie z zywej ksiazki i nie z BloFina - to dolne ograniczenie.")
    if not wiring.get("funding_rate_nonzero"):
        print("[koszt]   funding_r = 0 znaczy 'nie bylo czego liczyc',"
              " a nie 'funding nic nie kosztuje'.")

    print("[koszt] per symbol (profil replayu decyduje o floorze poslizgu):")
    for symbol, row in sorted((result.get("per_symbol") or {}).items()):
        sr = row.get("slip_r") or {}
        nr = row.get("net_r") or {}
        gr = row.get("gross_r") or {}
        print(f"    {symbol:<6} kandydatow {row.get('rows', 0):>5}"
              f" | netto dodatnich {row.get('net_positive', 0):>4}"
              f" | slip_r med {sr.get('median', 0):.4f}"
              f" | gross med {gr.get('median', 0):>7.4f}"
              f" | net med {nr.get('median', 0):>7.4f}")

    mf = result.get("major_floor") or {}
    if mf:
        mf_pos = mf.get("net_positive") or 0
        print("[koszt] POROWNANIE ze starym floorem major (6 bps RT):")
        print(f"    netto dodatnich {t['net_positive']} -> {mf_pos}"
              f" | mediana net_r {(result['net_r'] or {}).get('median')}"
              f" -> {(mf.get('net_r') or {}).get('median')}")
        if mf_pos < t["net_positive"]:
            print("    Stary floor jest teraz GORSZY od pomiaru - 6 bps to"
                  " wiecej niz zmierzony spread wiekszosci symboli.")
            print("    Ten kontrfaktyczny powstal, gdy nie bylo jeszcze pomiaru,"
                  " i sluzyl za GORNE ograniczenie korekty. Dzis jest juz tylko")
            print("    porownaniem ze stara stala - zostaje jako kontrola, ze"
                  " pomiar faktycznie zmienil sytuacje, a nie jako oszacowanie.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        print(f"[koszt] zapisano: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
