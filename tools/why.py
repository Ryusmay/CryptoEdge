"""Czemu nie weszlismy w X o 14:32.

To jest najczestsze pytanie do tego bota i do tej pory odpowiadalo sie na nie
czytaniem 80 tysiecy linii konsoli. Telemetria decyzji ma komplet danych,
brakowalo tylko sposobu, zeby o nie zapytac.

    python tools/why.py ETH                # ostatnie decyzje dla ETH
    python tools/why.py ETH --at 14:32     # co bylo wiadomo o tej godzinie
    python tools/why.py --summary          # lejek odrzucen z calego pliku
    python tools/why.py --id 3f2a...       # pelen slad jednej decyzji

UWAGA na dedupe: powtorzone odrzucenia z tym samym powodem sa scalane
(DECISION_TELEMETRY_DEDUPE_SECONDS, domyslnie 300 s). Przy --at narzedzie
mowi wprost, ile wiersz ma lat wzgledem pytanej godziny, zeby nie brac
scalonego wpisu za brak decyzji.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_LOG = ROOT / "logs" / "decision_telemetry.jsonl"


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Brak pliku telemetrii: {path}")
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # uszkodzona linia nie moze przewrocic calego zapytania
    return rows


def clock(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%d.%m %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "—"


def parse_at(value: str) -> float:
    """Przyjmuje HH:MM, HH:MM:SS albo pelna date. Bez daty = dzis."""
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m %H:%M:%S", "%d.%m %H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.year == 1900:
                parsed = parsed.replace(year=datetime.now().year)
            return parsed.timestamp()
        except ValueError:
            pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            today = datetime.now()
            return today.replace(hour=parsed.hour, minute=parsed.minute,
                                 second=parsed.second, microsecond=0).timestamp()
        except ValueError:
            pass
    raise SystemExit(f"Nie rozumiem godziny: {value!r} (uzyj HH:MM albo 'YYYY-MM-DD HH:MM')")


def describe(row: dict) -> str:
    verdict = str(row.get("decision") or "?")
    mark = {"ACCEPT": "WESZLO", "REJECT": "ODRZUT"}.get(verdict, verdict)
    parts = [f"{clock(row.get('ts'))}  {mark:7s} {row.get('symbol') or '—':<10s}"]
    direction = row.get("direction")
    if direction and direction != "NEUTRAL":
        parts.append(str(direction))
    reason = row.get("reason")
    if reason:
        parts.append(f"-> {reason}")
    extra = []
    for label, key in (("str", "strength"), ("netR", "expected_net_r"),
                       ("regime", "regime"), ("engine", "engine")):
        value = row.get(key)
        if value is None or value == "":
            continue
        extra.append(f"{label}={value:.2f}" if isinstance(value, float) else f"{label}={value}")
    line = " ".join(parts)
    return f"{line}\n         {' | '.join(extra)}" if extra else line


def show_symbol(rows: list[dict], symbol: str, at: float | None, limit: int) -> int:
    symbol = symbol.upper()
    hits = [r for r in rows if str(r.get("symbol") or "").upper() == symbol]
    if not hits:
        print(f"Brak jakiejkolwiek decyzji dla {symbol} w telemetrii.")
        print("Jesli spodziewales sie wpisu: symbol mogl nie przejsc przez lejek")
        print("wcale (np. odpadl juz na poziomie uniwersum) albo telemetria byla")
        print("wylaczona (DECISION_TELEMETRY_ENABLED).")
        return 1
    if at is None:
        print(f"=== {symbol}: {min(limit, len(hits))} ostatnich decyzji z {len(hits)} ===")
        for row in hits[-limit:]:
            print(describe(row))
        return 0

    before = [r for r in hits if float(r.get("ts") or 0) <= at]
    after = [r for r in hits if float(r.get("ts") or 0) > at]
    print(f"=== {symbol} o {datetime.fromtimestamp(at).strftime('%d.%m %H:%M:%S')} ===")
    if not before:
        first = float(after[0].get("ts") or 0) if after else 0
        print("Brak decyzji przed ta godzina.")
        if first:
            print(f"Pierwsza decyzja dla {symbol} jest dopiero o {clock(first)}.")
        return 0
    last = before[-1]
    age = at - float(last.get("ts") or 0)
    print(describe(last))
    print()
    if age > 1.0:
        print(f"To wiersz sprzed {int(age)} s wzgledem pytanej godziny.")
        print("Powtorzone odrzucenia z tym samym powodem sa scalane (dedupe),")
        print("wiec brak wpisu dokladnie o 14:32 nie znaczy, ze bot nie patrzyl -")
        print("znaczy, ze odpowiedz sie nie zmienila.")
    else:
        print("Wiersz z dokladnie tej chwili.")
    if after:
        print(f"\nNastepna decyzja: {describe(after[0])}")
    return 0


def show_summary(rows: list[dict], hours: float | None, top: int) -> int:
    if hours:
        cutoff = (datetime.now() - timedelta(hours=hours)).timestamp()
        rows = [r for r in rows if float(r.get("ts") or 0) >= cutoff]
        window = f"ostatnie {hours:g} h"
    else:
        window = "caly plik"
    decisions = [r for r in rows if r.get("event") == "DECISION"]
    if not decisions:
        print(f"Brak decyzji w oknie ({window}).")
        return 1
    accepted = [r for r in decisions if str(r.get("decision")).upper() == "ACCEPT"]
    rejected = [r for r in decisions if str(r.get("decision")).upper() == "REJECT"]
    print(f"=== Lejek decyzji ({window}) ===")
    print(f"decyzji {len(decisions)} | wejsc {len(accepted)} | odrzucen {len(rejected)}")
    if rejected:
        print("\nPowody odrzucen:")
        for reason, count in Counter(str(r.get("reason") or "?") for r in rejected).most_common(top):
            share = 100.0 * count / len(rejected)
            print(f"  {count:5d}  {share:5.1f}%  {reason}")
        print("\nNajczesciej odrzucane pary:")
        for symbol, count in Counter(str(r.get("symbol") or "?") for r in rejected).most_common(min(top, 10)):
            print(f"  {count:5d}  {symbol}")
    outcomes = [r for r in rows if r.get("event") == "OUTCOME"]
    if outcomes:
        pnl = sum(float(r.get("pnl_usd") or 0) for r in outcomes)
        wins = sum(1 for r in outcomes if float(r.get("pnl_usd") or 0) > 0)
        print(f"\nZamkniete: {len(outcomes)} | wygrane {wins} | PnL {pnl:+.4f}")
    print("\nPowtorzone odrzucenia sa scalane (dedupe), wiec liczby pokazuja")
    print("rozne decyzje, nie liczbe ocen kazdej swiecy.")
    return 0


def show_lineage(rows: list[dict], decision_id: str) -> int:
    hits = [r for r in rows if str(r.get("decision_id") or "").startswith(decision_id)]
    if not hits:
        print(f"Brak sladu dla decision_id zaczynajacego sie od {decision_id!r}.")
        return 1
    full = str(hits[0].get("decision_id") or "")
    print(f"=== Slad decyzji {full} ===")
    for row in hits:
        print(f"\n[{row.get('event')}] {clock(row.get('ts'))}")
        for key, value in row.items():
            if key in ("event", "ts") or value is None or value == "":
                continue
            print(f"  {key}: {value}")
    snapshot_id = hits[0].get("snapshot_id")
    if not snapshot_id:
        print("\nBrak snapshot_id - wiersz pochodzi sprzed wprowadzenia lineage (v20.16.0).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Czemu nie weszlismy w dany symbol.",
        epilog="Przyklad: python tools/why.py ETH --at 14:32",
    )
    ap.add_argument("symbol", nargs="?", help="para, np. ETH")
    ap.add_argument("--at", help="godzina HH:MM lub 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--summary", action="store_true", help="lejek odrzucen zamiast pojedynczej pary")
    ap.add_argument("--id", dest="decision_id", help="pelen slad jednej decyzji (prefiks wystarczy)")
    ap.add_argument("--hours", type=float, default=None, help="ogranicz --summary do ostatnich N godzin")
    ap.add_argument("--limit", type=int, default=15, help="ile ostatnich decyzji pokazac (domyslnie %(default)s)")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = ap.parse_args(argv)

    rows = load_rows(args.log)
    rows.sort(key=lambda r: float(r.get("ts") or 0))
    if not rows:
        print(f"Plik telemetrii jest pusty: {args.log}")
        return 1

    if args.decision_id:
        return show_lineage(rows, args.decision_id)
    if args.summary or not args.symbol:
        return show_summary(rows, args.hours, args.limit)
    return show_symbol(rows, args.symbol, parse_at(args.at) if args.at else None, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
