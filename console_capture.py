"""Zapisuje cala konsole (stdout+stderr) do logs/console.log.

Rotacja: CONSOLE_LOG_MAX_MB (domyslnie 5), zostaw CONSOLE_LOG_KEEP archiwow.
Dziala przy starcie i w trakcie zapisu.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent / "logs"
CONSOLE_FILE = LOGS_DIR / "console.log"

_file_handle = None
_bytes_written = 0
_lock = threading.Lock()


def _limits():
    try:
        import config
        max_mb = float(getattr(config, "CONSOLE_LOG_MAX_MB", 5) or 5)
        keep = int(getattr(config, "CONSOLE_LOG_KEEP", 8) or 8)
    except Exception:
        max_mb, keep = 5.0, 8
    return int(max(1.0, max_mb) * 1_000_000), max(1, keep)


def _prune_archives(keep: int) -> None:
    archives = sorted(
        LOGS_DIR.glob("console_*.log"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for old in archives[keep:]:
        try:
            old.unlink()
        except Exception:
            pass


def _open_log():
    global _file_handle, _bytes_written
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(CONSOLE_FILE, "a", encoding="utf-8", errors="replace", buffering=1)
    try:
        _bytes_written = CONSOLE_FILE.stat().st_size
    except Exception:
        _bytes_written = 0
    _file_handle = fh
    return fh


def rotate_now() -> None:
    """Zamknij console.log, przemianuj, otworz nowy, skasuj nadmiar archiwow."""
    global _file_handle, _bytes_written
    max_bytes, keep = _limits()
    try:
        if _file_handle is not None:
            try:
                _file_handle.flush()
                _file_handle.close()
            except Exception:
                pass
            _file_handle = None
        if CONSOLE_FILE.exists() and CONSOLE_FILE.stat().st_size > 0:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = LOGS_DIR / f"console_{stamp}.log"
            n = 1
            while dest.exists():
                dest = LOGS_DIR / f"console_{stamp}_{n}.log"
                n += 1
            CONSOLE_FILE.replace(dest)
        _prune_archives(keep)
    except Exception:
        pass
    _open_log()
    _ = max_bytes  # limits already applied via keep


class _Tee:
    def __init__(self, live, log_fn):
        self.live = live
        self.log_fn = log_fn

    def write(self, data):
        if not data:
            return 0
        try:
            self.live.write(data)
            self.live.flush()
        except Exception:
            pass
        self.log_fn(data)
        return len(data)

    def flush(self):
        try:
            self.live.flush()
        except Exception:
            pass
        global _file_handle
        try:
            if _file_handle:
                _file_handle.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return bool(self.live.isatty())
        except Exception:
            return False

    def fileno(self):
        return self.live.fileno()


def _write_log(data: str) -> None:
    global _file_handle, _bytes_written
    max_bytes, _keep = _limits()
    with _lock:
        if _file_handle is None:
            _open_log()
        try:
            _file_handle.write(data)
            _file_handle.flush()
            _bytes_written += len(data.encode("utf-8", errors="replace"))
        except Exception:
            try:
                _open_log()
                _file_handle.write(data)
            except Exception:
                return
        if _bytes_written >= max_bytes:
            rotate_now()
            header = (
                f"===== ROTATE {datetime.now().isoformat(timespec='seconds')} "
                f"pid={os.getpid()} =====\n"
            )
            try:
                _file_handle.write(header)
                _file_handle.flush()
                _bytes_written = len(header)
            except Exception:
                pass


def hide_console_window() -> bool:
    """Ukryj okno konsoli (Windows, ShowWindow SW_HIDE).

    Bezpieczny no-op poza Windows, oraz gdy GetConsoleWindow() nie zwroci
    uchwytu (np. proces juz uruchomiony bez okna - pythonw, sc.exe, itp.).
    Caly output nadal leci do logs/console.log przez _Tee powyzej (i do
    prawdziwego stdout/stderr, ktore po prostu nie maja juz widocznego
    okna) - ukrycie okna nic nie gubi, tylko chowa je z pulpitu/paska zadan.
    """
    if os.name != "nt":
        return False
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return False
        SW_HIDE = 0
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
        return True
    except Exception:
        return False


def install() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    max_bytes, keep = _limits()
    try:
        if CONSOLE_FILE.exists() and CONSOLE_FILE.stat().st_size >= max_bytes:
            rotate_now()
        else:
            _open_log()
            _prune_archives(keep)
    except Exception:
        _open_log()

    fh = _file_handle
    start = (
        f"\n\n===== START {datetime.now().isoformat(timespec='seconds')} "
        f"pid={os.getpid()} max_mb={max_bytes/1_000_000:.1f} keep={keep} =====\n"
    )
    try:
        fh.write(start)
        fh.flush()
        global _bytes_written
        _bytes_written += len(start)
    except Exception:
        pass

    sys.stdout = _Tee(sys.__stdout__, _write_log)
    sys.stderr = _Tee(sys.__stderr__, _write_log)

    def _hook(exc_type, exc, tb):
        sys.stderr.write("[FATAL] " + "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass

    sys.excepthook = _hook

    if hasattr(threading, "excepthook"):
        def _thread_hook(args):
            sys.stderr.write(
                "[FATAL-THREAD] "
                + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            )
        threading.excepthook = _thread_hook

    return CONSOLE_FILE
