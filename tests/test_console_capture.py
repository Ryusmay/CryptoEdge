import io
import sys
from pathlib import Path
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
