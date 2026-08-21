# ============================================================
# ETAP 2 — Protection
# 10. exchange-side SL
# 11. exchange-side TP (gdy ma sens)
# 12. emergency local protection
# 13. (recovery → restart_recovery.py)
# 14. kill switch
# ============================================================
# LIVE_EXECUTION_ENABLED=False → metody exchange-side są no-op / dry-run.
# Lokalna ochrona działa zawsze (paper + live).
# ============================================================

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List

import config


@dataclass
class ProtectionAttach:
    """Stan ochrony dla jednej pozycji."""
    symbol: str
    direction: str
    sl_price: Optional[float] = None
    structural_sl: Optional[float] = None
    tp_price: Optional[float] = None
    size_contracts: Optional[float] = None
    # exchange
    exchange_sl_ok: bool = False
    exchange_tp_ok: bool = False
    tpsl_id: Optional[str] = None
    client_order_id: Optional[str] = None
    last_error: Optional[str] = None
    # local emergency
    local_sl_armed: bool = True
    local_tp_armed: bool = False   # TP exchange preferowane; lokalne opcjonalne
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProtectionAttach":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


class ProtectionManager:
    """
    Zarządza SL/TP po stronie giełdy + lokalnym failsafe.
    """

    def __init__(self, executor=None, registry=None):
        self.executor = executor
        self.registry = registry
        self.by_key: Dict[str, ProtectionAttach] = {}  # "BTC:LONG" → attach
        self.kill_switch_active = False
        self.kill_reason: Optional[str] = None
        self._state_path = Path(__file__).resolve().parent / "logs" / "protection_state.json"

    @staticmethod
    def _key(symbol: str, direction: str) -> str:
        return f"{(symbol or '').upper()}:{(direction or '').upper()}"

    def live_exec(self) -> bool:
        return bool(getattr(config, "LIVE_EXECUTION_ENABLED", False)) and self.executor is not None

    # ------------------------------------------------------------------
    # 10 + 11  Exchange-side SL / TP
    # ------------------------------------------------------------------
    def attach_protection(
        self,
        symbol: str,
        direction: str,
        sl_price: float = None,
        tp_price: float = None,
        size_contracts: float = None,
        entry_price: float = None,
    ) -> ProtectionAttach:
        """
        Ustaw ochronę pozycji.
        - Zawsze arm lokalny SL (emergency).
        - Exchange TPSL tylko gdy LIVE_EXECUTION_ENABLED.
        - TP na giełdzie tylko gdy EXCHANGE_TP_ENABLED i tp_price sensowne.
        """
        key = self._key(symbol, direction)
        # quantize prices
        if self.registry:
            if sl_price is not None:
                q = self.registry.quantize_price(symbol, sl_price)
                if q is not None:
                    sl_price = q
            if tp_price is not None:
                q = self.registry.quantize_price(symbol, tp_price)
                if q is not None:
                    tp_price = q

        att = self.by_key.get(key) or ProtectionAttach(
            symbol=symbol.upper(), direction=direction.upper()
        )
        att.sl_price = sl_price
        if att.structural_sl is None and sl_price is not None:
            att.structural_sl = sl_price
        att.tp_price = tp_price
        att.size_contracts = size_contracts
        att.local_sl_armed = sl_price is not None
        # TP lokalne tylko jako backup gdy brak exchange TP
        att.local_tp_armed = False
        att.updated_at = time.time()

        use_exchange_tp = (
            bool(getattr(config, "EXCHANGE_TP_ENABLED", True))
            and tp_price is not None
            and self._tp_makes_sense(direction, entry_price, tp_price)
        )
        att.local_tp_armed = (not use_exchange_tp) and tp_price is not None and bool(
            getattr(config, "LOCAL_TP_BACKUP", False)
        )

        exchange_sl_on = bool(getattr(config, "EXCHANGE_SL_ENABLED", True))
        require_fill_size = bool(getattr(config, "PROTECTION_REQUIRE_FILL_SIZE", True))
        has_fill_size = size_contracts is not None and float(size_contracts or 0) > 0
        can_exchange = (
            self.live_exec()
            and exchange_sl_on
            and (sl_price is not None or use_exchange_tp)
            and (has_fill_size or not require_fill_size)
        )
        if self.live_exec() and exchange_sl_on and require_fill_size and not has_fill_size:
            att.last_error = "WAIT_FILL_SIZE"
            att.local_sl_armed = True
            print(f"[Protect] Exchange TPSL odłożone – brak filled size dla {symbol}")

        if can_exchange:
            result = self._place_exchange_tpsl(
                symbol=symbol,
                direction=direction,
                sl_price=sl_price if exchange_sl_on else None,
                tp_price=tp_price if use_exchange_tp else None,
                size_contracts=size_contracts,
            )
            att.exchange_sl_ok = bool(result.get("sl_ok"))
            att.exchange_tp_ok = bool(result.get("tp_ok"))
            att.tpsl_id = result.get("tpsl_id")
            att.client_order_id = result.get("client_order_id")
            att.last_error = result.get("error")
            if not att.exchange_sl_ok:
                att.local_sl_armed = True
                print(f"[Protect] Exchange SL FAIL {symbol} → local SL armed @ {sl_price}")
            else:
                print(f"[Protect] Exchange SL OK {symbol} @ {sl_price}")
            if use_exchange_tp and att.exchange_tp_ok:
                print(f"[Protect] Exchange TP OK {symbol} @ {tp_price}")
            elif use_exchange_tp:
                print(f"[Protect] Exchange TP FAIL {symbol}: {att.last_error}")
        else:
            att.exchange_sl_ok = False
            att.exchange_tp_ok = False
            if sl_price is not None:
                print(f"[Protect] Local SL armed {symbol} {direction} @ {sl_price}")

        self.by_key[key] = att
        self.save_state()
        return att

    def _tp_makes_sense(self, direction: str, entry: float, tp: float) -> bool:
        """TP ma sens tylko gdy po właściwej stronie wejścia i min. dystans."""
        if entry is None or tp is None or entry <= 0:
            return True  # nie blokuj gdy brak entry
        try:
            entry, tp = float(entry), float(tp)
        except (TypeError, ValueError):
            return False
        min_pct = float(getattr(config, "EXCHANGE_TP_MIN_DISTANCE_PCT", 0.3)) / 100.0
        if direction.upper() == "LONG":
            if tp <= entry:
                return False
            return (tp - entry) / entry >= min_pct
        else:
            if tp >= entry:
                return False
            return (entry - tp) / entry >= min_pct

    def _place_exchange_tpsl(
        self,
        symbol: str,
        direction: str,
        sl_price: float = None,
        tp_price: float = None,
        size_contracts: float = None,
    ) -> dict:
        """
        POST /api/v1/trade/order-tpsl
        side = przeciwny do pozycji (zamknięcie).
        """
        if not self.executor:
            return {"error": "NO_EXECUTOR"}
        spec = None
        if self.registry:
            spec = self.registry.get(symbol)
        inst_id = spec.inst_id if spec else f"{symbol.upper()}-USDT"
        # zamknięcie LONG = sell, SHORT = buy
        side = "sell" if direction.upper() == "LONG" else "buy"
        from order_models import new_client_order_id
        cid = new_client_order_id("PT")
        body = {
            "instId": inst_id,
            "marginMode": getattr(config, "BLOFIN_MARGIN_MODE", "cross"),
            "positionSide": getattr(config, "BLOFIN_POSITION_SIDE", "net"),
            "side": side,
            "reduceOnly": "true",
            "clientOrderId": cid,
            "size": str(size_contracts) if size_contracts else "-1",  # -1 = cała pozycja (gdy wspiera)
        }
        # niektóre API wymagają size > 0; fallback na min_size
        if not size_contracts and spec:
            body["size"] = str(spec.min_size)

        if sl_price is not None:
            body["slTriggerPrice"] = str(sl_price)
            body["slOrderPrice"] = "-1"  # market
            body["slTriggerPriceType"] = getattr(config, "TPSL_TRIGGER_TYPE", "last")
        if tp_price is not None:
            body["tpTriggerPrice"] = str(tp_price)
            body["tpOrderPrice"] = "-1"
            body["tpTriggerPriceType"] = getattr(config, "TPSL_TRIGGER_TYPE", "last")

        if "slTriggerPrice" not in body and "tpTriggerPrice" not in body:
            return {"error": "NO_SL_OR_TP"}

        resp = self.executor._request("POST", "/api/v1/trade/order-tpsl", body=body)
        out = {"client_order_id": cid, "sl_ok": False, "tp_ok": False}
        if resp.get("timeout"):
            out["error"] = "TIMEOUT_TPSL"
            # niepewność – lokalny SL zostaje
            return out
        if not resp.get("ok"):
            out["error"] = resp.get("error") or resp.get("msg")
            return out
        data = resp.get("data")
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
        out["tpsl_id"] = str(row.get("tpslId") or row.get("algoId") or row.get("orderId") or "") or None
        out["sl_ok"] = sl_price is not None
        out["tp_ok"] = tp_price is not None
        out["raw"] = row
        return out


    def update_exchange_sl(
        self,
        symbol: str,
        direction: str,
        new_sl: float,
        size_contracts: float = None,
    ) -> bool:
        """
        Trailing → podnieś exchange SL:
        cancel stare TPSL + place nowe z new_sl (gdy LIVE + EXCHANGE_SL).
        Lokalny SL aktualizowany zawsze.
        """
        key = self._key(symbol, direction)
        att = self.by_key.get(key)
        if att is None:
            att = ProtectionAttach(symbol=symbol.upper(), direction=direction.upper())
            self.by_key[key] = att
        if self.registry and new_sl is not None:
            q = self.registry.quantize_price(symbol, new_sl)
            if q is not None:
                new_sl = q
        old_sl = att.sl_price
        att.sl_price = new_sl
        att.local_sl_armed = new_sl is not None
        if size_contracts is not None:
            att.size_contracts = size_contracts
        att.updated_at = time.time()

        if not (self.live_exec() and bool(getattr(config, "EXCHANGE_SL_ENABLED", True))):
            self.save_state()
            return False
        if new_sl is None:
            self.save_state()
            return False
        # bez size – nie wysyłaj (WAIT_FILL)
        if bool(getattr(config, "PROTECTION_REQUIRE_FILL_SIZE", True)):
            if not att.size_contracts or float(att.size_contracts) <= 0:
                att.last_error = "WAIT_FILL_SIZE"
                self.save_state()
                return False

        # cancel poprzednie
        try:
            self.cancel_exchange_protection(symbol, direction)
        except Exception as e:
            print(f"[Protect] cancel before trail SL: {e}")

        result = self._place_exchange_tpsl(
            symbol=symbol,
            direction=direction,
            sl_price=new_sl,
            tp_price=att.tp_price if bool(getattr(config, "EXCHANGE_TP_ENABLED", False)) else None,
            size_contracts=att.size_contracts,
        )
        att.exchange_sl_ok = bool(result.get("sl_ok"))
        att.tpsl_id = result.get("tpsl_id")
        att.client_order_id = result.get("client_order_id")
        att.last_error = result.get("error")
        if att.exchange_sl_ok:
            print(f"[Protect] Trail SL updated {symbol} {old_sl} → {new_sl}")
        else:
            att.local_sl_armed = True
            print(f"[Protect] Trail SL exchange FAIL {symbol}: {att.last_error} (local OK)")
        self.save_state()
        return att.exchange_sl_ok

    def cancel_exchange_protection(self, symbol: str, direction: str) -> bool:
        att = self.by_key.get(self._key(symbol, direction))
        if not att or not att.tpsl_id or not self.live_exec():
            return False
        body = [{
            "instId": f"{symbol.upper()}-USDT",
            "tpslId": att.tpsl_id,
            "clientOrderId": att.client_order_id or "",
        }]
        resp = self.executor._request("POST", "/api/v1/trade/cancel-tpsl", body=body)
        if resp.get("ok"):
            att.exchange_sl_ok = False
            att.exchange_tp_ok = False
            att.tpsl_id = None
            self.save_state()
            return True
        return False

    # ------------------------------------------------------------------
    # 12. Emergency local protection
    # ------------------------------------------------------------------
    def check_local_protection(
        self,
        symbol: str,
        direction: str,
        current_price: float,
    ) -> Optional[str]:
        """
        Zwraca powód zamknięcia gdy lokalny SL/TP trafiony.
        exchange_sl_ok=True → lokalny SL i tak monitoruje jako failsafe
        (można wyłączyć: LOCAL_SL_ALWAYS=False gdy exchange OK).
        """
        att = self.by_key.get(self._key(symbol, direction))
        if not att or current_price is None:
            return None
        try:
            px = float(current_price)
        except (TypeError, ValueError):
            return None

        always_local = bool(getattr(config, "LOCAL_SL_ALWAYS", True))
        use_local_sl = att.local_sl_armed and (always_local or not att.exchange_sl_ok)
        if use_local_sl and att.sl_price is not None:
            structural = float(att.structural_sl if att.structural_sl is not None else att.sl_price)
            buf_pct = float(getattr(config, "EMERGENCY_SL_BUFFER_PCT", 0.15) or 0.15) / 100.0
            if direction.upper() == "LONG":
                emergency = min(float(att.sl_price), structural) * (1.0 - buf_pct)
                if px <= emergency:
                    return "local_emergency_sl"
            if direction.upper() == "SHORT":
                emergency = max(float(att.sl_price), structural) * (1.0 + buf_pct)
                if px >= emergency:
                    return "local_emergency_sl"

        if att.local_tp_armed and att.tp_price is not None:
            if direction.upper() == "LONG" and px >= float(att.tp_price):
                return "local_emergency_tp"
            if direction.upper() == "SHORT" and px <= float(att.tp_price):
                return "local_emergency_tp"
        return None

    def check_all_local(self, positions: list, price_map: dict) -> List[dict]:
        """Lista {pos, reason} do zamknięcia przez lokalny failsafe."""
        hits = []
        for pos in positions or []:
            sym = getattr(pos, "symbol", None) or (pos.get("symbol") if isinstance(pos, dict) else None)
            direction = getattr(pos, "direction", None) or (pos.get("direction") if isinstance(pos, dict) else None)
            if not sym:
                continue
            px = price_map.get(sym)
            reason = self.check_local_protection(sym, direction, px)
            if reason:
                hits.append({"pos": pos, "reason": reason, "price": px})
        return hits

    def disarm(self, symbol: str, direction: str):
        key = self._key(symbol, direction)
        self.by_key.pop(key, None)
        self.save_state()

    # ------------------------------------------------------------------
    # 14. Kill switch
    # ------------------------------------------------------------------
    def activate_kill_switch(self, reason: str = "manual") -> dict:
        """
        HARD STOP:
        - risk halted + paused
        - flaga kill_switch_active
        - zapis stanu
        Zamykanie pozycji: caller (trader.close_all / executor).
        """
        self.kill_switch_active = True
        self.kill_reason = reason
        result = {"kill": True, "reason": reason, "closed": [], "errors": []}
        print(f"[KILL SWITCH] AKTYWNY: {reason}")
        try:
            # plik znacznika
            p = Path(__file__).resolve().parent / "KILL_SWITCH"
            p.write_text(reason, encoding="utf-8")
        except Exception as e:
            result["errors"].append(str(e))
        self.save_state()
        return result

    def clear_kill_switch(self) -> str:
        """Wymaga świadomego odblokowania (nie przy zwykłym START)."""
        self.kill_switch_active = False
        self.kill_reason = None
        try:
            p = Path(__file__).resolve().parent / "KILL_SWITCH"
            if p.exists():
                p.unlink()
        except Exception:
            pass
        self.save_state()
        return "KILL_CLEARED"

    def is_killed(self) -> bool:
        if self.kill_switch_active:
            return True
        try:
            if (Path(__file__).resolve().parent / "KILL_SWITCH").exists():
                self.kill_switch_active = True
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    def save_state(self):
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "ts": time.time(),
                "kill_switch_active": self.kill_switch_active,
                "kill_reason": self.kill_reason,
                "attachments": {k: v.to_dict() for k, v in self.by_key.items()},
            }
            self._state_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"[Protect] save_state: {e}")

    def load_state(self):
        try:
            if not self._state_path.exists():
                return
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.kill_switch_active = bool(data.get("kill_switch_active"))
            self.kill_reason = data.get("kill_reason")
            self.by_key = {}
            for k, v in (data.get("attachments") or {}).items():
                try:
                    self.by_key[k] = ProtectionAttach.from_dict(v)
                except Exception:
                    pass
            print(f"[Protect] Loaded {len(self.by_key)} attachments, kill={self.kill_switch_active}")
        except Exception as e:
            print(f"[Protect] load_state: {e}")
