"""Empiryczne p(TP1) i p(TP2|TP1) z zapisanych raportow replayu.

POWOD. `config.py:805` mowi o sobie wprost: "Jawny, konserwatywny prior do
czasu uzyskania >= EXPECTED_R_MIN_CALIBRATION_OBS". Te dwie liczby -
DAYTRADING_PRIOR_P_TP1 = 0.55 i DAYTRADING_PRIOR_P_TP2_GIVEN_TP1 = 0.45 -
stoja po stronie BRUTTO w calym rachunku expected_net_r. Zmierzone wczesniej:
mediana brutto +0.0476R przeciwko medianie kosztu +0.1432R. Skoro koszt jest
modelem bez kalibracji, a brutto jest priorem bez kalibracji, to zanim ktos
zaplaci realnymi fillami za zmierzenie kosztu, warto zmierzyc te strone, ktora
jest darmowa - dane juz sa na dysku.

CZEGO TO NARZEDZIE NIE ROBI. Nie zapisuje niczego. `tools/calibrate_expectancy.py`
wola `calibrator.record(...)` na PRODUKCYJNYM kalibratorze, wiec samo jego
uruchomienie zmienia stan, ktorego uzylby LIVE. Tutaj czytamy i liczymy.

Nie zgadujemy tez TP z progow R. Tamto narzedzie ma fallback
`realised_r >= 1.0 or mfe_r >= 1.0`, ktory podstawia stale 1.0/1.618 za
faktyczne poziomy planu. Raporty maja jawne pola `tp1` i `tp2` - uzywamy
wylacznie ich, a wiersze bez nich sa liczone osobno jako pominiete.

DEDUPLIKACJA. Raporty pochodza z nakladajacych sie przebiegow (te same dni,
te same symbole, rozne wersje), wiec pulowanie ich wprost liczyloby te sama
transakcje wielokrotnie. Klucz dedupu jest jawny i raportowany.

PRZEDZIAL UFNOSCI. Przy probce rzedu 200 sam punktowy odsetek nic nie
rozstrzyga. Liczony jest przedzial Wilsona 95% - jesli obejmuje prior, to
znaczy, ze te dane NIE odrozniaja rzeczywistosci od zaslepki.

    python tools/tp_rates.py
    python tools/tp_rates.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402

REPORTS = ROOT / "reports" / "replay"

# Klucz dedupu: dwie rozne transakcje o identycznym symbolu, kierunku, profilu,
# wyniku, dlugosci ORAZ obu ekstremach to praktycznie niemozliwe.
DEDUP_FIELDS = ("symbol", "direction", "profile", "realised_r",
                "duration_bars_5m", "mfe_r", "mae_r")


def _key(row: dict) -> tuple:
    out = []
    for f in DEDUP_FIELDS:
        v = row.get(f)
        if isinstance(v, float):
            v = round(v, 9)
        out.append(v)
    return tuple(out)


def wilson(hits: int, n: int, z: float = 1.96) -> tuple:
    """Przedzial Wilsona - poprawny takze przy malym n i p blisko 0/1."""
    if n <= 0:
        return (0.0, 0.0)
    p = hits / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def collect(reports_dir: Path = REPORTS) -> dict:
    rows = []
    seen = set()
    stats = Counter()
    per_file = {}

    if not reports_dir.exists():
        return {"error": f"brak katalogu {reports_dir}"}

    for path in sorted(reports_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            stats["plikow_nieparsowalnych"] += 1
            continue
        stats["plikow"] += 1
        diag = data.get("diagnostics") or {}
        found = []
        for split in ("in_sample", "out_of_sample"):
            for row in (diag.get(split) or {}).get("trade_rows") or []:
                found.append((split, row))
        per_file[path.name] = len(found)
        if not found:
            stats["plikow_bez_transakcji"] += 1
            continue
        for split, row in found:
            stats["wierszy_surowych"] += 1
            # Tylko jawne flagi. Bez fallbacku na progi R - zgadywanie TP
            # z realised_r podstawia stale 1.0/1.618 za faktyczny plan.
            if row.get("tp1") is None or row.get("tp2") is None:
                stats["pominiete_brak_flag"] += 1
                continue
            k = _key(row)
            if k in seen:
                stats["duplikatow_odrzuconych"] += 1
                continue
            seen.add(k)
            rows.append({
                "mfe_r": float(row.get("mfe_r") or 0.0),
                "mae_r": float(row.get("mae_r") or 0.0),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "profile": str(row.get("profile") or "?"),
                "regime": str(row.get("regime") or "UNKNOWN"),
                "exit_reason": str(row.get("exit_reason") or "?"),
                "fill_kind": str(row.get("fill_kind") or "?"),
                "tp1": bool(row.get("tp1")),
                "tp2": bool(row.get("tp2")),
                "realised_r": float(row.get("realised_r") or 0.0),
                "split": split,
            })
    return {"rows": rows, "stats": dict(stats), "per_file": per_file}


def rates(rows: list) -> dict:
    n = len(rows)
    tp1 = sum(1 for r in rows if r["tp1"])
    tp2 = sum(1 for r in rows if r["tp2"])
    # p(TP2|TP1) liczone WYLACZNIE na transakcjach, ktore dotknely TP1.
    tp2_of_tp1 = sum(1 for r in rows if r["tp1"] and r["tp2"])
    out = {
        "n": n,
        "tp1_hits": tp1,
        "tp2_hits": tp2,
        "p_tp1": round(tp1 / n, 4) if n else None,
        "p_tp1_ci95": [round(x, 4) for x in wilson(tp1, n)] if n else None,
        "p_tp2_given_tp1": round(tp2_of_tp1 / tp1, 4) if tp1 else None,
        "p_tp2_given_tp1_ci95": [round(x, 4) for x in wilson(tp2_of_tp1, tp1)] if tp1 else None,
        "tp2_without_tp1": sum(1 for r in rows if r["tp2"] and not r["tp1"]),
    }
    return out


def _group(rows: list, field: str) -> dict:
    buckets = defaultdict(list)
    for r in rows:
        buckets[r.get(field) or "?"].append(r)
    return {k: rates(v) for k, v in sorted(buckets.items())}


def gross_at(p1: float, p2_given: float, f1: float = 0.5,
             r1: float = 1.5, r2: float = 2.2) -> float:
    """Ta sama formula co expected_net_r._gross_expected_r, przy DOMYSLNYM planie.

    Domyslne f1/r1/r2 sa wziete z fallbackow tej funkcji. Realne sygnaly maja
    wlasny plan i mnoznik jakosci, wiec ta liczba sluzy do porownania priora
    z empiria PRZY TYM SAMYM planie - a nie do przewidzenia brutto.
    """
    return -(1.0 - p1) + p1 * (f1 * r1 + (1.0 - f1) * p2_given * r2)


def break_even_p1(median_gross: float, median_cost: float, p2_given: float,
                  f1: float = 0.5, r1: float = 1.5, r2: float = 2.2) -> dict:
    """Ile musialoby wynosic p(TP1), zeby mediana kandydata wyszla na zero.

    Odwracamy gross = -(1-p1) + p1*K wzgledem p1, przy tym samym K. To jest
    PRZYBLIZENIE: uzywa domyslnego planu, a nie planu kazdego sygnalu.
    """
    k = f1 * r1 + (1.0 - f1) * p2_given * r2
    slope = 1.0 + k
    if slope <= 0:
        return {}
    return {
        "k": round(k, 4),
        "p1_implied_by_median_gross": round((1.0 + median_gross) / slope, 4),
        "p1_needed_for_zero_net": round((1.0 + median_cost) / slope, 4),
        "delta": round((median_cost - median_gross) / slope, 4),
    }


def _fmt(r: dict) -> str:
    if not r.get("n"):
        return "brak transakcji"
    ci1 = r.get("p_tp1_ci95") or [0, 0]
    ci2 = r.get("p_tp2_given_tp1_ci95")
    part2 = (f" | p(TP2|TP1) {r['p_tp2_given_tp1']:.3f} [{ci2[0]:.3f}-{ci2[1]:.3f}]"
             if r.get("p_tp2_given_tp1") is not None and ci2 else " | p(TP2|TP1) brak (0 TP1)")
    return (f"n={r['n']:>4} | p(TP1) {r['p_tp1']:.3f} [{ci1[0]:.3f}-{ci1[1]:.3f}]" + part2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Empiryczne p(TP1)/p(TP2|TP1) z raportow replayu. Nic nie zapisuje.")
    ap.add_argument("--reports", type=Path, default=REPORTS)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--median-gross", type=float, default=0.0476,
                    help="mediana gross_r z tools/cost_breakdown.py")
    ap.add_argument("--median-cost", type=float, default=0.1432,
                    help="mediana total_cost_r z tools/cost_breakdown.py")
    args = ap.parse_args(argv)

    got = collect(args.reports)
    if got.get("error"):
        print("[tp]", got["error"])
        return 2
    rows, stats = got["rows"], got["stats"]
    print(f"[tp] plikow {stats.get('plikow', 0)}"
          f" | bez transakcji {stats.get('plikow_bez_transakcji', 0)}"
          f" | wierszy surowych {stats.get('wierszy_surowych', 0)}"
          f" | duplikatow odrzuconych {stats.get('duplikatow_odrzuconych', 0)}"
          f" | bez jawnych flag {stats.get('pominiete_brak_flag', 0)}")
    print(f"[tp] klucz dedupu: {', '.join(DEDUP_FIELDS)}")
    if not rows:
        print("[tp] brak transakcji z jawnymi flagami tp1/tp2 - nie ma czego liczyc.")
        return 1

    glob = rates(rows)
    prior1 = float(getattr(config, "DAYTRADING_PRIOR_P_TP1", 0.55))
    prior2 = float(getattr(config, "DAYTRADING_PRIOR_P_TP2_GIVEN_TP1", 0.45))
    min_obs = int(getattr(config, "EXPECTED_R_MIN_CALIBRATION_OBS", 30))

    print(f"[tp] CALOSC   {_fmt(glob)}")
    print(f"[tp] PRIOR    p(TP1) {prior1:.3f} | p(TP2|TP1) {prior2:.3f}"
          f" | prog kalibracji n>={min_obs}")

    lo1, hi1 = glob["p_tp1_ci95"]
    if lo1 <= prior1 <= hi1:
        print("[tp] PRZEDZIAL OBEJMUJE PRIOR: te dane NIE odrozniaja rzeczywistosci"
              " od zaslepki. Punktowa roznica nic tu nie rozstrzyga.")
    elif hi1 < prior1:
        print("[tp] Prior jest ZA WYSOKI wzgledem tej probki - brutto w rachunku"
              " jest zawyzone.")
    else:
        print("[tp] Prior jest ZA NISKI wzgledem tej probki - brutto w rachunku"
              " jest zanizone.")

    if glob["tp2_without_tp1"]:
        print(f"[tp] UWAGA: {glob['tp2_without_tp1']} transakcji ma TP2 bez TP1"
              " - to lamie zalozenie lancucha w _gross_expected_r.")

    for field, label in (("profile", "profil"), ("symbol", "symbol"),
                         ("split", "split"), ("fill_kind", "fill")):
        print(f"[tp] wg {label}:")
        for key, r in _group(rows, field).items():
            print(f"    {str(key):<10} {_fmt(r)}")

    # KONTROLA KRZYZOWA. p(TP1) = 0.015 przy priorze 0.55 to roznica 36-krotna,
    # wiec zanim uwierzymy fladze `tp1`, sprawdzamy ja INNYM polem: maksymalnym
    # korzystnym wychyleniem. Jesli mfe_r prawie nigdy nie siega poziomu TP1,
    # flaga mowi prawde. Jesli siega czesto, a flaga stoi na False - flaga klamie.
    mfes = sorted(r["mfe_r"] for r in rows)
    n = len(mfes)

    def _share(thr):
        return sum(1 for v in mfes if v >= thr) / n

    print("[tp] kontrola krzyzowa przez mfe_r (maks. korzystne wychylenie):")
    print(f"    mediana mfe_r {mfes[n // 2]:.4f} | p90 {mfes[int(0.9 * (n - 1))]:.4f}"
          f" | max {mfes[-1]:.4f}")
    for thr in (0.5, 1.0, 1.5, 2.2):
        print(f"    udzial mfe_r >= {thr:>4}R : {_share(thr):.3f}")
    print(f"    dla porownania p(TP1) z flagi: {glob['p_tp1']:.3f}")

    # Czy model dwustanowy w ogole opisuje te transakcje? Formula
    # _gross_expected_r zaklada: albo TP1, albo pelna strata -1R. Jesli
    # dominuje wyjscie posrednie (time_stop przy okolicach zera), zalozenie
    # jest zlamane i sam prior nie jest tu glownym problemem.
    reals = sorted(r["realised_r"] for r in rows)
    mean_real = sum(reals) / n
    print("[tp] czy model dwustanowy (TP1 albo -1R) opisuje te dane:")
    print(f"    srednie realised_r {mean_real:+.4f} | mediana {reals[n // 2]:+.4f}")
    print(f"    udzial realised_r <= -0.90R (pelna strata): {sum(1 for v in reals if v <= -0.9) / n:.3f}")
    print(f"    udzial |realised_r| < 0.25R (wyjscie posrednie): "
          f"{sum(1 for v in reals if abs(v) < 0.25) / n:.3f}")
    print(f"    brutto z formuly przy empirycznym p(TP1): "
          f"{gross_at(glob['p_tp1'], glob.get('p_tp2_given_tp1') or 0.0):+.4f}")
    print("    ^ jesli to jest daleko od sredniego realised_r powyzej, to nie prior"
          " jest zepsuty, tylko KSZTALT modelu.")
    print("[tp] wg powodu wyjscia:")
    for reason, r in _group(rows, "exit_reason").items():
        sub = [x["realised_r"] for x in rows if x["exit_reason"] == reason]
        print(f"    {reason:<18} n={len(sub):>4} | srednie R {sum(sub) / len(sub):+.4f}")

    be = break_even_p1(args.median_gross, args.median_cost,
                       glob.get("p_tp2_given_tp1") or prior2)
    if be:
        print("[tp] ile p(TP1) trzeba, zeby mediana kandydata wyszla na zero"
              " (PRZYBLIZENIE, plan domyslny f1=0.5 r1=1.5 r2=2.2):")
        print(f"    p1 wynikajace z mediany brutto {args.median_gross:+.4f}:"
              f" {be['p1_implied_by_median_gross']:.4f}")
        print(f"    p1 potrzebne przy medianie kosztu {args.median_cost:+.4f}:"
              f" {be['p1_needed_for_zero_net']:.4f}")
        print(f"    brakuje: {be['delta']:+.4f} punktu prawdopodobienstwa")
        print(f"    dla porownania gross przy priorze: {gross_at(prior1, prior2):+.4f}"
              f" | przy empirii: {gross_at(glob['p_tp1'], glob.get('p_tp2_given_tp1') or prior2):+.4f}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "stats": stats, "global": glob,
            "prior": {"p_tp1": prior1, "p_tp2_given_tp1": prior2, "min_obs": min_obs},
            "by_profile": _group(rows, "profile"),
            "by_symbol": _group(rows, "symbol"),
            "by_split": _group(rows, "split"),
            "by_fill_kind": _group(rows, "fill_kind"),
            "break_even": be,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[tp] zapisano: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
