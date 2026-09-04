"""Kontrola POPRAWNOSCI wskaznikow - nie stabilnosci.

Dziewiec bramek charakterystyki dowodzi, ze wskazniki sie NIE ZMIENILY.
Zadna nie dowodzi, ze sa POPRAWNE. To narzedzie liczy te same wielkosci
druga, niezalezna droga (pandas ewm / niezalezne petle wyprowadzone
z definicji Wildera) i pokazuje rozjazd.

Nic nie zapisuje. Nie jest bramka - jest pomiarem.

Uruchomienie:
    python -X utf8 -u tools/indicator_truth.py --symbol BTC --days 30
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import indicators_full as ind

CACHE = ROOT / "data" / "replay"


# ---------------------------------------------------------------- referencje
# Wyprowadzone z definicji, nie przepisane z indicators_full.py.

def ref_true_range(h, l, c):
    """TR_i = max(H-L, |H-Cprev|, |L-Cprev|), i od 1."""
    return [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
            for i in range(1, len(c))]


def ref_wilder_rma(values, period):
    """RMA Wildera: zasiew srednia arytmetyczna, potem alpha = 1/period.

    Zwraca liste tej samej dlugosci co values, z None przed zasiewem.
    """
    if len(values) < period:
        return [None] * len(values)
    out = [None] * (period - 1)
    seed = math.fsum(values[:period]) / period   # fsum: inna kolejnosc niz sum()
    out.append(seed)
    prev = seed
    for v in values[period:]:
        prev = prev + (v - prev) / period        # postac rownowazna, inny zapis
        out.append(prev)
    return out


def ref_atr(h, l, c, period=14):
    tr = ref_true_range(h, l, c)
    rma = ref_wilder_rma(tr, period)
    return [None] + rma                          # wyrownanie do indeksu swiecy


def ref_rsi(c, period=14):
    ch = [c[i] - c[i - 1] for i in range(1, len(c))]
    gains = [v if v > 0 else 0.0 for v in ch]
    losses = [-v if v < 0 else 0.0 for v in ch]
    g = ref_wilder_rma(gains, period)
    d = ref_wilder_rma(losses, period)
    out = [None]
    for i in range(len(ch)):
        if g[i] is None or d[i] is None:
            out.append(None)
        elif d[i] == 0:
            out.append(100.0 if g[i] > 0 else 50.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + g[i] / d[i]))
    return out


def ref_ema(values, period):
    if len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    prev = math.fsum(values[:period]) / period
    out = [prev]
    for v in values[period:]:
        prev = prev + k * (v - prev)             # rownowazne v*k + prev*(1-k)
        out.append(prev)
    return out


def ref_adx(h, l, c, period=14):
    n = len(c)
    tr, pdm, mdm = [], [], []
    for i in range(1, n):
        up = h[i] - h[i - 1]
        dn = l[i - 1] - l[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    atr = ref_wilder_rma(tr, period)
    pd_ = ref_wilder_rma(pdm, period)
    md_ = ref_wilder_rma(mdm, period)
    dx = []
    for i in range(len(tr)):
        if atr[i] is None or atr[i] == 0:
            continue
        pdi = 100.0 * pd_[i] / atr[i]
        mdi = 100.0 * md_[i] / atr[i]
        s = pdi + mdi
        dx.append(100.0 * abs(pdi - mdi) / s if s else 0.0)
    if len(dx) < period:
        return None
    adx = math.fsum(dx[:period]) / period
    for v in dx[period:]:
        adx = adx + (v - adx) / period
    return adx


def ref_bollinger(c, period=20, mult=2.0):
    w = c[-period:]
    mid = math.fsum(w) / period
    var = math.fsum((x - mid) ** 2 for x in w) / period   # populacyjne
    sd = math.sqrt(var)
    return mid, mid + mult * sd, mid - mult * sd


# ------------------------------------------------------------------ porownanie

def compare(name, ours, theirs, tol_abs=1e-9, scale=None):
    pairs = [(a, b) for a, b in zip(ours, theirs)
             if a is not None and b is not None]
    if not pairs:
        return name, 0, None, None, "BRAK WSPOLNYCH PUNKTOW"
    worst_abs = 0.0
    worst_rel = 0.0
    for a, b in pairs:
        d = abs(a - b)
        worst_abs = max(worst_abs, d)
        ref = abs(b) if scale is None else scale
        if ref > 0:
            worst_rel = max(worst_rel, d / ref)
    verdict = "ZGODNE" if worst_abs <= tol_abs else (
        "drobny rozjazd" if worst_rel < 1e-6 else "ROZJAZD")
    return name, len(pairs), worst_abs, worst_rel, verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--tf", default="5m", help="5m|15m|1h|4h|1d")
    a = ap.parse_args()

    path = CACHE / f"{a.symbol}_{a.days}d.json"
    if not path.exists():
        print(f"brak {path}")
        return 2
    raw = json.loads(path.read_text(encoding="utf-8"))
    tf = (raw.get("bundle") or {}).get(a.tf)
    if not tf:
        print(f"brak interwalu {a.tf}; dostepne:",
              list((raw.get('bundle') or {})))
        return 2
    h = [float(x) for x in tf["highs"]]
    l = [float(x) for x in tf["lows"]]
    c = [float(x) for x in tf["closes"]]

    print(f"symbol {a.symbol}  interwal {a.tf}  barow {len(c)}  zakres ceny "
          f"{min(c):.6g} .. {max(c):.6g}")
    px = math.fsum(c) / len(c)
    print()

    rows = []
    rows.append(compare("ATR(14)", ind._atr_series(h, l, c, 14),
                        ref_atr(h, l, c, 14), tol_abs=px * 1e-12, scale=px))
    rows.append(compare("RSI(14)", ind._rsi_series(c, 14),
                        ref_rsi(c, 14), tol_abs=1e-9, scale=100.0))

    ours_ema = ind._ema(c, 21)
    ref_e = ref_ema(c, 21)
    rows.append(compare("EMA(21)", ours_ema, ref_e,
                        tol_abs=px * 1e-12, scale=px))

    ours_adx = ind._adx(h, l, c, 14)
    their_adx = ref_adx(h, l, c, 14)
    if ours_adx is not None and their_adx is not None:
        rows.append(compare("ADX(14) ostatni", [ours_adx], [their_adx],
                            tol_abs=1e-9, scale=100.0))

    bb = ind._bollinger(c, 20, 2.0)
    if bb:
        m, u, lo = ref_bollinger(c, 20, 2.0)
        rows.append(compare("BB mid/up/low", [bb["mid"], bb["upper"], bb["lower"]],
                            [m, u, lo], tol_abs=px * 1e-12, scale=px))

    w = max(len(r[0]) for r in rows)
    print(f"{'wskaznik'.ljust(w)}  {'punktow':>8}  {'max |bezwzgl|':>14}  "
          f"{'max wzgl':>10}  werdykt")
    print("-" * (w + 52))
    for name, n, wa, wr, verdict in rows:
        wa_s = "-" if wa is None else f"{wa:.3e}"
        wr_s = "-" if wr is None else f"{wr:.2e}"
        print(f"{name.ljust(w)}  {n:>8}  {wa_s:>14}  {wr_s:>10}  {verdict}")

    print()
    print("Uwaga: RSI w indicators_full jest round(...,2) w srodku, wiec")
    print("rozjazd do 5e-3 na skali 0-100 jest ZAMIERZONY, nie bledem.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
