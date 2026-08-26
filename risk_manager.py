# ============================================================
# Risk Manager - limity, drawdown, halt, rezerwa kapitalu
# ============================================================

from typing import Dict, List, Optional
import math
import config
from datetime import date, datetime, timedelta
import time


class RiskManager:
    def __init__(self, starting_capital: float = config.STARTING_CAPITAL):
        self.starting_capital = starting_capital
        self.current_capital = starting_capital
        # Osobny kapitał DEMO (paper) – nie miesza się z equity Blofin
        self.paper_capital = starting_capital
        self.paper_peak_equity = starting_capital
        self.live_equity = None  # ostatnie saldo z giełdy (podgląd)
        self.daily_start_capital = starting_capital
        self.daily_pnl = 0.0
        self.current_day = date.today()
        self.open_positions_count = 0
        self.is_halted = False
        self.halt_reason = None
        self.peak_equity = starting_capital
        self.max_drawdown_pct = 0.0
        self.paused = False
        self.symbol_cooldown = {}  # symbol -> datetime when free
        self.engine_symbol_cooldown = {}
        self.engine_symbol_loss_streak = {}
        self.last_btc_change = 0.0
        self.reject_log = []  # recent rejects for UI
        # Circuit breaker: seria strat
        self.consecutive_losses = 0
        self.loss_pause_until: Optional[datetime] = None
        # Ostatni znany reżim (ustawiany z pętli)
        self.last_regime: str = "UNKNOWN"
        self._position_tier_cache = {}

    def _venue_mmr(self, signal: Dict) -> tuple:
        """Resolve BloFin maintenance rate for the planned contract-size tier."""
        symbol = str(signal.get("symbol") or "").upper()
        default = float(getattr(config, "MAINTENANCE_MARGIN_RATE", 0.005))
        if not symbol:
            return default, "config"
        now = time.time()
        cached = self._position_tier_cache.get(symbol)
        if not cached or now - cached[0] > 3600:
            try:
                feed = getattr(getattr(self, "feeder", None), "blofin", None)
                tiers = feed.fetch_position_tiers(symbol, getattr(config, "MARGIN_MODE", "isolated")) if feed else []
                if tiers:
                    self._position_tier_cache[symbol] = (now, tiers)
                    cached = self._position_tier_cache[symbol]
            except Exception:
                cached = None
        tiers = cached[1] if cached else []
        if not tiers:
            return default, "config"
        contracts = 0.0
        try:
            registry = getattr(self, "instrument_registry", None)
            planned = float(signal.get("_planned_notional") or signal.get("size_usd") or 0)
            price = float(signal.get("price") or 0)
            if registry and planned > 0 and price > 0:
                conv = registry.notional_to_contracts(symbol, planned, price)
                contracts = float(conv.get("contracts") or 0)
        except Exception:
            contracts = 0.0
        tier = next((x for x in tiers if x["min_size"] <= contracts <= x["max_size"]), tiers[-1])
        mmr = float(tier.get("maintenance_margin_rate") or default)
        return (mmr if mmr > 0 else default), "blofin_position_tier"

    def apply_demo_capital(self):
        """Przywróć kapitał DEMO ($STARTING / paper_capital)."""
        cap = float(self.paper_capital or self.starting_capital or config.STARTING_CAPITAL)
        self.current_capital = cap
        self.peak_equity = max(float(self.paper_peak_equity or cap), cap)
        self.daily_start_capital = cap
        return cap

    def apply_live_equity(self, equity: float, available: float = None):
        """Ustaw podgląd kapitału z Blofin (LIVE) – bez ruszania paper_capital."""
        eq = float(equity or 0)
        self.live_equity = eq
        if eq > 0:
            self.current_capital = eq
            self.update_equity_peak(eq)
        return eq

    def note_paper_capital(self, capital: float = None):
        """Zapamiętaj aktualny kapitał jako DEMO (przed przełączeniem na LIVE)."""
        if capital is None:
            capital = self.current_capital
        self.paper_capital = float(capital)
        self.paper_peak_equity = max(float(self.paper_peak_equity or 0), float(capital))

    def _check_new_day(self):
        today = date.today()
        if today != self.current_day:
            self.current_day = today
            self.daily_start_capital = self.current_capital
            self.daily_pnl = 0.0
            if self.halt_reason and "Daily" in (self.halt_reason or ""):
                self.is_halted = False
                self.halt_reason = None
            print(f"[RiskManager] Nowy dzien – reset daily PnL. Kapital: ${self.current_capital:.2f}")

    def update_equity_peak(self, equity: float):
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity > 0:
            dd = (self.peak_equity - equity) / self.peak_equity
            self.max_drawdown_pct = max(self.max_drawdown_pct, dd)

    def check_drawdown(self, equity: float) -> bool:
        self.update_equity_peak(equity)
        limit = getattr(config, "MAX_DRAWDOWN_PCT", 0.25)
        if self.peak_equity <= 0:
            return False
        dd = (self.peak_equity - equity) / self.peak_equity
        if dd >= limit:
            self.is_halted = True
            self.halt_reason = f"Max drawdown {dd*100:.1f}% >= {limit*100:.0f}%"
            return True
        return False

    def update_capital(self, new_capital: float, pnl_change: float = 0.0):
        self._check_new_day()
        self.current_capital = new_capital
        self.daily_pnl += pnl_change
        self.update_equity_peak(new_capital)
        # W DEMO aktualizuj też paper_capital (oddzielone od LIVE equity)
        from cryptoedge.domain import trading_mode
        if trading_mode.is_paper(config):
            self.paper_capital = float(new_capital)
            self.paper_peak_equity = max(float(self.paper_peak_equity or 0), float(new_capital))

        daily_loss_pct = -self.daily_pnl / self.daily_start_capital if self.daily_start_capital > 0 else 0
        if daily_loss_pct >= config.DAILY_LOSS_LIMIT:
            self.is_halted = True
            self.halt_reason = f"Daily loss limit ({daily_loss_pct*100:.1f}%)"
            print(f"[RiskManager] BOT HALT: {self.halt_reason}")

    def sync_open_count(self, n: int):
        """Utrzymaj licznik zgodny z realną listą pozycji (po manual close itd.)."""
        self.open_positions_count = max(0, int(n))

    def set_regime(self, regime: str):
        self.last_regime = (regime or "UNKNOWN").upper()

    def note_trade_result(self, pnl: float):
        """Aktualizuj serię strat / wygranych po zamknięciu."""
        if pnl < 0:
            self.consecutive_losses += 1
            limit = int(getattr(config, "CONSECUTIVE_LOSS_LIMIT", 0) or 0)
            if limit > 0 and self.consecutive_losses >= limit:
                mins = int(getattr(config, "CONSECUTIVE_LOSS_PAUSE_MIN", 45) or 45)
                self.loss_pause_until = datetime.now() + timedelta(minutes=mins)
                print(f"[Risk] Circuit breaker: {self.consecutive_losses} strat z rzędu → pauza {mins} min")
        else:
            self.consecutive_losses = 0

    def prepare_signal_for_sizing(self, signal: Dict) -> Dict:
        """Nakłada wyłącznie deterministyczne mnożniki znane przed sizingiem.

        Bramka ryzyka nie może dopiero po obliczeniu notionalu zmniejszyć
        `_size_mult`, bo projected loss i rzeczywista pozycja używałyby innych
        wielkości. Metoda jest idempotentna (zawsze bierze minimum).
        """
        regime = str(signal.get("market_regime") or self.last_regime or "UNKNOWN").upper()
        if regime != "PANIC":
            return signal
        engine = str(signal.get("engine") or "trend").lower()
        multiplier = 1.0
        if engine == "daytrading":
            multiplier = getattr(config, "DAYTRADING_PANIC_SIZE_MULT", 1.0)
        elif engine not in ("reversal", "daytrading_v2", "daytradingv2"):
            multiplier = getattr(config, "REGIME_PANIC_TREND_SIZE_MULT", None)
            if multiplier is None:
                multiplier = getattr(config, "REGIME_PANIC_SIZE_MULT", 1.0)
        try:
            multiplier = float(multiplier)
        except (TypeError, ValueError):
            multiplier = 1.0
        if 0.0 <= multiplier < 1.0:
            signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), multiplier)
        return signal

    def can_open_position(self, signal: Dict, open_directions: List[str] = None) -> tuple:
        self._check_new_day()
        if str(getattr(self, "risk_state", "NORMAL")).upper() == "REDUCE_ONLY":
            return False, "RISK_REDUCE_ONLY"
        # Ksztalt sygnalu: cryptoedge.risk.validation, jeden wlasciciel reguly.
        from cryptoedge.risk import limits as risk_limits
        from cryptoedge.risk import validation as risk_validation

        direction = risk_validation.normalized_direction(signal)
        ok_shape, why_shape = risk_validation.validate_signal_shape(signal)
        if not ok_shape:
            return False, why_shape
        self.prepare_signal_for_sizing(signal)
        # DEMO nigdy nie blokuj przez „LIVE”
        if self.paused:
            return False, "Pauza (STOP/PAUSE) – kliknij START"
        if self.is_halted:
            return False, self.halt_reason or "Bot zatrzymany"
        # Projekcja dziennej straty: budzet i porownanie licza limits, tutaj
        # zostaje tylko odczyt stanu. Wyjatek nadal lapiemy na miejscu - to
        # brakujacy atrybut na self/config, nie blad reguly.
        try:
            budget = risk_limits.daily_loss_budget_remaining(
                self.daily_start_capital, self.daily_pnl, config
            )
            ok_daily, why_daily = risk_limits.projected_loss_ok(
                float(signal.get("_planned_notional") or 0),
                self._sl_distance_pct(signal),
                budget,
            )
        except (TypeError, ValueError, AttributeError):
            return False, "DAILY_PROJECTED_LOSS_UNAVAILABLE"
        if not ok_daily:
            return False, why_daily
        # Circuit breaker po serii strat
        if self.loss_pause_until and datetime.now() < self.loss_pause_until:
            left = (self.loss_pause_until - datetime.now()).total_seconds() / 60
            return False, f"LOSS_STREAK_PAUSE {left:.0f}min"
        elif self.loss_pause_until and datetime.now() >= self.loss_pause_until:
            self.loss_pause_until = None
            self.consecutive_losses = 0

        # Regime tiered: max pozycji w RANGE.
        # Sufit slotow liczy cryptoedge.risk.limits - jeden wlasciciel reguly.
        regime = (signal.get("market_regime") or self.last_regime or "UNKNOWN").upper()
        max_pos = risk_limits.max_positions_for_regime(regime, config)
        if regime == "RANGE":
            pass
        elif regime == "PANIC":
            # PANIC = silny jednokierunkowy ruch, nie halt.
            # V2/Trend/Reversal wchodzą; sloty jak zawsze (MAX_POSITIONS).
            eng = (signal.get("engine") or "trend").lower()
            if eng == "reversal":
                min_rev = float(getattr(config, "REVERSAL_MIN_STRENGTH", config.MIN_SIGNAL_STRENGTH))
                if float(signal.get("strength") or 0) < min_rev:
                    return False, f"REGIME_PANIC_REV_WEAK({float(signal.get('strength') or 0):.2f}<{min_rev})"
            elif eng in ("daytrading_v2", "daytradingv2"):
                pass  # V2: checklista w silniku, dummy strength nie jest bramką
            elif eng == "daytrading":
                panic_mult = getattr(config, "DAYTRADING_PANIC_SIZE_MULT", 1.0)
                try:
                    panic_mult = float(panic_mult)
                except (TypeError, ValueError):
                    panic_mult = 1.0
                if 0 <= panic_mult < 1.0:
                    signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), panic_mult)
                min_panic = float(getattr(config, "DAYTRADING_PANIC_MIN_STRENGTH", 0.0) or 0.0)
                if min_panic > 0 and float(signal.get("strength") or 0) < min_panic:
                    return False, f"REGIME_PANIC_DAY({float(signal.get('strength') or 0):.2f}<{min_panic})"
            else:
                panic_mult = getattr(config, "REGIME_PANIC_TREND_SIZE_MULT", None)
                if panic_mult is None:
                    panic_mult = getattr(config, "REGIME_PANIC_SIZE_MULT", 1.0)
                try:
                    panic_mult = float(panic_mult)
                except (TypeError, ValueError):
                    panic_mult = 1.0
                if 0 <= panic_mult < 1.0:
                    signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), panic_mult)
                min_panic = float(getattr(config, "REGIME_PANIC_TREND_MIN_STRENGTH", 0.0) or 0.0)
                if min_panic > 0 and float(signal.get("strength") or 0) < min_panic:
                    return False, f"REGIME_PANIC_TREND({float(signal.get('strength') or 0):.2f}<{min_panic})"
        ok_slot, why_slot = risk_limits.slot_available(self.open_positions_count, max_pos, regime)
        if not ok_slot:
            return False, why_slot
        ok_cap, why_cap = risk_limits.capital_sufficient(self.current_capital)
        if not ok_cap:
            return False, why_cap
        # PRIORYTET 5: osobny score per silnik
        eng_s = (signal.get("engine") or signal.get("score_type") or "trend").lower()
        if eng_s not in ("daytrading_v2", "daytradingv2"):
            if eng_s == "reversal":
                score = float(
                    signal["reversal_score"]
                    if signal.get("reversal_score") is not None
                    else (signal.get("strength") or 0)
                )
                min_s = float(getattr(config, "REVERSAL_MIN_STRENGTH", config.MIN_SIGNAL_STRENGTH))
            else:
                score = float(
                    signal["trend_score"]
                    if signal.get("trend_score") is not None
                    else (signal.get("strength") or 0)
                )
                min_s = float(config.MIN_SIGNAL_STRENGTH)
            signal["strength"] = score
            if score < min_s:
                return False, f"Za slaby sygnal {eng_s} ({score:.2f}<{min_s})"
        if bool(getattr(config, "USE_EXPECTED_R_FILTER", False)):
            try:
                from strength_calibration import get_calibrator
                er = signal.get("expected_r")
                if er is None:
                    er = get_calibrator().expected_r(signal.get("strength"))
                min_er = float(getattr(config, "MIN_EXPECTED_R", 0.55))
                if float(er) < min_er:
                    return False, f"LOW_EXPECTED_R({float(er):.2f}<{min_er})"
            except Exception:
                return False, "EXPECTED_R_UNAVAILABLE"

        # Heat limit: max % slotów w tym samym kierunku
        direction = signal.get("direction")
        if open_directions is None:
            open_directions = []
        ok_heat, why_heat = risk_limits.heat_limit_ok(direction, open_directions, config)
        if not ok_heat:
            return False, why_heat

        # Drift reconciliation – TYLKO LIVE. PAPER: lokalne pozycje vs pusta
        # giełda to stan oczekiwany, nie blokada wejść (RECONCILE_DRIFT).
        from cryptoedge.domain import trading_mode
        if trading_mode.is_live(config) and getattr(config, "BLOCK_ENTRIES_ON_RECONCILE_DRIFT", True):
            try:
                rec = getattr(self, "_reconciler_ref", None)
                if rec is not None and hasattr(rec, "blocks_new_entries") and rec.blocks_new_entries():
                    return False, "RECONCILE_DRIFT"
            except Exception:
                return False, "RECONCILE_CHECK_FAILED"

        # Etap 5: portfolio risk – rzeczywisty notional (nie approx)
        if getattr(config, "PORTFOLIO_RISK_ENABLED", True):
            try:
                from portfolio_risk import check_portfolio_limits
                positions = signal.get("_open_positions_ref")
                if positions is None:
                    positions = getattr(self, "_positions_ref", None) or []
                planned = signal.get("_planned_notional")
                if planned is None:
                    planned = self.calculate_position_size(signal)
                try:
                    planned = float(planned)
                except (TypeError, ValueError):
                    planned = 0.0
                signal["_planned_notional"] = planned
                equity = self.equity_for_sizing() if bool(getattr(config, "SIZE_ON_EQUITY", True)) else float(self.current_capital or 0)
                ok_p, why_p = check_portfolio_limits(
                    positions, equity, new_signal=signal, new_notional=planned
                )
                if not ok_p:
                    return False, why_p
            except Exception as e:
                print(f"[Risk] portfolio check err: {type(e).__name__}: {e}")
                return False, "PORTFOLIO_CHECK_FAILED"

        # Filtr strategii primary (4h) + fallback MTF
        require_pri = getattr(config, "REQUIRE_PRIMARY_STRATEGY", getattr(config, "REQUIRE_STRATEGY_1H", True))
        aggressive = getattr(config, "AGGRESSIVE_MODE", False)
        regime = (signal.get("market_regime") or "").upper()
        mtf = signal.get("mtf") or {}
        min_votes = int(getattr(config, "MTF_MIN_VOTES_FALLBACK", 2) or 2)
        direction = signal.get("direction")
        long_votes = int(mtf.get("long_votes") or 0)
        short_votes = int(mtf.get("short_votes") or 0)
        mtf_ok = (
            (direction == "LONG" and long_votes >= min_votes)
            or (direction == "SHORT" and short_votes >= min_votes)
        )

        is_v2 = eng_s in ("daytrading_v2", "daytradingv2") or signal.get("strategy_mode") == "DAYTRADING_V2"
        is_day = (eng_s == "daytrading" or signal.get("strategy_mode") == "DAYTRADING") and not is_v2
        if is_day:
            intraday = signal.get("intraday") or {}
            setup = str(signal.get("setup") or "")
            setup_ok = setup in (
                "intraday_5m_confirmed",
                "intraday_15m_confirmed",
                "intraday_confirmed",
            )
            if not setup_ok:
                return False, "DAY_SETUP_NOT_CONFIRMED"
            if str(signal.get("signal_source") or "") != "BLOFIN_NATIVE":
                return False, "DAY_NON_NATIVE_SOURCE"
        if require_pri and not aggressive and not is_day and not is_v2:
            strat = signal.get("strategy")
            strength = float(signal.get("strength") or 0)
            reasons_u = " ".join(str(r) for r in (signal.get("reasons") or []))
            soft_ok = (
                "PRIMARY_MTF_FALLBACK" in reasons_u
                or "PRIMARY_SOFT_PASS" in reasons_u
                or "STRAT_SOFT_ALIGN" in reasons_u
            )
            if strat:  # mamy wynik ewaluacji 4h
                if not strat.get("pass"):
                    # MTF majority lub soft-align (ADX+ST) → wpuść z mniejszym size
                    if mtf_ok or soft_ok:
                        signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), 0.6)
                    else:
                        return False, "STRAT_PRIMARY_FAIL"
                elif strat.get("direction") and strat["direction"] != direction:
                    if mtf_ok:
                        signal["_size_mult"] = min(float(signal.get("_size_mult") or 1.0), 0.5)
                    else:
                        return False, "STRAT_PRIMARY_CONFLICT"
            else:
                # Brak 4h (STRAT_PRIMARY_NA) – nie blokuj wszystkiego w RANGE:
                # OK gdy: MTF>=min  LUB  siła wysoka (RANGE: >=0.68, inaczej MIN+0.08)
                min_str = float(config.MIN_SIGNAL_STRENGTH)
                range_min = float(getattr(config, "STRAT_NA_RANGE_MIN_STRENGTH", 0.68))
                block_na_range = getattr(config, "BLOCK_STRAT_NA_IN_RANGE", True)
                if mtf_ok:
                    pass
                elif regime == "RANGE" and block_na_range:
                    if strength < range_min:
                        return False, f"STRAT_NA_RANGE_WEAK({strength:.2f}<{range_min})"
                elif strength < (min_str + 0.08):
                    return False, f"STRAT_NA_NO_MTF(L{long_votes}/S{short_votes}<{min_votes})"
                if strength < min_str:
                    return False, "STRAT_NA_WEAK"

        # reject_reason z silnika – pomiń tylko miękkie flagi
        rr = signal.get("reject_reason")
        if rr:
            ignore = {"REGIME_RANGE"}
            # STRAT_PRIMARY_NA nie ignorujemy gdy weszliśmy wyżej w filtr
            if not getattr(config, "BLOCK_RANGE_REGIME", False):
                ignore.add("REGIME_RANGE_BLOCK")
            if not getattr(config, "BLOCK_OB_THIN", False) and str(rr).startswith("OB_THIN"):
                ignore.add(rr)
            # PUMP/DUMP i OB_THIN przy BLOCK_* = True – nie ignoruj
            if rr not in ignore:
                return False, rr

        # Cooldown symbolu
        sym = signal.get("symbol")
        eng_key = (str(sym or "").upper(), str(signal.get("engine") or signal.get("score_type") or "trend").lower())
        engine_until = self.engine_symbol_cooldown.get(eng_key)
        if engine_until:
            if datetime.now() < engine_until:
                left = (engine_until - datetime.now()).total_seconds() / 60
                return False, f"ENGINE_COOLDOWN({eng_key[1]} {left:.0f}min)"
            del self.engine_symbol_cooldown[eng_key]
        if sym and sym in self.symbol_cooldown:
            until = self.symbol_cooldown[sym]
            if datetime.now() < until:
                left = (until - datetime.now()).total_seconds() / 60
                return False, f"COOLDOWN {left:.0f}min"
            else:
                del self.symbol_cooldown[sym]

        # Korelacja z BTC – twardy blok tylko gdy BTC_CORRELATION_HARD=True
        if getattr(config, "BTC_CORRELATION_FILTER", True) and getattr(config, "BTC_CORRELATION_HARD", False):
            btc = float(self.last_btc_change or 0)
            dump = float(getattr(config, "BTC_DUMP_THRESHOLD", -2.0))
            pump = float(getattr(config, "BTC_PUMP_THRESHOLD", 2.0))
            direction = signal.get("direction")
            if direction == "LONG" and btc <= dump and sym not in ("BTC", "TBTC"):
                return False, f"BTC_DUMP({btc:+.1f}%)"
            if direction == "SHORT" and btc >= pump and sym not in ("BTC", "TBTC"):
                return False, f"BTC_PUMP({btc:+.1f}%)"

        # Likwidacja dalej niż SL + buffer
        if bool(getattr(config, "REQUIRE_LIQ_BEYOND_SL", True)):
            ok_liq, why_liq = self._liquidation_beyond_sl(signal)
            if not ok_liq:
                return False, why_liq

        # Order book impact (VWAP) – zamiast samego depth > N
        if bool(getattr(config, "OB_IMPACT_FILTER", True)):
            ok_ob, why_ob = self._orderbook_impact_ok(signal)
            if not ok_ob:
                return False, why_ob

        # Portfolio open risk (SL$) — V2 capital_pct liczy size z 7.5% margin,
        # nie z odległości SL. Szeroki SL 1h * 7.5% * x10 przebijał 2.5%
        # MAX_PORTFOLIO_OPEN_RISK i zerował wejścia. Ekspozycja V2 = gross/margin.
        is_v2_pct = (
            (signal.get("engine") == "daytrading_v2" or signal.get("strategy_mode") == "DAYTRADING_V2")
            and str(getattr(config, "DAYTRADING_V2_SIZE_MODE", "capital_pct")) == "capital_pct"
        )
        if not is_v2_pct:
            ok_pr, why_pr = self._portfolio_open_risk_ok(signal)
            if not ok_pr:
                return False, why_pr

        # P2.9 dynamic correlation
        if bool(getattr(config, "DYN_CORR_FILTER", True)):
            try:
                from dynamic_correlation import get_correlation_engine
                eng = get_correlation_engine()
                # update price
                if signal.get("price"):
                    eng.update_price(signal.get("symbol"), signal["price"])
                ok_c, why_c, det = eng.gate(
                    signal.get("symbol") or "",
                    signal.get("direction") or "",
                    getattr(self, "_positions_ref", None) or [],
                    max_corr=float(getattr(config, "DYN_CORR_MAX", 0.75)),
                )
                signal["_dyn_corr"] = det
                if not ok_c:
                    return False, why_c
            except Exception as e:
                print(f"[Risk] dyn corr: {e}")
                return False, "DYNAMIC_CORRELATION_UNAVAILABLE"

        # P2.10 Expected Net R
        if bool(getattr(config, "USE_EXPECTED_NET_R_FILTER", True)):
            try:
                from expected_net_r import net_r_ok
                ok_n, why_n = net_r_ok(signal, self)
                if not ok_n:
                    return False, why_n
            except Exception as e:
                print(f"[Risk] net R: {e}")
                return False, "EXPECTED_NET_R_UNAVAILABLE"

        # 9. Dynamic spread threshold (z-score + vol + basis + exec cost)
        if bool(getattr(config, "DYNAMIC_SPREAD_FILTER", True)):
            try:
                from dynamic_spread import dynamic_spread_threshold
                ds = dynamic_spread_threshold(signal)
                signal["_spread_dyn"] = ds
                if not ds.get("ok"):
                    return False, ds.get("reason") or "SPREAD_DYN"
            except Exception as e:
                print(f"[Risk] dynamic spread: {e}")
                return False, "DYNAMIC_SPREAD_UNAVAILABLE"

        return True, "OK"

    def _orderbook_impact_ok(self, signal: Dict) -> tuple:
        """
        simulate_market_order → reject jeśli impact > threshold lub niedopełnienie.
        """
        ob = signal.get("order_book") or {}
        if not ob:
            if bool(getattr(config, "OB_IMPACT_REQUIRE_BOOK", False)):
                return False, "OB_IMPACT_NO_BOOK"
            return True, "OK"
        notional = signal.get("_planned_notional")
        if notional is None:
            try:
                notional = self.calculate_position_size(signal)
            except Exception:
                notional = 0
        try:
            notional = float(notional or 0)
        except (TypeError, ValueError):
            notional = 0.0
        if notional <= 0:
            return True, "OK"
        try:
            from orderbook_impact import impact_gate
            max_imp = float(getattr(config, "OB_MAX_IMPACT_PCT", 0.35))
            min_fill = float(getattr(config, "OB_MIN_FILL_RATIO", 0.95))
            ok, reason, sim = impact_gate(
                ob, signal.get("direction") or "LONG", notional,
                max_impact_pct=max_imp, min_fill_ratio=min_fill,
            )
            signal["_ob_impact"] = sim
            return ok, reason
        except Exception as e:
            print(f"[Risk] OB impact err: {type(e).__name__}: {e}")
            return False, "OB_IMPACT_UNAVAILABLE"


    def _open_risk_usd(self, positions=None) -> float:
        """Suma actual_risk_usd otwartych (fallback: margin * typical)."""
        positions = positions if positions is not None else (getattr(self, "_positions_ref", None) or [])
        total = 0.0
        for p in positions:
            r = None
            if hasattr(p, "actual_risk_usd") and getattr(p, "actual_risk_usd") is not None:
                r = float(p.actual_risk_usd)
            elif isinstance(p, dict) and p.get("actual_risk_usd") is not None:
                r = float(p["actual_risk_usd"])
            if r is None:
                # approx: notional * sl_dist ≈ size * (1/lev) * 0.5 as soft estimate
                try:
                    size = float(getattr(p, "size_usd", None) or (p.get("size_usd") if isinstance(p, dict) else 0) or 0)
                    lev = float(getattr(p, "leverage", None) or (p.get("leverage") if isinstance(p, dict) else None) or config.LEVERAGE)
                    r = size * (1.0 / max(lev, 1)) * 0.5
                except Exception:
                    r = 0.0
            total += max(0.0, r)
        return total

    def _portfolio_open_risk_ok(self, signal: dict) -> tuple:
        """
        MAX_PORTFOLIO_OPEN_RISK (2.5% equity) oraz MAX_CORRELATED_RISK (1% / cluster).
        """
        equity = self.equity_for_sizing() if bool(getattr(config, "SIZE_ON_EQUITY", True)) else float(self.current_capital or 0)
        if equity <= 0:
            return False, "NO_EQUITY"
        max_port = float(getattr(config, "MAX_PORTFOLIO_OPEN_RISK", 0.025))
        max_corr = float(getattr(config, "MAX_CORRELATED_RISK", 0.010))
        positions = getattr(self, "_positions_ref", None) or []
        open_risk = self._open_risk_usd(positions)
        # planowany risk nowego trade ≈ equity * risk_pct (użyj mid)
        try:
            planned_notional = float(signal.get("_planned_notional") or self.calculate_position_size(signal) or 0)
        except Exception:
            planned_notional = 0.0
        sl_dist = self._sl_distance_pct(signal)
        new_risk = planned_notional * sl_dist
        if open_risk + new_risk > equity * max_port:
            return False, (
                f"PORTFOLIO_RISK({(open_risk+new_risk)/equity*100:.2f}%>{max_port*100:.1f}%)"
            )
        # correlated: ten sam cluster
        try:
            from portfolio_risk import cluster_of
            new_sym = (signal.get("symbol") or "").upper()
            new_c = cluster_of(new_sym)
            cluster_risk = 0.0
            for p in positions:
                sym = (getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else "") or "").upper()
                if cluster_of(sym) == new_c:
                    if hasattr(p, "actual_risk_usd") and p.actual_risk_usd is not None:
                        cluster_risk += float(p.actual_risk_usd)
                    else:
                        size = float(getattr(p, "size_usd", 0) or 0)
                        lev = float(getattr(p, "leverage", config.LEVERAGE) or config.LEVERAGE)
                        cluster_risk += size * (1.0 / max(lev, 1)) * 0.5
            if cluster_risk + new_risk > equity * max_corr:
                return False, (
                    f"CORR_RISK({new_c}:{(cluster_risk+new_risk)/equity*100:.2f}%>{max_corr*100:.1f}%)"
                )
        except Exception as e:
            print(f"[Risk] corr check: {e}")
            return False, "CORRELATION_CHECK_FAILED"
        return True, "OK"

    def estimate_liq_distance_pct(self, leverage: float = None) -> float:
        """
        Przybliżony dystans entry→likwidacja jako ułamek ceny (isolated margin).
        ≈ (1/lev) * (1 - mmr)  −  mały bufor maintenance.
        """
        lev = float(leverage if leverage is not None else getattr(config, "LEVERAGE", 10))
        lev = max(lev, 1.0)
        mmr = float(getattr(config, "MAINTENANCE_MARGIN_RATE", 0.005))  # ~0.5% typowe USDT-M
        # prosto: strata 100% initial margin ≈ move 1/lev; mmr zjada część
        dist = (1.0 / lev) * (1.0 - mmr)
        # floor – nie mniej niż 0.5%
        return max(dist, 0.005)

    def estimate_liquidation_price(self, entry: float, direction: str, leverage: float = None,
                                     exchange_liq: float = None) -> float:
        """
        Primary: rzeczywista liquidation price z BloFin (jeśli dostępna).
        Fallback: model isolated (1/lev * (1-mmr)).
        """
        if exchange_liq is not None:
            try:
                el = float(exchange_liq)
                if el > 0:
                    return el
            except (TypeError, ValueError):
                pass
        dist = self.estimate_liq_distance_pct(leverage)
        if direction == "LONG":
            return entry * (1.0 - dist)
        return entry * (1.0 + dist)

    def _resolve_exchange_liq(self, signal: Dict) -> tuple:
        """(liq_price or None, source)."""
        for key in ("blofin_liq_price", "liquidation_price", "liq_price", "liqPx"):
            v = signal.get(key)
            if v is not None:
                try:
                    f = float(v)
                    if f > 0:
                        return f, "blofin_signal"
                except (TypeError, ValueError):
                    pass
        # z otwartej pozycji tego symbolu (LIVE sync)
        sym = (signal.get("symbol") or "").upper()
        for p in (getattr(self, "_positions_ref", None) or []):
            psym = (getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else "") or "").upper()
            if psym != sym:
                continue
            for attr in ("liquidation_price", "liq_price", "blofin_liq"):
                v = getattr(p, attr, None) if not isinstance(p, dict) else p.get(attr)
                if v is not None:
                    try:
                        f = float(v)
                        if f > 0:
                            return f, "blofin_position"
                    except (TypeError, ValueError):
                        pass
        return None, "model"

    def _liquidation_beyond_sl(self, signal: Dict) -> tuple:
        """
        NO TRADE gdy:
          distance(entry, liq)  ≤  LIQ_SL_BUFFER_MULT × distance(entry, SL)
        Liq: BloFin primary, model fallback.
        """
        try:
            entry = float(signal.get("price") or 0)
        except (TypeError, ValueError):
            return True, "OK"
        if entry <= 0:
            return True, "OK"

        direction = (signal.get("direction") or "").upper()
        lev = float(getattr(config, "LEVERAGE", 10))
        sl_dist = self._sl_distance_pct(signal)

        exch_liq, liq_src = self._resolve_exchange_liq(signal)
        if exch_liq is not None and entry > 0:
            liq_dist = abs(entry - float(exch_liq)) / entry
            liq_px = float(exch_liq)
        else:
            mmr, mmr_src = self._venue_mmr(signal)
            liq_dist = max((1.0 / max(lev, 1.0)) * (1.0 - mmr), 0.005)
            liq_px = entry * (1.0 - liq_dist if direction == "LONG" else 1.0 + liq_dist)
            liq_src = f"model:{mmr_src}"
            signal["_maintenance_margin_rate"] = mmr

        mult = float(getattr(config, "LIQ_SL_BUFFER_MULT", 1.5))
        vol_buf = 0.0
        if bool(getattr(config, "LIQ_ATR_BUFFER", True)):
            atr_pct = signal.get("atr_pct")
            if atr_pct is not None:
                try:
                    vol_buf = float(atr_pct) / 100.0 * float(getattr(config, "LIQ_ATR_BUFFER_MULT", 0.25))
                except (TypeError, ValueError):
                    vol_buf = 0.0
        required = mult * sl_dist + vol_buf
        signal["_liq_source"] = liq_src
        signal["_liq_price"] = liq_px
        signal["_liq_dist"] = liq_dist

        from cryptoedge.domain import trading_mode
        # PAPER + model MMR (~10% przy x10) vs 1.5×SL+ATR dawało LIQ_TOO_CLOSE
        # bez prawdziwej ceny liq z giełdy. LIVE z blofin_liq nadal twardy.
        if trading_mode.is_paper(config) and str(liq_src).startswith("model"):
            return True, "OK"

        if liq_dist <= required:
            return False, (
                f"LIQ_TOO_CLOSE(liq_dist={liq_dist*100:.2f}% ≤ "
                f"req={required*100:.2f}% = {mult}×SL+buf; liq≈{liq_px:.6g} src={liq_src})"
            )
        return True, "OK"

    def equity_for_sizing(self) -> float:
        """
        Equity = cash/capital + unrealized PnL otwartych pozycji.
        Używane do sizingu i limitów ekspozycji.
        """
        capital = float(self.current_capital or 0)
        unrealized = 0.0
        try:
            positions = getattr(self, "_positions_ref", None) or []
            for p in positions:
                if hasattr(p, "pnl"):
                    unrealized += float(getattr(p, "pnl", 0) or 0)
                elif isinstance(p, dict):
                    unrealized += float(p.get("pnl") or 0)
        except Exception:
            unrealized = 0.0
        # floor: nie size'uj na ujemnym equity
        return max(0.0, capital + unrealized)

    def _sl_distance_pct(self, signal: Dict) -> float:
        """
        Odległość SL od entry jako ułamek ceny (0.02 = 2%).
        Preferuj signal.sl_price / atr; fallback STOP_LOSS_PCT / leverage.
        """
        price = float(signal.get("price") or 0)
        sl = signal.get("sl_price")
        if price > 0 and sl is not None:
            try:
                dist = abs(price - float(sl)) / price
                if dist > 1e-6:
                    return dist
            except (TypeError, ValueError):
                pass
        # ATR-based: mult * atr_pct/100
        atr_pct = signal.get("atr_pct")
        if atr_pct is not None:
            try:
                atr_pct = float(atr_pct)
                mult = float(getattr(config, "ATR_SL_MULTIPLIER", 2.0))
                dist = (atr_pct / 100.0) * mult
                if dist > 1e-6:
                    return dist
            except (TypeError, ValueError):
                pass
        # fallback: STOP_LOSS_PCT jest w % PnL pozycji → / leverage = % ceny
        sl_pnl = abs(float(getattr(config, "STOP_LOSS_PCT", -22.0)))
        lev = max(float(getattr(config, "LEVERAGE", 10)), 1.0)
        return max(sl_pnl / 100.0 / lev, 0.005)  # floor 0.5% ceny

    def calculate_position_size(self, signal: Dict) -> float:
        """
        Risk-based sizing:
          risk_usd = equity * risk_pct          (0.30–0.90%)
          notional = risk_usd / sl_distance_pct
        Cap: max margin allocation + free equity.
        """
        self.prepare_signal_for_sizing(signal)
        equity = self.equity_for_sizing() if bool(getattr(config, "SIZE_ON_EQUITY", True)) else float(self.current_capital or 0)
        if not math.isfinite(equity) or equity <= 0:
            return 0.0

        regime = (signal.get("market_regime") or self.last_regime or "UNKNOWN").upper()
        lev = max(float(getattr(config, "LEVERAGE", 10)), 1.0)

        # --- Daytrading V2: sizing z ryzyka % kapitalu / odleglosc SL 1h,
        # NIE ze strength (punkt 20 planu hierarchii timeframe: "strength
        # tylko jako mnoznik, nie zamiast ATR"). risk_pct_of_capital jest
        # juz obliczone przez DayTradingEngineV2 (stala wartosc z configu),
        # wiec pomijamy TYLKO interpolacje strength->risk_pct ponizej
        # (blok "risk % z strength") - reszta funkcji (risk_usd/sl_dist/
        # notional/hard capy/orderbook impact) jest generyczna i dziala
        # identycznie dla V1 i V2, wiec nie duplikujemy jej.
        is_day_v2 = signal.get("engine") == "daytrading_v2" or signal.get("strategy_mode") == "DAYTRADING_V2"
        is_day = False
        is_rev = False
        v2_fixed_notional = None
        if is_day_v2:
            risk_pct = float(signal.get("risk_pct_of_capital",
                             getattr(config, "DAYTRADING_V2_RISK_PCT_OF_CAPITAL", 0.5))) / 100.0
            signal["_risk_pct"] = round(risk_pct, 6)
            signal["_risk_engine"] = "daytrading_v2"
            if str(getattr(config, "DAYTRADING_V2_SIZE_MODE", "capital_pct")) == "capital_pct":
                lo = float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_MIN", 5.0)) / 100.0
                hi = float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_MAX", 10.0)) / 100.0
                if bool(getattr(config, "DAYTRADING_V2_MARGIN_STRENGTH_SCALED", False)):
                    st = float(signal.get("strength") or 0.75)
                    floor = float(getattr(config, "MIN_SIGNAL_STRENGTH", 0.48))
                    t_str = 0.0 if hi <= lo else max(0.0, min(1.0, (st - floor) / max(1e-9, 1.0 - floor)))
                    margin_frac = lo + t_str * (hi - lo)
                else:
                    # Margin niezalezny od strength (domyslnie) - stala
                    # wartosc, przyciety do [MIN,MAX] nawet gdyby ktos
                    # ustawil DAYTRADING_V2_MARGIN_PCT_FIXED poza zakresem.
                    if signal.get("margin_pct") is not None:
                        fixed_pct = float(signal.get("margin_pct")) / 100.0
                    else:
                        fixed_pct = float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_FIXED", 7.5)) / 100.0
                    margin_frac = max(lo, min(hi, fixed_pct))
                htf_mult = float(signal.get("_htf_size_mult") or 1.0)
                if 0.0 < htf_mult < 1.0:
                    margin_frac = max(lo, margin_frac * htf_mult)
                    signal["_htf_size_applied"] = round(htf_mult, 3)
                signal["_v2_margin_pct"] = round(margin_frac * 100.0, 3)
                v2_fixed_notional = equity * margin_frac * lev
                print(f"[Size] {signal.get('symbol')} V2 margin={margin_frac*100:.1f}% "
                      f"(${equity*margin_frac:.2f}) notional=${v2_fixed_notional:.2f} lev=x{lev:.0f}")
        else:
            # --- risk % z strength ---
            # Trend: 0.50–0.75% equity | Reversal: 0.25–0.50% (bardziej konserwatywnie)
            is_rev = (signal.get("engine") == "reversal") or (signal.get("setup") == "reversal_confirmed")
            is_day = signal.get("engine") == "daytrading" or signal.get("strategy_mode") == "DAYTRADING"
            if is_day:
                risk_lo = float(getattr(config, "DAYTRADING_RISK_PCT_MIN", 0.0035))
                risk_hi = float(getattr(config, "DAYTRADING_RISK_PCT_MAX", 0.0060))
                risk_def = float(getattr(config, "DAYTRADING_RISK_PCT_DEFAULT", 0.0045))
            elif is_rev:
                risk_lo = float(getattr(config, "REVERSAL_RISK_PCT_MIN", 0.0025))  # 0.25%
                risk_hi = float(getattr(config, "REVERSAL_RISK_PCT_MAX", 0.0050))  # 0.50%
                risk_def = float(getattr(config, "REVERSAL_RISK_PCT_DEFAULT", 0.0035))  # 0.35%
            else:
                risk_lo = float(getattr(config, "RISK_PCT_MIN", 0.0050))   # 0.50% trend
                risk_hi = float(getattr(config, "RISK_PCT_MAX", 0.0075))   # 0.75%
                risk_def = float(getattr(config, "RISK_PCT_DEFAULT", 0.0060))  # 0.60%
            if bool(getattr(config, "SIZE_BY_STRENGTH", True)):
                st = float(signal.get("reversal_score") or signal.get("strength") or 0) if is_rev else float(signal.get("strength") or 0)
                if not math.isfinite(st):
                    return 0.0
                lo = float(getattr(config, "SIZE_STRENGTH_FLOOR", getattr(config, "MIN_SIGNAL_STRENGTH", 0.48)))
                if is_rev:
                    lo = float(getattr(config, "REVERSAL_MIN_STRENGTH", 0.48))
                hi = float(getattr(config, "SIZE_STRENGTH_CAP", 1.0))
                if hi > lo:
                    t_str = max(0.0, min(1.0, (st - lo) / (hi - lo)))
                else:
                    t_str = 1.0
                risk_pct = risk_lo + t_str * (risk_hi - risk_lo)
            else:
                risk_pct = risk_def
            signal["_risk_pct"] = round(risk_pct, 6)
            signal["_risk_engine"] = "daytrading" if is_day else ("reversal" if is_rev else "trend")
            if is_rev and bool(getattr(config, "REVERSAL_SIZE_CAPITAL_PCT", True)):
                lo = float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_MIN", 5.0)) / 100.0
                hi = float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_MAX", 10.0)) / 100.0
                st = float(signal.get("reversal_score") or signal.get("strength") or 0.6)
                floor = float(getattr(config, "REVERSAL_MIN_STRENGTH", 0.48))
                t_str = max(0.0, min(1.0, (st - floor) / max(1e-9, 1.0 - floor)))
                margin_frac = lo + t_str * (hi - lo)
                # reversal ostrozniej: 70% pasa 5-10%
                margin_frac *= float(getattr(config, "REVERSAL_SIZE_MULT", 0.70))
                signal["_v2_margin_pct"] = round(margin_frac * 100.0, 3)
                v2_fixed_notional = equity * margin_frac * lev
                print(f"[Size] {signal.get('symbol')} REV margin={margin_frac*100:.1f}% "
                      f"(${equity*margin_frac:.2f}) notional=${v2_fixed_notional:.2f}")

        # regime: RANGE half risk; PANIC = strong move (mult 1.0 = pełny size)
        if regime == "RANGE":
            risk_pct *= float(getattr(config, "REGIME_RANGE_SIZE_MULT", 0.50))
        elif regime == "PANIC" and not is_rev and not is_day and not is_day_v2:
            panic_mult = getattr(config, "REGIME_PANIC_TREND_SIZE_MULT", None)
            if panic_mult is None:
                panic_mult = getattr(config, "REGIME_PANIC_SIZE_MULT", 1.0)
            try:
                panic_mult = float(panic_mult)
            except (TypeError, ValueError):
                panic_mult = 1.0
            risk_pct *= panic_mult
        # 7. proxy 4H → mniejszy risk
        if signal.get("ohlcv_source") == "proxy_4h" or signal.get("proxy_4h"):
            risk_pct *= float(getattr(config, "PROXY_4H_RISK_MULT", 0.70))
        if signal.get("cross_market_risk_mult"):
            risk_pct *= float(signal["cross_market_risk_mult"])
        # 8. degraded 1D
        if signal.get("degraded_1d"):
            risk_pct *= float(getattr(config, "DEGRADED_1D_RISK_MULT", 0.75))
        # A prior curve with zero/few observations is not empirical expectancy.
        # Keep PAPER discovery active, but cap capital allocated to it.
        er_status = str(signal.get("expected_r_status") or "").upper()
        if er_status in ("PRIOR_ONLY", "LOW_SAMPLE") and not is_day:
            risk_pct *= float(getattr(config, "UNCALIBRATED_EXPECTED_R_SIZE_MULT", 0.65))
            signal["_uncalibrated_expected_r"] = True
        # EXTREME VOL: atr percentile / atr_ratio → risk × 0.5
        try:
            atr_pctile = signal.get("atr_percentile")
            if atr_pctile is None:
                reg = signal.get("market_regime_detail") or getattr(self, "last_regime_detail", None) or {}
                if isinstance(reg, dict):
                    atr_pctile = reg.get("atr_percentile")
            thr = float(getattr(config, "EXTREME_VOL_ATR_PCTILE", 85.0))
            if atr_pctile is not None and float(atr_pctile) >= thr:
                risk_pct *= float(getattr(config, "EXTREME_VOL_RISK_MULT", 0.50))
        except Exception:
            pass

        if v2_fixed_notional is not None:
            notional = float(v2_fixed_notional)
            sl_dist = self._sl_distance_pct(signal)
            signal["_risk_usd_at_sl"] = round(notional * max(sl_dist, 0.0), 4) if sl_dist else None
        else:
            risk_usd = equity * risk_pct
            sl_dist = self._sl_distance_pct(signal)  # fraction of price
            if not math.isfinite(sl_dist) or sl_dist <= 0:
                return 0.0
            notional = risk_usd / sl_dist

        # --- hard caps ---
        reserve = float(getattr(config, "CAPITAL_RESERVE_PCT", 0.20))
        usable = equity * (1.0 - reserve)
        used_margin = 0.0
        try:
            for p in (getattr(self, "_positions_ref", None) or []):
                if hasattr(p, "margin"):
                    used_margin += float(getattr(p, "margin", 0) or 0)
                elif isinstance(p, dict):
                    used_margin += float(p.get("margin") or 0)
        except Exception:
            pass
        free_equity = max(0.0, usable - used_margin)
        max_pos = max(1, int(config.MAX_POSITIONS))
        if regime == "RANGE":
            max_pos = min(max_pos, int(getattr(config, "REGIME_RANGE_MAX_POSITIONS", 5) or 5))
        free_slots = max(1, max_pos - self.open_positions_count)
        # V2/reversal capital_pct: NIE dziel depo na 10 slotow (to scinalo size do groszy)
        if v2_fixed_notional is not None:
            max_margin_slot = equity * (float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_MAX", 10.0)) / 100.0)
            max_notional = max_margin_slot * lev
        else:
            max_margin_slot = free_equity / free_slots if free_slots else 0.0
            max_notional = max_margin_slot * lev
        # global max % equity on one trade (hard cap notional <= MAX_NOTIONAL_EQUITY_FRAC * equity)
        max_equity_frac = float(getattr(config, "MAX_NOTIONAL_EQUITY_FRAC", 2.0))
        max_notional = min(max_notional, equity * max_equity_frac)
        # Margin pojedynczej pozycji nigdy nie przekracza ustalonego % equity.
        # To limit alokacji, nie zgoda na stratę tego procentu do SL.
        max_margin_frac = max(0.0, min(1.0, float(getattr(config, "MAX_POSITION_MARGIN_EQUITY_FRAC", 0.10))))
        max_notional = min(max_notional, equity * max_margin_frac * lev)
        signal["_max_position_margin_pct"] = round(max_margin_frac * 100.0, 4)

        notional = min(notional, max_notional)
        if not math.isfinite(notional) or notional <= 0:
            return 0.0
        # min notional floor
        min_n = float(getattr(config, "MIN_NOTIONAL_USD", 20.0))
        if notional > 0 and notional < min_n:
            # W risk_sl podniesienie do minimum zwiekszaloby strate przy SL ponad
            # zatwierdzony risk budget. Taki trade musi zostac pominiety.
            if is_day_v2 and str(getattr(config, "DAYTRADING_V2_SIZE_MODE", "risk_sl")) == "risk_sl":
                signal["reasons"] = list(signal.get("reasons") or []) + ["SIZE_BELOW_EXCHANGE_MIN"]
                return 0.0
            if max_notional >= min_n:
                notional = min_n
            else:
                return 0.0
        # Adaptacja size: strength / vol / seria strat / DD / dzienny limit.
        # Tylko jawny V2 capital_pct ma pas 5-10%. W trybie risk_sl nie wolno
        # podnosić notionalu do 5% margin, bo łamie to zatwierdzony risk budget.
        skip_adapt = is_day_v2 or signal.get("_v2_margin_pct") is not None
        try:
            from adaptive_size import apply_to_notional
            if not skip_adapt:
                notional = apply_to_notional(notional, signal, risk=self)
            elif v2_fixed_notional is not None:
                lo_n = equity * (float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_MIN", 5.0)) / 100.0) * lev
                hi_n = equity * (float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_MAX", 10.0)) / 100.0) * lev
                notional = min(max(notional, lo_n), hi_n)
        except Exception as e:
            print(f"[Risk] adaptive size: {e}")
            try:
                sm = float(signal.get("_size_mult") or 1.0)
                if 0 < sm < 1.0:
                    notional *= sm
            except (TypeError, ValueError):
                pass

        # PRIORYTET 13 — liquidity → impact → maximum safe notional
        # „chcę $5k, book uniesie $1.4k” → size = $1.4k (nie reject tylko OB_THIN)
        if bool(getattr(config, "OB_SIZE_FROM_LIQUIDITY", True)) and signal.get("_v2_margin_pct") is None:
            ob = signal.get("order_book") or {}
            if ob:
                try:
                    from orderbook_impact import max_safe_notional
                    max_imp = float(getattr(config, "OB_MAX_IMPACT_PCT", 0.35))
                    min_fill = float(getattr(config, "OB_MIN_FILL_RATIO", 0.95))
                    depth_frac = float(getattr(config, "OB_DEPTH_SIZE_FRAC", 0.35))
                    safe = max_safe_notional(
                        ob,
                        signal.get("direction") or "LONG",
                        max_impact_pct=max_imp,
                        min_fill_ratio=min_fill,
                        target_notional=notional,
                        depth_cap_frac=depth_frac,
                    )
                    signal["_ob_safe"] = safe
                    sn = float(safe.get("safe_notional") or 0)
                    if sn > 0 and sn < notional:
                        signal["reasons"] = list(signal.get("reasons") or []) + [
                            f"SIZE_CAP_OB({notional:.0f}→{sn:.0f}|depth={safe.get('depth_usd')})"
                        ]
                        notional = sn
                    elif sn <= 0 and bool(getattr(config, "OB_IMPACT_REQUIRE_BOOK", False)):
                        signal["_ob_safe"] = safe
                        return 0.0
                except Exception as e:
                    print(f"[Risk] OB safe size: {type(e).__name__}: {e}")

        # V2/reversal (capital_pct sizing): sizing powyzej jest liczony z %
        # marginu * dzwignia, calkowicie niezaleznie od odleglosci SL - wiec
        # dolarowe ryzyko (notional * sl_dist) potrafi samo, na JEDNYM trade,
        # przekroczyc caly limit portfela (_portfolio_open_risk_ok,
        # MAX_PORTFOLIO_OPEN_RISK) przy szerszym SL (podwyzszona zmiennosc),
        # co gwarantuje odrzucenie niezaleznie od tego, ile innych pozycji
        # jest otwartych - patrz komentarz przy DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE
        # w config.py. Zamiast czekac na twardy reject nizej w lejku, zmniejszamy
        # tu notional tak, by pojedynczy trade miescil sie w tym mniejszym,
        # per-trade suficie ryzyka - realny trade (mniejszy) zamiast zera.
        if v2_fixed_notional is not None and sl_dist and sl_dist > 0:
            max_risk_pct = float(getattr(config, "DAYTRADING_V2_MAX_RISK_PCT_PER_TRADE", 0.0) or 0.0)
            if max_risk_pct <= 0:
                max_risk_pct = float(getattr(config, "RISK_PCT_MAX", 0.009) or 0.009) * 100.0
            min_margin_n = equity * (float(getattr(config, "DAYTRADING_V2_MARGIN_PCT_MIN", 5.0)) / 100.0) * lev
            if max_risk_pct > 0:
                risk_cap_notional = (equity * (max_risk_pct / 100.0)) / sl_dist
                if risk_cap_notional < notional:
                    if risk_cap_notional + 1e-9 < min_margin_n:
                        signal["reasons"] = list(signal.get("reasons") or []) + [
                            f"SIZE_CAP_RISK_TOO_SMALL(sl_dist={sl_dist*100:.2f}%)"
                        ]
                        print(
                            f"[Size] {signal.get('symbol')} skip: risk$ przy min 5% margin "
                            f"({min_margin_n * sl_dist:.2f}) > sufit {max_risk_pct:.2f}% "
                            f"(SL {sl_dist*100:.2f}%)"
                        )
                        return 0.0
                    signal["reasons"] = list(signal.get("reasons") or []) + [
                        f"SIZE_CAP_RISK({notional:.0f}→{risk_cap_notional:.0f}|sl_dist={sl_dist*100:.2f}%)"
                    ]
                    print(
                        f"[Size] {signal.get('symbol')} cap notional "
                        f"${notional:.0f}->${risk_cap_notional:.0f} (SL {sl_dist*100:.2f}%)"
                    )
                    notional = risk_cap_notional

        return round(max(notional, 0.0), 4)

    def register_open(self):
        self.open_positions_count += 1

    def register_close(self, symbol: str = None, pnl: float = None, engine: str = None,
                       count_result: bool = True):
        self.open_positions_count = max(0, self.open_positions_count - 1)
        if symbol:
            mins = int(getattr(config, "SYMBOL_COOLDOWN_MINUTES", 20) or 0)
            if mins > 0 and bool(getattr(config, "CROSS_ENGINE_SYMBOL_COOLDOWN", False)):
                self.symbol_cooldown[symbol] = datetime.now() + timedelta(minutes=mins)
            key = (str(symbol).upper(), str(engine or "trend").lower())
            if key[1] == "daytrading":
                day_mins = int(getattr(config, "DAYTRADING_SIGNAL_COOLDOWN_MINUTES", 40) or 0)
                if day_mins > 0:
                    self.engine_symbol_cooldown[key] = datetime.now() + timedelta(minutes=day_mins)
            if count_result and pnl is not None and float(pnl) < 0:
                if key[1] == "daytrading_v2":
                    pass
                else:
                    streak = self.engine_symbol_loss_streak.get(key, 0) + 1
                    self.engine_symbol_loss_streak[key] = streak
                    threshold = int(getattr(config, "ENGINE_COOLDOWN_ESCALATION_LOSSES", 3) or 3)
                    base = int(getattr(config, "ENGINE_COOLDOWN_BASE_MINUTES", 20) or 0)
                    escalated = int(getattr(config, "ENGINE_COOLDOWN_ESCALATED_MINUTES", 75) or base)
                    engine_mins = escalated if streak >= threshold else base
                    if key[1] == "daytrading":
                        engine_mins = max(engine_mins, int(getattr(config, "DAYTRADING_SIGNAL_COOLDOWN_MINUTES", 40) or 0))
                    if engine_mins > 0:
                        self.engine_symbol_cooldown[key] = datetime.now() + timedelta(minutes=engine_mins)
                    if key[1] == "reversal":
                        rev_mins = int(getattr(config, "REVERSAL_LOSS_COOLDOWN_MINUTES", 60) or 60)
                        max_losses = int(getattr(config, "REVERSAL_MAX_CONSECUTIVE_SYMBOL_LOSSES", 2) or 2)
                        if streak >= max_losses:
                            rev_mins = max(rev_mins, int(getattr(config, "ENGINE_COOLDOWN_ESCALATED_MINUTES", 75) or 75))
                        self.engine_symbol_cooldown[key] = datetime.now() + timedelta(minutes=rev_mins)
            elif count_result and pnl is not None:
                self.engine_symbol_loss_streak[key] = 0
        if count_result and pnl is not None:
            try:
                self.note_trade_result(float(pnl))
            except Exception:
                pass

    def set_btc_change(self, btc_change: float):
        self.last_btc_change = float(btc_change or 0)

    def log_reject(self, symbol: str, direction: str, strength: float, reason: str, signal: Dict = None):
        self.reject_log.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "symbol": symbol,
            "direction": direction,
            "strength": round(float(strength or 0), 3),
            "reason": reason,
        })
        max_n = int(getattr(config, "MAX_REJECT_LOG", 30))
        self.reject_log = self.reject_log[:max_n]
        if signal is not None:
            try:
                from decision_telemetry import decision_snapshot
                decision_snapshot(signal, "REJECT", reason)
            except Exception:
                pass

    def get_status(self) -> Dict:
        from cryptoedge.domain import trading_mode
        self._check_new_day()
        daily_loss_pct = -self.daily_pnl / self.daily_start_capital * 100 if self.daily_start_capital > 0 else 0
        return {
            "capital": round(self.current_capital, 4),
            "daily_pnl": round(self.daily_pnl, 4),
            "daily_loss_pct": round(daily_loss_pct, 2),
            "open_positions": self.open_positions_count,
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "paused": self.paused,
            "peak_equity": round(self.peak_equity, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
            "mode": trading_mode.mode_label(config),
            "reserve_pct": float(getattr(config, "CAPITAL_RESERVE_PCT", 0.20)) * 100,
        }
