"""Bramka parytetu: ten sam replay, ta sama lista transakcji i odrzucen.

Kazdy etap migracji do modulow przechodzi przez to jedno wywolanie:

    python tools/parity.py                   # porownaj z baseline
    python tools/parity.py --write-baseline  # zapisz nowy baseline

Wynik jest jednoznaczny: exit 0 = identycznie, exit 1 = roznica z lista
konkretnych pozycji. Refaktor, ktory nie zmienia strategii, MUSI dawac 0.

Dane wejsciowe sa zamrozone (data/replay/*_{DAYS}d.json), wiec jedyna
zmienna jest kod. Zmiana progu w config.py przesuwa config_hash i widac ja
od razu jako zmiane konfiguracji, a nie jako tajemnicza roznice w wynikach.

Porownywane sa transakcje ORAZ lejek odrzucen. Sam net_r nie wystarcza:
refaktor moze przypadkiem przesunac powod odrzucenia bez zmiany wyniku
koncowego i taka zmiana tez ma byc widoczna.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402

CACHE = ROOT / "data" / "replay"
DEFAULT_BASELINE = ROOT / "tests" / "baselines" / "parity_v2.json"
DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "ZEC"]
# 30d, bo te bundle sa juz w repo - baseline i jego dane wejsciowe sa
# wersjonowane razem, wiec bramka jest odtwarzalna na czystym klonie.
# Pelny przebieg do wiekszych kamieni milowych: --days 90 (~15 min).
DEFAULT_DAYS = 30
# Te same wartosci co w _run_v2_90d.py - parytet ma odtwarzac realny przebieg,
# nie wlasny wariant.
SKIP_BARS = 1500
MAX_POSITIONS = 10
FEE_RT = 0.0012
SLIPPAGE_RT = 0.0006
IS_FRACTION = 0.70


# Progi, ktore czyta sie wprost w diffie. Reszta configu jest pokryta hashem.
WATCHED_CONFIG = [
    "MIN_SIGNAL_STRENGTH", "MAX_POSITIONS", "MAX_SAME_DIRECTION_PCT",
    "TAKER_FEE", "MAKER_FEE", "LEVERAGE",
    "DAYTRADING_MIN_EXPECTED_NET_R", "DAYTRADING_PRIOR_ONLY_MIN_EXPECTED_NET_R",
    "DAYTRADING_QUALITY_MIN", "DAYTRADING_MIN_GATE_VOTES",
    "DAYTRADING_SIZE_R_TARGET", "DAYTRADING_TIME_STOP_MIN_R",
    "DAYTRADING_HARD_TIME_STOP_HOURS", "DAYTRADING_V2_HARD_TIME_STOP_HOURS",
    "DAYTRADING_INVALIDATION_BARS", "DAYTRADING_NET_R_MIN_SAMPLE",
    "REVERSAL_MIN_STRENGTH", "REVERSAL_PRIOR_ONLY_MIN_EXPECTED_NET_R",
    "USE_EXPECTED_NET_R_FILTER",
]


def _scalar(value) -> bool:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(isinstance(x, (str, int, float, bool)) or x is None for x in value)
    return False


def config_fingerprint() -> dict:
    """Hash calego configu + czytelna lista progow.

    Hash lapie KAZDA zmiane ustawien, takze taka, o ktorej nikt nie pomyslal.
    Lista WATCHED sluzy tylko do tego, zeby typowa zmiana byla czytelna bez
    kopania w hashu.
    """
    items = []
    for name in sorted(dir(config)):
        if not name.isupper():
            continue
        value = getattr(config, name, None)
        if not _scalar(value):
            continue
        if isinstance(value, (set, frozenset)):
            value = sorted(value)
        items.append(f"{name}={value!r}")
    digest = hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()
    watched = {}
    for name in WATCHED_CONFIG:
        if hasattr(config, name):
            watched[name] = getattr(config, name)
    return {"hash": digest[:16], "keys": len(items), "watched": watched}


def _cache_bias(raw, ts5):
    memo = {}

    def fn(i):
        if i >= len(ts5):
            return None
        key = int(ts5[i]) // 14_400_000
        if key not in memo:
            memo[key] = raw(i)
        return memo[key]

    return fn


def _cache_trail(raw, ts5):
    memo = {}

    def fn(i, direction):
        if i >= len(ts5):
            return None
        key = (int(ts5[i]) // 3_600_000, direction)
        if key not in memo:
            memo[key] = raw(i, direction)
        return memo[key]

    return fn


def load_bundles(symbols, days: int) -> dict:
    bundles = {}
    missing = []
    for symbol in symbols:
        path = CACHE / f"{symbol}_{days}d.json"
        if not path.exists():
            missing.append(path.name)
            continue
        bundles[symbol] = json.loads(path.read_text(encoding="utf-8"))["bundle"]
    if missing:
        raise SystemExit(
            "Brak zamrozonych danych replay: " + ", ".join(missing) +
            f"\nUzupelnij {CACHE} albo podaj --symbols/--days pasujace do tego, co jest."
        )
    return bundles


def run_parity(symbols, days: int) -> dict:
    from daytrading_backtester import (
        production_signal_provider_v2, htf_bias_provider_v2,
        htf_trail_anchor_provider_v2, portfolio_replay_v2,
    )

    bundles = load_bundles(symbols, days)
    bar_count = min(len(b["5m"]["opens"]) for b in bundles.values())
    symbols_data = {}
    for symbol, bundle in bundles.items():
        signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)
        ts5 = bundle["5m"]["timestamps"]

        def gated(index, _raw=signal_at_raw, _ts=ts5, _end=bar_count):
            if not (SKIP_BARS <= index < _end):
                return None
            if int(_ts[index]) % 900_000 != 0:
                return None
            return _raw(index)

        symbols_data[symbol] = {
            "ohlcv_5m": bundle["5m"],
            "signal_at": gated,
            "htf_bias_at": _cache_bias(htf_bias_provider_v2(symbol, bundle), ts5),
            "htf_trail_anchor_at": _cache_trail(htf_trail_anchor_provider_v2(symbol, bundle), ts5),
            "notify_exit": getattr(engine, "notify_exit", None),
        }

    started = time.time()
    result = portfolio_replay_v2(
        symbols_data, max_positions=MAX_POSITIONS,
        fee_frac_round_trip=FEE_RT, slippage_frac_round_trip=SLIPPAGE_RT,
    )
    elapsed = time.time() - started

    split_i = SKIP_BARS + int((bar_count - SKIP_BARS) * IS_FRACTION)
    trades = []
    for symbol, trade in (result.get("trades_with_symbol") or []):
        trades.append({
            "symbol": symbol,
            "direction": getattr(trade, "direction", None),
            "entry_i": getattr(trade, "entry_i", None),
            "exit_i": getattr(trade, "exit_i", None),
            "exit_reason": getattr(trade, "exit_reason", None),
            "realised_r": round(float(getattr(trade, "realised_r", 0) or 0), 6),
            "tp1_done": bool(getattr(trade, "tp1_done", False)),
            "tp2_done": bool(getattr(trade, "tp2_done", False)),
            "fill_kind": getattr(trade, "fill_kind", None),
            "split": "IS" if (getattr(trade, "entry_i", 0) or 0) < split_i else "OOS",
        })
    # Kolejnosc z replayu jest deterministyczna, ale sortujemy jawnie, zeby
    # diff nie zalezal od niej nawet gdyby sie kiedys zmienila.
    trades.sort(key=lambda row: (row["entry_i"] or 0, row["symbol"]))
    net_r = round(sum(row["realised_r"] for row in trades), 6)
    return {
        "meta": {
            "symbols": list(symbols), "days": days, "bars": bar_count,
            "skip_bars": SKIP_BARS, "split_i": split_i,
            "max_positions": MAX_POSITIONS, "fee_rt": FEE_RT,
            "slippage_rt": SLIPPAGE_RT, "elapsed_s": round(elapsed, 1),
        },
        "config": config_fingerprint(),
        "totals": {
            "trades": len(trades),
            "net_r": net_r,
            "is_trades": sum(1 for r in trades if r["split"] == "IS"),
            "oos_trades": sum(1 for r in trades if r["split"] == "OOS"),
            "oos_net_r": round(sum(r["realised_r"] for r in trades if r["split"] == "OOS"), 6),
        },
        "funnel": {
            "rejected_for_slots": result.get("rejected_for_slots", 0),
            "rejected_for_direction": result.get("rejected_for_direction", 0),
            "reasons": dict(sorted((result.get("rejected_funnel") or {}).items())),
        },
        "trades": trades,
    }


def _diff_trades(old: list, new: list) -> list:
    """Roznice per transakcja. Klucz to (entry_i, symbol) - to samo wejscie
    porownujemy z tym samym wejsciem, zeby przesuniecie jednej transakcji nie
    rozjechalo calej listy."""
    def key(row):
        return (row.get("entry_i"), row.get("symbol"))

    old_map = {key(r): r for r in old}
    new_map = {key(r): r for r in new}
    out = []
    for k in sorted(set(old_map) | set(new_map), key=lambda t: (t[0] or 0, t[1] or "")):
        before, after = old_map.get(k), new_map.get(k)
        label = f"{k[1]} @bar {k[0]}"
        if before is None:
            out.append(f"  + NOWA   {label} {after['direction']} r={after['realised_r']:+.4f} ({after['exit_reason']})")
            continue
        if after is None:
            out.append(f"  - ZNIKLA {label} {before['direction']} r={before['realised_r']:+.4f} ({before['exit_reason']})")
            continue
        changed = [f for f in before if before.get(f) != after.get(f)]
        if changed:
            details = ", ".join(f"{f}: {before.get(f)!r} -> {after.get(f)!r}" for f in changed)
            out.append(f"  ~ ZMIANA {label} {details}")
    return out


def compare(baseline: dict, current: dict) -> list:
    problems = []
    old_cfg = (baseline.get("config") or {}).get("hash")
    new_cfg = (current.get("config") or {}).get("hash")
    if old_cfg != new_cfg:
        problems.append(f"KONFIGURACJA: hash {old_cfg} -> {new_cfg}")
        old_w = (baseline.get("config") or {}).get("watched") or {}
        new_w = (current.get("config") or {}).get("watched") or {}
        for name in sorted(set(old_w) | set(new_w)):
            if old_w.get(name) != new_w.get(name):
                problems.append(f"  {name}: {old_w.get(name)!r} -> {new_w.get(name)!r}")
    old_meta = baseline.get("meta") or {}
    new_meta = current.get("meta") or {}
    for field in ("symbols", "days", "bars", "skip_bars", "max_positions", "fee_rt", "slippage_rt"):
        if old_meta.get(field) != new_meta.get(field):
            problems.append(f"ZAKRES: {field} {old_meta.get(field)!r} -> {new_meta.get(field)!r}")
    old_tot = baseline.get("totals") or {}
    new_tot = current.get("totals") or {}
    for field in sorted(set(old_tot) | set(new_tot)):
        if old_tot.get(field) != new_tot.get(field):
            problems.append(f"WYNIK: {field} {old_tot.get(field)!r} -> {new_tot.get(field)!r}")
    old_fun = baseline.get("funnel") or {}
    new_fun = current.get("funnel") or {}
    if old_fun != new_fun:
        problems.append("LEJEK ODRZUCEN:")
        old_r = old_fun.get("reasons") or {}
        new_r = new_fun.get("reasons") or {}
        for field in ("rejected_for_slots", "rejected_for_direction"):
            if old_fun.get(field) != new_fun.get(field):
                problems.append(f"  {field}: {old_fun.get(field)} -> {new_fun.get(field)}")
        for name in sorted(set(old_r) | set(new_r)):
            if old_r.get(name) != new_r.get(name):
                problems.append(f"  {name}: {old_r.get(name, 0)} -> {new_r.get(name, 0)}")
    trade_diff = _diff_trades(baseline.get("trades") or [], current.get("trades") or [])
    if trade_diff:
        problems.append(f"TRANSAKCJE ({len(trade_diff)} roznic):")
        problems.extend(trade_diff[:40])
        if len(trade_diff) > 40:
            problems.append(f"  ... i {len(trade_diff) - 40} wiecej")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bramka parytetu replay V2.")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                    help="lista po przecinku (domyslnie: %(default)s)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--write-baseline", action="store_true",
                    help="zapisz biezacy wynik jako nowy punkt odniesienia")
    ap.add_argument("--json", type=Path, default=None,
                    help="dodatkowo zapisz surowy wynik do tego pliku")
    args = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    print(f"[parity] {len(symbols)} symboli x {args.days}d: {', '.join(symbols)}", flush=True)
    current = run_parity(symbols, args.days)
    meta, totals = current["meta"], current["totals"]
    print(f"[parity] {meta['elapsed_s']}s | transakcji {totals['trades']} "
          f"(IS {totals['is_trades']} / OOS {totals['oos_trades']}) | "
          f"net {totals['net_r']:+.4f}R (OOS {totals['oos_net_r']:+.4f}R) | "
          f"config {current['config']['hash']}", flush=True)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        # elapsed_s to czas wykonania, nie wlasciwosc wyniku - nie moze
        # powodowac roznicy przy nastepnym porownaniu.
        stored = json.loads(json.dumps(current))
        stored["meta"].pop("elapsed_s", None)
        args.baseline.write_text(json.dumps(stored, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[parity] zapisano baseline: {args.baseline}")
        return 0

    if not args.baseline.exists():
        print(f"[parity] BRAK BASELINE: {args.baseline}")
        print("[parity] uruchom najpierw: python tools/parity.py --write-baseline")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    comparable = json.loads(json.dumps(current))
    comparable["meta"].pop("elapsed_s", None)
    problems = compare(baseline, comparable)
    if not problems:
        print("[parity] IDENTYCZNIE — strategia nie zmieniła zachowania.")
        return 0
    print(f"[parity] RÓŻNI SIĘ ({len(problems)} pozycji):")
    for line in problems:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
