"""Downloadable, cached and causality-safe Historical Replay for Daytrading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from typing import Callable, Dict, Iterable

from daytrading_backtester import production_signal_provider, replay_daytrading
from daytrading_engine import STABLES
from blofin_feed import _bar_duration_ms, _merge_parsed_klines
import config


BASE = Path(__file__).resolve().parent
CACHE_DIR = BASE / "data" / "replay"
REPORT_DIR = BASE / "reports" / "replay"
RESULT_CACHE_DIR = CACHE_DIR / "results"
CACHE_MAX_AGE_HOURS = 24
TIMEFRAMES = {
    "5m": ("5m", 288),
    "15m": ("15m", 96),
    "1h": ("1H", 24),
    "4h": ("4H", 6),
    "1d": ("1D", 1),
}


@dataclass(frozen=True)
class ReplayRequest:
    symbols: tuple[str, ...] = ("BTC", "ETH", "SOL")
    universe_mode: str = "MANUAL"  # MANUAL | LIQUID | ALL
    liquid_limit: int = 30
    min_quote_volume: float = 5_000_000.0
    days: int = 90
    oos_fraction: float = 0.30
    fee_round_trip: float = 0.0012
    slippage_round_trip: float = 0.0006
    force_download: bool = False
    counterfactual_audit: bool = True
    max_workers: int = 0  # 0 = auto (liczba rdzeni CPU); symulacje IS/OOS per-symbol sa niezalezne


def _bot_version() -> str:
    try:
        from version import tag
        return tag()
    except Exception:
        return "unknown"


def _notify(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(message)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _cache_path(symbol: str, days: int) -> Path:
    return CACHE_DIR / f"{symbol.upper()}_{int(days)}d.json"


def _strategy_fingerprint(request: ReplayRequest, payload: dict) -> str:
    digest = hashlib.sha256()
    for name in ("daytrading_engine.py", "daytrading_backtester.py", "indicators.py",
                 "indicators_full.py", "expected_net_r.py", "historical_replay.py"):
        path = BASE / name
        if path.exists():
            digest.update(path.read_bytes())
    settings = {
        key: value for key, value in vars(config).items()
        if key.startswith("DAYTRADING_") and isinstance(value, (str, int, float, bool, type(None)))
    }
    data_tail = {
        tf: ((data.get("timestamps") or [None])[0], (data.get("timestamps") or [None])[-1],
             len(data.get("timestamps") or []))
        for tf, data in (payload.get("bundle") or {}).items()
    }
    digest.update(json.dumps({"settings": settings, "days": request.days,
                              "oos": request.oos_fraction, "fee": request.fee_round_trip,
                              "slippage": request.slippage_round_trip,
                              "counterfactual_audit": request.counterfactual_audit,
                              "data": data_tail}, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:20]


def _result_cache_path(symbol: str, days: int, fingerprint: str) -> Path:
    return RESULT_CACHE_DIR / f"{symbol.upper()}_{int(days)}d_{fingerprint}.json"


def _required_bars(days: int, per_day: int, timeframe: str) -> int:
    warmup = {"5m": 220, "15m": 420, "1h": 220, "4h": 260, "1d": 220}[timeframe]
    return int(days) * per_day + warmup


def _cache_is_fresh(payload: dict) -> bool:
    try:
        stamp = datetime.fromisoformat(str(payload.get("downloaded_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp).total_seconds() <= CACHE_MAX_AGE_HOURS * 3600
    except (TypeError, ValueError):
        return False


def discover_replay_symbols(feed, request: ReplayRequest) -> tuple[tuple[str, ...], dict]:
    """Resolve the current BloFin universe and record how it was selected."""
    mode = str(request.universe_mode or "MANUAL").upper()
    if mode == "MANUAL":
        symbols = tuple(dict.fromkeys(str(value).upper() for value in request.symbols if value))
        return symbols, {
            "mode": mode, "selection": "user supplied",
            "symbols_policy": "exact_user_list",
            "requested_symbols": list(symbols),
        }
    if mode not in ("LIQUID", "ALL"):
        raise ValueError(f"Unsupported replay universe: {mode}")
    tickers = feed.fetch_all_tickers() or {}
    if not tickers:
        raise RuntimeError(getattr(feed, "last_error", None) or "BloFin ticker universe unavailable")
    candidates = []
    for symbol, row in tickers.items():
        symbol = str(symbol).upper()
        quote_volume = float((row or {}).get("blofin_quote_volume") or 0.0)
        bid = float((row or {}).get("blofin_bid") or 0.0)
        ask = float((row or {}).get("blofin_ask") or 0.0)
        if symbol in STABLES or quote_volume <= 0 or bid <= 0 or ask <= 0 or ask < bid:
            continue
        candidates.append((symbol, quote_volume))
    candidates.sort(key=lambda item: item[1], reverse=True)
    if mode == "LIQUID":
        candidates = [item for item in candidates if item[1] >= float(request.min_quote_volume)]
        candidates = candidates[:max(1, int(request.liquid_limit))]
    symbols = tuple(symbol for symbol, _ in candidates)
    if not symbols:
        raise RuntimeError("BloFin universe is empty after liquidity and instrument filters")
    return symbols, {
        "mode": mode, "selection": "current BloFin ticker snapshot",
        "symbols_policy": "automatic_universe; manual symbol list is not applied",
        "ignored_symbols": [str(value).upper() for value in request.symbols if value],
        "liquid_limit": int(request.liquid_limit) if mode == "LIQUID" else None,
        "min_quote_volume": float(request.min_quote_volume) if mode == "LIQUID" else None,
        "candidate_count": len(tickers), "selected_count": len(symbols),
        "survivorship_warning": "Current listings cannot reconstruct delisted historical contracts.",
    }


def download_bundle(feed, symbol: str, days: int, force: bool = False,
                    progress: Callable[[str], None] | None = None) -> dict:
    """Get closed BloFin candles and funding, reusing an on-disk cache."""
    symbol = symbol.upper().replace("-USDT", "").replace("USDT", "")
    cache = _cache_path(symbol, days)
    stale_bundle: dict = {}
    if cache.exists() and not force:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            cached_bundle = cached.get("bundle", {})
            cache_complete = all(
                len((cached_bundle.get(tf) or {}).get("closes") or []) >= int(_required_bars(days, per_day, tf) * 0.90)
                for tf, (_, per_day) in TIMEFRAMES.items()
            )
            if cache_complete and _cache_is_fresh(cached):
                _notify(progress, f"{symbol}: używam cache {days} dni")
                return cached
            if cache_complete:
                # 21.08.2026: cache jest KOMPLETNY, ale przekroczyl
                # CACHE_MAX_AGE_HOURS (24h) - do tej pory byl wtedy
                # ignorowany w calosci i caly bundle (wszystkie 5
                # interwalow, w tym 5m/15m - dziesiatki tysiecy swiec na
                # symbol) lecial na nowo od zera. Trzymamy go jako baze
                # pod doszycie delty ponizej zamiast wyrzucac.
                stale_bundle = cached_bundle
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    bundle = {}
    for tf, (bar, per_day) in TIMEFRAMES.items():
        count = _required_bars(days, per_day, tf)
        old = stale_bundle.get(tf)
        old_timestamps = list((old or {}).get("timestamps") or [])
        if old_timestamps:
            # Mamy przestarzala, ale kompletna baze dla tego interwalu -
            # doszywamy REST-em tylko szacowany brakujacy ogon (od ostatniej
            # znanej swiecy do teraz), zamiast fetchowac cale `count` od
            # nowa. fetch_klines_ohlcv sam potrafi doszyc delte na WLASNYM
            # cache'u (patrz blofin_feed._KLINE_DISK_PERSIST_BARS), ale to
            # dziala tylko gdy trafi w identyczny (symbol,bar,limit) klucz -
            # replay zwykle prosi o wieksze `limit` niz normalny live fetch,
            # wiec bez tego i tak dostalby pelne okno. Tu liczymy delte
            # jawnie, na podstawie WLASNEGO (replay'owego) stale cache'u.
            gap_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - int(old_timestamps[-1])
            bar_ms = _bar_duration_ms(bar)
            fetch_count = min(count, max(20, int(gap_ms / bar_ms) + 20)) if bar_ms else count
            _notify(progress, f"{symbol}: {tf} doszywanie delty (~{fetch_count} świec zamiast {count})")
            fresh = feed.fetch_klines_ohlcv(symbol, bar=bar, limit=fetch_count) or {}
            data = _merge_parsed_klines(old, fresh, count) if fresh.get("closes") else old
        else:
            _notify(progress, f"{symbol}: pobieranie BloFin {tf} ({count} świec)")
            data = feed.fetch_klines_ohlcv(symbol, bar=bar, limit=count) or {}
        available = len(data.get("closes") or [])
        minimum = max({"5m": 220, "15m": 365, "1h": 120, "4h": 200, "1d": 120}[tf], int(count * 0.90))
        if available < minimum:
            error = getattr(feed, "last_error", None) or "za mało zamkniętych świec"
            raise RuntimeError(f"{symbol} {tf}: {available}/{minimum} wymaganych; {error}")
        bundle[tf] = data

    funding_limit = max(50, int(days * 3) + 20)  # settlement is normally every 8h
    _notify(progress, f"{symbol}: pobieranie historii funding")
    funding = feed.fetch_funding_rate_history(symbol, limit=funding_limit) or []
    payload = {
        "source": "BloFin",
        "symbol": symbol,
        "requested_days": int(days),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "bundle": bundle,
        "funding": funding,
    }
    _atomic_json(cache, payload)
    return payload


def _apply_funding(result: dict, timestamps: list, funding: list[dict]) -> None:
    """Book observed funding between entry and exit as a change in R."""
    for trade in result.get("trades") or []:
        if trade.exit_i is None or trade.entry_i >= len(timestamps) or trade.exit_i >= len(timestamps):
            continue
        start, end = int(timestamps[trade.entry_i]), int(timestamps[trade.exit_i])
        rates = [float(row.get("rate") or 0.0) for row in funding
                 if start < int(row.get("ts_ms") or 0) <= end]
        signed_cost = sum(rates) if trade.direction == "LONG" else -sum(rates)
        risk_fraction = trade.initial_risk / max(trade.entry, 1e-12)
        funding_r = -signed_cost / max(risk_fraction, 1e-9)
        trade.realised_r += funding_r
        trade.funding_r = funding_r


def _metrics(trades: Iterable) -> dict:
    trades = list(trades)
    values = [float(t.get("realised_r") if isinstance(t, dict) else t.realised_r) for t in trades]
    wins = [r for r in values if r > 0]
    losses = [r for r in values if r < 0]
    equity = peak = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    by_reason: dict[str, list[float]] = {}
    for t in trades:
        reason = t.get("exit_reason") if isinstance(t, dict) else getattr(t, "exit_reason", "") or "unknown"
        r = float(t.get("realised_r") if isinstance(t, dict) else t.realised_r)
        by_reason.setdefault(reason or "unknown", []).append(r)
    exit_reason_breakdown = {
        reason: {
            "n": len(rs), "win_rate": sum(1 for r in rs if r > 0) / len(rs) if rs else 0.0,
            "sum_r": sum(rs), "avg_r": sum(rs) / len(rs) if rs else 0.0,
        }
        for reason, rs in sorted(by_reason.items(), key=lambda kv: -len(kv[1]))
    }
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(values) if values else 0.0,
        "net_r": sum(values),
        "avg_r": sum(values) / len(values) if values else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown_r": max_dd,
        "exit_reason_breakdown": exit_reason_breakdown,
    }


def _trade_rows(result: dict, timestamps: list) -> list[dict]:
    rows = []
    for trade in result.get("trades") or []:
        rows.append({
            "direction": trade.direction,
            "entry_ts": timestamps[trade.entry_i] if trade.entry_i < len(timestamps) else None,
            "exit_ts": timestamps[trade.exit_i] if trade.exit_i is not None and trade.exit_i < len(timestamps) else None,
            "entry": trade.entry,
            "exit_reason": trade.exit_reason,
            "realised_r": trade.realised_r,
            "funding_r": getattr(trade, "funding_r", 0.0),
        })
    return rows


def _run_window(symbol: str, payload: dict, start_i: int, end_i: int,
                request: ReplayRequest, progress_queue=None, phase: str = "") -> dict:
    bundle = payload["bundle"]
    base_provider = production_signal_provider(symbol, bundle)
    total_bars = max(1, end_i - start_i)
    report_every = max(50, total_bars // 20)  # ~20 aktualizacji na okno, min co 50 swiec
    seen = 0
    audit_policies = {
        "DAY_HTF_CONFLICT": "remove hard block; use unambiguous 1h direction while retaining 4h conflict",
        "DAY_ADX_WEAK": "remove only the numeric ADX hard floor; missing ADX remains invalid",
    }
    audit_providers = {
        reason: production_signal_provider(symbol, bundle, audit_relax={reason})
        for reason in audit_policies
    } if request.counterfactual_audit else {}
    audit = {
        reason: {"policy": policy, "baseline_blocks": 0, "passed_full_funnel": 0,
                 "still_rejected": Counter()}
        for reason, policy in audit_policies.items()
    } if request.counterfactual_audit else {}

    def signal_at(index: int):
        if not start_i <= index < end_i:
            return None
        nonlocal seen
        seen += 1
        if progress_queue is not None and (seen % report_every == 0 or seen == total_bars):
            try:
                progress_queue.put_nowait((symbol, phase, seen, total_bars))
            except Exception:
                pass
        signal = base_provider(index)
        rejected = str(signal.get("reject_reason") or "")
        for reason, provider in audit_providers.items():
            if not rejected.startswith(reason):
                continue
            audit[reason]["baseline_blocks"] += 1
            alternative = provider(index)
            alternative_reject = str(alternative.get("reject_reason") or "")
            if alternative.get("direction") in ("LONG", "SHORT") and not alternative_reject:
                audit[reason]["passed_full_funnel"] += 1
            else:
                family = alternative_reject.split("(", 1)[0] or str(alternative.get("direction") or "UNKNOWN")
                audit[reason]["still_rejected"][family] += 1
        return signal

    result = replay_daytrading(
        bundle["5m"], signal_at,
        fee_frac_round_trip=request.fee_round_trip,
        slippage_frac_round_trip=request.slippage_round_trip,
    )
    timestamps = list(bundle["5m"].get("timestamps") or [])
    _apply_funding(result, timestamps, payload.get("funding") or [])
    audit_rows = {
        reason: {**row, "still_rejected": dict(row["still_rejected"]),
                 "incremental_pass_rate": (
                     row["passed_full_funnel"] / row["baseline_blocks"]
                     if row["baseline_blocks"] else 0.0
                 )}
        for reason, row in audit.items()
    }
    return {"metrics": _metrics(result.get("trades") or []),
            "trades": _trade_rows(result, timestamps),
            "counterfactual_filters": audit_rows}


def _compute_symbol_result(symbol: str, payload: dict, test_start: int, is_end: int,
                           oos_start: int, oos_end: int, purge: int, fingerprint: str,
                           request: ReplayRequest, progress_queue=None) -> tuple[str, dict]:
    """Czysta funkcja (bez sieci/feed) - bezpieczna do wywolania w osobnym procesie.
    Liczy IS+OOS dla jednego symbolu z juz pobranego (scache'owanego) bundle."""
    in_sample = _run_window(symbol, payload, test_start, is_end, request, progress_queue, "IS")
    out_sample = _run_window(symbol, payload, oos_start, oos_end, request, progress_queue, "OOS")
    symbol_result = {
        "in_sample": in_sample, "out_of_sample": out_sample,
        "bars_5m": len(payload["bundle"]["5m"].get("timestamps") or []),
        "purge_bars": purge, "strategy_fingerprint": fingerprint,
    }
    return symbol, symbol_result


def run_historical_replay(feed, request: ReplayRequest,
                          progress: Callable[[str], None] | None = None) -> dict:
    """Download data, run chronological IS/OOS replay and persist a JSON report.

    Pobieranie swiec zostaje sekwencyjne (siec, wspolny `feed`, i tak w wiekszosci
    trafia w cache po pierwszym uruchomieniu). Symulacja IS/OOS per-symbol jest
    czysto obliczeniowa i niezalezna miedzy symbolami, wiec leci rownolegle w
    puli procesow - to dominujacy koszt czasowy przy wielu symbolach/dniach.
    """
    started = datetime.now(timezone.utc)
    report = {
        "version": 1,
        "started_at": started.isoformat(),
        "source": "BloFin",
        "strategy": "DAYTRADING",
        "bot_version": _bot_version(),
        "execution": "signal_on_closed_bar_fill_next_open_stop_first",
        "costs": {"fee_round_trip": request.fee_round_trip,
                  "slippage_round_trip": request.slippage_round_trip,
                  "funding": "observed BloFin settlements when available"},
        "request": {"symbols": list(request.symbols), "universe_mode": request.universe_mode,
                    "symbols_effective_only_in_manual": True,
                    "liquid_limit": request.liquid_limit, "days": request.days,
                    "oos_fraction": request.oos_fraction,
                    "counterfactual_audit": request.counterfactual_audit},
        "symbols": {},
        "skipped": {},
        "portfolio_aggregation": "chronological exits across independent per-symbol replays",
        "portfolio_warning": "Concurrent slot/exposure competition is not simulated; portfolio R is diagnostic.",
    }
    symbols, universe_audit = discover_replay_symbols(feed, request)
    report["universe"] = universe_audit
    checkpoint_key = hashlib.sha256(json.dumps(report["request"], sort_keys=True).encode("utf-8")).hexdigest()[:16]
    checkpoint_path = REPORT_DIR / f"checkpoint_{checkpoint_key}.json"
    all_is, all_oos = [], []

    # --- Etap 1 (sekwencyjny, siec): pobierz/wczytaj z cache bundle per symbol.
    # Rownoczesnie: co juz ma gotowy wynik w result-cache, ladujemy od razu
    # (zero obliczen) - tylko brakujace ida do puli rownoleglej w etapie 2.
    pending: list[tuple] = []
    for position, symbol in enumerate(symbols, 1):
        _notify(progress, f"{symbol}: instrument {position}/{len(symbols)}")
        try:
            payload = download_bundle(feed, symbol, request.days, request.force_download, progress)
        except Exception as exc:
            report["skipped"][symbol] = str(exc)
            _notify(progress, f"{symbol}: pominięty — {exc}")
            _atomic_json(checkpoint_path, report)
            continue
        timestamps = list(payload["bundle"]["5m"].get("timestamps") or [])
        requested_bars = request.days * 288
        test_start = max(220, len(timestamps) - requested_bars)
        split = test_start + int((len(timestamps) - test_start) * (1.0 - request.oos_fraction))
        purge = 12
        fingerprint = _strategy_fingerprint(request, payload)
        result_cache = _result_cache_path(symbol, request.days, fingerprint)
        symbol_result = None
        if result_cache.exists() and not request.force_download:
            try:
                symbol_result = json.loads(result_cache.read_text(encoding="utf-8"))
                _notify(progress, f"{symbol}: przywrócono gotowy wynik z cache")
            except (OSError, UnicodeError, json.JSONDecodeError):
                symbol_result = None
        if symbol_result is not None:
            report["symbols"][symbol] = symbol_result
            all_is.extend(symbol_result["in_sample"]["trades"])
            all_oos.extend(symbol_result["out_of_sample"]["trades"])
            report["universe"]["completed_count"] = len(report["symbols"])
            _atomic_json(checkpoint_path, report)
        else:
            is_end = max(test_start, split - purge)
            oos_start = min(len(timestamps), split + purge)
            pending.append((symbol, payload, test_start, is_end, oos_start, len(timestamps),
                            purge, fingerprint, result_cache))

    # --- Etap 2 (rownolegly, CPU): symulacja IS/OOS dla symboli bez gotowego wyniku.
    if pending:
        workers = int(request.max_workers) or max(1, min(len(pending), os.cpu_count() or 4))
        _notify(progress, f"Symulacja {len(pending)} symboli równolegle ({workers} procesów)…")
        manager = multiprocessing.Manager()
        progress_queue = manager.Queue()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_compute_symbol_result, symbol, payload, test_start, is_end,
                           oos_start, oos_end, purge, fingerprint, request, progress_queue): symbol
                for symbol, payload, test_start, is_end, oos_start, oos_end, purge, fingerprint, _rc
                in pending
            }
            result_cache_by_symbol = {symbol: rc for symbol, *_rest, rc in pending}

            def _drain_progress():
                # Worker procesy pisza do wspolnej kolejki co ~5% swiec w oknie -
                # tu odbieramy to na biezaco (bez tego cisza az do konca symbolu).
                while True:
                    try:
                        sym, phase, seen, total = progress_queue.get_nowait()
                    except Exception:
                        break
                    _notify(progress, f"{sym} [{phase}]: {seen}/{total} świec ({100*seen//total}%)")

            remaining = set(futures.keys())
            while remaining:
                done, remaining = wait(remaining, timeout=0.5, return_when=FIRST_COMPLETED)
                _drain_progress()
                for future in done:
                    symbol = futures[future]
                    try:
                        symbol, symbol_result = future.result()
                    except Exception as exc:
                        report["skipped"][symbol] = str(exc)
                        _notify(progress, f"{symbol}: symulacja nieudana — {exc}")
                        _atomic_json(checkpoint_path, report)
                        continue
                    _atomic_json(result_cache_by_symbol[symbol], symbol_result)
                    report["symbols"][symbol] = symbol_result
                    all_is.extend(symbol_result["in_sample"]["trades"])
                    all_oos.extend(symbol_result["out_of_sample"]["trades"])
                    report["universe"]["completed_count"] = len(report["symbols"])
                    _atomic_json(checkpoint_path, report)
                    _notify(progress, f"{symbol}: gotowe ({len(report['symbols'])}/{len(symbols)})")
            _drain_progress()

    report["universe"]["tested_count"] = len(report["symbols"])
    report["universe"]["skipped_count"] = len(report["skipped"])
    all_is.sort(key=lambda row: int(row.get("exit_ts") or 0))
    all_oos.sort(key=lambda row: int(row.get("exit_ts") or 0))
    report["portfolio"] = {"in_sample": _metrics(all_is), "out_of_sample": _metrics(all_oos)}
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    stamp = started.strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"daytrading_replay_{request.days}d_{stamp}.json"
    _atomic_json(path, report)
    report["report_path"] = str(path)
    _notify(progress, "Replay zakończony")
    return report


# ============================================================
# Portfelowy replay V2 (punkt 25/30 planu) - jedna, wspolna ksiazka pozycji
# z limitem MAX_POSITIONS na CALE uniwersum naraz, nie 16 niezaleznych
# ksiazek jak run_historical_replay() powyzej. Uzywa DayTradingEngineV2 przez
# production_signal_provider_v2/htf_bias_provider_v2/htf_trail_anchor_provider_v2.
#
# UWAGA: brak tu rownoleglosci per-symbol (ProcessPoolExecutor) jak w V1 -
# konkurencja o wspolne sloty WYMAGA jednej, chronologicznej petli po
# wszystkich symbolach naraz, nie da sie tego rozbic na niezalezne procesy
# bez utraty sensu calego cwiczenia (portfelowej konkurencji).
# ============================================================


def _archive_bundles(bundles: Dict[str, dict], report_stem: str) -> Path:
    """Trwala kopia pobranych swiec (bundle + funding) obok raportu.

    UWAGA (21.08.2026): cache w data/replay/{SYMBOL}_{days}d.json jest
    keyowany TYLKO po symbolu i liczbie dni, wiec kolejny replay dla tych
    samych parametrow go nadpisuje (przesuniete okno czasowe) - a sam
    raport reports/replay/*.json nigdy nie przechowywal surowych swiec,
    tylko juz przetworzone wyniki (trades/metrics). Efekt: raport z
    wczorajszego replayu byl bezuzyteczny do pozniejszej offline'owej
    analizy, bo swiece, na ktorych powstal, juz nie istnialy. Ta funkcja
    zapisuje dokladna kopie kazdego pobranego bundle'a (ten sam format co
    data/replay/{SYMBOL}_{days}d.json) w katalogu powiazanym z konkretnym
    plikiem raportu, zeby przetrwala niezaleznie od tego, co pozniej stanie
    sie z ulotnym cache.
    """
    archive_dir = REPORT_DIR / "bundles" / report_stem
    for symbol, payload in bundles.items():
        _atomic_json(archive_dir / f"{symbol}.json", payload)
    return archive_dir


def run_portfolio_replay_v2(feed, request: "ReplayRequest",
                            progress: Callable[[str], None] | None = None) -> dict:
    from daytrading_backtester import (
        production_signal_provider_v2, htf_bias_provider_v2, htf_trail_anchor_provider_v2,
        portfolio_replay_v2,
    )
    started = datetime.now(timezone.utc)
    symbols, universe_meta = discover_replay_symbols(feed, request)
    excluded = {s.upper() for s in (getattr(config, "DAYTRADING_V2_EXCLUDED_SYMBOLS", None) or [])}
    symbols = tuple(s for s in symbols if s.upper() not in excluded)

    report = {
        "started_at": started.isoformat(), "bot_version": _bot_version(),
        "strategy": "DAYTRADING_V2", "request": vars(request),
        "universe": {**universe_meta, "excluded_by_v2_profile": sorted(excluded)},
        "skipped": {}, "symbols_downloaded": [],
    }

    bundles: Dict[str, dict] = {}
    for position, symbol in enumerate(symbols, 1):
        _notify(progress, f"{symbol}: instrument {position}/{len(symbols)}")
        try:
            payload = download_bundle(feed, symbol, request.days, request.force_download, progress)
        except Exception as exc:
            report["skipped"][symbol] = str(exc)
            _notify(progress, f"{symbol}: pominięty — {exc}")
            continue
        bundles[symbol] = payload
        report["symbols_downloaded"].append(symbol)

    if not bundles:
        report["error"] = "brak symboli z kompletnymi danymi po pobraniu"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        return report

    # Wspolny podzial IS/OOS - jak w run_historical_replay(), ale liczony
    # wzgledem NAJKROTSZEGO dostepnego symbolu, zeby indeks bara i mial
    # spojne znaczenie kalendarzowe dla calego portfela (patrz zalozenie w
    # docstringu portfolio_replay_v2).
    shortest_len = min(len(p["bundle"]["5m"].get("timestamps") or []) for p in bundles.values())
    requested_bars = request.days * 288
    test_start = max(220, shortest_len - requested_bars)
    split = test_start + int((shortest_len - test_start) * (1.0 - request.oos_fraction))
    purge = 12
    is_end = max(test_start, split - purge)
    oos_start = min(shortest_len, split + purge)
    oos_end = shortest_len

    def _build_symbols_data(start_i: int, end_i: int) -> Dict[str, dict]:
        symbols_data = {}
        for symbol, payload in bundles.items():
            bundle = payload["bundle"]
            signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)

            def gated_signal_at(index, _raw=signal_at_raw, _s=start_i, _e=end_i):
                return _raw(index) if _s <= index < _e else None

            symbols_data[symbol] = {
                "ohlcv_5m": bundle["5m"],
                "signal_at": gated_signal_at,
                "htf_bias_at": htf_bias_provider_v2(symbol, bundle),
                "htf_trail_anchor_at": htf_trail_anchor_provider_v2(symbol, bundle),
                "notify_exit": engine.notify_exit,
            }
        return symbols_data

    max_positions = int(getattr(config, "MAX_POSITIONS", 10) or 10)
    _notify(progress, f"Portfelowy replay IS: {len(bundles)} symboli, max_positions={max_positions}")
    is_result = portfolio_replay_v2(
        _build_symbols_data(test_start, is_end), max_positions,
        fee_frac_round_trip=request.fee_round_trip, slippage_frac_round_trip=request.slippage_round_trip,
    )
    _notify(progress, f"Portfelowy replay OOS: {len(bundles)} symboli")
    oos_result = portfolio_replay_v2(
        _build_symbols_data(oos_start, oos_end), max_positions,
        fee_frac_round_trip=request.fee_round_trip, slippage_frac_round_trip=request.slippage_round_trip,
    )

    report["portfolio"] = {
        "in_sample": {**_metrics(is_result["trades"]), "rejected_for_slots": is_result["rejected_for_slots"],
                     "by_symbol": is_result["by_symbol"]},
        "out_of_sample": {**_metrics(oos_result["trades"]), "rejected_for_slots": oos_result["rejected_for_slots"],
                          "by_symbol": oos_result["by_symbol"]},
    }
    # UI (pyside6_ui.py historical_replay_completed) czyta te dwa pola z
    # report["universe"] - run_historical_replay() (V1) je ustawia, ta
    # funkcja wczesniej nie, wiec panel Replay pokazywal zawsze 0/0.
    report["universe"]["tested_count"] = len(bundles)
    report["universe"]["skipped_count"] = len(report["skipped"])
    report["max_positions"] = max_positions
    report["split"] = {"test_start": test_start, "is_end": is_end, "oos_start": oos_start,
                       "oos_end": oos_end, "purge_bars": purge}
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    stamp = started.strftime("%Y%m%d_%H%M%S")
    report_stem = f"daytrading_v2_portfolio_replay_{request.days}d_{stamp}"
    path = REPORT_DIR / f"{report_stem}.json"
    archive_dir = _archive_bundles(bundles, report_stem)
    report["candles_archive"] = {
        "dir": str(archive_dir),
        "symbols": sorted(bundles.keys()),
        "format": "jeden plik JSON na symbol, ten sam schemat co data/replay/{SYMBOL}_{days}d.json "
                  "(bundle: {tf: {timestamps, opens, highs, lows, closes, volumes}}, funding: [...])",
        "note": "Trwala kopia niezalezna od ulotnego cache data/replay/ (ten jest nadpisywany "
                "kolejnymi runami dla tych samych dni) - powiazana z tym raportem, zeby swiece z "
                "tego backtestu dalo sie pozniej odtworzyc/przeanalizowac offline.",
    }
    _atomic_json(path, report)
    report["report_path"] = str(path)
    _notify(progress, "Portfelowy replay V2 zakończony")
    return report
