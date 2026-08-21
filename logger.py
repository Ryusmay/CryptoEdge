# ============================================================
# Logger - zapis decyzji i stanu (Windows-safe)
# ============================================================

import csv
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional
import config
import log_cleanup

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"


class BotLogger:
    def __init__(self):
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[Logger] Nie moge utworzyc folderu logs: {e}")

        self.log_file = LOGS_DIR / "bot_log.csv"
        self.signals_file = LOGS_DIR / "signals_history.csv"
        self.state_file = LOGS_DIR / "bot_state.json"
        self.cycle_file = LOGS_DIR / "cycle_debug.jsonl"
        self.last_state: Dict = {}
        self.enabled = True

        try:
            self._init_csv(self.log_file, [
                "timestamp", "event", "symbol", "direction", "price",
                "size", "pnl", "pnl_pct", "strength", "reasons", "capital"
            ])
            self._init_csv(self.signals_file, [
                "timestamp", "symbol", "direction", "strength", "price",
                "rs", "change_24h", "reasons"
            ])
        except PermissionError as e:
            print(f"[Logger] Permission denied przy CSV: {e}")
            print("[Logger] Zamknij Excel / druga instancje bota i sprobuj ponownie.")
            print("[Logger] Kontynuuje BEZ zapisu CSV (stan JSON bedzie proboj).")
            self.enabled = False
        except Exception as e:
            print(f"[Logger] Init CSV: {e}")
            self.enabled = False

        self._cycles_since_cleanup = 0
        try:
            self.cleanup_old_logs()
        except Exception as e:
            print(f"[Logger] Cleanup start: {e}")

    def cleanup_old_logs(self, force: bool = False):
        """
        Usuwa stare wiersze z bot_log.csv i signals_history.csv.
        Kryteria: starsze niz LOG_RETENTION_DAYS oraz max LOG_MAX_LINES.
        """
        if not self.enabled and not force:
            try:
                log_cleanup.run("start" if force else "disabled-csv")
            except Exception:
                pass
            if not force:
                return
        try:
            log_cleanup.run("cycle")
        except Exception:
            pass
        days = int(getattr(config, "LOG_RETENTION_DAYS", 3) or 3)
        max_lines = int(getattr(config, "LOG_MAX_LINES", 50000) or 50000)
        cutoff = datetime.now() - timedelta(days=days)
        for path in (self.log_file, self.signals_file):
            try:
                self._trim_csv(path, cutoff, max_lines)
            except PermissionError:
                print(f"[Logger] Cleanup: {path.name} zablokowany – pominiety")
            except Exception as e:
                print(f"[Logger] Cleanup {path.name}: {e}")

    def _trim_csv(self, path: Path, cutoff: datetime, max_lines: int):
        if not path.exists() or path.stat().st_size == 0:
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            rows = list(csv.reader(f))
        if not rows:
            return
        header, body = rows[0], rows[1:]
        if not body:
            return
        kept = []
        for row in body:
            if not row:
                continue
            ts = row[0]
            try:
                # isoformat seconds
                dt = datetime.fromisoformat(ts.replace("Z", ""))
                if dt >= cutoff:
                    kept.append(row)
            except Exception:
                # nieparsowalna data – zachowaj
                kept.append(row)
        # limit linii (najnowsze)
        if len(kept) > max_lines:
            kept = kept[-max_lines:]
        removed = len(body) - len(kept)
        if removed <= 0:
            return
        # atomic write
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(kept)
        try:
            os.replace(str(tmp), str(path))
        except Exception:
            # Windows fallback
            try:
                if path.exists():
                    path.unlink()
                tmp.rename(path)
            except Exception:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                raise
        print(f"[Logger] Cleanup {path.name}: usunieto {removed} starych wierszy, zostalo {len(kept)}")
        try:
            log_cleanup.run("csv")
        except Exception:
            pass

    def maybe_cleanup(self):
        """Wywoluj co cykl – cleanup co N cykli."""
        every = int(getattr(config, "LOG_CLEANUP_EVERY_CYCLES", 100) or 100)
        self._cycles_since_cleanup = getattr(self, "_cycles_since_cleanup", 0) + 1
        if self._cycles_since_cleanup >= every:
            self._cycles_since_cleanup = 0
            self.cleanup_old_logs()

    def _init_csv(self, path: Path, headers: list):
        """Tworzy plik tylko gdy nie istnieje. Nie nadpisuje istniejacego."""
        try:
            if path.exists() and path.stat().st_size > 0:
                return
        except OSError:
            pass

        # sprobuj append/create bez niszczenia
        try:
            # exclusive create – jesli istnieje, nie ruszaj
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            try:
                fd = os.open(str(path), flags)
                with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(headers)
                return
            except FileExistsError:
                return
            except OSError:
                # fallback: zwykly open jesli brak
                if not path.exists():
                    with open(path, "w", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow(headers)
        except PermissionError:
            raise
        except Exception as e:
            print(f"[Logger] _init_csv {path.name}: {e}")

    def log_event(self, event: str, symbol: str = "", direction: str = "",
                  price: float = 0, size: float = 0, pnl: float = 0,
                  pnl_pct: float = 0, strength: float = 0,
                  reasons: list = None, capital: float = 0):
        if not self.enabled:
            return
        try:
            with open(self.log_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    event, symbol, direction, f"{price:.6f}",
                    f"{size:.2f}", f"{pnl:.4f}", f"{pnl_pct:.2f}",
                    f"{strength:.3f}", "|".join(reasons or []), f"{capital:.4f}"
                ])
        except PermissionError:
            print("[Logger] bot_log.csv zablokowany (Excel / inny proces) – pomijam")
        except Exception as e:
            print(f"[Logger] Blad log_event: {e}")

    def log_cycle(self, payload: Dict):
        """Jedna linia JSON na cykl — stan handlu + top skipy."""
        if not self.enabled:
            return
        row = dict(payload or {})
        row.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        try:
            with open(self.cycle_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            print(f"[Logger] cycle_debug: {e}")
        reasons = [
            f"trade={row.get('trade_on')}",
            f"paused={row.get('paused')}",
            f"halt={row.get('halted')}",
            f"why={row.get('block') or 'ok'}",
            f"cands={row.get('candidates')}",
            f"opened={row.get('opened')}",
        ]
        for item in (row.get("skips") or [])[:8]:
            reasons.append(
                f"{item.get('symbol')}:{item.get('direction')}:"
                f"{item.get('why') or item.get('reject') or '?'}"
            )
        self.log_event(
            "CYCLE",
            symbol=str(row.get("cycle") or ""),
            reasons=reasons,
            capital=float(row.get("capital") or 0),
        )

    def log_signals(self, signals: List[Dict]):
        if not self.enabled:
            return
        try:
            with open(self.signals_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                day_mode = str(getattr(config, "STRATEGY_MODE", "DAYTRADING")).upper() == "DAYTRADING"
                rows = [s for s in signals if not (
                    day_mode and str(s.get("reject_reason") or "").startswith("DAY_NOT_IN_LIQUID_TOP")
                )]
                rows.sort(key=lambda s: float(s.get("strength") or 0), reverse=True)
                for s in rows[:20]:
                    if s.get("direction") == "NEUTRAL" and not day_mode:
                        continue
                    path = s.get("decision_path") or "NO_TRADE"
                    eng = s.get("engine") or s.get("score_type") or ""
                    # PATH + engine na początku reasons → łatwy split w analizie
                    reasons = list(s.get("reasons") or [])
                    prefix = [f"PATH={path}"]
                    if eng:
                        prefix.append(f"ENGINE={eng}")
                    if s.get("trend_score") is not None:
                        prefix.append(f"TREND_SCORE={s.get('trend_score')}")
                    if s.get("reversal_score") is not None:
                        prefix.append(f"REV_SCORE={s.get('reversal_score')}")
                    if s.get("reject_reason"):
                        prefix.append(f"REJECT={s.get('reject_reason')}")
                    if s.get("expected_net_r") is not None:
                        prefix.append(f"NET_R={s.get('expected_net_r')}")
                    if s.get("quality") is not None:
                        prefix.append(f"Q={s.get('quality')}")
                    prefix.append(f"MODE={str(s.get('strategy_mode') or getattr(config, 'STRATEGY_MODE', 'DAYTRADING')).upper()}")
                    writer.writerow([
                        ts, s["symbol"], s["direction"], s["strength"],
                        f"{s['price']:.6f}", s.get("rs", ""),
                        f"{s.get('change_24h', 0):.2f}",
                        "|".join(prefix + reasons)
                    ])
        except PermissionError:
            print("[Logger] signals_history.csv zablokowany – pomijam")
        except Exception as e:
            print(f"[Logger] Blad log_signals: {e}")

    def save_state(self, risk_status: Dict, open_positions: List[Dict],
                   extra: Optional[Dict] = None):
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "risk": risk_status,
            "open_positions": open_positions,
        }
        if extra:
            state.update(extra)
        # Native UI reads this first; disk is only persistence/recovery fallback.
        self.last_state = state
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            # unikalna nazwa tmp – unikamy kolizji
            fd, tmp_path = tempfile.mkstemp(prefix="state_", suffix=".json", dir=str(LOGS_DIR))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False, default=str)
                # Windows: replace moze fail jesli plik otwarty – retry prosta
                for attempt in range(3):
                    try:
                        os.replace(tmp_path, self.state_file)
                        break
                    except PermissionError:
                        if attempt == 2:
                            # zapis pod alternatywna nazwa
                            alt = LOGS_DIR / "bot_state_alt.json"
                            os.replace(tmp_path, alt)
                            print("[Logger] bot_state.json zablokowany – zapisano bot_state_alt.json")
                        else:
                            import time
                            time.sleep(0.15)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            print(f"[Logger] Blad save_state: {e}")
