# ============================================================
# 13. Restart recovery
# Po restarcie: wczytaj stan, uzbrój lokalne SL, reconcile vs giełda,
# opcjonalnie odtwórz exchange TPSL gdy brakuje.
# ============================================================

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

import config


# UI "Close all" zostawia KILL_SWITCH + protection_state.json.
# W PAPER to leftover po przycisku, nie operator halt — zdejmujemy przy starcie.
# LIVE i reason=operator zostają.
_PAPER_UI_STOP_REASONS = frozenset({"manual_close_all"})


def _is_paper_ui_stop_kill(reason: str) -> bool:
    raw = str(reason or "").strip().lower()
    if raw.startswith("kill_switch:"):
        raw = raw.split(":", 1)[1].strip()
    return raw in _PAPER_UI_STOP_REASONS


class RestartRecovery:
    def __init__(
        self,
        risk=None,
        trader=None,
        logger=None,
        protection=None,
        reconciler=None,
        executor=None,
        account_sync=None,
    ):
        self.risk = risk
        self.trader = trader
        self.logger = logger
        self.protection = protection
        self.reconciler = reconciler
        self.executor = executor
        self.account_sync = account_sync
        self.last_report: Dict[str, Any] = {}

    def run(self) -> dict:
        """
        Wywołać raz przy starcie aplikacji (przed pętlą).
        """
        report = {
            "ts": time.time(),
            "positions_restored": 0,
            "protection_loaded": 0,
            "protection_rearmed": 0,
            "reconcile": None,
            "kill_switch": False,
            "paper_ui_stop_cleared": False,
            "errors": [],
        }

        # 1) Protection state (JSON) — kill + attachmenty
        if self.protection:
            try:
                self.protection.load_state()
                report["protection_loaded"] = len(self.protection.by_key)
            except Exception as e:
                report["errors"].append(f"protect_load: {e}")

        # 2) Plik KILL_SWITCH nadpisuje JSON (operator drop-file)
        from_file = False
        try:
            kill_path = Path(__file__).resolve().parent / "KILL_SWITCH"
            if kill_path.exists() and self.protection:
                self.protection.kill_switch_active = True
                self.protection.kill_reason = kill_path.read_text(encoding="utf-8").strip() or "file"
                from_file = True
                report["kill_switch"] = True
        except Exception as e:
            report["errors"].append(f"kill_file: {e}")

        # 3) PAPER leftover Close-All ≠ disaster halt. Operator/LIVE zostaje.
        paper = getattr(config, "PAPER_TRADING", True)
        if isinstance(paper, str):
            paper = paper.strip().lower() in ("1", "true", "yes", "on", "demo", "paper")
        paper = bool(paper)
        if self.protection and (
            getattr(self.protection, "kill_switch_active", False)
            or (hasattr(self.protection, "is_killed") and self.protection.is_killed())
        ):
            reason = str(getattr(self.protection, "kill_reason", None) or "")
            if paper and _is_paper_ui_stop_kill(reason):
                try:
                    self.protection.clear_kill_switch()
                except Exception as e:
                    report["errors"].append(f"paper_kill_clear: {e}")
                report["kill_switch"] = False
                report["paper_ui_stop_cleared"] = True
                if self.risk:
                    hr = str(getattr(self.risk, "halt_reason", None) or "")
                    if (not hr) or ("KILL_SWITCH" in hr.upper() and _is_paper_ui_stop_kill(hr)):
                        self.risk.is_halted = False
                        self.risk.halt_reason = None
                        self.risk.paused = False
                print("[Recovery] PAPER: zdjęto leftover Close-All (manual_close_all) — handel dozwolony")
            else:
                report["kill_switch"] = True
                if self.risk:
                    self.risk.is_halted = True
                    self.risk.paused = True
                    self.risk.halt_reason = f"KILL_SWITCH: {reason or 'file'}"
                src = "z pliku" if from_file else "z protection_state"
                print(f"[Recovery] KILL SWITCH aktywny {src}: {reason or 'file'}")

        # 4) Pozycje lokalne – już zwykle z load_previous_state w app.py
        if self.trader:
            report["positions_restored"] = len(getattr(self.trader, "positions", []) or [])

        # 4) Ochrona oparta o RZECZYWISTY stan giełdy (gdy dostępny), potem local
        exchange_positions = []
        if self.reconciler and hasattr(self.reconciler, "fetch_exchange_positions"):
            try:
                exchange_positions = self.reconciler.fetch_exchange_positions() or []
                report["exchange_positions"] = len(exchange_positions)
            except Exception as e:
                report["errors"].append(f"ex_pos:{e}")

        def _norm_sym(raw):
            s = str(raw or "").upper()
            if "-" in s:
                s = s.split("-")[0]
            if s.endswith("USDT"):
                s = s[:-4]
            return s

        def _norm_dir(p):
            d = str(p.get("direction") or p.get("side") or p.get("positionSide") or "").upper()
            if d in ("BUY", "LONG"):
                return "LONG"
            if d in ("SELL", "SHORT"):
                return "SHORT"
            try:
                sz = float(p.get("size") or p.get("pos") or 0)
                return "SHORT" if sz < 0 else "LONG" if sz > 0 else "UNKNOWN"
            except Exception:
                return "UNKNOWN"

        # 4a) Exchange = źródło prawdy: uzbrój SL dla każdej otwartej pozycji na giełdzie
        if self.protection and exchange_positions:
            for ep in exchange_positions:
                try:
                    sym = _norm_sym(ep.get("symbol") or ep.get("instId"))
                    direction = _norm_dir(ep)
                    if not sym or direction == "UNKNOWN":
                        continue
                    # size z giełdy
                    try:
                        contracts = abs(float(ep.get("contracts") or ep.get("size") or ep.get("pos") or 0))
                    except Exception:
                        contracts = 0.0
                    entry = ep.get("avg_price") or ep.get("averagePrice") or ep.get("entry_price")
                    # SL lokalny z matching paper pos jeśli jest
                    sl = None
                    if self.trader:
                        for pos in getattr(self.trader, "positions", []) or []:
                            if getattr(pos, "symbol", "").upper() == sym and getattr(pos, "direction", "").upper() == direction:
                                sl = getattr(pos, "sl_price", None)
                                if not contracts:
                                    contracts = float(getattr(pos, "size_contracts", 0) or 0)
                                break
                    # fallback SL z % jeśli brak
                    if sl is None and entry:
                        try:
                            entry_f = float(entry)
                            sl_pct = abs(float(getattr(config, "STOP_LOSS_PCT", -22))) / 100.0 / max(float(getattr(config, "LEVERAGE", 10)), 1)
                            sl = entry_f * (1 - sl_pct) if direction == "LONG" else entry_f * (1 + sl_pct)
                        except Exception:
                            pass
                    # Never weaken a persisted trailing stop with an older
                    # position snapshot. LONG: higher is safer; SHORT: lower.
                    existing = self.protection.by_key.get(
                        self.protection._key(sym, direction)
                    )
                    existing_sl = getattr(existing, "sl_price", None) if existing else None
                    if existing_sl is not None:
                        try:
                            existing_sl = float(existing_sl)
                            if sl is None:
                                sl = existing_sl
                            elif direction == "LONG":
                                sl = max(float(sl), existing_sl)
                            else:
                                sl = min(float(sl), existing_sl)
                        except (TypeError, ValueError):
                            pass
                    if sl is not None:
                        reattach_ex = bool(getattr(config, "RECOVERY_REATTACH_EXCHANGE_SL", True))
                        self.protection.attach_protection(
                            symbol=sym,
                            direction=direction,
                            sl_price=sl,
                            size_contracts=contracts if contracts > 0 else None,
                            entry_price=float(entry) if entry else None,
                            place_exchange=reattach_ex,
                        )
                        report["protection_rearmed"] += 1
                except Exception as e:
                    report["errors"].append(f"ex_rearm:{e}")

        # 4b) Lokalne pozycje bez odpowiednika exchange – local SL only
        if self.trader and self.protection:
            for pos in list(getattr(self.trader, "positions", []) or []):
                try:
                    # Restored Position objects must keep propagating a newly
                    # tightened trailing SL to the protection layer.
                    def _sl_cb(sym, direction, new_sl, size_contracts, _pm=self.protection):
                        if _pm is not None:
                            _pm.update_exchange_sl(
                                sym, direction, new_sl=new_sl,
                                size_contracts=size_contracts,
                            )
                    pos.on_sl_updated = _sl_cb
                    sym = pos.symbol
                    direction = pos.direction
                    key = self.protection._key(sym, direction)
                    if key in self.protection.by_key:
                        # dopisz size_contracts z paper shadow jeśli brak
                        att = self.protection.by_key[key]
                        if not att.size_contracts and getattr(pos, "size_contracts", None):
                            att.size_contracts = pos.size_contracts
                        continue
                    sl = getattr(pos, "sl_price", None)
                    if sl is not None:
                        reattach_ex = bool(getattr(config, "RECOVERY_REATTACH_EXCHANGE_SL", True))
                        self.protection.attach_protection(
                            symbol=sym,
                            direction=direction,
                            sl_price=sl,
                            size_contracts=getattr(pos, "size_contracts", None),
                            entry_price=getattr(pos, "entry_price", None),
                            place_exchange=reattach_ex,
                        )
                        report["protection_rearmed"] += 1
                except Exception as e:
                    report["errors"].append(f"rearm {getattr(pos,'symbol', '?')}: {e}")

        # 5) Reconcile vs exchange (LIVE / gdy klucze)
        if self.reconciler and self.trader:
            try:
                paper = bool(getattr(config, "PAPER_TRADING", True))
                if not paper or bool(getattr(config, "RECOVERY_RECONCILE_IN_PAPER", False)):
                    rec = self.reconciler.reconcile(self.trader.positions, executor=self.executor,
                                                    protection=self.protection)
                    report["reconcile"] = {
                        "in_sync": rec.get("in_sync"),
                        "only_local": len(rec.get("only_local") or []),
                        "only_exchange": len(rec.get("only_exchange") or []),
                    }
                    print(self.reconciler.summary_text(rec))
                    # Orphan na giełdzie – tylko log (nie zamykaj automatycznie bez flagi)
                    if rec.get("only_exchange") and bool(getattr(config, "RECOVERY_WARN_ORPHANS", True)):
                        if self.risk:
                            self.risk.is_halted = True
                            self.risk.paused = True
                            self.risk.halt_reason = "RECOVERY_ORPHAN_EXCHANGE"
                        for o in rec["only_exchange"]:
                            print(
                                f"[Recovery] ORPHAN exchange {o.get('symbol')} {o.get('direction')} "
                                f"size={o.get('size')} – ręczna decyzja / kolejny etap"
                            )
                    if self.executor and bool(getattr(config, "AUTO_CANCEL_ORPHAN_ORDERS", True)):
                        active = [self.reconciler._norm_symbol(p) for p in self.reconciler.fetch_exchange_positions()]
                        report["orphan_orders_canceled"] = self.executor.cancel_orphan_orders(active)
            except Exception as e:
                report["errors"].append(f"reconcile: {e}")

        # 6) Timeout orders z executora – odśwież
        if self.executor and bool(getattr(config, "LIVE_EXECUTION_ENABLED", False)):
            try:
                for oid, order in list(getattr(self.executor, "orders", {}).items()):
                    if getattr(order, "timeout", False) or str(getattr(order, "state", "")) in (
                        "TIMEOUT", "UNKNOWN", "SUBMITTING", "SUBMITTED", "PARTIAL"
                    ):
                        self.executor.refresh_order(order)
            except Exception as e:
                report["errors"].append(f"order_refresh: {e}")

        self.last_report = report
        print(
            f"[Recovery] done: pos={report['positions_restored']} "
            f"protect={report['protection_loaded']}/{report['protection_rearmed']} "
            f"kill={report['kill_switch']} errors={len(report['errors'])}"
        )
        return report
