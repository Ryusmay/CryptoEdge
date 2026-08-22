import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import console_capture as cc

def test_tee_writes_file(tmp_path=None):
    logs = Path(cc.LOGS_DIR)
    logs.mkdir(parents=True, exist_ok=True)
    path = cc.install()
    print("HELLO_CONSOLE_CAPTURE")
    sys.stderr.write("ERR_LINE\n")
    sys.stdout.flush()
    text = path.read_text(encoding="utf-8", errors="replace")
    assert "HELLO_CONSOLE_CAPTURE" in text
    assert "ERR_LINE" in text
    assert "START" in text


class TestHideConsoleWindow(unittest.TestCase):
    """22.08.2026: okno konsoli jest teraz chowane po starcie natywnego UI
    (patrz pyside6_ui.run_pyside6_ui), zeby nie bylo dwoch otwartych okien
    obok siebie. Caly ten test celowo NIGDY nie wola prawdziwego
    ctypes.windll - podmieniamy je fejkiem, zeby test nie chowal naprawde
    zadnego realnego okna konsoli (np. tego, w ktorym leci run_tests.py)."""

    def test_noop_off_windows(self):
        with patch.object(cc.os, "name", "posix"):
            self.assertFalse(cc.hide_console_window())

    def test_hides_when_console_handle_present(self):
        fake_kernel32 = MagicMock()
        fake_kernel32.GetConsoleWindow.return_value = 12345
        fake_user32 = MagicMock()
        fake_windll = MagicMock(kernel32=fake_kernel32, user32=fake_user32)
        with patch.object(cc.os, "name", "nt"), \
                patch.object(cc.ctypes, "windll", fake_windll, create=True):
            self.assertTrue(cc.hide_console_window())
        fake_user32.ShowWindow.assert_called_once_with(12345, 0)

    def test_returns_false_when_no_console_handle(self):
        fake_kernel32 = MagicMock()
        fake_kernel32.GetConsoleWindow.return_value = 0
        fake_windll = MagicMock(kernel32=fake_kernel32)
        with patch.object(cc.os, "name", "nt"), \
                patch.object(cc.ctypes, "windll", fake_windll, create=True):
            self.assertFalse(cc.hide_console_window())

    def test_exception_from_winapi_is_swallowed(self):
        fake_windll = MagicMock()
        fake_windll.kernel32.GetConsoleWindow.side_effect = OSError("boom")
        with patch.object(cc.os, "name", "nt"), \
                patch.object(cc.ctypes, "windll", fake_windll, create=True):
            self.assertFalse(cc.hide_console_window())
