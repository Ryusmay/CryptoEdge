# ============================================================
# Sync konta Blofin – TYLKO ODCZYT (read-only API)
# ============================================================
# - saldo futures USDT (equity / available)
# - otwarte pozycje na giełdzie
# - ZERO zleceń, ZERO zamykania, ZERO otwierania
# ============================================================

import time
from typing import Optional, Dict, List, Any
import config


class AccountSync:
    """Read-only podgląd konta Blofin. Nigdy nie wysyła POST handlowych."""

    def __init__(self, feeder, risk):
        self.feeder = feeder
        self.risk = risk
        self._last_fetch = 0.0
        self._last_balance: Optional[Dict] = None
        self._last_positions: List[Dict] = []
        self._last_error = None
        self._last_positions_error = None

    def is_live(self) -> bool:
        return not bool(getattr(config, "PAPER_TRADING", True))

    def ready_for_live(self) -> bool:
        return bool(
            getattr(config, "BLOFIN_API_KEY", "")
            and getattr(config, "BLOFIN_API_SECRET", "")
            and getattr(config, "BLOFIN_API_PASSPHRASE", "")
        )

    def sync(self, force: bool = False) -> Optional[Dict]:
        """
        Paper: lokalny kapitał.
        Live: equity + pozycje z Blofin (GET only).
        """
        if not self.is_live():
            # DEMO: lokalny paper – nie nadpisuj z Blofin
            try:
                self.risk.apply_demo_capital()
            except Exception:
                pass
            eq = float(
                getattr(self.risk, "paper_capital", None)
                or self.risk.current_capital
                or config.STARTING_CAPITAL
            )
            return {
                "mode": "PAPER",
                "equity": eq,
                "available": eq,
                "source": "local",
                "positions": [],
                "positions_count": 0,
                "read_only": True,
            }

        if not self.ready_for_live():
            self._last_error = "Brak kluczy Blofin (Ustawienia → API Key/Secret/Passphrase)"
            return {
                "mode": "LIVE",
                "equity": None,
                "available": None,
                "source": "none",
                "error": self._last_error,
                "positions": [],
                "positions_count": 0,
                "read_only": True,
            }

        cache = float(getattr(config, "LIVE_BALANCE_CACHE_SECONDS", 15) or 15)
        if (
            not force
            and self._last_balance
            and (time.time() - self._last_fetch) < cache
        ):
            return self.snapshot()

        # --- balance ---
        bal = None
        try:
            bal = self.feeder.blofin.fetch_futures_balance()
        except Exception as e:
            self._last_error = str(e)

        if not bal:
            self._last_error = getattr(
                self.feeder.blofin, "last_error", None
            ) or self._last_error or "balance fail"
        else:
            equity = float(bal.get("equity") or 0)
            available = float(bal.get("available") or equity)
            if equity > 0:
                try:
                    # LIVE: pokazuj saldo giełdy; paper_capital zostaje nietknięty
                    self.risk.apply_live_equity(equity, available)
                except Exception:
                    try:
                        self.risk.current_capital = equity
                        self.risk.update_equity_peak(equity)
                    except Exception:
                        pass
                # Etap 4: ledger equity
                try:
                    if not hasattr(self, "ledger") or self.ledger is None:
                        from accounting import EquityLedger
                        self.ledger = EquityLedger(equity)
                    self.ledger.sync_exchange(equity, available)
                except Exception:
                    pass
            self._last_balance = {
                "mode": "LIVE",
                "equity": equity,
                "available": available,
                "currency": bal.get("currency") or "USDT",
                "source": "blofin",
                "read_only": True,
                "synced": True,
                "sync_ts": time.time(),
            }
            self._last_error = None

        # --- positions (osobny GET, błąd nie kasuje balance) ---
        try:
            self._last_positions = self.feeder.blofin.fetch_open_positions() or []
            self._last_positions_error = None
        except Exception as e:
            self._last_positions_error = str(e)
            # zostaw poprzednie

        self._last_fetch = time.time()
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        base = dict(self._last_balance) if self._last_balance else {
            "mode": "LIVE" if self.is_live() else "PAPER",
            "equity": None,
            "available": None,
            "source": "none",
        }
        base["positions"] = list(self._last_positions or [])
        base["positions_count"] = len(self._last_positions or [])
        base["read_only"] = True
        base["error"] = self._last_error
        base["positions_error"] = self._last_positions_error
        base["ts"] = self._last_fetch
        return base

    def get_exchange_positions(self) -> List[Dict]:
        return list(self._last_positions or [])
