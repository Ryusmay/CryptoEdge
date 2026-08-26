import json, time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

from daytrading_backtester import (
    production_signal_provider_v2, htf_bias_provider_v2,
    htf_trail_anchor_provider_v2, replay_daytrading_v2,
)

SKIP = 1500
SYMBOLS = ["ETH", "SOL", "XRP", "ZEC"]
all_rows = []
summary = []

for symbol in SYMBOLS:
    payload = json.loads(Path(f"data/replay/{symbol}_90d.json").read_text())
    bundle = payload["bundle"]
    ohlcv = bundle["5m"]
    n = len(ohlcv["opens"])
    ts5 = ohlcv["timestamps"]
    print(f"\n=== {symbol} bars={n} ===", flush=True)
    signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)
    n_calls = [0]

    def gated(index, _raw=signal_at_raw, _ts=ts5, _n=n, _c=n_calls):
        if not (SKIP <= index < _n):
            return None
        if int(_ts[index]) % 900_000 != 0:
            return None
        _c[0] += 1
        return _raw(index)

    bias_raw = htf_bias_provider_v2(symbol, bundle)
    trail_raw = htf_trail_anchor_provider_v2(symbol, bundle)
    bm, tm = {}, {}

    def htf_bias_at(i, _ts=ts5, _raw=bias_raw, _m=bm):
        key = int(_ts[i]) // 14_400_000
        if key not in _m:
            _m[key] = _raw(i)
        return _m[key]

    def htf_trail_at(i, direction, _ts=ts5, _raw=trail_raw, _m=tm):
        key = (int(_ts[i]) // 3_600_000, direction)
        if key not in _m:
            _m[key] = _raw(i, direction)
        return _m[key]

    t0 = time.time()
    result = replay_daytrading_v2(
        ohlcv, gated, htf_bias_at=htf_bias_at, htf_trail_anchor_at=htf_trail_at,
        notify_exit=engine.notify_exit, fee_frac_round_trip=0.0012,
        slippage_frac_round_trip=0.0006,
    )
    split_i = SKIP + int((n - SKIP) * 0.70)
    rows = []
    for t in result["trades"]:
        rows.append({
            "symbol": symbol, "direction": t.direction,
            "realised_r": float(t.realised_r), "exit_reason": t.exit_reason,
            "tp1_done": bool(t.tp1_done), "tp2_done": bool(t.tp2_done),
            "entry_i": t.entry_i, "exit_i": t.exit_i,
            "split": "IS" if t.entry_i < split_i else "OOS",
        })
    all_rows.extend(rows)
    print(f"{symbol} {time.time()-t0:.1f}s evals={n_calls[0]} n={result['count']} net={result['net_r']:+.2f} wr={result['win_rate']*100:.1f}%", flush=True)
    summary.append({"symbol": symbol, "n": result["count"], "net": result["net_r"], "wr": result["win_rate"], "seconds": time.time()-t0, "evals": n_calls[0]})

# merge BTC if present
btc_path = Path("/tmp/v2_replay_90d_btc.json")
if btc_path.exists():
    btc = json.loads(btc_path.read_text())
    for r in btc.get("trades") or []:
        r = dict(r)
        r.setdefault("symbol", "BTC")
        all_rows.append(r)
    print("merged BTC", len(btc.get("trades") or []))

all_rows.sort(key=lambda r: (r.get("entry_i") or 0, r.get("symbol") or ""))


def summarize(label, subset):
    rs = [r["realised_r"] for r in subset]
    if not rs:
        print(f"{label}: n=0")
        return
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    eq = peak = dd = 0.0
    for x in rs:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    pf = (sum(wins) / abs(sum(losses))) if losses else None
    print(f"\n=== {label} ===")
    print(f"n={len(rs)} WR={len(wins)/len(rs)*100:.1f}% net={sum(rs):+.2f} mean={sum(rs)/len(rs):+.3f} PF={None if pf is None else round(pf,2)} DD={dd:.2f}")
    byk = defaultdict(list)
    for t in subset:
        k = "TP2" if t.get("tp2_done") else ("TP1" if t.get("tp1_done") else "noTP_" + str(t.get("exit_reason")))
        byk[k].append(t["realised_r"])
    for k, v in sorted(byk.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:20s} n={len(v):3d} ({100*len(v)/len(rs):4.1f}%) sum={sum(v):+8.2f} mean={sum(v)/len(v):+6.2f}")
    bys = defaultdict(list)
    for t in subset:
        bys[t.get("symbol") or "?"].append(t["realised_r"])
    for s, v in sorted(bys.items()):
        w = sum(1 for x in v if x > 0)
        print(f"  {s:4s} n={len(v):3d} WR={w/len(v)*100:5.1f}% sum={sum(v):+7.2f}")
    for d in ("LONG", "SHORT"):
        v = [t["realised_r"] for t in subset if t.get("direction") == d]
        if v:
            w = sum(1 for x in v if x > 0)
            print(f"  {d:5s} n={len(v):3d} WR={w/len(v)*100:.1f}% sum={sum(v):+.2f}")


print("\nper symbol:", json.dumps(summary, indent=2))
summarize("ALL 90d", all_rows)
summarize("IS", [r for r in all_rows if r.get("split") == "IS"])
summarize("OOS", [r for r in all_rows if r.get("split") == "OOS"])
Path("/tmp/v2_replay_90d.json").write_text(json.dumps({"trades": all_rows, "summary": summary}, indent=2))
try:
    Path("/workspace/artifacts/v2_replay_90d.json").write_text(json.dumps({"trades": all_rows, "summary": summary}, indent=2))
except Exception as e:
    print("art", e)
print("saved")
