import json, math, time, statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict, Counter

SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "ZEC"]
SKIP = 1500
CACHE = Path(__file__).resolve().parent / "data" / "replay"


def replay_one(symbol: str) -> dict:
    from daytrading_backtester import (
        production_signal_provider_v2, htf_bias_provider_v2,
        htf_trail_anchor_provider_v2, replay_daytrading_v2,
    )
    payload = json.loads((CACHE / f"{symbol}_90d.json").read_text())
    bundle = payload["bundle"]
    ohlcv = bundle["5m"]
    n = len(ohlcv.get("opens") or [])
    ts5 = ohlcv["timestamps"]
    signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)
    n_calls = 0

    def gated(index, _raw=signal_at_raw, _ts=ts5, _s=SKIP, _e=n):
        nonlocal n_calls
        if not (_s <= index < _e):
            return None
        if int(_ts[index]) % 900_000 != 0:
            return None
        n_calls += 1
        return _raw(index)

    bias_raw = htf_bias_provider_v2(symbol, bundle)
    trail_raw = htf_trail_anchor_provider_v2(symbol, bundle)
    bias_memo, trail_memo = {}, {}

    def htf_bias_at(i):
        if i >= len(ts5):
            return None
        key = int(ts5[i]) // 14_400_000
        if key not in bias_memo:
            bias_memo[key] = bias_raw(i)
        return bias_memo[key]

    def htf_trail_at(i, direction):
        if i >= len(ts5):
            return None
        key = (int(ts5[i]) // 3_600_000, direction)
        if key not in trail_memo:
            trail_memo[key] = trail_raw(i, direction)
        return trail_memo[key]

    t0 = time.time()
    result = replay_daytrading_v2(
        ohlcv, gated,
        htf_bias_at=htf_bias_at,
        htf_trail_anchor_at=htf_trail_at,
        notify_exit=engine.notify_exit,
        fee_frac_round_trip=0.0012,
        slippage_frac_round_trip=0.0006,
    )
    trades = []
    for t in result.get("trades") or []:
        trades.append({
            "symbol": symbol,
            "direction": t.direction,
            "entry": t.entry,
            "exit_reason": t.exit_reason,
            "realised_r": float(t.realised_r),
            "tp1_done": bool(t.tp1_done),
            "tp2_done": bool(t.tp2_done),
            "entry_i": t.entry_i,
            "exit_i": t.exit_i,
        })
    return {
        "symbol": symbol, "n_bars": n, "evals": n_calls,
        "seconds": time.time() - t0,
        "count": result.get("count"), "net_r": result.get("net_r"),
        "win_rate": result.get("win_rate"), "trades": trades,
        "first_ts": ts5[0], "last_ts": ts5[-1],
    }


def pct(xs, q):
    s = sorted(xs)
    if not s:
        return None
    i = (len(s) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (i - lo)


def summarize(label, subset):
    rs = [r["realised_r"] for r in subset]
    if not rs:
        print(f"\n{label}: n=0")
        return
    ntr = len(rs)
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    mean = sum(rs) / ntr
    eq = peak = maxdd = 0.0
    for x in rs:
        eq += x
        peak = max(peak, eq)
        maxdd = max(maxdd, peak - eq)
    pf = (sum(wins) / abs(sum(losses))) if losses else None
    payoff = abs((sum(wins) / len(wins)) / (sum(losses) / len(losses))) if wins and losses else None
    print(f"\n=== {label} ===")
    print(f"n={ntr} WR={len(wins)/ntr*100:.1f}% net={sum(rs):+.2f}R mean={mean:+.3f} med={pct(rs,0.5):+.3f} PF={None if pf is None else round(pf,2)} maxDD={maxdd:.2f}")
    if payoff:
        print(f"win avg={sum(wins)/len(wins):+.2f} loss avg={sum(losses)/len(losses):+.2f} payoff={payoff:.2f}")
    print(f"p5={pct(rs,0.05):+.2f} p25={pct(rs,0.25):+.2f} p50={pct(rs,0.5):+.2f} p75={pct(rs,0.75):+.2f} p90={pct(rs,0.90):+.2f} max={max(rs):+.2f}")
    byk = defaultdict(list)
    for t in subset:
        kind = "TP2" if t["tp2_done"] else (
            "TP1_BE_sl" if t["tp1_done"] and t["exit_reason"] == "sl" else (
                "TP1_other" if t["tp1_done"] else "noTP_" + t["exit_reason"]))
        byk[kind].append(t["realised_r"])
    print("path:")
    for k, vals in sorted(byk.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:16s} n={len(vals):3d} ({100*len(vals)/ntr:4.1f}%) mean={sum(vals)/len(vals):+6.2f} sum={sum(vals):+8.2f}")
    print("symbol:")
    bys = defaultdict(list)
    for t in subset:
        bys[t["symbol"]].append(t["realised_r"])
    for s in SYMBOLS:
        vals = bys.get(s) or []
        if not vals:
            continue
        w = sum(1 for x in vals if x > 0)
        print(f"  {s:4s} n={len(vals):3d} WR={w/len(vals)*100:5.1f}% mean={sum(vals)/len(vals):+6.2f} sum={sum(vals):+7.2f}")
    print("dir:")
    for ddir in ("LONG", "SHORT"):
        vals = [t["realised_r"] for t in subset if t["direction"] == ddir]
        if not vals:
            continue
        w = sum(1 for x in vals if x > 0)
        print(f"  {ddir:5s} n={len(vals):3d} WR={w/len(vals)*100:5.1f}% mean={sum(vals)/len(vals):+6.2f} sum={sum(vals):+7.2f}")


if __name__ == "__main__":
    t0 = time.time()
    parts = []
    with ProcessPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(replay_one, s): s for s in SYMBOLS}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                part = fut.result()
            except Exception as e:
                print(f"{s} FAIL {type(e).__name__}: {e}", flush=True)
                continue
            parts.append(part)
            print(f"{part['symbol']} {part['seconds']:.1f}s evals={part['evals']} n={part['count']} net={part['net_r']:+.2f}", flush=True)

    rows = []
    for p in parts:
        rows.extend(p["trades"])
    rows.sort(key=lambda r: (r["entry_i"], r["symbol"]))
    n_bars = max(p["n_bars"] for p in parts)
    split_i = SKIP + int((n_bars - SKIP) * 0.70)
    for r in rows:
        r["split"] = "IS" if r["entry_i"] < split_i else "OOS"

    ts0 = min(p["first_ts"] for p in parts)
    ts1 = max(p["last_ts"] for p in parts)
    print(f"\nwall {time.time()-t0:.1f}s  range {datetime.fromtimestamp(ts0/1000, tz=timezone.utc).date()} -> {datetime.fromtimestamp(ts1/1000, tz=timezone.utc).date()}")
    print("NOTE: 5 niezależnych książek (MAX_POSITIONS nie dzieli slotów; na 30d slot reject=0)")

    summarize("ALL 90d", rows)
    summarize("IS ~63d", [r for r in rows if r["split"] == "IS"])
    summarize("OOS ~27d", [r for r in rows if r["split"] == "OOS"])

    def bucket(r):
        edges = [(-1.25, "<-1.25"), (-1.05, "-1.25..-1.05"), (-0.4, "-1.05..-0.4"),
                 (0, "-0.4..0"), (0.4, "0..0.4"), (1.05, "0.4..1.05"),
                 (1.6, "1.05..1.6"), (2.5, "1.6..2.5"), (9, "2.5+")]
        for e, lab in edges:
            if r < e:
                return lab
        return "2.5+"

    print("\nHIST ALL")
    c = Counter(bucket(r["realised_r"]) for r in rows)
    for k in ["<-1.25", "-1.25..-1.05", "-1.05..-0.4", "-0.4..0", "0..0.4", "0.4..1.05", "1.05..1.6", "1.6..2.5", "2.5+"]:
        v = c.get(k, 0)
        print(f"  {k:16s} {v:3d} {100*v/max(len(rows),1):5.1f}%")

    out = {
        "engine": "DAYTRADING_V2 19.25.17",
        "mode": "per_symbol_parallel",
        "n": len(rows),
        "net_r": sum(r["realised_r"] for r in rows),
        "range": [
            datetime.fromtimestamp(ts0 / 1000, tz=timezone.utc).isoformat(),
            datetime.fromtimestamp(ts1 / 1000, tz=timezone.utc).isoformat(),
        ],
        "per_symbol": [{k: p[k] for k in ("symbol", "seconds", "evals", "count", "net_r", "win_rate")} for p in parts],
        "trades": rows,
    }
    Path("/tmp/v2_replay_90d.json").write_text(json.dumps(out, indent=2, default=str))
    art = Path("/workspace/artifacts/v2_replay_90d.json")
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text(json.dumps(out, indent=2, default=str))
    print("saved")
