# -*- coding: utf-8 -*-
"""Buduje plik mikrostruktury, ktory czyta silnik, z surowych pomiarow BloFina.

    python tools/build_blofin_microstructure.py            # zapisz
    python tools/build_blofin_microstructure.py --check    # porownaj bajt w bajt

DLACZEGO TO NARZEDZIE ISTNIEJE. Plik, ktory czytal silnik
(`venue_microstructure_20260903.json`), nie mial producenta w repozytorium -
powstal narzedziem, ktorego tu nie ma. Ten plik ma: powstaje z plikow w
`data/`, deterministycznie, i `--check` mowi, czy to, co jest w repo, jest
tym, co to narzedzie by dzis wyprodukowalo.

WEJSCIE. Tylko dwie pelne doby BloFina (`doba_pelna: true`, ksiega size=100,
24/24 migawki, pelna krzywa notionali 50..25000 USD). Pozostale pliki BloFina
w `data/` sa pominiete swiadomie: trzy to okna 3-5 minut, a doba z 09-09
liczyla ksiege size=20, ktora moze ucinac przejscie wiekszego zlecenia,
i nie ma zapisanej flagi kompletnosci.

AGREGACJA, per symbol obecny w obu dobach:
- `rt_bps_by_notional[n]` = srednia z dwoch dob z `rt_bps_avg` przy notionale n.
  To jest koszt round-trip PRZECHODZONY PRZEZ REALNA KSIEGE, usredniony po
  migawkach - wlasciwa wielkosc dla poslizgu zlecenia tej wielkosci.
- `spread_bps_avg` = srednia z dwoch dob.
- `top1_depth_usd` = minimum z dwoch dob, tak jak w plikach zrodlowych
  (minimum z migawek). To jest przypadek NAJGORSZY, nie typowy - trzymane
  informacyjnie, silnik nie uzywa go do decyzji, gdy jest tabela kosztu.

Srednia, a nie maksimum, bo dwie doby roznia sie realnie (BTC przy 75 USD:
0.3543 bps w dobie 09-13, 0.0408 w dobie 09-15) i zadna z nich nie jest
"prawdziwa" - obie sa probka. Dwie probki to malo; `n_days` jest zapisane
przy kazdym symbolu, zeby nikt nie wzial tego za rozklad.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
INPUTS = [
    "venue_microstructure_20260913_doba1_blofin.json",
    "venue_microstructure_20260915_doba2_blofin.json",
]
OUTPUT = DATA / "venue_microstructure_blofin.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mean(xs):
    xs = [float(x) for x in xs if x is not None]
    return round(sum(xs) / len(xs), 6) if xs else None


def build() -> dict:
    docs = []
    for name in INPUTS:
        path = DATA / name
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("venue") != "blofin" or doc.get("doba_pelna") is not True:
            raise SystemExit(f"{name}: to nie jest pelna doba BloFina - odmawiam")
        docs.append((name, path, doc))

    common = set(docs[0][2]["instruments"])
    for _, _, doc in docs[1:]:
        common &= set(doc["instruments"])

    instruments = {}
    for sym in sorted(common):
        rows = [doc["instruments"][sym] for _, _, doc in docs]
        sizes = set()
        for r in rows:
            sizes |= set((r.get("koszt_przejscia_wg_notionalu") or {}).keys())
        rt = {}
        for n in sorted(sizes, key=float):
            vals = [((r.get("koszt_przejscia_wg_notionalu") or {}).get(n) or {}).get("rt_bps_avg")
                    for r in rows]
            if all(v is not None for v in vals):
                rt[str(int(float(n)))] = _mean(vals)
        asks = [((r.get("top1_depth_usd") or {}).get("ask")) for r in rows]
        bids = [((r.get("top1_depth_usd") or {}).get("bid")) for r in rows]
        instruments[sym] = {
            "spread_bps_avg": _mean(r.get("spread_bps_avg") for r in rows),
            "rt_bps_by_notional": rt,
            "top1_depth_usd": {
                "ask": min(a for a in asks if a is not None) if any(a is not None for a in asks) else None,
                "bid": min(b for b in bids if b is not None) if any(b is not None for b in bids) else None,
            },
            "n_days": len(rows),
        }

    return {
        "as_of": " + ".join(doc.get("as_of", "") for _, _, doc in docs),
        "source": "tools/build_blofin_microstructure.py - srednia z dwoch pelnych dob "
                  "BloFin REST /market/books size=100, 24 migawki co 1 h",
        "venue": "blofin",
        "venue_note": "sama gielda, na ktorej bot handluje - nie proxy. "
                      "Dwie doby to dwie probki, nie rozklad.",
        "inputs": [{"file": name, "sha256": _sha256(path)} for name, path, _ in docs],
        "method": {
            "rt_bps_by_notional": "srednia rt_bps_avg z dob, przy tym samym notionale",
            "spread_bps_avg": "srednia z dob",
            "top1_depth_usd": "minimum z dob (najgorszy przypadek, informacyjnie)",
        },
        "instruments": instruments,
    }


def render(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="nie zapisuj; exit 1, jesli plik w repo rozni sie od wyniku")
    args = ap.parse_args(argv)
    text = render(build())
    if args.check:
        same = OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == text
        print(f"[blofin] {OUTPUT.name}: {'IDENTYCZNIE' if same else 'ROZNI SIE'}")
        return 0 if same else 1
    OUTPUT.write_text(text, encoding="utf-8")
    n = len(json.loads(text)["instruments"])
    print(f"[blofin] zapisano {OUTPUT.name}: {n} symboli")
    return 0


if __name__ == "__main__":
    sys.exit(main())
