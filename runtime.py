# ============================================================
# CryptoEdge Runtime - wspolny stan aplikacji
# ============================================================

from __future__ import annotations
import threading
import time
from typing import Optional, Dict, Any


class BotRuntime:
    """Singleton – bot + dashboard dzielą ten sam stan."""

    _instance: Optional["BotRuntime"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.feeder = None
        self.signal_engine = None
        self.risk = None
        self.trader = None
        self.logger = None
        self.running = False
        self.engine_enabled = False   # analiza: cykle / sygnały / dashboard
        self.trading_enabled = False  # handel: otwieranie pozycji
        self.cycle = 0
        self.last_price_map: Dict[str, float] = {}
        self.last_price_map_ts: float = 0.0
        self.last_full_scan_ts: float = 0.0
        self.last_coins: list = []
        self.last_signals: list = []
        self.last_error: Optional[str] = None
        self.app_mode = True
        self.started_at: Optional[float] = None  # time.time() start analizy
        self.trading_started_at: Optional[float] = None
        self.last_heartbeat: float = 0.0
        self.last_state_snapshot: Dict[str, Any] = {}
        self.last_completed_cycle: int = 0
        self.analysis_loading: bool = False
        # Wybudza petle natychmiast po akcji UI; nie czekamy na koniec interwalu.
        self.analysis_wakeup = threading.Event()

    @classmethod
    def get(cls) -> "BotRuntime":
        with cls._lock:
            if cls._instance is None:
                cls._instance = BotRuntime()
            return cls._instance

    def attach(self, feeder, signal_engine, risk, trader, logger):
        self.feeder = feeder
        self.signal_engine = signal_engine
        self.risk = risk
        self.trader = trader
        self.logger = logger
        # opcjonalne – ustawiane w app.py
        if not hasattr(self, "protection"):
            self.protection = None
        if not hasattr(self, "executor"):
            self.executor = None
        if not hasattr(self, "reconciler"):
            self.reconciler = None

    @staticmethod
    def _is_hard_halt(reason: str) -> bool:
        """Halts that a generic Start/Resume action must never clear."""
        value = (reason or "").lower()
        return any(token in value for token in (
            "daily", "drawdown", "kill_switch", "kill switch",
            "reconcile", "drift", "orphan", "recovery",
        ))

    def start_analysis(self) -> str:
        """Tylko analiza: cykle, sygnały, dashboard — BEZ otwierania pozycji."""
        import time as _t
        import config as _cfg
        import settings_store as _settings
        _settings.apply_settings()
        self.engine_enabled = True
        # Analiza NIE gasi handlu, jeśli już leci (kolejność przycisków).
        keep_trade = bool(self.trading_enabled)
        if not keep_trade:
            self.trading_enabled = False
        self.analysis_loading = True
        self.running = True
        self.last_full_scan_ts = 0.0  # wymuś pełny skan zaraz po starcie, nie szybki tick na pustym cache
        if not self.started_at:
            self.started_at = _t.time()
        if self.risk and not self.trading_enabled:
            self.risk.paused = True
        self.analysis_wakeup.set()
        mode = "DEMO" if getattr(_cfg, "PAPER_TRADING", True) else "LIVE"
        strategy = str(getattr(_cfg, "STRATEGY_MODE", "DAYTRADING") or "DAYTRADING").upper()
        return f"ANALYSIS_ON ({mode}/{strategy}) – bez handlu"

    def start_trading(self) -> str:
        """Analiza + handel (otwieranie pozycji)."""
        import time as _t
        import config as _cfg
        import settings_store as _settings
        _settings.apply_settings()
        self.engine_enabled = True
        self.trading_enabled = True
        # START BOT moze byc pierwsza akcja; wtedy rowniez czekamy na pelny cykl.
        if self.last_completed_cycle < self.cycle or self.cycle == 0:
            self.analysis_loading = True
        self.running = True
        if not self.started_at:
            self.started_at = _t.time()
        if not self.trading_started_at:
            self.trading_started_at = _t.time()
        if self.risk:
            self.risk.paused = False
            hard = self.risk.halt_reason or ""
            if self.risk.is_halted and not self._is_hard_halt(hard):
                self.risk.is_halted = False
                self.risk.halt_reason = None
            if self.trader:
                try:
                    self.risk.sync_open_count(len(self.trader.positions))
                except Exception:
                    pass
            if bool(getattr(_cfg, "PAPER_TRADING", True)):
                try:
                    if float(self.risk.current_capital or 0) < 1:
                        self.risk.apply_demo_capital()
                except Exception:
                    pass
        self.analysis_wakeup.set()
        mode = "DEMO" if getattr(_cfg, "PAPER_TRADING", True) else "LIVE"
        strategy = str(getattr(_cfg, "STRATEGY_MODE", "DAYTRADING") or "DAYTRADING").upper()
        return f"TRADING_ON ({mode}/{strategy})"

    def start_engine(self) -> str:
        """Kompatybilność: pełny start = analiza + handel."""
        return self.start_trading()

    def stop_trading(self) -> str:
        """Wyłącz tylko handel – analiza może dalej działać."""
        self.trading_enabled = False
        self.trading_started_at = None
        if self.risk:
            self.risk.paused = True
        self.analysis_wakeup.set()
        return "TRADING_OFF – analiza kontynuuje"

    def stop_engine(self) -> str:
        """
        STOP: brak nowych cykli + zamknij pozycje na plusie;
        neutralne/minus → TP +5% PnL, SL bez zmian.
        """
        self.engine_enabled = False
        self.trading_enabled = False
        self.analysis_loading = False
        self.started_at = None
        self.trading_started_at = None
        if self.risk:
            self.risk.paused = True
        summary = ""
        try:
            if self.trader:
                tp_pct = float(getattr(__import__("config"), "STOP_ENGINE_TP_PCT", 5.0))
                age_s = max(0.0, time.time() - float(self.last_price_map_ts or 0.0)) if self.last_price_map_ts else float("inf")
                res = self.trader.on_engine_stop(self.last_price_map or {}, take_profit_pct=tp_pct,
                                                 price_map_age_s=age_s)
                n_c = len(res.get("closed_profit") or [])
                n_k = len(res.get("kept_with_tp") or [])
                stale_note = " | ceny nieswieze - nic nie zamknieto" if res.get("prices_stale") else ""
                summary = f" | +zamknieto {n_c}, zostawiono {n_k} (TP {tp_pct:.0f}%){stale_note}"
        except Exception as e:
            print(f"[STOP] on_engine_stop error: {e}")
            summary = f" | (adjust error: {e})"
        return "ENGINE_OFF" + summary

    def kill_switch(self, reason: str = "manual") -> str:
        """
        HARD kill:
        1) halt/pause natychmiast (zero nowych wejść)
        2) zrzut stanu pozycji PRZED czyszczeniem
        3) LIVE: emergency close na giełdzie + confirm reconcile
        4) dopiero potem lokalne close_all / disarm protection
        """
        import config as _cfg
        reason = reason or "manual"
        self.engine_enabled = False
        if self.risk:
            self.risk.is_halted = True
            self.risk.halt_reason = f"KILL_SWITCH: {reason}"
            self.risk.paused = True

        # 2) snapshot pozycji zanim cokolwiek czyścimy
        positions_snapshot = list(getattr(self.trader, "positions", []) or [])
        syms = list({getattr(p, "symbol", None) for p in positions_snapshot if getattr(p, "symbol", None)})
        if not syms and getattr(self, "protection", None):
            try:
                syms = list({k.split(":")[0] for k in self.protection.by_key})
            except Exception:
                pass

        exchange_results = []
        if bool(getattr(_cfg, "LIVE_EXECUTION_ENABLED", False)) and getattr(self, "executor", None):
            try:
                exchange_results = self.executor.emergency_close_all(
                    syms,
                    reconciler=getattr(self, "reconciler", None),
                )
                not_flat = [r for r in exchange_results if not r.get("confirmed_flat")]
                if not_flat:
                    print(f"[KILL] UWAGA: niepotwierdzone flat: {not_flat}")
            except Exception as e:
                print(f"[KILL] exchange close error: {e}")

        closed = 0
        try:
            if self.trader and hasattr(self.trader, "close_all"):
                n = self.trader.close_all(self.last_price_map or {}, reason="kill_switch")
                closed = n if isinstance(n, int) else len(n or [])
            elif self.trader:
                for pos in list(getattr(self.trader, "positions", []) or []):
                    px = (self.last_price_map or {}).get(pos.symbol) or pos.entry_price
                    self.trader.close_position(pos, float(px), "kill_switch")
                    closed += 1
        except Exception as e:
            print(f"[KILL] local close error: {e}")

        if getattr(self, "protection", None):
            try:
                self.protection.activate_kill_switch(reason)
            except Exception as e:
                print(f"[KILL] protection flag error: {e}")

        confirmed = sum(1 for r in exchange_results if r.get("confirmed_flat"))
        return (
            f"KILL_SWITCH ({reason}) local_closed≈{closed} "
            f"exchange_confirmed={confirmed}/{len(exchange_results)}"
        )

    def pause(self) -> str:
        if not self.risk:
            return "Brak runtime"
        self.risk.paused = True
        return "PAUSED"

    def resume(self) -> str:
        """
        Wznowienie nowych wejsc.
        Twardych haltow (daily loss / max drawdown) NIE kasujemy –
        wymagaja nowego dnia / restartu / recznej interwencji.
        """
        if not self.risk:
            return "Brak runtime"
        self.risk.paused = False
        hard = (self.risk.halt_reason or "")
        hard_l = hard.lower()
        if self.risk.is_halted and self._is_hard_halt(hard):
            return f"RESUMED (pause off; hard halt nadal aktywny: {hard})"
        # Miekkie halty / pozostale – pozwol na trade
        if self.risk.is_halted and hard and not self._is_hard_halt(hard):
            self.risk.is_halted = False
            self.risk.halt_reason = None
            return "RESUMED (wyczyszczono miekkie zatrzymanie)"
        return "RESUMED"

    def close_all(self) -> str:
        if not self.trader:
            return "Brak runtime"
        with self.trader.lock:
            n = len(self.trader.positions)
            self.trader.close_all(self.last_price_map or {}, reason="close_all")
        return f"CLOSED {n}"

    def close_symbol(self, symbol: str) -> str:
        """Ręczne zamknięcie jednej pozycji DEMO (market z last_price_map)."""
        if not self.trader:
            return "Brak runtime"
        symbol = (symbol or "").strip()
        if not symbol or symbol in ("—", "brak pozycji"):
            return "Brak symbolu"
        pnl = self.trader.close_by_symbol(symbol, self.last_price_map or {}, reason="manual")
        if pnl is None:
            return f"Brak pozycji {symbol}"
        return f"CLOSED {symbol} PnL ${pnl:+.4f}"

    def snapshot(self) -> Dict[str, Any]:
        if not self.risk:
            return {"running": self.running, "error": "not_started"}
        st = self.risk.get_status()
        if self.trader:
            st.update(self.trader.get_funds_breakdown())
            st["metrics"] = self.trader.session_metrics()
        st["running"] = self.running
        st["engine_enabled"] = bool(self.engine_enabled)
        st["analysis_loading"] = bool(self.analysis_loading)
        st["last_completed_cycle"] = int(self.last_completed_cycle)
        st["cycle"] = self.cycle
        st["last_error"] = self.last_error
        if self.engine_enabled and self.started_at:
            st["uptime_sec"] = int(time.time() - self.started_at)
        else:
            st["uptime_sec"] = 0
        return st

    def mark_started(self):
        if self.started_at is None:
            self.started_at = time.time()

    def heartbeat(self):
        self.last_heartbeat = time.time()
