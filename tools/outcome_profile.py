"""Profil zbioru wynikow - wejscie do przeprojektowania _gross_expected_r.

Model dwustanowy zaklada: TP1 albo -1R. Ten skrypt sprawdza, ile w tym
prawdy, i pokazuje trzeci stan (wyjscie czasowe blisko zera), ktorego
model nie widzi.

Nic nie zapisuje. Pomiar, nie bramka.

    python -X utf8 -u tools/outcome_profile.py
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs" / "analysis" / "outcomes.jsonl"


def load(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("marker"):
            continue
        if isinstance(r, dict) and "realised_r" in r:
            rows.append(r)
    return rows


def q(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[i]


def pct(n, d):
    return 0.0 if not d else 100.0 * n / d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=pathlib.Path, default=DEFAULT)
    a = ap.parse_args()
    rows = load(a.file)
    if not rows:
        print("brak wierszy w", a.file)
        return 2
    n = len(rows)
    print("=" * 72)
    print("ZBIOR: {} transakcji, {} symboli".format(
        n, len({r.get("symbol") for r in rows})))
    print("=" * 72)

    # --- 1. czy nowe pola sa ---
    print("\n--- 1. KOMPLETNOSC NOWYCH POL ---")
    for f in ("limit_price", "exit_price", "fee_rt", "slip_rt", "fill_kind"):
        have = sum(1 for r in rows if r.get(f) is not None)
        print("  {:<12} obecne w {:>5} / {} ({:.1f}%)".format(
            f, have, n, pct(have, n)))

    # --- 2. limit czy market: przeslanka poprawki prowizji v20.60.0 ---
    print("\n--- 2. RODZAJ WEJSCIA (przeslanka poprawki prowizji) ---")
    kinds = Counter(r.get("fill_kind") or "?" for r in rows)
    for k, c in kinds.most_common():
        print("  {:<10} {:>5}  {:.1f}%".format(k, c, pct(c, n)))
    fees = Counter(round(float(r["fee_rt"]), 6)
                   for r in rows if r.get("fee_rt") is not None)
    if fees:
        print("  zaplacone fee_rt:")
        for f, c in sorted(fees.items()):
            print("    {:.6f}  x{:>5}  {:.1f}%".format(f, c, pct(c, n)))

    # --- 3. jakosc fillu: limit_price vs entry ---
    print("\n--- 3. JAKOSC FILLU (limit_price vs entry) ---")
    better = same = worse = 0
    gains = []
    for r in rows:
        lp, e, d = r.get("limit_price"), r.get("entry"), r.get("direction")
        if lp is None or e is None:
            continue
        # LONG: fill ponizej limitu = lepiej. SHORT: powyzej = lepiej.
        adv = (lp - e) if d == "LONG" else (e - lp)
        if adv > 1e-12:
            better += 1
            gains.append(adv / e)
        elif adv < -1e-12:
            worse += 1
        else:
            same += 1
    tot = better + same + worse
    if tot:
        print("  lepiej niz limit : {:>5}  {:.1f}%".format(better, pct(better, tot)))
        print("  dokladnie limit  : {:>5}  {:.1f}%".format(same, pct(same, tot)))
        print("  gorzej niz limit : {:>5}  {:.1f}%".format(worse, pct(worse, tot)))
        if gains:
            print("  mediana poprawy  : {:.4f}% ceny".format(100 * q(gains, 0.5)))
    else:
        print("  brak danych (limit_price puste)")

    # --- 4. rozklad realised_r: serce sprawy ---
    print("\n--- 4. ROZKLAD realised_r ---")
    rs = [float(r["realised_r"]) for r in rows]
    for p in (0.05, 0.25, 0.5, 0.75, 0.95):
        print("  q{:<4} {:>9.4f}".format(int(p * 100), q(rs, p)))
    print("  srednia {:>7.4f}   suma {:>9.4f}".format(
        math.fsum(rs) / n, math.fsum(rs)))

    print("\n  KUBELKI (test zalozenia dwustanowego):")
    buckets = [
        ("pelny stop      r <= -0.90", lambda x: x <= -0.90),
        ("duza strata -0.90..-0.25  ", lambda x: -0.90 < x <= -0.25),
        ("BLISKO ZERA -0.25..+0.25  ", lambda x: -0.25 < x < 0.25),
        ("maly zysk +0.25..+0.90    ", lambda x: 0.25 <= x < 0.90),
        ("duzy zysk   r >= +0.90    ", lambda x: x >= 0.90),
    ]
    for name, f in buckets:
        c = sum(1 for x in rs if f(x))
        print("    {:<28} {:>5}  {:>5.1f}%".format(name, c, pct(c, n)))

    # --- 5. powody wyjscia ---
    print("\n--- 5. POWODY WYJSCIA ---")
    by = defaultdict(list)
    for r in rows:
        by[r.get("exit_reason") or "?"].append(float(r["realised_r"]))
    print("  {:<22} {:>5} {:>7} {:>9} {:>9}".format(
        "powod", "n", "% ", "srednia", "suma"))
    for k, v in sorted(by.items(), key=lambda kv: -math.fsum(kv[1])):
        print("  {:<22} {:>5} {:>6.1f}% {:>9.4f} {:>9.3f}".format(
            k, len(v), pct(len(v), n), math.fsum(v) / len(v), math.fsum(v)))

    # --- 6. TP1/TP2: co model zaklada ---
    print("\n--- 6. TP1 / TP2 (model zaklada p_tp1=0.55) ---")
    tp1 = sum(1 for r in rows if r.get("tp1"))
    tp2 = sum(1 for r in rows if r.get("tp2"))
    print("  dotknelo TP1: {:>5}  {:.1f}%".format(tp1, pct(tp1, n)))
    print("  dotknelo TP2: {:>5}  {:.1f}%".format(tp2, pct(tp2, n)))
    if tp1:
        print("  p(TP2|TP1)  : {:.3f}   (model zaklada 0.45)".format(tp2 / tp1))

    # --- 7. MFE/MAE: jak daleko szly ---
    print("\n--- 7. MFE / MAE ---")
    mfe = [float(r.get("mfe_r") or 0.0) for r in rows]
    mae = [float(r.get("mae_r") or 0.0) for r in rows]
    print("  MFE  q25 {:.3f}  mediana {:.3f}  q75 {:.3f}  q95 {:.3f}".format(
        q(mfe, .25), q(mfe, .5), q(mfe, .75), q(mfe, .95)))
    print("  MAE  q25 {:.3f}  mediana {:.3f}  q75 {:.3f}  q95 {:.3f}".format(
        q(mae, .25), q(mae, .5), q(mae, .75), q(mae, .95)))
    reached1 = sum(1 for x in mfe if x >= 1.0)
    print("  MFE >= 1.0R (dotarlo do 1R w ogole): {:>5}  {:.1f}%".format(
        reached1, pct(reached1, n)))

    # --- 8. per symbol ---
    print("\n--- 8. PER SYMBOL ---")
    sym = defaultdict(list)
    for r in rows:
        sym[r.get("symbol") or "?"].append(float(r["realised_r"]))
    print("  {:<10} {:>5} {:>9} {:>9}".format("symbol", "n", "suma", "srednia"))
    for k, v in sorted(sym.items(), key=lambda kv: -math.fsum(kv[1])):
        print("  {:<10} {:>5} {:>9.3f} {:>9.4f}".format(
            k, len(v), math.fsum(v), math.fsum(v) / len(v)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
