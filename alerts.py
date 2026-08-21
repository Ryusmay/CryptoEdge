# ============================================================
# Alerty systemowe (Windows toast + dzwiek)
# ============================================================

import threading
import config

_last = {}
_lock = threading.Lock()


def _cooldown_ok(key: str, seconds: float = 8.0) -> bool:
    import time
    with _lock:
        now = time.time()
        if now - _last.get(key, 0) < seconds:
            return False
        _last[key] = now
        return True


def _beep():
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        try:
            import winsound
            winsound.Beep(880, 180)
        except Exception:
            pass


def _toast_powershell(title: str, msg: str):
    import subprocess
    title_e = title.replace("'", "''")
    msg_e = msg.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        f"$n.BalloonTipTitle = '{title_e}'; "
        f"$n.BalloonTipText = '{msg_e}'; "
        "$n.ShowBalloonTip(4000); "
        "Start-Sleep -Milliseconds 4500; "
        "$n.Dispose()"
    )
    try:
        flags = 0
        try:
            flags = subprocess.CREATE_NO_WINDOW
        except Exception:
            pass
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception:
        pass


def _toast_win10(title: str, msg: str) -> bool:
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, msg, duration=4, threaded=True)
        return True
    except Exception:
        return False


def notify(title: str, message: str, kind: str = "info"):
    if not getattr(config, "ALERTS_ENABLED", True):
        return
    key = f"{kind}:{title}:{message[:40]}"
    if not _cooldown_ok(key, 6.0):
        return

    def _run():
        try:
            if getattr(config, "ALERT_SOUND", True):
                _beep()
            if getattr(config, "ALERT_PUSH", True):
                if not _toast_win10(title, message):
                    _toast_powershell(title, message)
        except Exception as e:
            print(f"[Alert] {title}: {message} ({e})")

    threading.Thread(target=_run, daemon=True).start()
    print(f"[Alert] {title}: {message}")


def alert_halt(reason: str):
    if getattr(config, "ALERT_ON_HALT", True):
        notify("CryptoEdge HALT", reason or "Bot zatrzymany", "halt")


def alert_margin_call(symbol: str, pnl: float):
    if getattr(config, "ALERT_ON_MARGIN_CALL", True):
        notify("Margin Call", f"{symbol} PnL ${pnl:+.4f}", "mc")


def alert_close(symbol: str, direction: str, reason: str, pnl: float):
    if getattr(config, "ALERT_ON_CLOSE", True):
        notify("Zamknieto pozycje", f"{direction} {symbol} | {reason} | ${pnl:+.4f}", "close")


def alert_partial(symbol: str, pct: float, pnl: float):
    notify("Partial TP", f"{symbol} zamknieto {pct*100:.0f}% | ${pnl:+.4f}", "partial")


def alert_feed_fail(msg: str):
    if getattr(config, "ALERT_ON_FEED_FAIL", True):
        notify("Feed / dane", msg, "feed")


def alert_open(symbol: str, direction: str):
    if getattr(config, "ALERT_ON_OPEN", False):
        notify("Nowa pozycja", f"{direction} {symbol}", "open")
