# ============================================================
# InstrumentRegistry – specyfikacja instrumentów Blofin SWAP
# tickSize, lotSize, minSize, maxMarketSize, contractValue
# ============================================================

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List

import config


@dataclass
class InstrumentSpec:
    inst_id: str                    # BTC-USDT
    symbol: str                     # BTC
    base: str = "BTC"
    quote: str = "USDT"
    contract_value: float = 1.0     # wartość 1 kontraktu w base (np. 0.001 BTC)
    tick_size: float = 0.01
    lot_size: float = 1.0
    min_size: float = 1.0
    max_limit_size: float = 1e12
    max_market_size: float = 1e12
    max_leverage: float = 20.0
    state: str = "live"
    contract_type: str = "linear"   # linear | inverse
    raw: dict = field(default_factory=dict)

    @property
    def is_tradable(self) -> bool:
        return (self.state or "").lower() in ("live", "online", "trading", "")


class InstrumentRegistry:
    """
    Cache instrumentów z GET /api/v1/market/instruments.
    Konwersja notional USD ↔ liczba kontraktów + walidacja tick/lot/min/max.
    """

    def __init__(self, feeder=None, ttl_seconds: float = 3600.0):
        self.feeder = feeder
        self.ttl = ttl_seconds
        self._by_inst: Dict[str, InstrumentSpec] = {}
        self._by_symbol: Dict[str, InstrumentSpec] = {}
        self._loaded_at = 0.0
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    def ensure_loaded(self, force: bool = False) -> bool:
        if not force and self._by_inst and (time.time() - self._loaded_at) < self.ttl:
            return True
        return self.reload()

    def reload(self) -> bool:
        try:
            data = self._fetch_instruments()
            if not data:
                self.last_error = self.last_error or "brak danych instruments"
                return bool(self._by_inst)  # zostaw stary cache
            by_inst: Dict[str, InstrumentSpec] = {}
            by_sym: Dict[str, InstrumentSpec] = {}
            for row in data:
                spec = self._parse_row(row)
                if not spec:
                    continue
                by_inst[spec.inst_id] = spec
                by_sym[spec.symbol.upper()] = spec
            if by_inst:
                self._by_inst = by_inst
                self._by_symbol = by_sym
                self._loaded_at = time.time()
                self.last_error = None
                return True
            self.last_error = "pusta lista instruments"
            return False
        except Exception as e:
            self.last_error = str(e)
            return bool(self._by_inst)

    def _fetch_instruments(self) -> List[dict]:
        """Public endpoint – bez auth. Ta sama ścieżka co DataFeeder (jeden last_error)."""
        feed = getattr(self.feeder, "blofin", None) if self.feeder is not None else None
        if feed is not None:
            if hasattr(feed, "fetch_instruments"):
                payload = feed.fetch_instruments()
            elif hasattr(feed, "_get"):
                payload = feed._get("market/instruments")
            else:
                payload = None
            rows = (payload or {}).get("data") or []
            if rows:
                return rows
            self.last_error = getattr(feed, "last_error", None) or "brak danych instruments"
            return []
        import requests
        from blofin_feed import configure_blofin_session
        url = "https://openapi.blofin.com/api/v1/market/instruments"
        sess = requests.Session()
        configure_blofin_session(sess)
        # SWAP USDT-M
        for params in ({"instType": "SWAP"}, None):
            try:
                r = sess.get(url, params=params, timeout=15)
                if r.status_code != 200:
                    self.last_error = f"instruments HTTP {r.status_code}"
                    continue
                payload = r.json()
                code = payload.get("code")
                if str(code) not in ("0", "success") and code != 0:
                    self.last_error = f"instruments code={code} {payload.get('msg')}"
                    continue
                rows = payload.get("data") or []
                if rows:
                    return rows
            except Exception as e:
                self.last_error = f"instruments err: {e}"
        return []

    def _parse_row(self, row: dict) -> Optional[InstrumentSpec]:
        try:
            inst = str(row.get("instId") or "")
            if not inst or "-" not in inst:
                return None
            base, quote = inst.split("-", 1)
            from universe_policy import crypto_perpetual_allowed
            if not crypto_perpetual_allowed(base, row):
                return None
            # tylko USDT linear swap
            if quote.upper() != "USDT":
                return None
            ct = (row.get("contractType") or row.get("instType") or "linear").lower()
            if "inverse" in ct:
                return None

            def f(key, default=0.0):
                try:
                    v = row.get(key)
                    return float(v) if v is not None and v != "" else float(default)
                except (TypeError, ValueError):
                    return float(default)

            return InstrumentSpec(
                inst_id=inst,
                symbol=base.upper(),
                base=base.upper(),
                quote=quote.upper(),
                contract_value=f("contractValue", 1.0) or 1.0,
                tick_size=f("tickSize", 0.01) or 0.01,
                lot_size=f("lotSize", 1.0) or 1.0,
                min_size=f("minSize", 1.0) or 1.0,
                max_limit_size=f("maxLimitSize", 1e12) or 1e12,
                max_market_size=f("maxMarketSize", 1e12) or 1e12,
                max_leverage=f("maxLeverage", 20) or 20,
                state=str(row.get("state") or "live"),
                contract_type="linear",
                raw=row,
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, symbol_or_inst: str) -> Optional[InstrumentSpec]:
        self.ensure_loaded()
        key = (symbol_or_inst or "").upper().strip()
        if not key:
            return None
        if key in self._by_inst:
            return self._by_inst[key]
        if key in self._by_symbol:
            return self._by_symbol[key]
        # BTCUSDT / BTC-USDT
        if key.endswith("USDT") and "-" not in key:
            return self._by_symbol.get(key[:-4]) or self._by_inst.get(f"{key[:-4]}-USDT")
        if "-" in key:
            return self._by_inst.get(key) or self._by_symbol.get(key.split("-")[0])
        return self._by_symbol.get(key)

    def count(self) -> int:
        return len(self._by_inst)

    # ------------------------------------------------------------------
    # Quantize helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _round_to_step(value: float, step: float, mode: str = "down") -> float:
        if step <= 0:
            return value
        n = value / step
        if mode == "up":
            n = math.ceil(n - 1e-12)
        elif mode == "nearest":
            n = round(n)
        else:
            n = math.floor(n + 1e-12)
        return round(n * step, 12)

    def quantize_price(self, symbol: str, price: float) -> Optional[float]:
        spec = self.get(symbol)
        if not spec or price is None:
            return None
        return self._round_to_step(float(price), spec.tick_size, mode="nearest")

    def quantize_size(self, symbol: str, size_contracts: float, mode: str = "down") -> Optional[float]:
        spec = self.get(symbol)
        if not spec or size_contracts is None:
            return None
        q = self._round_to_step(float(size_contracts), spec.lot_size, mode=mode)
        return q

    # ------------------------------------------------------------------
    # USD ↔ contracts
    # ------------------------------------------------------------------
    def notional_to_contracts(
        self,
        symbol: str,
        notional_usd: float,
        price: float,
        leverage: float = 1.0,
    ) -> dict:
        """
        notional_usd = wartość pozycji w USDT (margin * leverage ≈ size_usd).
        Dla linear USDT: contracts = notional / (price * contractValue)

        Zwraca:
          ok, contracts, contracts_raw, notional_usd, price, error, spec fields
        """
        spec = self.get(symbol)
        if not spec:
            return {"ok": False, "error": f"UNKNOWN_INSTRUMENT:{symbol}", "contracts": 0.0}
        if not spec.is_tradable:
            return {"ok": False, "error": f"NOT_TRADABLE:{spec.state}", "contracts": 0.0}
        try:
            price = float(price)
            notional_usd = float(notional_usd)
        except (TypeError, ValueError):
            return {"ok": False, "error": "BAD_NUMBER", "contracts": 0.0}
        if price <= 0 or notional_usd <= 0:
            return {"ok": False, "error": "NON_POSITIVE", "contracts": 0.0}

        cv = spec.contract_value if spec.contract_value > 0 else 1.0
        # 1 kontrakt ≈ price * contractValue USDT (linear)
        contract_usd = price * cv
        if contract_usd <= 0:
            return {"ok": False, "error": "BAD_CONTRACT_VALUE", "contracts": 0.0}

        raw = notional_usd / contract_usd
        contracts = self._round_to_step(raw, spec.lot_size, mode="down")
        # po zaokrągleniu – faktyczny notional do risk management
        actual_notional = float(contracts) * float(contract_usd)

        return {
            "ok": contracts > 0,
            "contracts_raw": raw,
            "contracts": contracts,
            "notional_usd": notional_usd,          # żądany
            "actual_notional_usd": actual_notional,  # po lot size
            "notional_delta_usd": actual_notional - notional_usd,
            "price": price,
            "contract_value": cv,
            "contract_usd": contract_usd,
            "lot_size": spec.lot_size,
            "min_size": spec.min_size,
            "max_market_size": spec.max_market_size,
            "inst_id": spec.inst_id,
            "leverage": leverage,
            "max_leverage": float(getattr(spec, "max_leverage", 0) or 0),
        }

    def contracts_to_notional(self, symbol: str, contracts: float, price: float) -> Optional[float]:
        spec = self.get(symbol)
        if not spec or price is None:
            return None
        cv = spec.contract_value if spec.contract_value > 0 else 1.0
        return float(contracts) * float(price) * cv

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate_order(
        self,
        symbol: str,
        size_contracts: float,
        price: Optional[float] = None,
        order_type: str = "market",
    ) -> dict:
        """
        Walidacja tick / lot / min / max.
        Zwraca: ok, size, price, errors[], adjusted
        """
        spec = self.get(symbol)
        errors: List[str] = []
        if not spec:
            return {"ok": False, "errors": [f"UNKNOWN_INSTRUMENT:{symbol}"], "size": 0, "price": price}

        size = float(size_contracts or 0)
        adj_size = self._round_to_step(size, spec.lot_size, mode="down")
        if adj_size != size:
            errors.append(f"LOT_ADJUST {size}→{adj_size}")
        size = adj_size

        if size < spec.min_size:
            errors.append(f"BELOW_MIN_SIZE {size}<{spec.min_size}")
        max_sz = spec.max_market_size if order_type == "market" else spec.max_limit_size
        if size > max_sz:
            errors.append(f"ABOVE_MAX_SIZE {size}>{max_sz}")

        adj_price = price
        if price is not None and order_type != "market":
            adj_price = self._round_to_step(float(price), spec.tick_size, mode="nearest")
            if abs(adj_price - float(price)) > 1e-12:
                errors.append(f"TICK_ADJUST {price}→{adj_price}")

        # błędy krytyczne vs informacyjne
        critical = [e for e in errors if e.startswith(("BELOW_MIN", "ABOVE_MAX", "UNKNOWN"))]
        ok = len(critical) == 0 and size > 0
        return {
            "ok": ok,
            "size": size,
            "price": adj_price,
            "errors": errors,
            "inst_id": spec.inst_id,
            "min_size": spec.min_size,
            "lot_size": spec.lot_size,
            "tick_size": spec.tick_size,
            "max_size": max_sz,
        }
