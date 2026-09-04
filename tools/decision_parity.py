"""Bramka parytetu decyzji: replay kontra runtime, na wyjsciu z pozycji.

CO TA BRAMKA PORoWNUJE. Ten sam stan pozycji i ta sama obserwacja podane
obu torom, a potem porownanie STANU PO: stop, flagi partiali, fakt
zamkniecia i powod. Nie porownuje wyniku w R - dwa tory moga dojsc do tej
samej sredniej roznymi decyzjami, a wtedy bramka na wyniku milczy.

DLACZEGO JEST POTRZEBNA. Oba tory wolaja ten sam reducer
`v2_trade_lifecycle.decide_v2_lifecycle`, wiec sama decyzja jest z definicji
identyczna. Rozjezdza sie to, co kazdy tor z ta decyzja ROBI - i to jest
mierzone tutaj. Zmierzone rozjazdy w dniu powstania bramki (v20.71.0):

  1. BE po TP1: reducer zwraca `new_sl = entry`, replay zapisuje entry
     (daytrading_backtester.py:625-626), a runtime nadpisuje go zaraz potem
     w `_update_trailing` na `entry +/- DAYTRADING_BREAK_EVEN_BUFFER_PCT`
     (paper_trader.py:479-486). Przy medianie stopa ~0,65% ceny to ~0,28R.
  2. Kotwica trailingu: replay podaje potwierdzony swing 1h minus bufor ATR
     (daytrading_backtester.py:202-247), runtime chandelier `HH - 1.5*ATR`
     (paper_trader.py:453-462), bo klucza `trailing_anchor_1h` nie produkuje
     w tym repozytorium nikt.

CZEGO NIE OBEJMUJE, zeby nikt nie wzial jej za wieksza niz jest:
  - frakcji zamykanych na TP1/TP2 (runtime liczy je w `PaperTrader.
    partial_close`, ktorego bez instancji tradera nie da sie tu wywolac);
    to pilnuje osobny test frakcji;
  - ziarnistosci danych - bramka podaje OBU torom ten sam ksztalt
    obserwacji (high=low=close), zeby mierzyc POLITYKE, a nie to, ze replay
    widzi caly bar 5m, a runtime pojedynczy tick. Ta roznica jest realna
    i opisana osobno, ale nie jest bledem parytetu kodu;
  - sciezki wejscia - replay jednosymbolowy swiadomie nie ma RiskManagera.

CZTERY LINIE, KTORE TU DUBLUJE. Po stronie runtime bramka sama ustawia
`partial_tp1_done` / `partial_tp2_done` / `sl_price` po decyzji tp1/tp2,
tak jak robi to `paper_trader.py:1344-1361`. To jest jedyny fragment
przepisany zamiast wywolany, i celowo jest to fragment bez zadnej logiki
decyzyjnej. Cala polityka - `decide_v2_exit` i `update_pnl` - jest wolana
z prawdziwego kodu.

    python -X utf8 -u tools/decision_parity.py
    python -X utf8 -u tools/decision_parity.py --sabotaz be
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BAR_S = 300.0


def scenariusze() -> list:
    """Siatka stanow, w ktorych tory moga sie rozjechac.

    Kazdy scenariusz to wejscie plus sciezka cen w krokach bara 5m. Ceny sa
    tak dobrane, zeby po drodze dotknac TP1, potem TP2, a na koncu wrocic
    pod wejscie - czyli przejsc przez BE i przez trailing.
    """
    out = []
    for kierunek in ("LONG", "SHORT"):
        znak = 1.0 if kierunek == "LONG" else -1.0
        entry = 100.0
        ryzyko = 4.0                      # SL 4% ceny, TP1 = 1R, TP2 = 2R
        sl = entry - znak * ryzyko
        tp1 = entry + znak * ryzyko
        tp2 = entry + znak * 2 * ryzyko
        sciezka = [
            entry + znak * 0.5,           # drobny ruch w kierunku
            tp1,                          # dotkniecie TP1 -> BE
            entry + znak * 1.5,           # cofniecie ponad wejscie
            entry - znak * 0.1,           # ZEJSCIE PONIZEJ WEJSCIA: tu BE
        ]                                 # z buforem i bez buforu daja co innego
        out.append({
            "nazwa": f"{kierunek}_do_TP1_i_powrot_pod_wejscie",
            "kierunek": kierunek, "entry": entry, "sl": sl,
            "tp1": tp1, "tp2": tp2, "ryzyko": ryzyko,
            "sciezka": sciezka, "kotwica": None,
        })
        sciezka2 = [
            tp1,                          # TP1
            tp2,                          # TP2 -> wlacza sie trailing
            entry + znak * 2.5,           # cofniecie po TP2
            entry + znak * 1.0,
        ]
        out.append({
            "nazwa": f"{kierunek}_do_TP2_i_trailing",
            "kierunek": kierunek, "entry": entry, "sl": sl,
            "tp1": tp1, "tp2": tp2, "ryzyko": ryzyko,
            "sciezka": sciezka2,
            # Kotwica podana JAWNIE i identyczna dla obu torow. Bramka mierzy,
            # czy tor ja uszanuje, a nie skad ja bierze - skad ja bierze to
            # osobny rozjazd, opisany w naglowku.
            "kotwica": entry + znak * 1.2,
        })
        out.append({
            "nazwa": f"{kierunek}_stop_bez_TP1",
            "kierunek": kierunek, "entry": entry, "sl": sl,
            "tp1": tp1, "tp2": tp2, "ryzyko": ryzyko,
            "sciezka": [entry - znak * 1.0, entry - znak * 3.0, sl],
            "kotwica": None,
        })
    return out


def stan_replay(sc: dict, sabotaz: str = "") -> list:
    """Przebieg po stronie replayu. Wola PRAWDZIWE `_process_trade_bar_v2`."""
    import config
    from daytrading_backtester import ReplayTradeV2, _process_trade_bar_v2

    t = ReplayTradeV2(
        direction=sc["kierunek"], entry_i=0, entry=sc["entry"], sl=sc["sl"],
        tp1=sc["tp1"], tp2=sc["tp2"],
        tp1_frac=float(getattr(config, "DAYTRADING_V2_TP1_FRAC", 0.5)),
        initial_risk=sc["ryzyko"],
    )
    t.highest = sc["entry"]
    t.lowest = sc["entry"]
    slady = []
    for i, cena in enumerate(sc["sciezka"], start=1):
        if sabotaz == "be" and t.tp1_done:
            # SABOTAZ: cofa dokladnie poprawke v20.72.0 - replay wraca do
            # golego `entry` jako stopu BE, tak jak bylo przed przeniesieniem
            # bufora do wspolnego reducera. Jesli bramka tego nie zauwazy,
            # nie zauwazy niczego, bo to jest jedyny rozjazd, jaki mierzy.
            t.sl = sc["entry"]
        zamkniete = _process_trade_bar_v2(
            t, i, float(cena), float(cena), float(cena),
            None, sc["kotwica"], 0.0011, 0.0006, 576, 0.3,
        )
        slady.append({
            "krok": i, "cena": cena, "sl": round(float(t.sl), 6),
            "tp1": bool(t.tp1_done), "tp2": bool(t.tp2_done),
            "zamkniete": bool(zamkniete), "powod": t.exit_reason or "",
        })
        if zamkniete:
            break
    return slady


def stan_live(sc: dict) -> list:
    """Przebieg po stronie runtime. Wola PRAWDZIWE `Position.update_pnl`
    i `Position.decide_v2_exit`; przepisane sa tylko cztery linie zapisu
    stanu po decyzji tp1/tp2 (paper_trader.py:1344-1361)."""
    from paper_trader import Position

    sygnal = {
        "symbol": "TEST", "direction": sc["kierunek"], "price": sc["entry"],
        "strength": 0.8, "engine": "daytrading_v2",
        "sl_price": sc["sl"], "tp1_price": sc["tp1"], "tp2_price": sc["tp2"],
        "tp_plan": {"tp1_r": 1.0, "tp2_r": 2.0, "tp3": "trailing"},
    }
    pos = Position(sygnal, size_usd=1000.0)
    pos.highest_price = sc["entry"]
    pos.lowest_price = sc["entry"]
    sygnal_biezacy = {"trailing_anchor_1h": sc["kotwica"]}

    slady = []
    for i, cena in enumerate(sc["sciezka"], start=1):
        # Wiek pozycji ustawiany wprost, zeby zegar byl ten sam co w replayu
        # (replay liczy (i - entry_i) * 300 s).
        pos.entry_time = datetime.now() - timedelta(seconds=i * BAR_S)
        pos.update_pnl(float(cena))
        d = pos.decide_v2_exit(float(cena), sygnal_biezacy)
        zamkniete, powod = False, ""
        akcja = getattr(d, "action", None) if d is not None else None
        if akcja == "tp1":
            pos.partial_tp1_done = True
            pos.partial_taken = True
            if getattr(d, "new_sl", None) is not None:
                pos.sl_price = float(d.new_sl)
            pos.breakeven_active = True
        elif akcja == "tp2":
            pos.partial_tp2_done = True
            if getattr(d, "new_sl", None) is not None:
                pos.sl_price = float(d.new_sl)
            pos.trailing_active = True
        elif akcja:
            zamkniete, powod = True, str(akcja)
        slady.append({
            "krok": i, "cena": cena, "sl": round(float(pos.sl_price), 6),
            "tp1": bool(getattr(pos, "partial_tp1_done", False)),
            "tp2": bool(getattr(pos, "partial_tp2_done", False)),
            "zamkniete": zamkniete, "powod": powod,
        })
        if zamkniete:
            break
    return slady


POLA = ("sl", "tp1", "tp2", "zamkniete", "powod")


def porownaj(sc: dict, sabotaz: str = "") -> list:
    """Zwraca liste roznic. Pusta lista = parytet."""
    r = stan_replay(sc, sabotaz)
    z = stan_live(sc)
    roznice = []
    if len(r) != len(z):
        roznice.append(
            f"rozna dlugosc przebiegu: replay {len(r)} krokow, runtime {len(z)}"
            f" (jeden tor zamknal pozycje wczesniej)")
    for kr, kz in zip(r, z):
        for pole in POLA:
            a, b = kr[pole], kz[pole]
            if pole == "sl" and isinstance(a, float) and isinstance(b, float):
                if abs(a - b) <= 1e-9:
                    continue
            elif a == b:
                continue
            roznice.append(
                f"krok {kr['krok']} (cena {kr['cena']}): {pole} "
                f"replay={a!r} runtime={b!r}")
    return roznice


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sabotaz", default="",
                    help="'be' = wstrzyknij bufor BE do replayu; bramka MUSI paść")
    args = ap.parse_args()

    wszystkie = 0
    zle = 0
    for sc in scenariusze():
        roznice = porownaj(sc, args.sabotaz)
        wszystkie += 1
        stan = "PARYTET" if not roznice else "ROZJAZD"
        print(f"[{stan}] {sc['nazwa']}")
        for r in roznice:
            print(f"    {r}")
        if roznice:
            zle += 1

    print()
    print(f"scenariuszy {wszystkie}, z rozjazdem {zle}")
    if args.sabotaz:
        if zle == 0:
            print("BRAMKA BEZWARTOSCIOWA: sabotaz przeszedl niezauwazony")
            return 1
        print("BRAMKA WIARYGODNA: sabotaz wykryty")
        return 0
    return 0 if zle == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
