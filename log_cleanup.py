"""Automatyczne czyszczenie folderu logs/.

Wywolywane przy starcie i co LOG_CLEANUP_EVERY_CYCLES.
Nie rusza: bot_state.json, protection_state.json, settings, secrets.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

import config

LOGS_DIR = Path(__file__).resolve().parent / "logs"

KEEP_NAMES = {
    "bot_state.json",
    "protection_state.json",
    "console.log",  # aktywny plik; archiwa console_*.log sa kasowane
}

# Wzorce do kasowania po wieku (mtime)
AGE_GLOBS = (
    "console_*.log",
    "*.jsonl",
    "*.tmp",
    "cycle_debug.jsonl",
    "bot_log.csv.tmp",
    "signals_history.csv.tmp",
)


def _retention_seconds() -> float:
    days = float(getattr(config, "LOG_RETENTION_DAYS", 3) or 3)
    return max(0.25, days) * 86400


def _dir_max_bytes() -> int:
    mb = float(getattr(config, "LOG_DIR_MAX_MB", 80) or 80)
    return int(max(10.0, mb) * 1_000_000)


def _iter_age_targets() -> Iterable[Path]:
    for g in AGE_GLOBS:
        yield from LOGS_DIR.glob(g)


def purge_old_files() -> int:
    if not LOGS_DIR.exists():
        return 0
    cutoff = time.time() - _retention_seconds()
    removed = 0
    for path in _iter_age_targets():
        if not path.is_file() or path.name in KEEP_NAMES:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
                print(f"[Logs] usunieto stary {path.name}")
        except Exception as e:
            print(f"[Logs] nie moge usunac {path.name}: {e}")
    return removed


def enforce_dir_cap() -> int:
    """Gdy folder logs/ za duzy — kasuj najstarsze archiwa (nie aktywne CSV/state)."""
    if not LOGS_DIR.exists():
        return 0
    cap = _dir_max_bytes()
    files = [p for p in LOGS_DIR.iterdir() if p.is_file()]
    total = 0
    for p in files:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    if total <= cap:
        return 0
    removable = []
    for p in files:
        if p.name in KEEP_NAMES or p.name in ("bot_log.csv", "signals_history.csv"):
            continue
        try:
            removable.append((p.stat().st_mtime, p, p.stat().st_size))
        except OSError:
            pass
    removable.sort()  # oldest first
    removed = 0
    for _mtime, path, size in removable:
        if total <= cap:
            break
        try:
            path.unlink()
            total -= size
            removed += 1
            print(f"[Logs] cap {cap/1e6:.0f}MB – usunieto {path.name}")
        except Exception:
            pass
    return removed


def prune_console_keep() -> int:
    keep = int(getattr(config, "CONSOLE_LOG_KEEP", 8) or 8)
    archives = sorted(
        LOGS_DIR.glob("console_*.log"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    removed = 0
    for old in archives[max(1, keep):]:
        try:
            old.unlink()
            removed += 1
            print(f"[Logs] console keep={keep} – usunieto {old.name}")
        except Exception:
            pass
    return removed


def run(reason: str = "cycle") -> None:
    if not bool(getattr(config, "LOG_CLEANUP_ENABLED", True)):
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    n = 0
    try:
        n += purge_old_files()
        n += prune_console_keep()
        n += enforce_dir_cap()
    except Exception as e:
        print(f"[Logs] cleanup ({reason}): {e}")
        return
    if n:
        print(f"[Logs] cleanup ({reason}): {n} plikow")
