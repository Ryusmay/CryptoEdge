"""CLI for CryptoEdge historical replay (V1 or V2)."""
from __future__ import annotations
import argparse
import json
from blofin_feed import BlofinFeed
from historical_replay import ReplayRequest, run_historical_replay, run_portfolio_replay_v2


def main() -> int:
    parser = argparse.ArgumentParser(description="CryptoEdge causal BloFin historical replay")
    parser.add_argument("--engine", choices=["v1", "v2"], default="v2",
                        help="v2 = DAYTRADING_V2 (domyslnie); v1 = stary daytrading")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Exact symbols for --universe manual")
    parser.add_argument("--universe", choices=["manual", "liquid", "all"], default="manual")
    parser.add_argument("--liquid-limit", type=int, default=30)
    parser.add_argument("--oos", type=float, default=0.30)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-filter-audit", action="store_true",
                        help="V1 only: disable HTF/ADX counterfactual audit")
    parser.add_argument("--workers", type=int, default=0,
                        help="V1 only: parallel IS/OOS workers (0 = auto)")
    args = parser.parse_args()
    if not 7 <= args.days <= 365:
        parser.error("--days must be between 7 and 365")
    if not 0.10 <= args.oos <= 0.50:
        parser.error("--oos must be between 0.10 and 0.50")
    if args.workers < 0:
        parser.error("--workers must be >= 0")
    manual_symbols = args.symbols or ["BTC", "ETH", "SOL"]
    if args.universe != "manual" and args.symbols:
        parser.error("--symbols can only be used with --universe manual")
    request = ReplayRequest(
        symbols=tuple(x.upper() for x in manual_symbols) if args.universe == "manual" else (),
        universe_mode=args.universe.upper(),
        liquid_limit=args.liquid_limit,
        days=args.days,
        oos_fraction=args.oos,
        force_download=args.refresh,
        counterfactual_audit=not args.no_filter_audit,
        max_workers=args.workers,
    )
    feed = BlofinFeed()
    if args.engine == "v2":
        print("[Replay] silnik DAYTRADING_V2 (portfel, MAX_POSITIONS, fill next open, SL first)")
        result = run_portfolio_replay_v2(feed, request, print)
    else:
        print("[Replay] silnik DAYTRADING V1")
        result = run_historical_replay(feed, request, print)
    summary = {
        "engine": args.engine,
        "strategy": result.get("strategy"),
        "report": result.get("report_path") or result.get("report"),
        "error": result.get("error"),
        "portfolio": result.get("portfolio") or result.get("is") or result.get("oos"),
        "is": result.get("is_metrics") or (result.get("is") or {}).get("metrics"),
        "oos": result.get("oos_metrics") or (result.get("oos") or {}).get("metrics"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
