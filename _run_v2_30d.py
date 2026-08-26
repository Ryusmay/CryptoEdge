"""30d V2 replay: pivot 5 / 8 bars / 2.0 ATR / SL buf 1.0. 15m signal gate."""
from __future__ import annotations
import json, time
from pathlib import Path
from blofin_feed import BlofinFeed
from historical_replay import download_bundle
from daytrading_backtester import (
    production_signal_provider_v2, htf_bias_provider_v2,
    htf_trail_anchor_provider_v2, replay_daytrading_v2,
)

SYMBOLS = ("BTC", "ETH", "SOL")
DAYS = 30
SKIP = 500  # ~1.7d 5m warmup
OUT = Path("/tmp/v2_replay_30d_htf.json")


def _sl_pct(sig) -> float | None:
    px = float(sig.get("price") or 0)
    sl = sig.get("sl_price")
    if not px or sl is None:
        return None
    return abs(px - float(sl)) / px * 100.0


def run_one(feed, symbol: str) -> dict:
    payload = download_bundle(feed, symbol, DAYS, force=False)
    bundle = payload["bundle"]
    ohlcv = bundle["5m"]
    n = len(ohlcv["opens"])
    ts5 = ohlcv["timestamps"]
    signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)
    n_calls = [0]
    sls, tp1s = [], []

    def gated(index):
        if not (SKIP <= index < n):
            return None
        if int(ts5[index]) % 900_000 != 0:
            return None
        n_calls[0] += 1
        sig = signal_at_raw(index)
        if sig and sig.get("direction") in ("LONG", "SHORT") and not sig.get("reject_reason"):
            sp = _sl_pct(sig)
            if sp is not None:
                sls.append(sp)
            px = float(sig.get("price") or 0)
            tp1 = sig.get("tp1_price")
            if px and tp1 is not None:
                tp1s.append(abs(px - float(tp1)) / px * 100.0)
        return sig

    t0 = time.time()
    result = replay_daytrading_v2(
        ohlcv, gated,
        htf_bias_at=htf_bias_provider_v2(symbol, bundle),
        htf_trail_anchor_at=htf_trail_anchor_provider_v2(symbol, bundle),
        notify_exit=engine.notify_exit,
        fee_frac_round_trip=0.0012, slippage_frac_round_trip=0.0006,
    )
    rows = [{
        "symbol": symbol, "direction": t.direction, "realised_r": float(t.realised_r),
        "exit_reason": t.exit_reason, "tp1_done": bool(t.tp1_done), "tp2_done": bool(t.tp2_done),
        "entry_i": t.entry_i, "exit_i": t.exit_i,
    } for t in result["trades"]]
    sls.sort(); tp1s.sort()
    mid = lambda xs: xs[len(xs)//2] if xs else None
    out = {
        "symbol": symbol, "bars": n, "seconds": time.time() - t0, "evals": n_calls[0],
        "signals": len(sls), "median_sl_pct": mid(sls), "median_tp1_pct": mid(tp1s),
        "count": result["count"], "net_r": result["net_r"], "win_rate": result["win_rate"],
        "avg_r": result["avg_r"], "trades": rows,
    }
    print(
        f"DONE {symbol} {out['seconds']:.0f}s n={out['count']} net={out['net_r']:+.2f}R "
        f"wr={out['win_rate']*100:.0f}% medSL={out['median_sl_pct']}",
        flush=True,
    )
    return out


def main():
    feed = BlofinFeed()
    per = []
    for s in SYMBOLS:
        print(f"=== {s} ===", flush=True)
        per.append(run_one(feed, s))
    all_t = [t for p in per for t in p["trades"]]
    rs = [t["realised_r"] for t in all_t]
    report = {
        "params": {"pivot": 5, "min_bars": 8, "min_atr": 2.0, "sl_buf": 1.0, "days": DAYS},
        "symbols": per,
        "trades": len(all_t),
        "net_r": sum(rs),
        "win_rate": (sum(r > 0 for r in rs) / len(rs)) if rs else 0.0,
        "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
    }
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("params", "trades", "net_r", "win_rate", "avg_r")}, indent=2))


if __name__ == "__main__":
    main()
