# ============================================================
# CryptoEdge – analiza logów CSV
# Użycie:
#   python analyze_logs.py
#   python analyze_logs.py --days 1
#   python analyze_logs.py --symbol EDGE
# ============================================================

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

BASE = Path(__file__).resolve().parent
LOGS = BASE / "logs"
BOT_LOG = LOGS / "bot_log.csv"
SIGNALS_LOG = LOGS / "signals_history.csv"


def parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except Exception:
        return None


def load_csv(path: Path) -> List[Dict]:
    if not path.exists():
        print(f"[!] Brak pliku: {path}")
        return []
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def fnum(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def filter_days(rows: List[Dict], days: Optional[float], ts_key="timestamp") -> List[Dict]:
    if not days:
        return rows
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for r in rows:
        dt = parse_ts(r.get(ts_key, ""))
        if dt is None or dt >= cutoff:
            out.append(r)
    return out


def analyze_trades(rows: List[Dict], symbol: Optional[str] = None) -> None:
    opens = [r for r in rows if (r.get("event") or "").upper() == "OPEN"]
    closes = [r for r in rows if (r.get("event") or "").upper() in ("CLOSE", "CLOSED", "STOP_LOSS", "TRAILING_STOP", "MARGIN_CALL", "TAKE_PROFIT")]
    # unify: any event that is not OPEN and has pnl
    if not closes:
        closes = [
            r for r in rows
            if (r.get("event") or "").upper() != "OPEN"
            and (r.get("pnl") not in (None, "", "0", "0.0", "0.0000") or r.get("pnl_pct") not in (None, "", "0", "0.00"))
        ]
        # still filter close-like
        closes = [r for r in rows if (r.get("event") or "").upper() not in ("OPEN", "")]

    # Prefer explicit CLOSE-type from paper_trader log_event
    close_events = {
        "CLOSE", "CLOSED", "STOP_LOSS", "TRAILING_STOP", "MARGIN_CALL",
        "TAKE_PROFIT", "PARTIAL_TP", "STALE_NO_MOVE", "OPPOSITE_SIGNAL",
        "SUPERTREND_FLIP", "HTF_OPPOSITE", "DRAWDOWN", "DAILY_LIMIT", "MANUAL"
    }
    closes = [r for r in rows if (r.get("event") or "").upper() in close_events
              or (r.get("event") or "").upper().startswith("CLOSE")]

    if symbol:
        sym = symbol.upper()
        opens = [r for r in opens if (r.get("symbol") or "").upper() == sym]
        closes = [r for r in closes if (r.get("symbol") or "").upper() == sym]

    print("=" * 60)
    print("  WYNIKI TRANSAKCJI (bot_log.csv)")
    print("=" * 60)
    print(f"Otwarcia: {len(opens)}  |  Zamknięcia: {len(closes)}")

    if not closes:
        print("Brak zamkniętych pozycji w logu (albo event nie jest logowany jako CLOSE).")
        if opens:
            print("\nOstatnie OPEN:")
            for r in opens[-8:]:
                print(f"  {r.get('timestamp')} {r.get('direction')} {r.get('symbol')} "
                      f"@{r.get('price')} size={r.get('size')} str={r.get('strength')}")
        return

    pnls = [fnum(r.get("pnl")) for r in closes]
    pcts = [fnum(r.get("pnl_pct")) for r in closes]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    zeros = [p for p in pnls if p == 0]
    total = sum(pnls)
    avg = total / len(pnls) if pnls else 0
    win_rate = 100.0 * len(wins) / len(pnls) if pnls else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (float("inf") if wins else 0)

    print(f"\nPnL łącznie:     ${total:+.4f}")
    print(f"Win rate:        {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L / {len(zeros)}Z)")
    print(f"Avg trade:       ${avg:+.4f}")
    print(f"Avg win / loss:  ${avg_win:+.4f} / ${avg_loss:+.4f}")
    print(f"Profit factor:   {pf if pf != float('inf') else '∞':.2f}" if pf != float("inf") else "Profit factor:   ∞")
    if pcts:
        print(f"Avg PnL %:       {sum(pcts)/len(pcts):+.2f}%")
        print(f"Best / Worst %:  {max(pcts):+.2f}% / {min(pcts):+.2f}%")

    # by direction
    print("\n── LONG vs SHORT ──")
    for d in ("LONG", "SHORT"):
        subset = [r for r in closes if (r.get("direction") or "").upper() == d]
        if not subset:
            continue
        s = sum(fnum(r.get("pnl")) for r in subset)
        w = sum(1 for r in subset if fnum(r.get("pnl")) > 0)
        print(f"  {d:5} n={len(subset):3}  PnL ${s:+.4f}  WR {100*w/len(subset):.0f}%")

    # by reason / event
    print("\n── Powód zamknięcia (event) ──")
    by_ev = defaultdict(list)
    for r in closes:
        by_ev[(r.get("event") or "?").upper()].append(fnum(r.get("pnl")))
    for ev, vals in sorted(by_ev.items(), key=lambda x: -len(x[1])):
        print(f"  {ev:18} n={len(vals):3}  PnL ${sum(vals):+.4f}")

    # by symbol
    print("\n── Top symbole (PnL) ──")
    by_sym = defaultdict(list)
    for r in closes:
        by_sym[(r.get("symbol") or "?").upper()].append(fnum(r.get("pnl")))
    ranked = sorted(by_sym.items(), key=lambda x: sum(x[1]))
    print("  Najgorsze:")
    for sym, vals in ranked[:8]:
        print(f"    {sym:10} n={len(vals):2}  ${sum(vals):+.4f}")
    print("  Najlepsze:")
    for sym, vals in ranked[-8:][::-1]:
        print(f"    {sym:10} n={len(vals):2}  ${sum(vals):+.4f}")

    print("\n── Ostatnie zamknięcia ──")
    for r in closes[-12:]:
        print(f"  {r.get('timestamp')} {(r.get('event') or ''):14} "
              f"{r.get('direction')} {r.get('symbol'):8} "
              f"PnL ${fnum(r.get('pnl')):+.4f} ({fnum(r.get('pnl_pct')):+.1f}%)")


def analyze_signals(rows: List[Dict], symbol: Optional[str] = None) -> None:
    if symbol:
        rows = [r for r in rows if (r.get("symbol") or "").upper() == symbol.upper()]
    print("\n" + "=" * 60)
    print("  SYGNAŁY (signals_history.csv)")
    print("=" * 60)
    print(f"Wierszy: {len(rows)}")
    if not rows:
        return

    dirs = Counter((r.get("direction") or "?").upper() for r in rows)
    print("Kierunki:", dict(dirs))

    # reason frequency
    reason_c = Counter()
    for r in rows:
        for part in (r.get("reasons") or "").split("|"):
            p = part.strip()
            if not p:
                continue
            # normalize
            key = p.split("(")[0].strip()
            reason_c[key] += 1
    print("\n── Najczęstsze powody (top 15) ──")
    for k, v in reason_c.most_common(15):
        print(f"  {k:28} {v}")

    # reject-like tags
    tags = ("OB_THIN", "PUMP_CHASE", "DUMP_CHASE", "STRAT_PRIMARY_NA", "REGIME_VS",
            "COUNTER_NO_STRAT", "VOL_WEAK", "SRC_DIVERGENCE")
    print("\n── Tagi ryzyka ──")
    for t in tags:
        n = sum(1 for r in rows if t in (r.get("reasons") or ""))
        if n:
            print(f"  {t:28} {n}")

    # top symbols by signal count
    print("\n── Najczęściej sygnalizowane ──")
    sc = Counter((r.get("symbol") or "?").upper() for r in rows)
    for sym, n in sc.most_common(12):
        print(f"  {sym:10} {n}")


def main():
    ap = argparse.ArgumentParser(description="Analiza logów CryptoEdge")
    ap.add_argument("--days", type=float, default=None, help="Tylko ostatnie N dni (np. 1, 0.5)")
    ap.add_argument("--symbol", type=str, default=None, help="Filtr symbolu (np. EDGE)")
    ap.add_argument("--signals-only", action="store_true")
    ap.add_argument("--trades-only", action="store_true")
    args = ap.parse_args()

    trades = filter_days(load_csv(BOT_LOG), args.days)
    signals = filter_days(load_csv(SIGNALS_LOG), args.days)

    if not args.signals_only:
        analyze_trades(trades, args.symbol)
    if not args.trades_only:
        analyze_signals(signals, args.symbol)

    print("\n(Gotowe. Parametry: --days 1  --symbol EDGE  --trades-only  --signals-only)")


if __name__ == "__main__":
    main()
