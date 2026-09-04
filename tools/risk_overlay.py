"""Ile sygnalow replayu odrzucilaby PRODUKCYJNA bramka ryzyka.

POWOD. Etap 6 deklaruje "replay zmienia wylacznie data source, clock
i executor" i stawia warunek wyjscia "parytet decyzji runtime/replay".
Zmierzone: `can_open_position` ma 77 wystapien w repo i ANI JEDNEGO
w plikach replayu (`daytrading_backtester.py`, `historical_replay.py`,
`tools/parity.py`, `run_historical_replay.py`). Lejek wejsc replayu to
`max_positions` + `max_same_direction`; produkcyjny to dodatkowo REDUCE_ONLY,
halt, budzet dzienny, seria strat, sloty per regime, korelacja, Expected
Net R, dynamiczny spread i orderbook impact.

Konsekwencja jest powazniejsza niz sam etap 6: `tools/parity.py` mierzy
replay wzgledem WLASNEGO baseline'u (to bramka regresji i tak dziala), wiec
liczba net_r opisuje strategie, ktorej produkcja moglaby nie handlowac.

METODA - swiadomie DOLNE OGRANICZENIE. Przed kazdym sprawdzeniem stan konta
jest resetowany do maksymalnie poblazliwego: pelny kapital, zero otwartych
pozycji, zero serii strat, pelny budzet dzienny, brak haltu, PAPER (wiec bez
blokady na drift). W takich warunkach WSZYSTKIE kontrole zalezne od stanu
przechodza z definicji. Zostaja wylacznie kontrole zalezne od SAMEGO
SYGNALU. Kazde odrzucenie jest wiec odrzuceniem, ktore produkcja zrobilaby
TYM BARDZIEJ przy realnym stanie konta - liczba nie jest oszacowaniem,
tylko dolnym progiem.

Czego ten pomiar NIE mowi: ile dolozylyby sloty, korelacja, budzet dzienny
i seria strat. Te wymagaja modelu konta, ktorego replay nie ma, a zgadywanie
go dalo by liczbe wygladajaca na pomiar.

WYNIK (30d x BTC, ETH, SOL, XRP, ZEC, config 60c1af1e975f04f6):
  1062 kandydatow kierunkowych, produkcja przepuszcza 25 (2.35%).
  928 odrzucen NON_POSITIVE_NET_R, 109 DAY_PRIOR_NET_R_LOW.
  Rozklad expected_net_r wg bramki: mediana -0.0914, p10 -0.2204,
  dodatnich 134/1062, ponizej -0.10 az 494. Odrzucenia NIE sa graniczne.

I rzecz najwazniejsza: ta liczba jest JUZ W SYGNALE. `daytrading_engine_v2`
(linia 760) wola te sama funkcje `expected_net_r()`, co bramka ryzyka
(`risk_manager.py:458`) - sprawdzone porownaniem, ktore dalo 0 rozbieznosci.
Silnik wystawia wiec setup, ktoremu sam przypisal ujemna oczekiwana wartosc,
a `tools/parity.py` nie ma czego by to odsialo. `historical_replay.py:814`
tego filtra UZYWA (`"final_gate": net_r_ok`) - parity swiadomie prowadzi
wezszy lejek i jego docstring nigdy nie twierdzil inaczej.

Wniosek do czytania z ostroznoscia: to NIE dowodzi, ze strategia z wlaczonym
filtrem byla by rentowna. Dowodzi, ze -5.8939R z `parity.py` opisuje
konfiguracje handlujaca setupami, ktore system sam ocenia na minus - wiec nie
jest werdyktem o strategii. Bramka regresji dziala; blad bylby w czytaniu jej
net_r jako wyniku.

    python tools/risk_overlay.py            # zmierz
    python tools/risk_overlay.py --symbols BTC --days 30
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

import config  # noqa: E402
from parity import (  # noqa: E402
    DEFAULT_DAYS, DEFAULT_SYMBOLS, SKIP_BARS, load_bundles, config_fingerprint,
)


def _permissive_risk():
    """RiskManager w stanie, w ktorym KAZDA kontrola zalezna od konta przechodzi.

    To jest cala metoda tego pomiaru: skoro stan nie moze niczego odrzucic,
    kazde odrzucenie pochodzi od samego sygnalu.
    """
    from risk_manager import RiskManager

    capital = float(getattr(config, "INITIAL_CAPITAL", 1000) or 1000)
    risk = RiskManager(starting_capital=capital)
    risk.is_halted = False
    risk.halt_reason = None
    risk.paused = False
    risk.risk_state = "NORMAL"
    risk.reduce_only_reason = None
    risk.current_capital = capital
    risk.daily_start_capital = capital
    risk.daily_pnl = 0.0
    risk.loss_pause_until = None
    risk.consecutive_losses = 0
    risk.open_positions_count = 0
    return risk


def _reason_bucket(reason: str) -> str:
    """Powod odrzucenia bez liczb, zeby histogram sie nie rozsypal."""
    text = str(reason or "").strip()
    for cut in ("(", "["):
        if cut in text:
            text = text.split(cut, 1)[0]
    return text.strip() or "UNKNOWN"


def measure(symbols, days: int) -> dict:
    from daytrading_backtester import production_signal_provider_v2

    bundles = load_bundles(symbols, days)
    bar_count = min(len(b["5m"]["opens"]) for b in bundles.values())

    total = 0
    accepted = 0
    reasons: Counter = Counter()
    per_symbol = {}
    net_values: list = []
    both: list = []

    for symbol, bundle in bundles.items():
        signal_at, _engine = production_signal_provider_v2(symbol, bundle)
        ts5 = bundle["5m"]["timestamps"]
        seen = 0
        ok = 0
        for index in range(SKIP_BARS, bar_count):
            # Ta sama kadencja decyzji, co w tools/parity.py - inaczej
            # mierzylbym inny strumien niz ten, ktory daje net_r.
            if int(ts5[index]) % 900_000 != 0:
                continue
            signal = signal_at(index)
            if not signal:
                continue
            # Wiersz bez kierunku to "tu nie ma setupu", a nie proba wejscia.
            # Pierwsza wersja tego pomiaru liczyla je jako odrzucenia i dawala
            # 99.88% - liczbe o mojej uprzezy, nie o bramce ryzyka.
            direction = str(signal.get("direction") or "").upper()
            if direction not in ("LONG", "SHORT"):
                continue
            seen += 1
            risk = _permissive_risk()
            try:
                allowed, why = risk.can_open_position(dict(signal), open_directions=[])
            except Exception as exc:
                allowed, why = False, f"RAISED:{type(exc).__name__}"
            if allowed:
                ok += 1
            else:
                reasons[_reason_bucket(why)] += 1
            # Margines, nie tylko werdykt. Odrzucenie przy net_r = -0.01 znaczy
            # co innego niz przy -0.30: pierwsze mowi "bramka sie waha", drugie
            # "to setup o ujemnej oczekiwanej wartosci".
            probe = dict(signal)
            # Sprawdzone, nie zalozone: to JEDEN wlasciciel liczby.
            # `daytrading_engine_v2.py:760` wola te sama funkcje
            # `expected_net_r()`, co bramka ryzyka (`risk_manager.py:458`),
            # i zapisuje wynik do sygnalu. Podejrzewalem dwa konkurencyjne
            # modele - porownanie dalo 0 rozbieznosci na 1062 sygnalach
            # i identyczne mediany, czyli bylo tautologia. Zostaje jako
            # kontrola: gdyby ktos kiedys rozdzielil te sciezki, ta liczba
            # przestanie byc zerem.
            engine_net = probe.get("expected_net_r")
            try:
                from expected_net_r import expected_net_r as _enr
                gate_net = round(float(_enr(probe)["net_r"]), 4)
                net_values.append(gate_net)
                if engine_net is not None:
                    both.append((round(float(engine_net), 4), gate_net))
            except Exception:
                pass
        per_symbol[symbol] = {"signals": seen, "accepted": ok,
                              "rejected": seen - ok}
        total += seen
        accepted += ok

    return {
        "meta": {"symbols": list(symbols), "days": days, "bars": bar_count,
                 "skip_bars": SKIP_BARS},
        "config": config_fingerprint(),
        "totals": {
            "signals": total, "accepted": accepted, "rejected": total - accepted,
            "rejected_pct": round(100.0 * (total - accepted) / total, 2) if total else 0.0,
        },
        "reasons": dict(reasons.most_common()),
        "per_symbol": per_symbol,
        "net_r": _distribution(net_values),
        # Kontrola jednego wlasciciela: silnik i bramka musza liczyc TE SAMA
        # liczbe. Kazda wartosc != 0 znaczy, ze sciezki sie rozjechaly.
        "one_owner_check": {
            "n": len(both),
            "engine_positive": sum(1 for e, _g in both if e > 0),
            "gate_positive": sum(1 for _e, g in both if g > 0),
            "engine_yes_gate_no": sum(1 for e, g in both if e > 0 >= g),
            "gate_yes_engine_no": sum(1 for e, g in both if g > 0 >= e),
            "engine_median": _distribution([e for e, _g in both]).get("median"),
            "gate_median": _distribution([g for _e, g in both]).get("median"),
        },
    }


def _distribution(values: list) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)

    def pct(p):
        return ordered[min(n - 1, max(0, int(round(p * (n - 1)))))]

    return {
        "n": n, "min": ordered[0], "p10": pct(0.10), "median": pct(0.50),
        "p90": pct(0.90), "max": ordered[-1],
        "above_zero": sum(1 for v in ordered if v > 0),
        "below_minus_010": sum(1 for v in ordered if v < -0.10),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Ile sygnalow replayu odrzuca produkcyjna bramka ryzyka")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    result = measure(args.symbols, args.days)
    t = result["totals"]
    print(f"[overlay] sygnalow {t['signals']} | przepuszczonych {t['accepted']}"
          f" | ODRZUCONYCH {t['rejected']} ({t['rejected_pct']}%)"
          f" | config {result['config'].get('hash')}")
    print("[overlay] powody odrzucenia (DOLNE ograniczenie - stan konta zawsze poblazliwy):")
    for reason, count in result["reasons"].items():
        print(f"    {count:>6}  {reason}")
    dist = result.get("net_r") or {}
    if dist:
        print(f"[overlay] expected_net_r wg BRAMKI: min {dist['min']}"
              f" | p10 {dist['p10']} | mediana {dist['median']}"
              f" | p90 {dist['p90']} | max {dist['max']}")
        print(f"[overlay] dodatnich {dist['above_zero']}/{dist['n']}"
              f" | ponizej -0.10: {dist['below_minus_010']}")
    one = result.get("one_owner_check") or {}
    if one.get("n"):
        rozjazd = one["engine_yes_gate_no"] + one["gate_yes_engine_no"]
        print(f"[overlay] kontrola jednego wlasciciela na {one['n']} sygnalach:"
              f" rozjazdow {rozjazd}"
              f" (silnik {one['engine_median']} vs bramka {one['gate_median']})")
        if rozjazd:
            print("[overlay] UWAGA: silnik i bramka licza ROZNE expected_net_r")
    for symbol, row in sorted(result["per_symbol"].items()):
        print(f"    {symbol:<10} sygnalow {row['signals']:>5}"
              f" przepuszczonych {row['accepted']:>5}")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        print(f"[overlay] zapisano: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
