"""Co da sie PRZEWIDZIEC w momencie wejscia - material na nowy _gross_expected_r.

Model trojstanowy odtwarzajacy zmierzone czestosci jest tautologia: powod
wyjscia znany jest dopiero PO fakcie. Model musi liczyc oczekiwane R z cech
znanych PRZY WEJSCIU. Ten skrypt sprawdza, ktore z dostepnych cech w ogole
cokolwiek separuja - i czy separacja utrzymuje sie poza probka.

Cechy dostepne w zbiorze: symbol, direction, profile, regime, sl_dist, czas.
Nie ma strength ani scoringu - to ograniczenie zbioru, nie wybor.

Podzial IS/OOS po CZASIE (nie losowo), bo dane sa szeregiem czasowym.

Nic nie zapisuje.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs" / "analysis" / "outcomes.jsonl"

STATES = ("hard_time_stop", "time_stop", "sl")


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
        if isinstance(r, dict) and "realised_r" in r and not r.get("marker"):
            rows.append(r)
    rows.sort(key=lambda r: r.get("entry_utc") or "")
    return rows


def wilson(k, n, z=1.96):
    """Przedzial Wilsona - uczciwy przy malym n, w przeciwienstwie do k/n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def mean(v):
    return math.fsum(v) / len(v) if v else 0.0


def stderr(v):
    if len(v) < 2:
        return float("nan")
    m = mean(v)
    var = math.fsum((x - m) ** 2 for x in v) / (len(v) - 1)
    return math.sqrt(var / len(v))


def describe(rows, label, base=None):
    """n, srednia R, blad standardowy, czestosci stanow."""
    rs = [float(r["realised_r"]) for r in rows]
    m = mean(rs)
    se = stderr(rs)
    cnt = {s: sum(1 for r in rows if r.get("exit_reason") == s) for s in STATES}
    tag = ""
    if base is not None and len(rs) >= 2 and not math.isnan(se) and se > 0:
        t = (m - base) / se
        if abs(t) >= 2.0:
            tag = "  <-- ODSTAJE (|t|={:.1f})".format(abs(t))
    print("  {:<22} n={:<5} sr={:>8.4f} +-{:>6.4f}  hts={:>4.0f}% ts={:>4.0f}% sl={:>4.0f}%{}"
          .format(label, len(rs), m, se if not math.isnan(se) else 0.0,
                  100.0 * cnt["hard_time_stop"] / len(rs),
                  100.0 * cnt["time_stop"] / len(rs),
                  100.0 * cnt["sl"] / len(rs), tag))
    return m


def group_report(rows, key_fn, title, base, min_n=15):
    print("\n--- {} ---".format(title))
    g = defaultdict(list)
    for r in rows:
        g[key_fn(r)].append(r)
    for k in sorted(g, key=lambda k: -mean([float(x["realised_r"]) for x in g[k]])):
        if len(g[k]) < min_n:
            continue
        describe(g[k], str(k), base)
    small = [k for k in g if len(g[k]) < min_n]
    if small:
        print("  (pominieto {} grup ponizej n={})".format(len(small), min_n))


def sl_bucket(r):
    d = r.get("sl_dist")
    if d is None:
        return "?"
    d = float(d)
    if d < 0.020:
        return "sl_dist <2.0%"
    if d < 0.030:
        return "sl_dist 2.0-3.0%"
    if d < 0.045:
        return "sl_dist 3.0-4.5%"
    return "sl_dist >=4.5%"


def dur_bucket(r):
    b = r.get("duration_bars_5m")
    if b is None:
        return "?"
    b = int(b)
    if b <= 12:
        return "trzymanie <=1h"
    if b <= 48:
        return "trzymanie 1-4h"
    if b <= 144:
        return "trzymanie 4-12h"
    return "trzymanie >12h"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=pathlib.Path, default=DEFAULT)
    ap.add_argument("--oos", type=float, default=0.30)
    a = ap.parse_args()
    rows = load(a.file)
    n = len(rows)
    if n < 50:
        print("za malo danych:", n)
        return 2

    cut = int(n * (1.0 - a.oos))
    is_rows, oos_rows = rows[:cut], rows[cut:]

    print("=" * 78)
    print("MATERIAL NA _gross_expected_r")
    print("=" * 78)
    print("wszystkich {}  |  IS {} ({}..{})  |  OOS {} ({}..{})".format(
        n, len(is_rows), (is_rows[0].get("entry_utc") or "?")[:10],
        (is_rows[-1].get("entry_utc") or "?")[:10],
        len(oos_rows), (oos_rows[0].get("entry_utc") or "?")[:10],
        (oos_rows[-1].get("entry_utc") or "?")[:10]))

    print("\n--- STOPA BAZOWA (model bez zadnych cech) ---")
    base_all = describe(rows, "wszystko")
    base_is = describe(is_rows, "IS")
    base_oos = describe(oos_rows, "OOS")
    print("\n  Stabilnosc IS->OOS: {:+.4f} -> {:+.4f}  (roznica {:+.4f})".format(
        base_is, base_oos, base_oos - base_is))

    print("\n--- PRAWDOPODOBIENSTWA STANOW z przedzialem Wilsona (cale dane) ---")
    for s in STATES:
        k = sum(1 for r in rows if r.get("exit_reason") == s)
        lo, hi = wilson(k, n)
        rs = [float(r["realised_r"]) for r in rows if r.get("exit_reason") == s]
        print("  {:<16} p={:.3f} [{:.3f}, {:.3f}]   E[R|stan]={:+.4f}".format(
            s, k / n, lo, hi, mean(rs)))
    print("\n  Model trojstanowy odtwarza srednia z definicji:")
    tot = math.fsum(
        (sum(1 for r in rows if r.get("exit_reason") == s) / n) *
        mean([float(r["realised_r"]) for r in rows if r.get("exit_reason") == s])
        for s in STATES)
    print("    suma p*E = {:+.4f}   zmierzona srednia = {:+.4f}".format(tot, base_all))

    # --- cechy znane przy wejsciu ---
    for key, title in (
        (lambda r: r.get("profile"), "WG PROFILU V2 (znany przy wejsciu)"),
        (lambda r: r.get("regime"), "WG REZIMU RYNKU (znany przy wejsciu)"),
        (lambda r: r.get("direction"), "WG KIERUNKU (znany przy wejsciu)"),
        (sl_bucket, "WG SL_DIST (znany przy wejsciu)"),
        (lambda r: r.get("symbol"), "WG SYMBOLU (znany przy wejsciu)"),
    ):
        group_report(rows, key, title, base_all)

    print("\n--- WG CZASU TRZYMANIA (NIE znany przy wejsciu - tylko diagnostyka) ---")
    group_report(rows, dur_bucket, "czas trzymania", base_all)

    print("\n" + "=" * 78)
    print("UWAGA METODOLOGICZNA")
    print("=" * 78)
    print("Zbior zawiera WYLACZNIE transakcje, ktore obecna bramka przepuscila.")
    print("To probka ocenzurowana: nie da sie z niej oszacowac rozkladu setupow,")
    print("ktore bramka odrzucila. Kazdy model dopasowany tutaj przewiduje")
    print("warunkowo - 'skoro bramka to przepuscila, to ile z tego bedzie'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
