import json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from daytrading_backtester import (
    production_signal_provider_v2, htf_bias_provider_v2,
    htf_trail_anchor_provider_v2, replay_daytrading_v2,
)

symbol = sys.argv[1].upper()
SKIP = 1500
payload = json.loads(Path(f"data/replay/{symbol}_90d.json").read_text())
bundle = payload["bundle"]
ohlcv = bundle["5m"]
n = len(ohlcv["opens"])
ts5 = ohlcv["timestamps"]
print(f"{symbol} bars={n}", flush=True)
signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)
n_calls = [0]

def gated(index):
    if not (SKIP <= index < n):
        return None
    if int(ts5[index]) % 900_000 != 0:
        return None
    n_calls[0] += 1
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
split_i = SKIP + int((n - SKIP) * 0.70)
rows = []
for t in result["trades"]:
    rows.append({
        "symbol": symbol, "direction": t.direction, "realised_r": float(t.realised_r),
        "exit_reason": t.exit_reason, "tp1_done": bool(t.tp1_done), "tp2_done": bool(t.tp2_done),
        "entry_i": t.entry_i, "exit_i": t.exit_i,
        "split": "IS" if t.entry_i < split_i else "OOS",
    })
out = {"symbol": symbol, "seconds": time.time()-t0, "evals": n_calls[0],
       "count": result["count"], "net_r": result["net_r"], "win_rate": result["win_rate"],
       "trades": rows}
Path(f"/tmp/v2_replay_90d_{symbol}.json").write_text(json.dumps(out, indent=2))
print(f"DONE {symbol} {out['seconds']:.1f}s n={out['count']} net={out['net_r']:+.2f} wr={out['win_rate']*100:.1f}%", flush=True)
