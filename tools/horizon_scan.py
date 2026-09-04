"""Gdzie chodzi cena w oknie 10h, 24h i 48h od TYCH SAMYCH punktow wejscia.

Po co: propozycja kalibracji (TP1 1.0R, TP2 1.618R, soft 24h, hard 48h)
zaklada, ze przy dluzszym horyzoncie te poziomy staja sie osiagalne.
W oknie 10h nie sa - zmierzone P(MFE>=1.0R) = 10.7%, P(MFE>=1.618R) = 2.7%.

Ten skrypt liczy to WPROST na bundlach, bez przepuszczania strategii.
Nie zmienia zadnego parametru. Nic nie zapisuje.

Dwa warianty, bo mowia co innego:
  SUROWY   - dokad cena doszla, ignorujac stopa (gdzie MOZNA bylo trafic)
  ZE STOPEM- jesli SL zostalby trafiony pierwszy, transakcja konczy sie tam
             (co realnie da sie zlapac)

UWAGA: nie uwzglednia fundingu. Przy 48h to okolo szesciu settlementow
zamiast jednego, a naliczanie fundingu jest obecnie zepsute (0 z 560
niezerowych), wiec wyniki dla 48h sa OPTYMISTYCZNE.
"""
from __future__ import annotations

import json
import math
import pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "replay"
HORIZONS = (("10h", 120), ("24h", 288), ("48h", 576))


def load_trades():
    out = []
    p = ROOT / "docs" / "analysis" / "outcomes.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if (isinstance(r, dict) and r.get("entry_utc") and not r.get("marker")
                and r.get("entry") and r.get("sl")):
            out.append(r)
    return out


def iso_ms(s):
    s = s.replace("Z", "+00:00")
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def q(v, p):
    if not v:
        return float("nan")
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))]


def main():
    trades = load_trades()
    by_sym = {}
    for t in trades:
        by_sym.setdefault(t["symbol"], []).append(t)

    # wyniki: horyzont -> lista slownikow
    res = {name: [] for name, _ in HORIZONS}
    dropped = {name: 0 for name, _ in HORIZONS}

    for sym, ts_list in sorted(by_sym.items()):
        path = CACHE / f"{sym}_90d.json"
        if not path.exists():
            continue
        tf = json.loads(path.read_text(encoding="utf-8"))["bundle"]["5m"]
        stamps = [int(x) for x in tf["timestamps"]]
        highs = [float(x) for x in tf["highs"]]
        lows = [float(x) for x in tf["lows"]]
        closes = [float(x) for x in tf["closes"]]
        idx = {t: i for i, t in enumerate(stamps)}
        n = len(stamps)

        for t in ts_list:
            i0 = idx.get(iso_ms(t["entry_utc"]))
            if i0 is None:
                continue
            entry = float(t["entry"])
            sl = float(t["sl"])
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            long = (t.get("direction") == "LONG")
            cost = 0.0
            if t.get("fee_rt") is not None and t.get("sl_dist"):
                cost = (float(t["fee_rt"]) + float(t.get("slip_rt") or 0.0)) \
                    / float(t["sl_dist"])

            for name, bars in HORIZONS:
                end = i0 + bars
                if end >= n:                      # okno ucieta przez koniec danych
                    dropped[name] += 1
                    continue
                mfe = mae = 0.0
                stopped_at = None
                mfe_s = mae_s = 0.0
                for j in range(i0 + 1, end + 1):
                    hi, lo = highs[j], lows[j]
                    up = (hi - entry) / risk if long else (entry - lo) / risk
                    dn = (entry - lo) / risk if long else (hi - entry) / risk
                    mfe = max(mfe, up)
                    mae = max(mae, dn)
                    if stopped_at is None:
                        mfe_s = max(mfe_s, up)
                        mae_s = max(mae_s, dn)
                        if dn >= 1.0:
                            stopped_at = j
                mark = ((closes[end] - entry) if long else (entry - closes[end])) / risk
                r_stop = -1.0 if stopped_at is not None else mark
                res[name].append({
                    "mfe": mfe, "mae": mae, "mfe_s": mfe_s,
                    "mark": mark, "r_stop": r_stop, "cost": cost,
                    "stopped": stopped_at is not None,
                })

    print("=" * 74)
    print("ROZKLAD RUCHU CENY OD TYCH SAMYCH WEJSC, TRZY HORYZONTY")
    print("=" * 74)
    print("bez fundingu (przy 48h ~6 settlementow, naliczanie obecnie zepsute)\n")

    for name, bars in HORIZONS:
        v = res[name]
        if not v:
            continue
        mfe = [x["mfe"] for x in v]
        mfe_s = [x["mfe_s"] for x in v]
        print("--- {} ({} barow) | n={} | pominietych przez koniec danych: {} ---"
              .format(name, bars, len(v), dropped[name]))
        print("  MFE surowy   mediana {:.3f}R   q75 {:.3f}   q95 {:.3f}".format(
            q(mfe, .5), q(mfe, .75), q(mfe, .95)))
        print("  MFE ze stopem mediana {:.3f}R  q75 {:.3f}   q95 {:.3f}".format(
            q(mfe_s, .5), q(mfe_s, .75), q(mfe_s, .95)))
        print("  osiagalnosc TP (ze stopem):")
        for x in (0.30, 0.50, 0.75, 1.00, 1.618, 2.00):
            k = sum(1 for y in mfe_s if y >= x)
            print("     {:>5.3f}R : {:>5.1f}%".format(x, 100.0 * k / len(v)))
        st = sum(1 for x in v if x["stopped"])
        net = [x["r_stop"] - x["cost"] for x in v]
        print("  stopniete: {:.1f}%   E[R netto trzymania do konca]: {:+.4f}  "
              "(mediana {:+.4f})".format(
                  100.0 * st / len(v), math.fsum(net) / len(net), q(net, .5)))
        print()

    print("=" * 74)
    print("CZYTANIE: 'MFE ze stopem' to jedyna uczciwa miara osiagalnosci TP -")
    print("uwzglednia, ze czesc transakcji wyleci na stopie zanim dojdzie wyzej.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
