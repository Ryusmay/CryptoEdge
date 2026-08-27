# ============================================================
# Paper Trader - TP / SL / Trailing Stop + dźwignia x3
# ============================================================

import math
import threading
import time
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import config
import uuid

def _is_v2(engine) -> bool:
    s = str(engine or "").upper()
    return s in ("DAYTRADING_V2", "DAYTRADINGV2")


class Position:
    def __init__(self, signal: Dict, size_usd: float, leverage: int = config.LEVERAGE):
        self.id = str(uuid.uuid4())[:8]
        self.symbol = signal["symbol"]
        self.direction = signal["direction"]
        self.entry_price = signal["price"]
        # 7. Price layers
        try:
            from price_layers import extract_price_layers
            layers = extract_price_layers(signal, execution_price=self.entry_price)
        except Exception:
            layers = {}
        self.strategy_price = layers.get("strategy_price") or self.entry_price
        self.decision_price = layers.get("decision_price") or self.strategy_price
        self.submitted_price = layers.get("submitted_price") or self.decision_price
        self.fill_price = layers.get("fill_price") or self.entry_price
        self.execution_price = layers.get("execution_price") or self.entry_price
        self.mark_price = layers.get("mark_price") or self.entry_price
        self.index_price = layers.get("index_price")
        self.basis_pct = layers.get("basis_pct")
        self.price_layers = layers
        self.size_usd = size_usd  # requested notional (przed lot)
        self.leverage = leverage
        self.margin = size_usd / leverage
        self.margin_mode = str(getattr(config, "BLOFIN_MARGIN_MODE", "isolated") or "isolated").lower()
        self.exchange_sl_planned = bool(signal.get("sl_price"))
        self.exchange_sl_price = signal.get("sl_price")
        # Shadow contracts – ta sama matematyka co LIVE (InstrumentRegistry)
        self.size_contracts = None
        self.actual_notional = size_usd
        self.contract_value = None
        try:
            from instrument_registry import InstrumentRegistry
            reg = getattr(type(self), "_shared_registry", None)
            # Never perform an unscoped network instrument lookup from the
            # fill path. Runtime injects the already-loaded registry through
            # PaperTrader/RiskManager; tests and degraded PAPER may proceed
            # without contract conversion.
            if reg is not None:
                price = float(signal.get("price") or 0)
                if price > 0:
                    conv = reg.notional_to_contracts(
                        signal.get("symbol") or "", size_usd, price, leverage=leverage
                    )
                    if conv.get("ok"):
                        self.size_contracts = float(conv["contracts"])
                        self.actual_notional = float(
                            conv.get("actual_notional_usd") or conv.get("notional_usd") or size_usd
                        )
                        if self.actual_notional <= 0:
                            self.actual_notional = size_usd
                        self.contract_value = conv.get("contract_value")
                        # size_usd / margin oparte o actual (spójne z LIVE risk)
                        if bool(getattr(config, "PAPER_USE_ACTUAL_NOTIONAL", True)):
                            self.size_usd = self.actual_notional
                            self.margin = self.size_usd / max(leverage, 1)
        except Exception as e:
            print(f"[Paper] shadow contracts skip: {e}")
        self.entry_time = datetime.now()
        self.strength = signal["strength"]
        self.reasons = signal.get("reasons", [])
        self.status = "OPEN"
        self.exit_price = None
        self.exit_time = None
        self.pnl = 0.0
        self.pnl_pct = 0.0
        self.partial_realized_pnl = 0.0
        self.partial_realized_fees = 0.0
        self.partial_realized_funding = 0.0

        # TP / SL poziomy
        self.tp_price = signal.get("tp_price")
        self.sl_price = signal.get("sl_price")
        self.engine = signal.get("engine") or signal.get("score_type") or "trend"
        self.v2_profile = signal.get("v2_profile")
        self.reversal_profile = signal.get("reversal_profile")
        self.market_regime = signal.get("market_regime")
        self.fill_kind = signal.get("fill_kind") or "market"
        self.entry_side = "maker" if str(self.fill_kind).lower() in ("maker", "resting_limit", "limit_maker") else "taker"
        self.exit_side = "taker"
        self.decision_id = signal.get("decision_id")
        self.decision_path = signal.get("decision_path")
        self.tp_plan = signal.get("tp_plan") if isinstance(signal.get("tp_plan"), dict) else None

        if self.tp_price is None or self.sl_price is None:
            # Uzupełniaj TYLKO brakującą nogę. V2 ma sl_price + tp1/tp2, bez
            # tp_price — stary `or` nadpisywał swing 1h na STOP_LOSS_PCT (~2.2%).
            has_structured_tp = signal.get("tp1_price") is not None or signal.get("tp2_price") is not None
            if self.direction == "LONG":
                if self.tp_price is None and not has_structured_tp:
                    self.tp_price = self.entry_price * (1 + config.TAKE_PROFIT_PCT / 100 / leverage)
                if self.sl_price is None:
                    self.sl_price = self.entry_price * (1 + config.STOP_LOSS_PCT / 100 / leverage)
            else:
                if self.tp_price is None and not has_structured_tp:
                    self.tp_price = self.entry_price * (1 - config.TAKE_PROFIT_PCT / 100 / leverage)
                if self.sl_price is None:
                    self.sl_price = self.entry_price * (1 - config.STOP_LOSS_PCT / 100 / leverage)

        self.initial_sl_price = float(self.sl_price) if self.sl_price is not None else None
        self.initial_risk_abs = (
            abs(float(self.entry_price) - self.initial_sl_price)
            if self.initial_sl_price is not None else 0.0
        )

        # PRIORYTET 9 — TP oparte o R:R (gdy sygnał ma plan)
        # TP1=1R, TP2=2R, reszta trailing; frakcje z tp_plan lub config
        self.tp1_price = signal.get("tp1_price")
        self.tp2_price = signal.get("tp2_price") or signal.get("tp_price")
        if self.tp_plan is None and self.sl_price and self.entry_price:
            risk = abs(self.entry_price - float(self.sl_price))
            if risk > 0:
                if self.direction == "LONG":
                    self.tp1_price = self.tp1_price or (self.entry_price + risk * 1.0)
                    self.tp2_price = self.tp2_price or (self.entry_price + risk * 2.0)
                else:
                    self.tp1_price = self.tp1_price or (self.entry_price - risk * 1.0)
                    self.tp2_price = self.tp2_price or (self.entry_price - risk * 2.0)
                self.tp_plan = {
                    "tp1_r": 1.0,
                    "tp2_r": 2.0,
                    "tp3": "trailing",
                    "frac_tp1": float(getattr(config, "REVERSAL_TP1_FRAC", 0.25)),
                    "frac_tp2": float(getattr(config, "REVERSAL_TP2_FRAC", 0.35)),
                    "frac_trail": float(getattr(config, "REVERSAL_TP3_FRAC", 0.40)),
                }
                if self.engine == "reversal":
                    self.tp_price = self.tp2_price  # hard TP = 2R; reszta trail

        # Trailing stop
        self.trailing_active = False
        self.trailing_stop_price = None
        self.live_atr = None  # odświeżany z bieżącego skanu; None = użyj atr z wejścia
        self.breakeven_active = False
        self._last_trailing_tighten_at = 0.0
        # Jazda z trendem – bez limitu TP
        self.signal_strength = float(signal.get("strength") or 0)
        self.atr = signal.get("atr")  # do trailing ATR
        st = signal.get("supertrend") or (signal.get("strategy") or {}).get("supertrend")
        if isinstance(st, dict):
            self.entry_supertrend = "up" if st.get("is_up") else "down"
        else:
            self.entry_supertrend = st  # "up"/"down"/None
        fr = (signal.get("funding") or {}).get("funding_rate")
        self.funding_rate = fr
        _fi = signal.get("funding") or {}
        self.funding_interval_h = float(_fi.get("funding_interval_h") or _fi.get("funding_interval") or 8)
        self.next_funding_ts = _fi.get("next_funding_ts")
        self.atr_pct = signal.get("atr_pct")
        self.funding_paid = 0.0
        self.slip_rt = None
        if str(self.engine) in ("daytrading_v2",) or str(signal.get("strategy_mode") or "") == "DAYTRADING_V2":
            try:
                from v2_profiles import paper_slip_round_trip
                self.slip_rt = paper_slip_round_trip(self.symbol, signal)
            except Exception:
                self.slip_rt = 0.0006
        self.partial_taken = False
        self.partial_tp1_done = False
        self.partial_tp2_done = False
        self.original_size = size_usd
        # partial TP: preferuj 1R z planu, inaczej legacy % PnL
        self.partial_tp_price = self.tp1_price or signal.get("tp1_price")
        if self.partial_tp_price is None and getattr(config, "PARTIAL_TP_ENABLED", True):
            trig_pnl = float(getattr(config, "PARTIAL_TP_TRIGGER_PCT", 50.0))
            trig = trig_pnl / 100.0 / max(leverage, 1)
            if self.direction == "LONG":
                self.partial_tp_price = self.entry_price * (1 + trig)
            else:
                self.partial_tp_price = self.entry_price * (1 - trig)

        self.ride_trend = bool(getattr(config, "RIDE_TREND", True)) and (
            self.signal_strength >= getattr(config, "RIDE_TREND_MIN_STRENGTH", 0.55)
            or (signal.get("trend") in ("UP", "STRONG_UP", "DOWN", "STRONG_DOWN"))
            or bool((signal.get("strategy") or {}).get("pass"))
            or bool(signal.get("ride_hint"))
        )
        if self.engine == "daytrading":
            self.ride_trend = False
        if _is_v2(self.engine):
            self.ride_trend = True
        self.bias_1d = signal.get("bias_1d")
        self.bias_4h = signal.get("bias_4h")
        self.day_invalidation_count = 0
        self.day_last_invalidation_bar = None

        self.highest_price = self.entry_price  # dla LONG
        self.lowest_price = self.entry_price   # dla SHORT
        # Margin call: strata ~80% marginu → likwidacja paper
        # ruch ceny do MC ≈ (margin * threshold) / notional = threshold / leverage
        mc_move = getattr(config, "MARGIN_CALL_THRESHOLD", 0.80) / max(self.leverage, 1)
        if self.direction == "LONG":
            self.margin_call_price = self.entry_price * (1 - mc_move)
        else:
            self.margin_call_price = self.entry_price * (1 + mc_move)
        self.margin_ratio = 1.0  # 1 = pelny margin, 0 = zjedzony


    def update_pnl(self, current_price: float):
        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            return
        if not math.isfinite(current_price) or current_price <= 0:
            return
        if self.direction == "LONG":
            self.highest_price = max(self.highest_price, current_price)
        else:
            self.lowest_price = min(self.lowest_price, current_price)

        # Funding is a discrete venue settlement, not continuous interest.
        if getattr(config, "FUNDING_ACCRUAL", True) and getattr(self, "funding_rate", None):
            try:
                from accounting import funding_payment
                now = __import__("time").time()
                period_h = max(0.25, float(getattr(self, "funding_interval_h", 8.0) or 8.0))
                period_s = period_h * 3600.0
                next_ts = float(getattr(self, "next_funding_ts", 0) or 0)
                if next_ts <= 0:
                    next_ts = (int(now // period_s) + 1) * period_s
                settlements = 0
                while now >= next_ts and settlements < 10:
                    paid = float(funding_payment(
                        self.size_usd, self.funding_rate, self.direction,
                        hours_held=None, period_hours=period_h,
                    ))
                    self.funding_paid = float(getattr(self, "funding_paid", 0.0) or 0.0) + paid
                    next_ts += period_s
                    settlements += 1
                self.next_funding_ts = next_ts
            except Exception:
                pass

        try:
            from accounting import unrealized_pnl
            u = unrealized_pnl(
                self.size_usd, self.entry_price, current_price, self.direction,
                leverage=self.leverage,
                funding_paid=getattr(self, "funding_paid", 0.0) or 0.0,
            )
            self.pnl = float(u["net_usd"])
            self.pnl_pct = float(u["pnl_pct"])
            self.pnl_gross = float(u["gross_usd"])
        except Exception:
            if self.direction == "LONG":
                price_change = (current_price - self.entry_price) / self.entry_price
            else:
                price_change = (self.entry_price - current_price) / self.entry_price
            self.pnl = self.size_usd * price_change - float(getattr(self, "funding_paid", 0) or 0)
            self.pnl_pct = price_change * self.leverage * 100

        # Margin ratio: 1.0 = OK, spada gdy strata zjada depozyt
        if self.margin > 0:
            loss = max(0.0, -self.pnl)
            self.margin_ratio = max(0.0, 1.0 - loss / self.margin)
        else:
            self.margin_ratio = 1.0

        # V2 przechodzi przez wspólny reducer także przy aktualizacji kwotowania.
        # Chandelier jest wyłącznie adapterem danych/kotwicą; nie zawiera reguł
        # decyzji. Stary automatyczny ratchet 15m był runtime-only i został
        # odłączony, bo replay go nie wykonywał.
        if _is_v2(self.engine) and getattr(self, "partial_tp2_done", False):
            self.v2_trailing_anchor_1h = self._chandelier_stop()
            self.decide_v2_exit(current_price)
        elif config.TRAILING_STOP_ENABLED:
            self._update_trailing(current_price, mtf_aligned=getattr(self, "_mtf_aligned", False))

    def pnl_at_stop(self) -> Optional[float]:
        """Niezrealizowany PnL (notional) gdyby cena dobiła aktualny SL."""
        try:
            sl = float(self.sl_price)
            entry = float(self.entry_price)
            size = float(self.size_usd)
            if entry <= 0:
                return None
            if self.direction == "LONG":
                move = (sl - entry) / entry
            else:
                move = (entry - sl) / entry
            return size * move
        except (TypeError, ValueError):
            return None

    def _effective_atr(self) -> Optional[float]:
        """Żywy ATR gdy flaga ON i jest wartość; inaczej ATR z wejścia."""
        if getattr(config, "LIVE_ATR_TRAILING_ENABLED", True):
            live = getattr(self, "live_atr", None)
            try:
                if live is not None and float(live) > 0:
                    return float(live)
            except (TypeError, ValueError):
                pass
        frozen = getattr(self, "atr", None)
        try:
            if frozen is not None and float(frozen) > 0:
                return float(frozen)
        except (TypeError, ValueError):
            pass
        return None

    def _trail_distance_pct(self, current_price: float = None) -> float:
        """
        Trailing: preferuj ATR (w % AKTUALNEJ ceny), inaczej % z config.
        Żywy ATR = dystans od bieżącej zmienności, nie zamrożonej z wejścia.
        """
        atr_now = self._effective_atr()
        ref = None
        try:
            if current_price is not None:
                ref = float(current_price)
        except (TypeError, ValueError):
            ref = None
        if not ref or ref <= 0:
            if self.direction == "LONG":
                ref = float(self.highest_price or self.entry_price or 0)
            else:
                ref = float(self.lowest_price or self.entry_price or 0)
        if getattr(config, "USE_ATR_STOPS", True) and atr_now and ref and ref > 0:
            atr_mult = float(
                getattr(config, "DAYTRADING_TRAIL_ATR_MULT", 1.10)
                if self.engine == "daytrading" or _is_v2(self.engine)
                else getattr(config, "ATR_TRAIL_MULTIPLIER", 1.5)
            )
            base = (float(atr_now) / ref) * atr_mult * 100.0 * self.leverage
            # floor/ceiling
            base = max(1.5, min(base, 12.0))
        else:
            base = config.TRAILING_STOP_DISTANCE_PCT
        if not getattr(config, "TRAILING_TIGHTEN", True):
            return base
        pnl = max(self.pnl_pct, 0)
        factor = max(0.4, 1.0 - (pnl - config.TRAILING_STOP_ACTIVATION_PCT) / 30.0)
        return base * factor

    def _trailing_cooldown_blocks(self) -> bool:
        """True = nie zaciskaj teraz (kolejne zacieśnienie w oknie cooldownu).
        Pierwsza aktywacja traila (brak trailing_stop_price) zawsze przechodzi."""
        if self.trailing_stop_price is None:
            return False
        interval = float(getattr(config, "TRAILING_MIN_UPDATE_INTERVAL_SEC", 0.0) or 0.0)
        if interval <= 0:
            return False
        last = float(getattr(self, "_last_trailing_tighten_at", 0.0) or 0.0)
        return (time.monotonic() - last) < interval

    def _clamp_tighter_sl(self, candidate) -> Optional[float]:
        """LONG tylko w górę, SHORT tylko w dół, nigdy luźniej niż initial_sl."""
        try:
            cand = float(candidate)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(cand) or cand <= 0:
            return None
        sl = float(self.sl_price) if self.sl_price is not None else None
        initial = float(self.initial_sl_price) if self.initial_sl_price is not None else None
        if self.direction == "LONG":
            if initial is not None:
                cand = max(cand, initial)
            if sl is not None and cand <= sl + 1e-12:
                return None
            return cand
        if initial is not None:
            cand = min(cand, initial)
        if sl is not None and cand >= sl - 1e-12:
            return None
        return cand

    def _apply_tighter_sl(self, candidate, why: str) -> bool:
        new = self._clamp_tighter_sl(candidate)
        if new is None:
            return False
        self.sl_price = new
        try:
            self._sync_exchange_trailing_sl()
        except Exception:
            pass
        print(f"[SL] {self.symbol} {why} -> {new:.8g}")
        return True

    @staticmethod
    def _last_confirmed_fractal(lows, highs, direction: str, left: int = 2, right: int = 2):
        n = len(lows) if direction == "LONG" else len(highs)
        if n < left + right + 1:
            return None
        last_i = n - 1 - right
        for i in range(last_i, left - 1, -1):
            if direction == "LONG":
                pivot = float(lows[i])
                window = [float(x) for x in list(lows[i - left:i]) + list(lows[i + 1:i + 1 + right])]
                if window and pivot < min(window):
                    return pivot
            else:
                pivot = float(highs[i])
                window = [float(x) for x in list(highs[i - left:i]) + list(highs[i + 1:i + 1 + right])]
                if window and pivot > max(window):
                    return pivot
        return None

    def ratchet_structure_sl(self, frame_15m=None) -> bool:
        """Po TP1: SL na ostatni confirmed 15m HL/LH ± ATR*mult. Tylko zacieśnia."""
        after = str(getattr(config, "DAYTRADING_V2_SL_RATCHET_AFTER", "tp1") or "none").lower()
        if after in ("none", "off", "0", ""):
            return False
        if getattr(self, "partial_tp2_done", False):
            return False
        if after == "tp1" and not getattr(self, "partial_tp1_done", False):
            return False
        if after == "tp2":
            return False
        if frame_15m is None:
            try:
                from market_store import STORE
                frame_15m = STORE.get_ohlcv(self.symbol, "15m") or {}
            except Exception:
                return False
        lows = list((frame_15m or {}).get("lows") or [])
        highs = list((frame_15m or {}).get("highs") or [])
        k = max(1, int(getattr(config, "DAYTRADING_V2_SL_RATCHET_FRACTAL", 2) or 2))
        pivot = self._last_confirmed_fractal(lows, highs, self.direction, left=k, right=k)
        if pivot is None:
            return False
        atr = self._effective_atr() or 0.0
        buf = float(getattr(config, "DAYTRADING_V2_SL_RATCHET_ATR_MULT", 0.5) or 0.0) * float(atr)
        if self.direction == "LONG":
            candidate = pivot - buf
        else:
            candidate = pivot + buf
        px = float(self.highest_price if self.direction == "LONG" else self.lowest_price)
        if self.direction == "LONG" and candidate >= px:
            return False
        if self.direction == "SHORT" and candidate <= px:
            return False
        return self._apply_tighter_sl(candidate, "ratchet 15m")

    def _chandelier_stop(self) -> Optional[float]:
        atr = self._effective_atr()
        if not atr:
            return None
        k = float(getattr(config, "DAYTRADING_V2_CHANDELIER_ATR_MULT", 1.5) or 1.5)
        if self.direction == "LONG":
            hh = float(self.highest_price or self.entry_price)
            return hh - k * float(atr)
        ll = float(self.lowest_price or self.entry_price)
        return ll + k * float(atr)

    def _update_trailing(self, current_price: float, mtf_aligned: bool = False):
        """Włącza i przesuwa trailing stop – glowny mechanizm realizacji zysku."""
        price_move = ((current_price - self.entry_price) / self.entry_price
                      if self.direction == "LONG" else
                      (self.entry_price - current_price) / self.entry_price)
        fee_rt = 2.0 * float(getattr(config, "TAKER_FEE", getattr(config, "COMMISSION_RATE", 0.0006)) or 0)
        slip_rt = float(getattr(self, "slip_rt", None) or getattr(config, "SLIPPAGE", 0.0008) or 0)
        risk_frac = float(self.initial_risk_abs or 0) / max(float(self.entry_price or 0), 1e-12)
        net_buffer = float(getattr(config, "TRAILING_MIN_NET_BUFFER_R", 0.15) or 0.15) * risk_frac
        net_trail_ready = price_move >= (fee_rt + slip_rt + net_buffer)
        if _is_v2(self.engine) and self.initial_risk_abs > 0:
            # V2: brak wejściowego SL. BE po TP1, trail po TP2.
            partial_reached = (
                getattr(self, "partial_taken", False)
                or getattr(self, "partial_tp1_done", False)
                or getattr(self, "partial_tp2_done", False)
            )
            if partial_reached and bool(getattr(config, "DAYTRADING_V2_BE_AFTER_TP1", True)):
                buffer_frac = float(getattr(config, "DAYTRADING_BREAK_EVEN_BUFFER_PCT", 0.18)) / 100.0
                be_stop = self.entry_price * (1 + buffer_frac if self.direction == "LONG" else 1 - buffer_frac)
                improved = ((self.direction == "LONG" and be_stop > self.sl_price)
                            or (self.direction == "SHORT" and be_stop < self.sl_price))
                if improved:
                    self.sl_price = be_stop
                    self.breakeven_active = True
                    self._sync_exchange_trailing_sl()
            if getattr(self, "partial_tp2_done", False):
                if not self.trailing_active and net_trail_ready:
                    self.trailing_active = True
                    print(f"[Paper] V2 trailing po TP2 {self.symbol}")
        elif (self.engine == "daytrading") and self.initial_risk_abs > 0:
            current_r = self._current_r(current_price)
            if current_r >= float(getattr(config, "DAYTRADING_BREAK_EVEN_R", 1.0)):
                buffer_frac = float(getattr(config, "DAYTRADING_BREAK_EVEN_BUFFER_PCT", 0.18)) / 100.0
                be_stop = self.entry_price * (1 + buffer_frac if self.direction == "LONG" else 1 - buffer_frac)
                improved = ((self.direction == "LONG" and be_stop > self.sl_price)
                            or (self.direction == "SHORT" and be_stop < self.sl_price))
                if improved:
                    self.sl_price = be_stop
                    self.breakeven_active = True
                    self._sync_exchange_trailing_sl()
            if (not self.trailing_active and net_trail_ready
                    and current_r >= float(getattr(config, "DAYTRADING_TRAIL_ACTIVATION_R", 1.5))):
                self.trailing_active = True
                print(f"[Paper] Day trailing aktywowany dla {self.symbol} @ {current_r:.2f}R")
        elif not self.trailing_active:
            if net_trail_ready and self.pnl_pct >= config.TRAILING_STOP_ACTIVATION_PCT:
                self.trailing_active = True
                print(f"[Paper] Trailing STOP aktywowany dla {self.symbol} @ PnL {self.pnl_pct:+.2f}%")

        if not self.trailing_active:
            return

        if _is_v2(self.engine) and getattr(self, "partial_tp2_done", False):
            new_trail = self._chandelier_stop()
            if new_trail is not None and not self._trailing_cooldown_blocks():
                if self._apply_tighter_sl(new_trail, "chandelier"):
                    self.trailing_stop_price = float(self.sl_price)
                    self._last_trailing_tighten_at = time.monotonic()
            return

        # Candle-gate: gdy 15m/1h nadal zgodne z pozycją – nie zaciskaj trail
        # (zostaw aktualny poziom, tylko pozwól na luźniejsze / bez dalszego raise)
        trail_dist = self._trail_distance_pct(current_price) / 100 / self.leverage
        if self.engine not in ("daytrading", "daytrading_v2") and getattr(config, "TRAILING_CANDLE_GATE", True) and mtf_aligned:
            # szerszy trail (mniej agresywne zaciskanie)
            trail_dist *= 1.35

        if self.direction == "LONG":
            new_trail = self.highest_price * (1 - trail_dist)
            improved = self.trailing_stop_price is None or new_trail > self.trailing_stop_price
            if improved and not self._trailing_cooldown_blocks():
                self.trailing_stop_price = new_trail
                self._last_trailing_tighten_at = time.monotonic()
                if self.trailing_stop_price > self.sl_price:
                    self.sl_price = self.trailing_stop_price
                    self._sync_exchange_trailing_sl()
        else:
            new_trail = self.lowest_price * (1 + trail_dist)
            improved = self.trailing_stop_price is None or new_trail < self.trailing_stop_price
            if improved and not self._trailing_cooldown_blocks():
                self.trailing_stop_price = new_trail
                self._last_trailing_tighten_at = time.monotonic()
                if self.trailing_stop_price < self.sl_price:
                    self.sl_price = self.trailing_stop_price
                    self._sync_exchange_trailing_sl()


    def _current_r(self, current_price: float) -> float:
        risk = float(getattr(self, "initial_risk_abs", 0.0) or 0.0)
        move = float(current_price) - self.entry_price
        return 0.0 if risk <= 0 else (move if self.direction == "LONG" else -move) / risk

    def _mfe_r(self) -> float:
        risk = float(getattr(self, "initial_risk_abs", 0.0) or 0.0)
        if risk <= 0:
            return 0.0
        if self.direction == "LONG":
            return (float(self.highest_price) - self.entry_price) / risk
        return (self.entry_price - float(self.lowest_price)) / risk

    def daytrading_time_stop_due(self, current_price: float, now: datetime = None) -> bool:
        now = now or datetime.now()
        age_h = max(0.0, (now - self.entry_time).total_seconds() / 3600.0)
        if _is_v2(self.engine):
            if getattr(self, "partial_taken", False) or getattr(self, "partial_tp1_done", False):
                return False
            skip_mfe = float(getattr(config, "DAYTRADING_V2_UNCLOG_SKIP_MFE_R", 0.5) or 0.0)
            if skip_mfe > 0 and self._mfe_r() >= skip_mfe:
                return False
            hours = float(getattr(config, "DAYTRADING_V2_TIME_STOP_HOURS", 24.0))
            min_r = float(getattr(config, "DAYTRADING_V2_TIME_STOP_MIN_R", 0.35))
            return age_h >= hours and self._current_r(current_price) < min_r
        if self.engine != "daytrading":
            return False
        return age_h >= float(getattr(config, "DAYTRADING_TIME_STOP_HOURS", 6.0)) and self._current_r(current_price) < float(getattr(config, "DAYTRADING_TIME_STOP_MIN_R", 0.50))

    def daytrading_hard_time_stop_due(self, now: datetime = None) -> bool:
        now = now or datetime.now()
        age_h = max(0.0, (now - self.entry_time).total_seconds() / 3600.0)
        if _is_v2(self.engine):
            return age_h >= float(getattr(config, "DAYTRADING_V2_HARD_TIME_STOP_HOURS", 48.0))
        if self.engine != "daytrading":
            return False
        return age_h >= float(getattr(config, "DAYTRADING_HARD_TIME_STOP_HOURS", 10.0))

    def daytrading_invalidation(self, signal: Dict) -> Optional[str]:
        """Require invalidation on distinct closed 5m bars, not repeated UI cycles."""
        if self.engine != "daytrading" or not signal:
            return None
        intra = signal.get("intraday") or {}
        frames = intra.get("tf") or {}
        m5, m15 = frames.get("5m") or {}, frames.get("15m") or {}
        bar_ts = (intra.get("bar_ts") or {}).get("5m")
        st_up = (m5.get("supertrend") or {}).get("is_up")
        ema_up = m15.get("ema_fast_above_slow")
        invalid = (
            (self.direction == "LONG" and st_up is False and ema_up is False)
            or (self.direction == "SHORT" and st_up is True and ema_up is True)
        )
        htf_bias = intra.get("bias_4h_1h")
        if htf_bias in ("LONG", "SHORT") and htf_bias != self.direction:
            invalid = True
        if bar_ts is None or bar_ts == self.day_last_invalidation_bar:
            return None
        self.day_last_invalidation_bar = bar_ts
        self.day_invalidation_count = self.day_invalidation_count + 1 if invalid else 0
        required = max(1, int(getattr(config, "DAYTRADING_INVALIDATION_BARS", 2)))
        return "day_setup_invalidated" if self.day_invalidation_count >= required else None

    def v2_htf_invalidation(self, signal: Dict) -> Optional[str]:
        """V2: 4H/1D nie zamyka pozycji (kontekst wejścia). Flaga = stary exit."""
        if not _is_v2(self.engine) or not signal:
            return None
        if not bool(getattr(config, "DAYTRADING_V2_EXIT_ON_HTF_REVERSAL", False)):
            return None
        b1 = signal.get("bias_1d")
        b4 = signal.get("bias_4h")
        if b1 in ("LONG", "SHORT") and b1 != self.direction:
            return "v2_1d_reversal"
        if b4 in ("LONG", "SHORT") and b4 != self.direction:
            return "v2_4h_reversal"
        return None

    def recalculate_after_fill(self, fill_price: float, fill_notional: float = None, risk_manager=None):
        """
        Po fillu: actual entry → actual risk → actual SL → ten sam liq buffer co pre-trade.
        Wymusza risk invariant; przy fail ustawia risk_invariant_ok=False.
        """
        if fill_price is None or float(fill_price) <= 0:
            return
        fill_price = float(fill_price)
        old_entry = float(self.entry_price)
        self.execution_price = fill_price
        self.entry_price = fill_price
        if fill_notional is not None:
            try:
                self.size_usd = float(fill_notional)
                self.actual_notional = float(fill_notional)
                self.margin = self.size_usd / max(self.leverage, 1)
            except (TypeError, ValueError):
                pass
        # przeskaluj SL/TP/MC względnym dystansem od starego entry
        if old_entry > 0:
            if self.sl_price is not None:
                sl_rel = (float(self.sl_price) - old_entry) / old_entry
                self.sl_price = fill_price * (1.0 + sl_rel)
            if self.tp_price is not None:
                tp_rel = (float(self.tp_price) - old_entry) / old_entry
                self.tp_price = fill_price * (1.0 + tp_rel)
            if self.tp1_price is not None:
                tp1_rel = (float(self.tp1_price) - old_entry) / old_entry
                self.tp1_price = fill_price * (1.0 + tp1_rel)
                self.partial_tp_price = self.tp1_price
            if self.tp2_price is not None:
                tp2_rel = (float(self.tp2_price) - old_entry) / old_entry
                self.tp2_price = fill_price * (1.0 + tp2_rel)
            if getattr(self, "margin_call_price", None) is not None:
                mc_rel = (float(self.margin_call_price) - old_entry) / old_entry
                self.margin_call_price = fill_price * (1.0 + mc_rel)
        self.initial_sl_price = float(self.sl_price) if self.sl_price is not None else None
        self.initial_risk_abs = abs(fill_price - self.initial_sl_price) if self.initial_sl_price is not None else 0.0

        self.actual_risk_usd = None
        self.liq_buffer_ok = None
        self.risk_invariant_ok = True
        self.risk_invariant_reason = "OK"

        if risk_manager is not None:
            try:
                # SL distance (ta sama logika co pre-trade _sl_distance_pct)
                sig = {
                    "price": fill_price,
                    "sl_price": self.sl_price,
                    "atr_pct": getattr(self, "atr_pct", None),
                    "direction": self.direction,
                }
                if hasattr(risk_manager, "_sl_distance_pct"):
                    sl_dist = risk_manager._sl_distance_pct(sig)
                else:
                    sl_dist = abs(fill_price - float(self.sl_price)) / fill_price
                self.sl_distance_pct = sl_dist
                self.actual_risk_usd = float(self.size_usd) * sl_dist

                # 5. ten sam test co pre-trade _liquidation_beyond_sl
                if hasattr(risk_manager, "_liquidation_beyond_sl"):
                    ok_liq, why_liq = risk_manager._liquidation_beyond_sl(sig)
                else:
                    ok_liq, why_liq = True, "NOT_AVAILABLE"
                self.liq_buffer_ok = bool(ok_liq)
                if hasattr(risk_manager, "estimate_liq_distance_pct"):
                    self.liq_distance_pct = risk_manager.estimate_liq_distance_pct(self.leverage)
                else:
                    self.liq_distance_pct = max((1.0 / max(self.leverage, 1.0)) * 0.995, 0.005)
                if not ok_liq:
                    self.risk_invariant_ok = False
                    self.risk_invariant_reason = why_liq

                # 4. risk invariant: risk$ ≤ MAX risk% equity
                if hasattr(risk_manager, "equity_for_sizing"):
                    equity = risk_manager.equity_for_sizing()
                else:
                    equity = float(getattr(risk_manager, "current_capital", 0) or 0)
                max_risk_pct = float(getattr(config, "RISK_PCT_MAX", 0.0075))
                max_risk_usd = equity * max_risk_pct * 1.15  # 15% tolerance post-fill (slip)
                if equity > 0 and self.actual_risk_usd is not None:
                    if self.actual_risk_usd > max_risk_usd:
                        self.risk_invariant_ok = False
                        self.risk_invariant_reason = (
                            f"RISK_INVARIANT({self.actual_risk_usd:.4f}>{max_risk_usd:.4f})"
                        )
                # portfolio open risk
                max_port = float(getattr(config, "MAX_PORTFOLIO_OPEN_RISK", 0.025))
                others = 0.0
                try:
                    for p in (getattr(risk_manager, "_positions_ref", None) or []):
                        if p is self:
                            continue
                        if getattr(p, "actual_risk_usd", None) is not None:
                            others += float(p.actual_risk_usd)
                except Exception:
                    pass
                if equity > 0 and (others + float(self.actual_risk_usd or 0)) > equity * max_port:
                    self.risk_invariant_ok = False
                    self.risk_invariant_reason = (
                        f"PORTFOLIO_RISK_POST({(others+float(self.actual_risk_usd or 0))/equity*100:.2f}%>{max_port*100:.1f}%)"
                    )
            except Exception as e:
                print(f"[Fill] recalc risk: {e}")
                self.risk_invariant_ok = False
                self.risk_invariant_reason = f"RECALC_ERR:{e}"

        try:
            self._sync_exchange_trailing_sl()
        except Exception:
            pass
        print(
            f"[Fill] {self.symbol} entry {old_entry:.6g} -> {fill_price:.6g} | "
            f"SL {self.sl_price} | risk$ {self.actual_risk_usd} | "
            f"inv={self.risk_invariant_ok} ({self.risk_invariant_reason})"
        )

    def _sync_exchange_trailing_sl(self):
        """
        Trailing podniósł lokalny SL → callback do ProtectionManager.
        Position NIE trzyma referencji do ProtectionManager (czyste rozdzielenie).
        PaperTrader ustawia on_sl_updated przy open.
        """
        cb = getattr(self, "on_sl_updated", None)
        if not callable(cb):
            return
        try:
            cb(
                self.symbol,
                self.direction,
                float(self.sl_price) if self.sl_price is not None else None,
                getattr(self, "size_contracts", None),
            )
        except Exception as e:
            print(f"[Trail] SL callback error {getattr(self, 'symbol', '?')}: {e}")

    def check_tp_sl(self, current_price: float) -> Optional[str]:
        """
        Filozofia: przy sile i sprzyjajacym trendzie jedziemy ile sie da (nawet 100%+).
        Realizacja zysku = wylacznie trailing (podnoszony z PnL).
        Twardy TP wylaczony gdy NO_HARD_TP / ride_trend.
        SL zawsze chroni. Margin call = strata zjada depozyt.
        """
        # Adapter do czystego reduktora - blizniaka v2_trade_lifecycle dla
        # sciezki nie-V2. Decyzje podejmuje cryptoedge.portfolio.exit_rules;
        # tutaj zostaje wylacznie zbudowanie widoku i zapis etapu partiala,
        # ktory wczesniej byl efektem ubocznym wewnatrz tego zapytania.
        from cryptoedge.portfolio.exit_rules import (
            PriceExitView, decide_price_exit,
        )

        view = PriceExitView(
            direction=self.direction,
            engine=self.engine,
            pnl=float(getattr(self, "pnl", 0) or 0),
            pnl_pct=float(getattr(self, "pnl_pct", 0) or 0),
            margin=float(getattr(self, "margin", 0) or 0),
            margin_call_price=getattr(self, "margin_call_price", None),
            sl_price=self.sl_price,
            tp_price=self.tp_price,
            tp1_price=getattr(self, "tp1_price", None),
            tp2_price=getattr(self, "tp2_price", None),
            partial_tp_price=getattr(self, "partial_tp_price", None),
            tp_plan=getattr(self, "tp_plan", None),
            trailing_active=bool(self.trailing_active),
            trailing_stop_price=self.trailing_stop_price,
            partial_tp1_done=bool(getattr(self, "partial_tp1_done", False)),
            partial_tp2_done=bool(getattr(self, "partial_tp2_done", False)),
            partial_taken=bool(getattr(self, "partial_taken", False)),
            breakeven_active=bool(getattr(self, "breakeven_active", False)),
            ride_trend=bool(getattr(self, "ride_trend", True)),
            hard_tp_after_stop=bool(getattr(self, "hard_tp_after_stop", False)),
        )
        decision = decide_price_exit(view, current_price, config)
        if decision.partial_stage:
            self._partial_stage = decision.partial_stage
        return decision.action

    def decide_v2_exit(self, current_price: float, signal: Optional[Dict] = None):
        """Adapter PAPER/LIVE do tego samego reducera co replay V2."""
        if not _is_v2(self.engine) or self.initial_risk_abs <= 0:
            return None
        from v2_trade_lifecycle import (
            V2Observation, V2TradeView, decide_v2_lifecycle,
        )
        signal = signal or {}
        bias = signal.get("bias_1d") or signal.get("bias_4h")
        # Snapshot runtime może dostarczyć tę samą kotwicę 1h co replay.
        # Dla starszego payloadu zachowujemy ostatnią kauzalną kotwicę pozycji.
        anchor = signal.get("trailing_anchor_1h")
        if anchor is None:
            anchor = getattr(self, "v2_trailing_anchor_1h", None)
        else:
            self.v2_trailing_anchor_1h = anchor
        age_s = max(0.0, (datetime.now() - self.entry_time).total_seconds())
        d = decide_v2_lifecycle(
            V2TradeView(
                self.direction, float(self.entry_price), float(self.sl_price),
                float(self.tp1_price), float(self.tp2_price),
                bool(self.partial_tp1_done), bool(self.partial_tp2_done), self._mfe_r(),
            ),
            V2Observation(
                float(current_price), float(current_price), float(current_price),
                age_s, bias, anchor,
            ),
            initial_risk=self.initial_risk_abs,
        )
        if d.new_sl is not None:
            if self._apply_tighter_sl(float(d.new_sl), "v2_shared_1h_anchor"):
                self.trailing_stop_price = float(self.sl_price)
                self.trailing_active = bool(self.partial_tp2_done)
                self._sync_exchange_trailing_sl()
        return d

    def close(self, exit_price: float, reason: str = "signal"):
        self.exit_price = exit_price
        self.exit_time = datetime.now()
        self.status = "CLOSED"

        try:
            from accounting import realized_pnl
            r = realized_pnl(
                self.size_usd, self.entry_price, exit_price, self.direction,
                leverage=self.leverage,
                funding_paid=getattr(self, "funding_paid", 0.0) or 0.0,
                entry_side=self.entry_side,
                exit_side=self.exit_side,
                include_slippage=True,
                slip_frac=getattr(self, "slip_rt", None),
            )
            self.pnl = float(r["net_usd"])
            self.pnl_pct = float(r["pnl_pct"])
            self.pnl_gross = float(r["gross_usd"])
            self.fees_paid = float(r["fee_entry"] + r["fee_exit"] + r["slippage"])
            self.pnl_breakdown = r
        except Exception:
            if self.direction == "LONG":
                price_change = (exit_price - self.entry_price) / self.entry_price
            else:
                price_change = (self.entry_price - exit_price) / self.entry_price
            cost = float(getattr(config, "TAKER_FEE", config.COMMISSION_RATE)) * 2 + float(
                getattr(config, "SLIPPAGE", 0.0008)
            )
            net_change = price_change - cost
            self.pnl = self.size_usd * net_change - float(getattr(self, "funding_paid", 0) or 0)
            self.pnl_pct = (self.pnl / self.size_usd) * self.leverage * 100 if self.size_usd else 0.0
            self.fees_paid = self.size_usd * cost
        return self.pnl


class PaperTrader:
    def __init__(self, risk_manager, logger=None, protection=None):
        self.risk = risk_manager
        self.logger = logger
        self.protection = protection  # ProtectionManager (Etap 2)
        registry = getattr(risk_manager, "instrument_registry", None)
        if registry is not None:
            Position._shared_registry = registry
        from entry_reservations import EntryReservationBook
        self.entry_reservations = EntryReservationBook(
            ttl_seconds=float(getattr(config, "ENTRY_RESERVATION_TTL_SEC", 30.0))
        )
        self.lock = threading.RLock()
        self.positions: List[Position] = []
        self.closed_positions: List[Position] = []
        self.partial_closes: List[Position] = []
        self.trade_count = 0
        self._limit_queue: dict = {}

    def has_position(self, symbol: str) -> bool:
        """Czy jest juz otwarta pozycja na ten symbol."""
        return any(p.symbol == symbol for p in self.positions)

    def _should_park_limit(self, signal: Dict) -> bool:
        if signal.get("limit_fill_now"):
            return False
        if not bool(getattr(config, "DAYTRADING_V2_LIMIT_IN_ZONE", True)):
            return False
        if not _is_v2(signal.get("engine")):
            return False
        if self.has_position(str(signal.get("symbol") or "")):
            return False
        return signal.get("limit_price") is not None

    def _park_limit(self, signal: Dict) -> bool:
        """Park a V2 limit once.

        Replay keeps the first pending signal until fill/expiry and ignores
        repeated signals for that symbol.  PAPER must do the same: replacing
        the row every scan used to move ``deadline`` forever and made the
        original deadline.
        """
        import time as _time
        sym = str(signal.get("symbol") or "").upper()
        if sym in self._limit_queue:
            return False
        from v2_parity_policy import limit_timeout_seconds
        timeout_seconds = limit_timeout_seconds()
        bars = max(1, int(timeout_seconds // 900))
        self._limit_queue[sym] = {
            "signal": dict(signal),
            "limit": float(signal["limit_price"]),
            "queued_at": _time.time(),
            "deadline": _time.time() + timeout_seconds,
        }
        print(f"[Paper] LIMIT queued {signal.get('direction')} {sym} @ {float(signal['limit_price']):.6f} timeout={bars}×15m")
        return True

    def has_pending_limit(self, symbol: str) -> bool:
        return str(symbol or "").upper() in self._limit_queue

    def cancel_pending_limit(self, symbol: str) -> Optional[Dict]:
        """Publiczne anulowanie zaparkowanego limitu.

        Kolejka jest kluczowana symbolem, wiec to jedyny mozliwy klucz.
        Zwraca usunieta pozycje kolejki albo None, gdy nie bylo czego
        anulowac - dzieki temu wolajacy odroznia anulowanie od no-opu
        zamiast zgadywac po stanie kolejki.
        """
        sym = str(symbol or "").upper()
        if not sym:
            return None
        return self._limit_queue.pop(sym, None)

    def pending_limit_orders(self, now: float = None) -> List[Dict]:
        """Serializable snapshot for state/UI; pending orders are not positions."""
        import time as _time
        now = _time.time() if now is None else float(now)
        rows = []
        for sym, row in self._limit_queue.items():
            sig = row.get("signal") or {}
            rows.append({
                "symbol": sym,
                "direction": sig.get("direction"),
                "limit_price": float(row["limit"]),
                "queued_at": float(row.get("queued_at") or 0.0),
                "deadline": float(row["deadline"]),
                "seconds_remaining": max(0.0, float(row["deadline"]) - now),
                "strength": sig.get("strength"),
                "expected_net_r": sig.get("expected_net_r"),
                "engine": sig.get("engine"),
            })
        return sorted(rows, key=lambda item: (item["deadline"], item["symbol"]))

    def process_limit_queue(self, prices: Dict, now: float = None) -> List[Position]:
        """Fill a planned retest limit or cancel it after expiry.

        Brak retestu nie jest zgoda na pozniejsze wejscie market. Taki
        fallback gonil cene i byl bezposrednim zrodlem spoznionych wejsc.
        """
        import time as _time
        now = _time.time() if now is None else float(now)
        opened: List[Position] = []
        for sym, row in list(self._limit_queue.items()):
            sig = dict(row["signal"])
            limit = float(row["limit"])
            px = prices.get(sym) or prices.get(sym.replace("-USDT", ""))
            try:
                px = float(px) if px is not None else None
            except (TypeError, ValueError):
                px = None
            tagged = False
            if px is not None:
                from v2_parity_policy import limit_touched
                tagged = limit_touched(sig.get("direction"), limit, price=px) is not None
            if tagged:
                sig["price"] = limit
                sig["limit_fill_now"] = True
                sig["fill_kind"] = "limit"
            elif now >= float(row["deadline"]):
                self._limit_queue.pop(sym, None)
                try:
                    self.risk.log_reject(
                        sym, sig.get("direction"), sig.get("strength"),
                        "V2_LIMIT_EXPIRED_NO_FILL", signal=sig,
                    )
                except Exception:
                    pass
                print(f"[Paper] LIMIT expired {sig.get('direction')} {sym} @ {limit:.6f} — no chase")
                continue
            else:
                continue
            self._limit_queue.pop(sym, None)
            pos = self.open_position(sig)
            if pos:
                opened.append(pos)
        return opened

    def open_position(self, signal: Dict) -> Optional[Position]:
        try:
            from universe_policy import crypto_perpetual_allowed
            if not crypto_perpetual_allowed(signal.get("symbol") or "", signal):
                self.risk.log_reject(signal.get("symbol"), signal.get("direction"),
                                     signal.get("strength"), "NON_CRYPTO_INSTRUMENT", signal=signal)
                return None
        except Exception as e:
            print(f"[Universe] policy unavailable: {e}")
            return None
        try:
            price = float(signal.get("price"))
            strength = float(signal.get("strength"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price) or price <= 0 or not math.isfinite(strength):
            return None

        # Realizm paper: half-spread z OB w cenie wejscia. MUSI byc zrobione
        # PRZED sizingiem i risk-gate'ami (calculate_position_size,
        # can_open_position) - dawniej byl robiony na samym koncu, tuz przed
        # Position(...). Dla mocno illikwidnych symboli (szeroki ob_spread_pct)
        # to poszerzało realny entry_px o kilkadziesiat pb wzgledem ceny, na
        # ktorej liczono juz sl_dist/SIZE_CAP_RISK - SL strukturalny (staly,
        # z 1h swingu) zostawal w tym samym miejscu, wiec realna odleglosc
        # entry->SL po poszerzeniu ceny bywala wyraznie wieksza niz ta, na
        # ktorej oparto sizing. Ryzyko$ (notional*sl_dist) przekraczalo wtedy
        # per-trade sufit (DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE), a lapal to
        # dopiero post-fill RISK_INVARIANT w Position/close_position: pozycja
        # otwierala sie i w ulamku sekundy byla przymusowo zamykana ze strata
        # (RISK_INVARIANT_FAIL), dodatkowo uzbrajajac ENGINE_COOLDOWN (20 min)
        # na tym symbolu - realnie blokujac kolejne, poprawne wejscia. Liczac
        # spread na starcie, sizing/can_open_position/Position dostaja jedna,
        # spojna cene wykonania.
        signal = dict(signal)
        if getattr(config, "USE_ORDERBOOK_SPREAD", True):
            ob = signal.get("order_book") or {}
            sp = ob.get("ob_spread_pct")
            if sp is not None:
                try:
                    half = float(sp) / 100.0 / 2.0
                    if signal.get("direction") == "LONG":
                        price *= (1 + half)
                    else:
                        price *= (1 - half)
                    signal["price"] = price
                except (TypeError, ValueError):
                    pass
        # Shadow Mode: reversal tylko loguje, bez egzekucji
        try:
            from shadow_mode import should_execute, process_reversal_signals
            if not should_execute(signal):
                process_reversal_signals([signal])
                block_reason = signal.get("paper_reversal_block_reason") or "REVERSAL_SHADOW_ONLY"
                try:
                    from decision_telemetry import decision_snapshot
                    decision_snapshot(signal, "REJECT", block_reason)
                except Exception:
                    pass
                print(
                    f"[Paper] SHADOW skip reversal {signal.get('direction')} "
                    f"{signal.get('symbol')} | {block_reason}"
                )
                return None
        except Exception as e:
            print(f"[Paper] shadow gate: {e}")
        # Kill switch – zero nowych wejść
        if self.protection and getattr(self.protection, "is_killed", lambda: False)():
            print("[Paper] BLOKADA: KILL SWITCH aktywny")
            return None
        # Synchronizuj licznik z realną listą (manual close / desync)
        try:
            self.risk.sync_open_count(len(self.positions))
        except Exception:
            pass

        # LIVE bez executora: blokada. DEMO (PAPER_TRADING=True) zawsze handluje lokalnie.
        from cryptoedge.domain import trading_mode
        if trading_mode.is_live(config):
            # PaperTrader is a simulator, never a LIVE execution path.
            try:
                self.risk.log_reject(
                    signal.get("symbol"), signal.get("direction"),
                    signal.get("strength"), "PAPER_TRADER_FORBIDDEN_IN_LIVE",
                    signal=signal,
                )
            except Exception:
                pass
            print("[Live] Blocked: PaperTrader cannot create exchange fills.")
            return None
            # 26.08.2026: tu stalo 10 linii nieosiagalnych z definicji - drugi
            # blok kontroli LIVE siedzial ZA bezwarunkowym `return None`
            # powyzej i nigdy sie nie wykonal. Usuniete, bo sugerowal, ze LIVE
            # ma dwie rozne bramki, podczas gdy dziala wylacznie pierwsza.
            # Zachowanie bez zmian: PaperTrader w LIVE i tak jest zablokowany.
        # V2: max N wejsc na ten sam symbol/kierunek; inne silniki: 1
        same = [p for p in self.positions if p.symbol == signal.get("symbol")]
        if same:
            if _is_v2(signal.get("engine") or signal.get("strategy_mode")):
                max_n = max(1, int(getattr(config, "DAYTRADING_V2_MAX_ENTRIES_PER_SWING", 1)))
                same_dir = [p for p in same if p.direction == signal.get("direction")]
                if len(same_dir) >= max_n or any(p.direction != signal.get("direction") for p in same):
                    return None
            else:
                return None

        open_dirs = [p.direction for p in self.positions]
        # podaj pozycje + planowany notional do limitu portfela
        try:
            self.risk._positions_ref = list(self.positions)
        except Exception:
            pass
        size = self.risk.calculate_position_size(signal)
        signal = dict(signal)
        # final actual_notional (po lot size) – portfolio risk dostaje TO, nie raw size
        actual = size
        try:
            from instrument_registry import InstrumentRegistry
            reg = getattr(Position, "_shared_registry", None)
            px = float(signal.get("price") or 0)
            if px > 0 and reg is not None:
                conv = reg.notional_to_contracts(
                    signal.get("symbol") or "", size, px,
                    leverage=float(getattr(config, "LEVERAGE", 10)),
                )
                if conv.get("ok"):
                    actual = float(conv.get("actual_notional_usd") or size)
                    if actual <= 0:
                        actual = size
        except Exception:
            actual = size
        signal["_planned_notional"] = actual
        signal["_requested_notional"] = size
        engine_name = signal.get("engine") or signal.get("score_type") or "trend"
        reserved, reserve_reason = self.entry_reservations.reserve(
            signal.get("symbol"), engine_name, len(self.positions), int(getattr(config, "MAX_POSITIONS", 1))
        )
        if not reserved:
            self.risk.log_reject(signal.get("symbol"), signal.get("direction"),
                                 signal.get("strength"), reserve_reason, signal=signal)
            return None
        can_open, reason = self.risk.can_open_position(signal, open_directions=open_dirs)
        if not can_open:
            self.entry_reservations.release(signal.get("symbol"), engine_name)
            try:
                self.risk.log_reject(signal.get("symbol"), signal.get("direction"),
                                     signal.get("strength"), reason, signal=signal)
            except Exception:
                pass
            print(f"[Open skip] {signal.get('direction')} {signal.get('symbol')} "
                  f"str={float(signal.get('strength') or 0):.2f} -> {reason}")
            return None

        if size < 1.0:
            self.entry_reservations.release(signal.get("symbol"), engine_name)
            try:
                self.risk.log_reject(signal.get("symbol"), signal.get("direction"),
                                     signal.get("strength"), f"SIZE_TOO_SMALL({size})", signal=signal)
            except Exception:
                pass
            print(f"[Open skip] {signal.get('symbol')} size=${size:.4f} too small")
            return None

        # Limit wolno zaparkować dopiero po sizingu i kompletnym risk gate.
        # Wcześniej kolejka powstawała przed can_open_position(), przez co
        # ujemny Expected Net R był widoczny jako aktywne LIMIT_PENDING.
        # Fill zostanie sprawdzony ponownie z bieżącymi kosztami i ryzykiem.
        if self._should_park_limit(signal):
            self.entry_reservations.release(signal.get("symbol"), engine_name)
            self._park_limit(signal)
            return None

        try:
            from decision_telemetry import decision_snapshot
            decision_snapshot(signal, "ACCEPT", "PAPER_OPEN")
        except Exception:
            pass

        pos = Position(signal, size)
        with self.lock:
            self.positions.append(pos)
        if _is_v2(signal.get("engine")):
            swing_end = (((signal.get("swing") or {}).get("end") or {}).get("index"))
            engine = getattr(self, "v2_engine", None)
            if engine is not None and swing_end is not None and hasattr(engine, "notify_entry_fill"):
                engine.notify_entry_fill(signal.get("symbol"), int(swing_end))
        self.entry_reservations.release(signal.get("symbol"), engine_name)
        # 8. actual-fill recalculation (paper: entry already = exec after spread)
        try:
            pos.recalculate_after_fill(pos.entry_price, pos.size_usd, risk_manager=self.risk)
            if getattr(pos, "risk_invariant_ok", True) is False:
                reason = getattr(pos, "risk_invariant_reason", "RISK_INVARIANT_FAIL")
                print(f"[Fill] VOID {pos.symbol}: {reason} (bez straty, bez cooldown)")
                with self.lock:
                    if pos in self.positions:
                        self.positions.remove(pos)
                return None
        except Exception as e:
            print(f"[Fill] recalc skip: {e}")
        self.risk.register_open()
        self.trade_count += 1
        # Pelny lifecycle w jednym CSV: ACCEPT nie zastępuje zdarzenia OPEN.
        try:
            if self.logger:
                self.logger.log_event(
                    "OPEN", pos.symbol, pos.direction, pos.entry_price,
                    pos.size_usd, 0.0, 0.0, pos.strength,
                    list(pos.reasons or [])[:8] + [
                        f"position_id={pos.id}",
                        f"sl={float(pos.sl_price):.8g}",
                        f"tp1={float(pos.tp1_price):.8g}" if pos.tp1_price else "tp1=none",
                        f"slip_rt={float(pos.slip_rt or 0):.6f}",
                    ], self.risk.current_capital,
                )
        except Exception as e:
            print(f"[Paper] log OPEN: {e}")

        # Etap 2: uzbrój ochronę (exchange SL gdy LIVE, zawsze lokalny SL)
        if self.protection:
            try:
                # callback – Position nie trzyma ProtectionManager
                def _sl_cb(sym, direction, new_sl, size_contracts, _pm=self.protection):
                    if _pm is None:
                        return
                    _pm.update_exchange_sl(sym, direction, new_sl=new_sl, size_contracts=size_contracts)
                pos.on_sl_updated = _sl_cb
                use_tp = (
                    bool(getattr(config, "EXCHANGE_TP_ENABLED", False))
                    and not getattr(config, "NO_HARD_TP", True)
                )
                sl_attach = pos.sl_price
                if _is_v2(getattr(pos, "engine", None)) and not bool(getattr(config, "DAYTRADING_V2_ENTRY_SL", False)):
                    sl_attach = None
                    pos.exchange_sl_planned = False
                self.protection.attach_protection(
                    symbol=pos.symbol,
                    direction=pos.direction,
                    sl_price=sl_attach,
                    tp_price=pos.tp_price if use_tp else None,
                    size_contracts=getattr(pos, "size_contracts", None),
                    entry_price=pos.entry_price,
                )
            except Exception as e:
                print(f"[Protect] attach error: {e}")

        print(f"[Paper] OTWARTO {pos.direction} {pos.symbol}")
        str_bit = "" if getattr(pos, "engine", "") in ("daytrading_v2", "daytrading") and str(getattr(pos, "engine", "")).endswith("v2") else f" | Strength: {pos.strength:.2f}"
        if str(getattr(pos, "engine", "") or "").lower() in ("daytrading_v2", "daytradingv2"):
            str_bit = ""
        print(f"         Entry: ${pos.entry_price:.6f} | Size: ${size:.2f}{str_bit}")
        print(f"         TP: ${pos.tp_price:.6f} | SL: ${pos.sl_price:.6f}")
        print(
            f"         Margin: ${pos.margin:.4f} ({getattr(pos, 'margin_mode', 'isolated')}) "
            f"| MarginCall @ ${pos.margin_call_price:.6f}"
        )
        if getattr(pos, "exchange_sl_planned", False):
            print(f"         Exchange SL (plan): ${getattr(pos, 'exchange_sl_price', pos.sl_price):.6f}")
        if config.TRAILING_STOP_ENABLED:
            print(f"         Trailing: ON @+{config.TRAILING_STOP_ACTIVATION_PCT}% (dynamiczny, bez limitu TP)")
        if getattr(pos, "ride_trend", False):
            print(f"         Mode: RIDE TREND – zysk tylko na trailing (bez twardego TP)")
        return pos

    def update_positions(self, price_map: Dict[str, float]):
        with self.lock:
            self._update_positions_unlocked(price_map)

    def _update_positions_unlocked(self, price_map: Dict[str, float]):
        for pos in self.positions:
            if pos.symbol in price_map:
                pos.update_pnl(price_map[pos.symbol])

    def check_exits(self, signals: List[Dict], price_map: Dict[str, float]):
        with self.lock:
            self._check_exits_unlocked(signals, price_map)

    def _check_exits_unlocked(self, signals: List[Dict], price_map: Dict[str, float]):
        all_signal_map = {s["symbol"]: s for s in signals}
        signal_map = {s["symbol"]: s for s in signals if s["direction"] != "NEUTRAL"}
        to_close = []

        for pos in self.positions:
            current_price = price_map.get(pos.symbol)
            if not current_price:
                continue

            # Candle-gate flag PRZED update (trailing czyta _mtf_aligned)
            if pos.symbol in signal_map:
                sig0 = signal_map[pos.symbol]
                mtf0 = sig0.get("mtf") or {}
                if pos.direction == "LONG":
                    pos._mtf_aligned = bool(mtf0.get("hold_long") or (mtf0.get("long_votes") or 0) >= 2)
                else:
                    pos._mtf_aligned = bool(mtf0.get("hold_short") or (mtf0.get("short_votes") or 0) >= 2)
            else:
                pos._mtf_aligned = False

            # Żywy ATR z bieżącego skanu (fail-open: zostaje poprzedni / z wejścia)
            sig_any = all_signal_map.get(pos.symbol)
            if sig_any:
                atr_now = sig_any.get("atr")
                try:
                    if atr_now is not None and float(atr_now) > 0:
                        pos.live_atr = float(atr_now)
                    atr_pct_now = sig_any.get("atr_pct")
                    if atr_pct_now is not None:
                        pos.atr_pct = float(atr_pct_now)
                except (TypeError, ValueError):
                    pass

            pos.update_pnl(current_price)

            # 0. Emergency local protection (failsafe nawet przy exchange SL)
            if self.protection:
                try:
                    em = self.protection.check_local_protection(
                        pos.symbol, pos.direction, current_price
                    )
                    if em:
                        to_close.append((pos, current_price, em))
                        continue
                except Exception:
                    pass

            # 1. Jeden lifecycle V2 dla runtime i replay. Pozostałe silniki
            # zachowują swój dotychczasowy adapter.
            v2_decision = pos.decide_v2_exit(current_price, all_signal_map.get(pos.symbol)) if _is_v2(pos.engine) else None
            if v2_decision and v2_decision.action == "tp1":
                self.partial_close(pos, float(v2_decision.price), reason="partial_tp1")
                pos.partial_tp1_done = True
                pos.partial_taken = True
                if v2_decision.new_sl is not None:
                    pos.sl_price = float(v2_decision.new_sl)
                    pos.breakeven_active = True
                    pos._sync_exchange_trailing_sl()
                continue
            if v2_decision and v2_decision.action == "tp2":
                self.partial_close(pos, float(v2_decision.price), reason="partial_tp2")
                pos.partial_tp2_done = True
                pos.partial_taken = True
                pos.trailing_active = True
                if v2_decision.new_sl is not None:
                    pos.sl_price = float(v2_decision.new_sl)
                    pos._sync_exchange_trailing_sl()
                continue
            if v2_decision and v2_decision.action:
                to_close.append((pos, float(v2_decision.price or current_price), v2_decision.action))
                continue

            reason = None if _is_v2(pos.engine) else pos.check_tp_sl(current_price)
            if reason == "partial_tp":
                stage = int(getattr(pos, "_partial_stage", 1) or 1)
                if stage >= 2:
                    self.partial_close(pos, current_price, reason="partial_tp2")
                    pos.partial_tp2_done = True
                    pos.partial_taken = True
                else:
                    self.partial_close(pos, current_price, reason="partial_tp1")
                    pos.partial_tp1_done = True
                    # trend: jeden scale-out kończy partial; reversal czeka na TP2
                    if getattr(pos, "engine", "trend") not in ("reversal", "daytrading", "daytrading_v2"):
                        pos.partial_taken = True
                        pos.partial_tp2_done = True
                continue
            if reason:
                to_close.append((pos, current_price, reason))
                continue

            if not _is_v2(pos.engine) and pos.daytrading_time_stop_due(current_price):
                to_close.append((pos, current_price, "day_time_stop"))
                continue
            if not _is_v2(pos.engine) and pos.daytrading_hard_time_stop_due():
                to_close.append((pos, current_price, "day_hard_time_stop"))
                continue
            if _is_v2(getattr(pos, "engine", None)):
                invalidation = pos.v2_htf_invalidation(all_signal_map.get(pos.symbol))
            else:
                invalidation = pos.daytrading_invalidation(all_signal_map.get(pos.symbol))
            if invalidation:
                to_close.append((pos, current_price, invalidation))
                continue

            # Early loss cut: wiek + strata + MTF przeciw
            if getattr(config, "EARLY_LOSS_CUT_ENABLED", True) and not _is_v2(getattr(pos, "engine", None)):
                try:
                    age_min = (datetime.now() - pos.entry_time).total_seconds() / 60.0
                except Exception:
                    age_min = 0
                cut_min = float(getattr(config, "EARLY_LOSS_CUT_MINUTES", 60))
                cut_pnl = float(getattr(config, "EARLY_LOSS_CUT_PNL_PCT", -8.0))
                if age_min >= cut_min and pos.pnl_pct <= cut_pnl:
                    mtf_against = False
                    if pos.symbol in signal_map:
                        mtf = signal_map[pos.symbol].get("mtf") or {}
                        if pos.direction == "LONG":
                            mtf_against = (mtf.get("short_votes") or 0) >= 2 or mtf.get("hold_short")
                        else:
                            mtf_against = (mtf.get("long_votes") or 0) >= 2 or mtf.get("hold_long")
                    require_mtf = getattr(config, "EARLY_LOSS_CUT_REQUIRE_MTF", True)
                    if (not require_mtf) or mtf_against:
                        to_close.append((pos, current_price, "early_loss_cut"))
                        continue

            # 2. Przeciwny silny sygnal / ST / HTF V1
            # V2: 15m opposite NIE zamyka. 4H/1D też nie (EXIT_ON_HTF_REVERSAL=False).
            if _is_v2(getattr(pos, "engine", None)):
                continue
            if pos.symbol in signal_map:
                sig = signal_map[pos.symbol]
                trail_lock = pos.trailing_active and pos.pnl_pct > 5
                if (sig["direction"] != pos.direction and sig["strength"] >= 0.70
                        and not trail_lock):
                    to_close.append((pos, current_price, "opposite_signal"))
                    continue
                # SuperTrend flip vs kierunek pozycji
                if getattr(config, "EXIT_ON_SUPERTREND_FLIP", True):
                    st = sig.get("supertrend") or (sig.get("strategy") or {}).get("supertrend")
                    st_dir = None
                    if isinstance(st, dict):
                        st_dir = "up" if st.get("is_up") else "down"
                    elif st in ("up", "down"):
                        st_dir = st
                    if st_dir:
                        if pos.direction == "LONG" and st_dir == "down":
                            to_close.append((pos, current_price, "supertrend_flip"))
                            continue
                        if pos.direction == "SHORT" and st_dir == "up":
                            to_close.append((pos, current_price, "supertrend_flip"))
                            continue
                # Daily conflict
                if getattr(config, "EXIT_ON_HTF_OPPOSITE", True):
                    daily = sig.get("strategy_daily") or {}
                    if daily.get("pass") and daily.get("direction") in ("LONG", "SHORT"):
                        if daily["direction"] != pos.direction and not trail_lock:
                            to_close.append((pos, current_price, "htf_opposite"))
                            continue

        for pos, price, reason in to_close:
            self.close_position(pos, price, reason)

    def partial_close(self, pos: Position, exit_price: float, reason: str = "partial_tp"):
        """Zamyka część pozycji – PnL jak full close: realized_pnl() (fee+slip+funding pro-rata).
        Reversal: TP1=25% @1R, TP2=35% @2R (reszta trailing).
        """
        is_rev = getattr(pos, "engine", "trend") == "reversal"
        plan = getattr(pos, "tp_plan", None) or {}
        if getattr(pos, "engine", "trend") in ("daytrading", "daytrading_v2") and reason == "partial_tp1":
            key = "DAYTRADING_V2_TP1_FRAC" if _is_v2(pos.engine) else "DAYTRADING_TP1_FRAC"
            pct = float(plan.get("frac_tp1") or getattr(config, key, 0.50))
            pos.partial_stage = 1
        elif is_rev and reason == "partial_tp1":
            pct = float(plan.get("frac_tp1") or getattr(config, "REVERSAL_TP1_FRAC", 0.25))
            pos.partial_stage = 1
        elif getattr(pos, "engine", "trend") in ("daytrading", "daytrading_v2") and reason == "partial_tp2":
            if _is_v2(getattr(pos, "engine", None)):
                pct = float(plan.get("frac_tp2") or getattr(config, "DAYTRADING_V2_TP2_FRAC", 0.50))
                if pct <= 0:
                    pos.partial_tp2_done = True
                    return
                pct = min(0.9, max(0.1, pct))
                pos.partial_stage = 2
            else:
                target_frac_of_original = float(plan.get("frac_tp2") or getattr(config, "DAYTRADING_TP2_FRAC", 0.25))
                orig = float(getattr(pos, "original_size", None) or pos.size_usd)
                close_target = orig * target_frac_of_original
                pct = min(0.9, max(0.1, close_target / max(pos.size_usd, 1e-9)))
                pos.partial_stage = 2
        elif is_rev and reason == "partial_tp2":
            # 35% oryginalnej → przelicz względem aktualnego remain
            target_frac_of_original = float(plan.get("frac_tp2") or getattr(config, "REVERSAL_TP2_FRAC", 0.35))
            orig = float(getattr(pos, "original_size", None) or pos.size_usd)
            close_target = orig * target_frac_of_original
            pct = min(0.9, max(0.1, close_target / max(pos.size_usd, 1e-9)))
            pos.partial_stage = 2
        else:
            pct = float(getattr(config, "PARTIAL_TP_PCT", 0.50))
            pos.partial_stage = max(int(getattr(pos, "partial_stage", 0) or 0), 1)
        pct = min(max(pct, 0.1), 0.9)
        close_contracts = None
        current_contracts = float(getattr(pos, "size_contracts", 0) or 0)
        registry = getattr(Position, "_shared_registry", None)
        if current_contracts > 0 and registry is not None:
            try:
                close_contracts = float(registry.quantize_size(
                    pos.symbol, current_contracts * pct, mode="down"
                ) or 0)
                if close_contracts <= 0:
                    return None
                pct = min(1.0, close_contracts / current_contracts)
            except (TypeError, ValueError, AttributeError):
                close_contracts = None
        close_size = pos.size_usd * pct
        remain = pos.size_usd - close_size
        if remain < 0.5:
            return self.close_position(pos, exit_price, "take_profit")

        # funding pro-rata na zamykaną część
        fund_total = float(getattr(pos, "funding_paid", 0) or 0)
        fund_part = fund_total * pct
        try:
            from accounting import realized_pnl
            r = realized_pnl(
                close_size, pos.entry_price, exit_price, pos.direction,
                leverage=pos.leverage,
                funding_paid=fund_part,
                entry_side=getattr(pos, "entry_side", "taker"),
                exit_side="taker",
                include_slippage=True,
                slip_frac=getattr(pos, "slip_rt", None),
            )
            # partial: entry fee już „zapłacone” przy open całej pozycji –
            # licz exit fee + slip + pro-rata funding; entry fee proporcjonalnie
            pnl = float(r["net_usd"])
            pnl_pct = float(r["pnl_pct"])
            fees = float(r["fee_entry"] + r["fee_exit"] + r["slippage"])
            breakdown = r
        except Exception:
            if pos.direction == "LONG":
                price_change = (exit_price - pos.entry_price) / pos.entry_price
            else:
                price_change = (pos.entry_price - exit_price) / pos.entry_price
            cost = float(getattr(config, "TAKER_FEE", config.COMMISSION_RATE)) * 2 + float(
                getattr(config, "SLIPPAGE", 0.0008)
            )
            net = price_change - cost
            pnl = close_size * net - fund_part
            pnl_pct = (pnl / close_size) * pos.leverage * 100 if close_size else 0.0
            fees = close_size * cost
            breakdown = None

        pos.size_usd = remain
        pos.margin = remain / max(pos.leverage, 1)
        # shadow contracts – proporcjonalnie
        if getattr(pos, "size_contracts", None):
            try:
                if close_contracts is not None:
                    pos.size_contracts = max(0.0, current_contracts - close_contracts)
                else:
                    pos.size_contracts = float(pos.size_contracts) * (1.0 - pct)
            except (TypeError, ValueError):
                pass
        if getattr(pos, "actual_notional", None):
            try:
                pos.actual_notional = float(pos.actual_notional) * (1.0 - pct)
            except (TypeError, ValueError):
                pass
        pos.funding_paid = fund_total - fund_part
        pos.partial_realized_pnl = float(getattr(pos, "partial_realized_pnl", 0.0) or 0.0) + pnl
        pos.partial_realized_fees = float(getattr(pos, "partial_realized_fees", 0.0) or 0.0) + fees
        pos.partial_realized_funding = float(getattr(pos, "partial_realized_funding", 0.0) or 0.0) + fund_part
        pos.partial_taken = True
        # Trailing: po TP2 (reversal) albo od razu (trend single partial)
        stage = int(getattr(pos, "partial_stage", 0) or 0)
        if (getattr(pos, "engine", "trend") != "reversal") or stage >= 2:
            pos.trailing_active = True
            if pos.direction == "LONG":
                pos.trailing_stop_price = exit_price * (1 - pos._trail_distance_pct() / 100 / pos.leverage)
            else:
                pos.trailing_stop_price = exit_price * (1 + pos._trail_distance_pct() / 100 / pos.leverage)

        new_capital = self.risk.current_capital + pnl
        self.risk.update_capital(new_capital, pnl)

        from copy import copy
        hist = copy(pos)
        hist.size_usd = close_size
        hist.actual_notional = close_size
        if current_contracts > 0:
            hist.size_contracts = close_contracts if close_contracts is not None else current_contracts * pct
        hist.margin = close_size / max(pos.leverage, 1)
        hist.pnl = pnl
        hist.pnl_pct = pnl_pct
        hist.fees_paid = fees
        hist.pnl_breakdown = breakdown
        hist.exit_price = exit_price
        hist.exit_time = datetime.now()
        hist.status = "PARTIAL"
        self.partial_closes.append(hist)

        print(f"[Paper] PARTIAL TP {pos.direction} {pos.symbol} | {pct*100:.0f}% @ ${exit_price:.6f}")
        print(f"         PnL czesci: ${pnl:+.4f} ({pnl_pct:+.2f}%) | zostaje size ${remain:.2f} + trailing")
        try:
            from alerts import alert_partial
            alert_partial(pos.symbol, pct, pnl)
        except Exception:
            pass
        try:
            if self.logger:
                self.logger.log_event(
                    "PARTIAL", pos.symbol, pos.direction, exit_price,
                    close_size, pnl, pnl_pct, pos.strength,
                    [f"position_id={pos.id}", f"stage={stage}", f"reason={reason}"],
                    self.risk.current_capital,
                )
        except Exception as e:
            print(f"[Paper] log PARTIAL: {e}")
        return pnl

    def close_by_symbol(self, symbol: str, price_map: dict = None, reason: str = "manual") -> Optional[float]:
        """Ręczne zamknięcie jednej pozycji paper po symbolu."""
        symbol = (symbol or "").upper().strip()
        if not symbol:
            return None
        price_map = price_map or {}
        with self.lock:
            pos = next((p for p in self.positions if (p.symbol or "").upper() == symbol), None)
            if not pos:
                return None
            px = price_map.get(pos.symbol) or price_map.get(symbol)
            if px is None:
                px = getattr(pos, "entry_price", None)
            if px is None:
                return None
            return self.close_position(pos, float(px), reason)

    def close_position(self, pos: Position, exit_price: float, reason: str):

        final_leg_pnl = pos.close(exit_price, reason)
        pnl = float(final_leg_pnl) + float(getattr(pos, "partial_realized_pnl", 0.0) or 0.0)
        pos.final_leg_pnl = float(final_leg_pnl)
        pos.pnl = pnl
        original_size = float(getattr(pos, "original_size", 0.0) or 0.0)
        if original_size > 0:
            pos.pnl_pct = pnl / original_size * max(float(getattr(pos, "leverage", 1) or 1), 1.0) * 100.0
        pos.fees_paid = float(getattr(pos, "fees_paid", 0.0) or 0.0) + float(
            getattr(pos, "partial_realized_fees", 0.0) or 0.0
        )
        if pos in self.positions:
            self.positions.remove(pos)
        self.closed_positions.append(pos)
        self.risk.register_close(getattr(pos, "symbol", None), pnl=pnl, engine=getattr(pos, "engine", None))
        if _is_v2(getattr(pos, "engine", None)):
            mapped = "sl" if reason in ("stop_loss", "margin_call") else (
                "tp" if "tp" in str(reason) or reason == "take_profit" else (
                "htf_reversal" if "reversal" in str(reason) else "trailing"))
            try:
                eng = getattr(self, "v2_engine", None)
                if eng is not None:
                    eng.notify_exit(pos.symbol, pos.direction, mapped, pnl=pnl)
            except Exception as e:
                print(f"[V2] notify_exit: {e}")
        try:
            from decision_telemetry import outcome_snapshot
            outcome_snapshot(pos, pnl, reason)
        except Exception:
            pass
        # 12. kalibracja strength → realized R
        try:
            from strength_calibration import get_calibrator
            risk_usd = float(getattr(pos, "actual_risk_usd", None) or 0) or (
                abs(float(pos.pnl) / max(abs(float(pos.pnl_pct) or 1), 1e-6) * 1)  # fallback weak
            )
            # realized R ≈ pnl / risk_at_entry
            if getattr(pos, "actual_risk_usd", None):
                realized_r = float(pnl) / max(float(pos.actual_risk_usd), 1e-9)
            else:
                # approx from pnl_pct vs typical SL
                sl_pct = abs(float(getattr(__import__('config'), 'STOP_LOSS_PCT', 22)))
                realized_r = float(getattr(pos, "pnl_pct", 0) or 0) / max(sl_pct, 1e-6)
            get_calibrator().update_from_trade(float(getattr(pos, "signal_strength", None) or getattr(pos, "strength", 0) or 0), realized_r)
        except Exception as e:
            print(f"[Calib] update: {e}")
        if getattr(pos, "engine", None) in ("daytrading", "daytrading_v2"):
            try:
                from day_expectancy_calibration import get_day_calibrator
                hit_tp1 = bool(getattr(pos, "partial_tp1_done", False))
                hit_tp2 = bool(reason == "take_profit" and hit_tp1)
                get_day_calibrator().record(
                    hit_tp1, hit_tp2,
                    profile=getattr(pos, "v2_profile", None),
                    regime=getattr(pos, "market_regime", None),
                )
            except Exception as e:
                print(f"[DayCalib] update: {e}")
        if self.protection:
            try:
                self.protection.disarm(pos.symbol, pos.direction)
            except Exception:
                pass

        # Partial legs were credited when they filled. Only the final leg is
        # booked here; `pnl` is the complete position-level result.
        new_capital = self.risk.current_capital + final_leg_pnl
        self.risk.update_capital(new_capital, final_leg_pnl)

        label = {
            "take_profit": "TP",
            "stop_loss": "SL",
            "trailing_stop": "TRAIL",
            "opposite_signal": "OPP",
            "margin_call": "MC",
            "supertrend_flip": "ST_FLIP",
            "htf_opposite": "HTF",
            "early_loss_cut": "TIME_CUT",
        }.get(reason, reason)

        print(f"[Paper] ZAMKNIĘTO {pos.direction} {pos.symbol} | {label}")
        print(f"         Exit: ${exit_price:.6f} | PnL: ${pnl:+.4f} ({pos.pnl_pct:+.2f}%) | Kapitał: ${self.risk.current_capital:.4f}")

        # Log CLOSE do CSV (symetria z OPEN)
        try:
            if self.logger:
                self.logger.log_event(
                    "CLOSE", pos.symbol, pos.direction, exit_price,
                    pos.size_usd, pnl, pos.pnl_pct, pos.strength,
                    list(pos.reasons or [])[:6] + [
                        f"position_id={pos.id}",
                        f"exit={label}",
                        f"gross={float(getattr(pos, 'pnl_gross', 0) or 0):+.6f}",
                        f"costs={float(getattr(pos, 'fees_paid', 0) or 0):.6f}",
                        f"funding={float(getattr(pos, 'funding_paid', 0) or 0):+.6f}",
                    ],
                    self.risk.current_capital,
                )
        except Exception as e:
            print(f"[Paper] log CLOSE: {e}")

        try:
            from alerts import alert_close, alert_margin_call
            if reason == "margin_call":
                alert_margin_call(pos.symbol, pnl)
            else:
                alert_close(pos.symbol, pos.direction, label, pnl)
        except Exception:
            pass

        return pnl



    def on_engine_stop(self, price_map: dict = None, take_profit_pct: float = 5.0,
                       price_map_age_s: float = 0.0) -> dict:
        """
        Przy STOP:
        - pozycje na plusie PO KOSZTACH zamkniecia (PnL% > oczekiwany koszt
          prowizji+poslizgu z dzwignia) -> zamknij od razu
        - neutralne / na minusie (po kosztach) -> zostaw, SL bez zmian,
          TP = +take_profit_pct% zysku (z dzwignia)

        Jesli `price_map_age_s` przekracza STOP_ENGINE_MAX_PRICE_AGE_S, ceny sa
        zbyt nieswieze, zeby na ich podstawie realnie zamykac pozycje (decyzja
        "na plusie" moglaby sie okazac blednym odczytem sprzed kilkunastu minut
        rynku) - wtedy KAZDA pozycja idzie do galezi "zostaw z dostosowanym TP",
        niezaleznie od PnL. To bezpieczniejszy domyslny wybor niz realizacja
        czegos na podstawie starych danych.
        """
        from cryptoedge.portfolio import (
            format_age, max_price_age_s, may_realize_profit, resolve_close_price,
        )

        price_map = price_map or {}
        max_age = max_price_age_s()
        stale = not may_realize_profit(price_map_age_s)
        closed = []
        adjusted = []
        with self.lock:
            for pos in list(self.positions):
                px = resolve_close_price(pos, price_map, price_map_age_s,
                                         "stop_take_profit").price
                try:
                    pos.update_pnl(float(px))
                except Exception:
                    pass
                lev = max(float(getattr(pos, "leverage", 1) or 1), 1.0)
                # Ta sama funkcja accounting i ten sam slip_rt co rzeczywisty close.
                # Nie wolno kwalifikowac zysku statycznym config.SLIPPAGE, gdy fill
                # uzywa dynamicznego impactu/profilu instrumentu.
                projected_net = float("-inf")
                try:
                    from accounting import realized_pnl
                    projected_net = float(realized_pnl(
                        pos.size_usd, pos.entry_price, float(px), pos.direction,
                        leverage=lev,
                        funding_paid=getattr(pos, "funding_paid", 0.0) or 0.0,
                        entry_side=getattr(pos, "entry_side", "taker"),
                        exit_side="taker", include_slippage=True,
                        slip_frac=getattr(pos, "slip_rt", None),
                    )["net_usd"])
                except Exception as e:
                    print(f"[STOP] projected net {pos.symbol}: {e}")
                pnl_pct = float(getattr(pos, "pnl_pct", 0) or 0)
                if not stale and projected_net > 0.0:
                    self.close_position(pos, float(px), "stop_take_profit")
                    closed.append(pos.symbol)
                else:
                    # TP na +5% PnL (uwzględnia leverage)
                    move = float(take_profit_pct) / 100.0 / lev
                    if pos.direction == "LONG":
                        pos.tp_price = pos.entry_price * (1.0 + move)
                    else:
                        pos.tp_price = pos.entry_price * (1.0 - move)
                    pos.ride_trend = False
                    pos.hard_tp_after_stop = True
                    # trailing może zostać – SL już jest
                    adjusted.append({
                        "symbol": pos.symbol,
                        "direction": pos.direction,
                        "pnl_pct": round(float(pos.pnl_pct or 0), 2),
                        "tp": pos.tp_price,
                        "sl": pos.sl_price,
                    })
                    stale_note = f" [ceny sprzed {format_age(price_map_age_s)} - pominieto decyzje o zamknieciu]" if stale else ""
                    print(
                        f"[STOP] Zostawiam {pos.direction} {pos.symbol} "
                f"PnL {pos.pnl_pct:+.2f}% | TP->${pos.tp_price:.6f} (+{take_profit_pct:.0f}% PnL) | SL ${pos.sl_price:.6f}{stale_note}"
                    )
        if closed:
            print(f"[STOP] Zamknięto na plusie (po kosztach): {', '.join(closed)}")
        if stale:
            print(f"[STOP] last_price_map ma {format_age(price_map_age_s)} ({max_age:.0f}s limit) - zadna pozycja nie zostala zamknieta na podstawie tych cen.")
        return {"closed_profit": closed, "kept_with_tp": adjusted, "prices_stale": stale}


    def close_all(self, price_map: dict, reason: str = "close_all", price_map_age_s=None):
        with self.lock:
            return self._close_all_unlocked(price_map, reason, price_map_age_s)

    def _close_all_unlocked(self, price_map: dict, reason: str = "close_all",
                            price_map_age_s=None):
        """Zamyka wszystkie otwarte pozycje po aktualnej cenie.

        25.08.2026: kill_switch() i close_all() wolaly to bez informacji o
        wieku mapy cen. Przy zamrozonym feedzie (zaobserwowano 43788 s) ksiega
        dostawala fill po cenie sprzed godzin, a brak symbolu w mapie ksiegowal
        PnL = 0 po entry_price - jedno i drugie po cichu. Awaryjne zamkniecie
        nadal ma sie wykonac, ale ma zostawic slad w powodzie zamkniecia, zeby
        historia nie udawala normalnej transakcji.
        """
        from cryptoedge.portfolio import (
            format_age, max_price_age_s, prices_are_stale, resolve_close_price,
        )

        price_map = price_map or {}
        if prices_are_stale(price_map_age_s):
            print(f"[CLOSE_ALL] UWAGA: ceny maja {format_age(price_map_age_s)} "
                  f"({max_price_age_s():.0f}s limit) - zamykam mimo to, "
                  f"wyniki oznaczone STALE_PRICE.")
        closed = []
        for pos in list(self.positions):
            decision = resolve_close_price(pos, price_map, price_map_age_s, reason)
            if decision.is_fallback:
                print(f"[CLOSE_ALL] {pos.symbol}: brak ceny w mapie - ksieguje po entry (PnL 0).")
            pnl = self.close_position(pos, decision.price, decision.reason)
            closed.append((pos.symbol, pnl))
        return closed

    def session_metrics(self) -> dict:
        with self.lock:
            closed = list(self.closed_positions)
        if not closed:
            return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                    "profit_factor": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "net_pnl": 0.0}
        wins = [p for p in closed if p.pnl > 0]
        losses = [p for p in closed if p.pnl <= 0]
        gp = sum(p.pnl for p in wins)
        gl = abs(sum(p.pnl for p in losses))
        pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)
        longs = [p for p in closed if p.direction == "LONG"]
        shorts = [p for p in closed if p.direction == "SHORT"]
        breakdowns = [getattr(p, "pnl_breakdown", {}) or {} for p in closed]
        return {
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1),
            "profit_factor": round(min(pf, 99.0), 2),
            "avg_win": round(gp / len(wins), 4) if wins else 0.0,
            "avg_loss": round(-gl / len(losses), 4) if losses else 0.0,
            "net_pnl": round(sum(p.pnl for p in closed), 4),
            "long_trades": len(longs),
            "short_trades": len(shorts),
            "long_pnl": round(sum(p.pnl for p in longs), 4),
            "short_pnl": round(sum(p.pnl for p in shorts), 4),
            "expectancy": round(sum(p.pnl for p in closed) / len(closed), 4) if closed else 0.0,
            "gross_pnl": round(sum(float(b.get("gross_usd") or 0) for b in breakdowns), 4),
            "fee_entry": round(sum(float(b.get("fee_entry") or 0) for b in breakdowns), 4),
            "fee_exit": round(sum(float(b.get("fee_exit") or 0) for b in breakdowns), 4),
            "slippage": round(sum(float(b.get("slippage") or 0) for b in breakdowns), 4),
            "funding": round(sum(float(b.get("funding_paid") or 0) for b in breakdowns), 4),
        }

    def get_funds_breakdown(self) -> dict:
        """Kapital / zajete (margin) / wolne srodki + niezrealizowany PnL + portfolio risk."""
        with self.lock:
            positions_snapshot = list(self.positions)
        used = sum(getattr(p, "margin", p.size_usd / max(p.leverage, 1)) for p in positions_snapshot)
        unrealized = sum(p.pnl for p in positions_snapshot)
        capital = self.risk.current_capital
        # equity = capital + unrealized (capital juz bez zamknietego; paper trzyma capital po close)
        equity = capital + unrealized
        free = max(0.0, capital - used)
        out = {
            "capital": round(capital, 4),
            "used_margin": round(used, 4),
            "free_margin": round(free, 4),
            "unrealized_pnl": round(unrealized, 4),
            "equity": round(equity, 4),
        }
        try:
            from portfolio_risk import portfolio_snapshot
            snap = portfolio_snapshot(positions_snapshot, equity)
            out["gross_exposure"] = snap["exposure"]["gross_usd"]
            out["net_exposure"] = snap["exposure"]["net_usd"]
            out["effective_leverage"] = snap["leverage"]["effective_leverage"]
            out["margin_usage"] = snap["leverage"]["margin_usage"]
            out["cluster_max"] = snap["clusters"].get("max_cluster_id")
            out["cluster_max_gross"] = snap["clusters"].get("max_cluster_gross")
            out["portfolio"] = snap
        except Exception:
            pass
        return out

    def get_closed_summary(self, limit: int = 20):
        rows = []
        with self.lock:
            snapshot = list(self.closed_positions[-limit:])
        for p in snapshot:
            rows.append({
                "id": p.id,
                "symbol": p.symbol,
                "direction": p.direction,
                "entry": p.entry_price,
                "exit": p.exit_price,
                "pnl": round(p.pnl, 4),
                "pnl_pct": round(p.pnl_pct, 2),
                "entry_time": p.entry_time.isoformat(timespec="seconds") if p.entry_time else None,
                "exit_time": p.exit_time.isoformat(timespec="seconds") if p.exit_time else None,
                "sl": getattr(p, "sl_price", None),
                "tp": getattr(p, "tp_price", None),
                "tp1": getattr(p, "tp1_price", None),
                "tp2": getattr(p, "tp2_price", None),
                "leverage": getattr(p, "leverage", None),
                "strength": getattr(p, "strength", None),
                "engine": getattr(p, "engine", None),
                "decision_path": getattr(p, "decision_path", None),
                "reasons": list(getattr(p, "reasons", None) or [])[:8],
                "status": getattr(p, "status", None),
                "fees": getattr(p, "fees_paid", None),
                "gross_pnl": (getattr(p, "pnl_breakdown", {}) or {}).get("gross_usd"),
                "fee_entry": (getattr(p, "pnl_breakdown", {}) or {}).get("fee_entry"),
                "fee_exit": (getattr(p, "pnl_breakdown", {}) or {}).get("fee_exit"),
                "slippage": (getattr(p, "pnl_breakdown", {}) or {}).get("slippage"),
                "funding": (getattr(p, "pnl_breakdown", {}) or {}).get("funding_paid"),
            })
        return list(reversed(rows))

    def get_open_summary(self) -> List[Dict]:
        with self.lock:
            positions = list(self.positions)
        rows = []
        for p in positions:
            age_sec = int((datetime.now() - p.entry_time).total_seconds()) if p.entry_time else 0
            if age_sec >= 3600:
                age = f"{age_sec // 3600}h {(age_sec % 3600) // 60}m"
            elif age_sec >= 60:
                age = f"{age_sec // 60}m {age_sec % 60}s"
            else:
                age = f"{age_sec}s" if p.entry_time else "—"
            rows.append({
                "id": p.id,
                "symbol": p.symbol,
                "direction": p.direction,
                "entry": p.entry_price,
                "tp": p.tp_price,
                "tp1": getattr(p, "tp1_price", None),
                "tp2": getattr(p, "tp2_price", None),
                "sl": p.sl_price,
                "trailing_active": p.trailing_active,
                "trailing_stop": p.trailing_stop_price,
                "breakeven_active": bool(getattr(p, "breakeven_active", False)),
                "pnl_at_stop": p.pnl_at_stop(),
                "size": p.size_usd,
                "margin": round(getattr(p, "margin", p.size_usd / max(p.leverage, 1)), 4),
                "margin_ratio": round(getattr(p, "margin_ratio", 1.0), 3),
                "margin_call_price": getattr(p, "margin_call_price", None),
                "age_sec": age_sec,
                "age": age,
                "entry_time": p.entry_time.isoformat(timespec="seconds") if p.entry_time else None,
                "pnl": round(p.pnl, 4),
                "pnl_pct": round(p.pnl_pct, 2),
                "strength": p.strength,
                "market": getattr(p, "current_price", None) or p.entry_price,
            })
        return rows

    def export_open_positions(self) -> list:
        with self.lock:
            positions = list(self.positions)
        rows = []
        for p in positions:
            rows.append({
                "id": p.id,
                "symbol": p.symbol,
                "direction": p.direction,
                "entry_price": p.entry_price,
                "size_usd": p.size_usd,
                "actual_notional": getattr(p, "actual_notional", p.size_usd),
                "size_contracts": getattr(p, "size_contracts", None),
                "contract_value": getattr(p, "contract_value", None),
                "leverage": p.leverage,
                "margin": p.margin,
                "tp_price": p.tp_price,
                "sl_price": p.sl_price,
                "strength": p.strength,
                "reasons": p.reasons,
                "trailing_active": p.trailing_active,
                "trailing_stop_price": p.trailing_stop_price,
                "live_atr": getattr(p, "live_atr", None),
                "breakeven_active": bool(getattr(p, "breakeven_active", False)),
                "atr": getattr(p, "atr", None),
                "highest_price": p.highest_price,
                "lowest_price": p.lowest_price,
                "margin_call_price": getattr(p, "margin_call_price", None),
                "ride_trend": getattr(p, "ride_trend", False),
                "funding_paid": getattr(p, "funding_paid", 0.0),
                "slip_rt": getattr(p, "slip_rt", None),
                "partial_taken": getattr(p, "partial_taken", False),
                "partial_tp1_done": getattr(p, "partial_tp1_done", False),
                "partial_tp2_done": getattr(p, "partial_tp2_done", False),
                "partial_stage": getattr(p, "partial_stage", 0),
                "tp1_price": getattr(p, "tp1_price", None),
                "tp2_price": getattr(p, "tp2_price", None),
                "tp_plan": getattr(p, "tp_plan", None),
                "engine": getattr(p, "engine", "trend"),
                "decision_path": getattr(p, "decision_path", None),
                "original_size": getattr(p, "original_size", p.size_usd),
                "actual_risk_usd": getattr(p, "actual_risk_usd", None),
                "entry_time": p.entry_time.isoformat() if p.entry_time else None,
            })
        return rows

    def restore_open_positions(self, rows: list):
        """Odtworz pozycje po restarcie bota."""
        if not rows:
            return 0
        from datetime import datetime as _dt
        n = 0
        for r in rows:
            try:
                sig = {
                    "symbol": r["symbol"],
                    "direction": r["direction"],
                    "price": r["entry_price"],
                    "strength": r.get("strength") or 0.7,
                    "reasons": r.get("reasons") or ["RESTORED"],
                    "tp_price": r.get("tp_price"),
                    "sl_price": r.get("sl_price"),
                    "tp1_price": r.get("tp1_price"),
                    "tp2_price": r.get("tp2_price"),
                    "tp_plan": r.get("tp_plan"),
                    "engine": r.get("engine") or "trend",
                    "decision_path": r.get("decision_path"),
                    "slip_rt": r.get("slip_rt"),
                }
                pos = Position(sig, r["size_usd"], r.get("leverage") or config.LEVERAGE)
                pos.id = r.get("id") or pos.id
                if r.get("entry_time"):
                    try:
                        pos.entry_time = _dt.fromisoformat(r["entry_time"])
                    except Exception:
                        pass
                pos.trailing_active = bool(r.get("trailing_active"))
                pos.trailing_stop_price = r.get("trailing_stop_price")
                pos.breakeven_active = bool(r.get("breakeven_active"))
                if r.get("live_atr") is not None:
                    try:
                        pos.live_atr = float(r["live_atr"])
                    except (TypeError, ValueError):
                        pass
                if r.get("atr") is not None:
                    try:
                        pos.atr = float(r["atr"])
                    except (TypeError, ValueError):
                        pass
                pos.highest_price = r.get("highest_price") or pos.entry_price
                pos.lowest_price = r.get("lowest_price") or pos.entry_price
                if r.get("margin_call_price"):
                    pos.margin_call_price = r["margin_call_price"]
                pos.ride_trend = bool(r.get("ride_trend"))
                pos.actual_notional = float(r.get("actual_notional") or pos.size_usd)
                pos.size_contracts = r.get("size_contracts", pos.size_contracts)
                pos.contract_value = r.get("contract_value", pos.contract_value)
                pos.funding_paid = float(r.get("funding_paid") or 0.0)
                if r.get("slip_rt") is not None:
                    try:
                        pos.slip_rt = float(r["slip_rt"])
                    except (TypeError, ValueError):
                        pass
                pos.partial_taken = bool(r.get("partial_taken"))
                pos.partial_tp1_done = bool(r.get("partial_tp1_done"))
                pos.partial_tp2_done = bool(r.get("partial_tp2_done"))
                pos.partial_stage = int(r.get("partial_stage") or 0)
                pos.original_size = float(r.get("original_size") or pos.size_usd)
                pos.actual_risk_usd = r.get("actual_risk_usd", getattr(pos, "actual_risk_usd", None))
                if self.protection:
                    def _sl_cb(sym, direction, new_sl, size_contracts, _pm=self.protection):
                        if _pm is not None:
                            _pm.update_exchange_sl(
                                sym, direction, new_sl=new_sl,
                                size_contracts=size_contracts,
                            )
                    pos.on_sl_updated = _sl_cb
                with self.lock:
                    self.positions.append(pos)
                self.risk.register_open()
                n += 1
            except Exception as e:
                print(f"[Paper] Restore fail {r.get('symbol')}: {e}")
        return n
