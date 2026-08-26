# ============================================================
# Position reconciliation – bot vs giełda
# Porównuje symbol + kierunek + WIELKOŚĆ (z tolerancją)
# Drift blokuje nowe wejścia LIVE (flagi na risk)
# ============================================================

from __future__ import annotations

import time
from typing import List, Dict, Optional, Any, Tuple

import config


class PositionReconciler:
    """
    Porównuje lokalne pozycje z Blofin.
    Nie otwiera/zamyka – raport + flaga drift do risk gate.
    """

    def __init__(self, feeder=None, account_sync=None):
        self.feeder = feeder
        self.account_sync = account_sync
        self.last_report: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.drift_blocks_entries = False  # True gdy local↔exchange rozjechane

    def fetch_exchange_positions(self) -> List[dict]:
        if self.account_sync and hasattr(self.account_sync, "_last_positions"):
            try:
                if hasattr(self.account_sync, "sync"):
                    self.account_sync.sync(force=True)
            except Exception as e:
                self.last_error = str(e)
            return list(getattr(self.account_sync, "_last_positions", []) or [])
        if self.feeder and hasattr(self.feeder, "blofin"):
            try:
                return self.feeder.blofin.fetch_open_positions() or []
            except Exception as e:
                self.last_error = str(e)
                return []
        return []

    @staticmethod
    def _norm_symbol(p: dict) -> str:
        s = (p.get("symbol") or p.get("instId") or "").upper()
        if "-" in s:
            s = s.split("-")[0]
        if s.endswith("USDT"):
            s = s[:-4]
        return s

    @staticmethod
    def _norm_direction(p: dict) -> str:
        d = (p.get("direction") or p.get("side") or p.get("positionSide") or "").upper()
        if d in ("BUY", "LONG"):
            return "LONG"
        if d in ("SELL", "SHORT"):
            return "SHORT"
        try:
            sz = float(p.get("size") or p.get("pos") or p.get("contracts") or 0)
            if sz < 0:
                return "SHORT"
            if sz > 0:
                return "LONG"
        except (TypeError, ValueError):
            pass
        return "UNKNOWN"

    @staticmethod
    def _size(p: dict) -> float:
        for k in ("contracts", "size", "pos", "size_contracts", "position_size"):
            if p.get(k) is not None:
                try:
                    return abs(float(p[k]))
                except (TypeError, ValueError):
                    pass
        return 0.0

    @staticmethod
    def _local_contracts(p: Any) -> float:
        """Lokalnie: preferuj size_contracts, inaczej 0 (paper USD ≠ contracts)."""
        if hasattr(p, "size_contracts") and getattr(p, "size_contracts", None) is not None:
            try:
                return abs(float(p.size_contracts))
            except (TypeError, ValueError):
                pass
        if isinstance(p, dict):
            for k in ("size_contracts", "contracts", "size"):
                if p.get(k) is not None:
                    try:
                        return abs(float(p[k]))
                    except (TypeError, ValueError):
                        pass
        return 0.0

    def reconcile(self, local_positions: List[Any], executor=None, protection=None) -> dict:
        # Błąd z poprzedniego cyklu nie może zatruwać kolejnego raportu, ale
        # błąd bieżącego odczytu pozycji musi pozostać stanem UNKNOWN.
        self.last_error = None
        exchange = self.fetch_exchange_positions()
        local_map: Dict[Tuple[str, str], Any] = {}
        for p in local_positions or []:
            if hasattr(p, "symbol"):
                sym = str(p.symbol).upper()
                direction = str(getattr(p, "direction", "")).upper()
                size_usd = float(getattr(p, "size_usd", 0) or 0)
                contracts = self._local_contracts(p)
                local_map[(sym, direction)] = {
                    "symbol": sym,
                    "direction": direction,
                    "size_usd": size_usd,
                    "contracts": contracts,
                    "entry": getattr(p, "entry_price", None),
                    "source": "local",
                    "obj": p,
                }
            elif isinstance(p, dict):
                sym = self._norm_symbol(p)
                direction = self._norm_direction(p)
                local_map[(sym, direction)] = {
                    **p,
                    "symbol": sym,
                    "direction": direction,
                    "contracts": self._local_contracts(p),
                    "source": "local",
                }

        ex_map: Dict[Tuple[str, str], dict] = {}
        for p in exchange:
            sym = self._norm_symbol(p)
            direction = self._norm_direction(p)
            if direction == "UNKNOWN" or not sym:
                continue
            ex_map[(sym, direction)] = {
                "symbol": sym,
                "direction": direction,
                "size": self._size(p),
                "contracts": self._size(p),
                "entry": p.get("avg_price") or p.get("averagePrice") or p.get("entry_price"),
                "upl": p.get("upl") or p.get("unrealized_pnl"),
                "raw": p,
                "source": "exchange",
            }

        matched = []
        size_mismatch = []
        only_local = []
        only_exchange = []

        size_tol_pct = float(getattr(config, "RECONCILE_SIZE_TOLERANCE_PCT", 5.0)) / 100.0
        size_tol_abs = float(getattr(config, "RECONCILE_SIZE_TOLERANCE_ABS", 0.01))

        all_keys = set(local_map.keys()) | set(ex_map.keys())
        for key in sorted(all_keys):
            loc = local_map.get(key)
            ex = ex_map.get(key)
            if loc and ex:
                row = {
                    "local": loc,
                    "exchange": ex,
                    "symbol": key[0],
                    "direction": key[1],
                    "size_ok": True,
                    "size_diff_pct": 0.0,
                }
                loc_c = float(loc.get("contracts") or 0)
                ex_c = float(ex.get("contracts") or 0)
                # porównuj size tylko gdy lokalnie znamy contracts (LIVE)
                if loc_c > 0 or ex_c > 0:
                    base = max(loc_c, ex_c, 1e-12)
                    diff_pct = abs(loc_c - ex_c) / base
                    abs_diff = abs(loc_c - ex_c)
                    row["size_diff_pct"] = round(diff_pct * 100, 3)
                    row["local_contracts"] = loc_c
                    row["exchange_contracts"] = ex_c
                    if diff_pct > size_tol_pct and abs_diff > size_tol_abs:
                        row["size_ok"] = False
                        size_mismatch.append(row)
                matched.append(row)
            elif loc and not ex:
                only_local.append(loc)
            elif ex and not loc:
                only_exchange.append(ex)

        in_sync = (
            len(only_local) == 0
            and len(only_exchange) == 0
            and len(size_mismatch) == 0
        )
        # Drift blokuje TYLKO LIVE. Paper trzyma pozycje lokalnie, a konto
        # BloFin (read-only / puste) jest puste — only_local ≠ drift.
        from cryptoedge.domain import trading_mode
        live = trading_mode.is_live(config)
        block_on_drift = bool(getattr(config, "BLOCK_ENTRIES_ON_RECONCILE_DRIFT", True))
        self.drift_blocks_entries = (not in_sync) and block_on_drift and live


        report = {
            "ts": time.time(),
            "matched": matched,
            "size_mismatch": size_mismatch,
            "only_local": only_local,
            "only_exchange": only_exchange,
            "local_count": len(local_map),
            "exchange_count": len(ex_map),
            "in_sync": in_sync,
            "drift_blocks_entries": self.drift_blocks_entries,
            "error": self.last_error,
        }
        if report.get("error"):
            # Brak odpowiedzi z venue nie jest dowodem płaskiego konta.
            # W LIVE fail closed; PAPER może dalej zarządzać lokalnym stanem.
            report["in_sync"] = False
            if live:
                self.drift_blocks_entries = True
                report["drift_blocks_entries"] = True
        # Pelny startup/runtime audit: working orders oraz protective orders.
        try:
            orders = executor.fetch_open_orders() if executor is not None else []
        except Exception as e:
            orders = []
            report["orders_error"] = str(e)
        active_symbols = {key[0] for key in ex_map}
        report["open_orders"] = orders
        report["orphan_orders"] = [o for o in orders if self._norm_symbol(o) not in active_symbols]
        attachments = getattr(protection, "by_key", {}) if protection is not None else {}
        report["protective_missing"] = ([
            {"symbol": key[0], "direction": key[1]}
            for key in ex_map if key not in attachments and key[0] not in attachments
        ] if protection is not None else [])
        if report.get("orders_error"):
            # UNKNOWN is not the same as an empty exchange order set. In LIVE
            # fail closed until a later reconciliation can prove the state.
            report["in_sync"] = False
            if live:
                self.drift_blocks_entries = True
                report["drift_blocks_entries"] = True
        if report["orphan_orders"] or report["protective_missing"]:
            report["in_sync"] = False
            if live:
                self.drift_blocks_entries = True
                report["drift_blocks_entries"] = True
        self.last_report = report
        return report

    def blocks_new_entries(self) -> bool:
        return bool(self.drift_blocks_entries)

    def confirm_flat(self, symbols: List[str] = None) -> dict:
        """
        Po emergency close: sprawdź czy giełda jest płaska na symbolach.
        """
        exchange = self.fetch_exchange_positions()
        remaining = []
        want = {s.upper() for s in (symbols or [])}
        for p in exchange:
            sym = self._norm_symbol(p)
            if want and sym not in want:
                continue
            sz = self._size(p)
            if sz > float(getattr(config, "RECONCILE_SIZE_TOLERANCE_ABS", 0.01)):
                remaining.append({
                    "symbol": sym,
                    "direction": self._norm_direction(p),
                    "size": sz,
                })
        return {
            "flat": len(remaining) == 0,
            "remaining": remaining,
        }

    def summary_text(self, report: dict = None) -> str:
        r = report or self.last_report or {}
        lines = [
            f"Reconcile: local={r.get('local_count')} exchange={r.get('exchange_count')} "
            f"sync={'OK' if r.get('in_sync') else 'DRIFT'}"
            f"{' BLOCK_ENTRIES' if r.get('drift_blocks_entries') else ''}",
        ]
        for g in r.get("only_local") or []:
            lines.append(
                f"  GHOST local: {g.get('symbol')} {g.get('direction')} "
                f"contracts={g.get('contracts')}"
            )
        for o in r.get("only_exchange") or []:
            lines.append(
                f"  ORPHAN exchange: {o.get('symbol')} {o.get('direction')} "
                f"size={o.get('size')}"
            )
        for m in r.get("size_mismatch") or []:
            lines.append(
                f"  SIZE_MISMATCH: {m.get('symbol')} {m.get('direction')} "
                f"local={m.get('local_contracts')} ex={m.get('exchange_contracts')} "
                f"diff={m.get('size_diff_pct')}%"
            )
        if r.get("error"):
            lines.append(f"  error: {r['error']}")
        return "\n".join(lines)
