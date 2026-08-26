import json, time, math
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

from daytrading_backtester import (
    production_signal_provider_v2, htf_bias_provider_v2,
    htf_trail_anchor_provider_v2, replay_daytrading_v2,
)

symbol = "BTC"
SKIP = 1500
payload = json.loads(Path("data/replay/BTC_90d.json").read_text())
bundle = payload["bundle"]
ohlcv = bundle["5m"]
n = len(ohlcv["opens"])
ts5 = ohlcv["timestamps"]
print(f"BTC bars={n} {datetime.fromtimestamp(ts5[0]/1000,tz=timezone.utc).date()}->{datetime.fromtimestamp(ts5[-1]/1000,tz=timezone.utc).date()}", flush=True)

signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)
n_calls = [0]

def gated(index):
    if not (SKIP <= index < n):
        return None
    if int(ts5[index]) % 900_000 != 0:
        return None
    n_calls[0] += 1
    if n_calls[0] % 400 == 0:
        print(f"  eval={n_calls[0]} i={index}", flush=True)
    return signal_at_raw(index)

bias_raw = htf_bias_provider_v2(symbol, bundle)
trail_raw = htf_trail_anchor_provider_v2(symbol, bundle)
bm, tm = {}, {}

def htf_bias_at(i):
    key = int(ts5[i]) // 14_400_000
    if key not in bm:
        bm[key] = bias_raw(i)
    return bm[key]

def htf_trail_at(i, direction):
    key = (int(ts5[i]) // 3_600_000, direction)
    if key not in tm:
        tm[key] = trail_raw(i, direction)
    return tm[key]

t0 = time.time()
result = replay_daytrading_v2(
    ohlcv, gated, htf_bias_at=htf_bias_at, htf_trail_anchor_at=htf_trail_at,
    notify_exit=engine.notify_exit, fee_frac_round_trip=0.0012, slippage_frac_round_trip=0.0006,
)
print(f"DONE {time.time()-t0:.1f}s evals={n_calls[0]} n={result['count']} net={result['net_r']:+.2f} wr={result['win_rate']*100:.1f}%", flush=True)

split_i = SKIP + int((n - SKIP) * 0.70)
rows = []
for t in result["trades"]:
    rows.append({
        "direction": t.direction, "realised_r": float(t.realised_r),
        "exit_reason": t.exit_reason, "tp1_done": bool(t.tp1_done),
        "tp2_done": bool(t.tp2_done), "entry_i": t.entry_i,
        "split": "IS" if t.entry_i < split_i else "OOS",
    })

def summarize(label, subset):
    rs = [r["realised_r"] for r in subset]
    if not rs:
        print(f"{label}: n=0"); return
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    eq = peak = dd = 0
    for x in rs:
        eq += x; peak = max(peak, eq); dd = max(dd, peak - eq)
    print(f"{label}: n={len(rs)} WR={len(wins)/len(rs)*100:.1f}% net={sum(rs):+.2f} mean={sum(rs)/len(rs):+.3f} DD={dd:.2f}")
    byk = defaultdict(list)
    for t in subset:
        k = "TP2" if t["tp2_done"] else ("TP1" if t["tp1_done"] else "noTP_"+t["exit_reason"])
        byk[k].append(t["realised_r"])
    for k, v in sorted(byk.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:16s} n={len(v):3d} ({100*len(v)/len(rs):4.1f}%) sum={sum(v):+7.2f}")
    for d in ("LONG", "SHORT"):
        vals = [r["realised_r"] for r in subset if r["direction"] == d]
        if vals:
            w = sum(1 for x in vals if x > 0)
            print(f"  {d:5s} n={len(vals):3d} WR={w/len(vals)*100:.1f}% sum={sum(vals):+.2f}")

summarize("BTC ALL", rows)
summarize("BTC IS", [r for r in rows if r["split"]=="IS"])
summarize("BTC OOS", [r for r in rows if r["split"]=="OOS"])
Path("/tmp/v2_replay_90d_btc.json").write_text(json.dumps({"symbol":"BTC","trades":rows,"net":result["net_r"]}, indent=2))
print("saved /tmp/v2_replay_90d_btc.json")
