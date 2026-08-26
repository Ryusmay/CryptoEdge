import json, time, math, statistics
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter, defaultdict
from daytrading_backtester import (
    production_signal_provider_v2, htf_bias_provider_v2,
    htf_trail_anchor_provider_v2, portfolio_replay_v2,
)

CACHE = Path("data/replay")
SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "ZEC"]
SKIP = 1500
bundles = {}
for s in SYMBOLS:
    bundles[s] = json.loads((CACHE / f"{s}_90d.json").read_text())["bundle"]
n = min(len(b["5m"]["opens"]) for b in bundles.values())
print("n", n, flush=True)


def cache_bias(raw, ts5):
    memo = {}

    def fn(i):
        if i >= len(ts5):
            return None
        key = int(ts5[i]) // 14_400_000
        if key not in memo:
            memo[key] = raw(i)
        return memo[key]

    return fn


def cache_trail(raw, ts5):
    memo = {}

    def fn(i, direction):
        if i >= len(ts5):
            return None
        key = (int(ts5[i]) // 3_600_000, direction)
        if key not in memo:
            memo[key] = raw(i, direction)
        return memo[key]

    return fn


n_calls = [0]
symbols_data = {}
for symbol, bundle in bundles.items():
    signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)
    ts5 = bundle["5m"]["timestamps"]

    def gated(index, _raw=signal_at_raw, _ts=ts5, _s=SKIP, _e=n, _n=n_calls):
        if not (_s <= index < _e):
            return None
        if int(_ts[index]) % 900_000 != 0:
            return None
        _n[0] += 1
        if _n[0] % 2000 == 0:
            print(f"  eval={_n[0]} bar={index}/{_e}", flush=True)
        return _raw(index)

    symbols_data[symbol] = {
        "ohlcv_5m": bundle["5m"], "signal_at": gated,
        "htf_bias_at": cache_bias(htf_bias_provider_v2(symbol, bundle), ts5),
        "htf_trail_anchor_at": cache_trail(htf_trail_anchor_provider_v2(symbol, bundle), ts5),
        "notify_exit": engine.notify_exit,
    }

t0 = time.time()
result = portfolio_replay_v2(
    symbols_data, max_positions=10,
    fee_frac_round_trip=0.0012, slippage_frac_round_trip=0.0006,
)
print(f"DONE {time.time()-t0:.1f}s evals={n_calls[0]} n={result['count']} net={result['net_r']:+.2f}", flush=True)

pairs = result.get("trades_with_symbol") or []
ts5 = bundles["BTC"]["5m"]["timestamps"]
split_i = SKIP + int((n - SKIP) * 0.70)


def pct(xs, q):
    s = sorted(xs)
    if not s:
        return None
    i = (len(s) - 1) * q
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (i - lo)


rows = []
for sym, t in pairs:
    rows.append({
        "symbol": sym, "direction": t.direction, "entry": t.entry,
        "exit_reason": t.exit_reason, "realised_r": float(t.realised_r),
        "tp1_done": bool(t.tp1_done), "tp2_done": bool(t.tp2_done),
        "remaining": float(t.remaining),
        "entry_i": t.entry_i, "exit_i": t.exit_i,
        "split": "IS" if t.entry_i < split_i else "OOS",
    })


def summarize(label, subset):
    rs = [r["realised_r"] for r in subset]
    if not rs:
        print(f"\n{label}: n=0")
        return
    ntr = len(rs)
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    mean = sum(rs) / ntr
    eq = peak = maxdd = 0
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


summarize("ALL 90d 24.05-22.08.2026", rows)
summarize("IS ~63d", [r for r in rows if r["split"] == "IS"])
summarize("OOS ~27d", [r for r in rows if r["split"] == "OOS"])


def bucket(r):
    edges = [(-1.25, "<-1.25"), (-1.05, "-1.25..-1.05"), (-0.4, "-1.05..-0.4"), (0, "-0.4..0"),
             (0.4, "0..0.4"), (1.05, "0.4..1.05"), (1.6, "1.05..1.6"), (2.5, "1.6..2.5"), (9, "2.5+")]
    for e, lab in edges:
        if r < e:
            return lab
    return "2.5+"


print("\nHIST ALL")
c = Counter(bucket(r["realised_r"]) for r in rows)
for k in ["<-1.25", "-1.25..-1.05", "-1.05..-0.4", "-0.4..0", "0..0.4", "0.4..1.05", "1.05..1.6", "1.6..2.5", "2.5+"]:
    v = c.get(k, 0)
    print(f"  {k:16s} {v:3d} {100*v/len(rows):5.1f}%")

payload = {
    "engine": "DAYTRADING_V2 19.25.17",
    "n": len(rows),
    "net_r": sum(r["realised_r"] for r in rows),
    "range": [
        datetime.fromtimestamp(ts5[0] / 1000, tz=timezone.utc).isoformat(),
        datetime.fromtimestamp(ts5[-1] / 1000, tz=timezone.utc).isoformat(),
    ],
    "trades": rows,
}
Path("/tmp/v2_replay_90d.json").write_text(json.dumps(payload, indent=2, default=str))
art = Path("/workspace/artifacts/v2_replay_90d.json")
art.parent.mkdir(parents=True, exist_ok=True)
art.write_text(json.dumps(payload, indent=2, default=str))
print("saved")
