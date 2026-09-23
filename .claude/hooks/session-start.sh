#!/bin/bash
# SessionStart hook for Claude Code on the web: installs what CI installs
# (.github/workflows/tests.yml) so `python run_tests.py` and the frontend
# vitest suite run in a fresh container.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Same env as CI: PySide6 offscreen, deterministic set ordering for bit-exact gates.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo 'export QT_QPA_PLATFORM=offscreen'
    echo 'export PYTHONHASHSEED=0'
    echo 'export PYTHONIOENCODING=utf-8'
  } >> "$CLAUDE_ENV_FILE"
fi

# System libraries Qt needs in offscreen mode (best effort; skipped if apt is unavailable).
missing_qt_libs() {
  for lib in libEGL.so.1 libGL.so.1 libxkbcommon.so.0 libdbus-1.so.3; do
    ldconfig -p | grep -q "$lib" || return 0
  done
  return 1
}
if command -v apt-get >/dev/null 2>&1 && missing_qt_libs; then
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -qq >/dev/null 2>&1 || true
  $SUDO apt-get install -y -qq libegl1 libgl1 libxkbcommon0 libdbus-1-3 >/dev/null 2>&1 \
    || echo "session-start: apt install of Qt libs failed, PySide6 tests may fail" >&2
fi

python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt
# The image's Debian python3-cryptography satisfies requirements.txt but ships
# without cffi, so anything importing its hazmat bindings fails.
python3 -c "import _cffi_backend" 2>/dev/null \
  || python3 -m pip install --quiet --disable-pip-version-check cffi

# npm ci, not npm install: install rewrites the committed package-lock.json.
# Skipped when node_modules is already present in the cached container.
if [ -f frontend/package-lock.json ] && [ ! -d frontend/node_modules ] && command -v npm >/dev/null 2>&1; then
  (cd frontend && npm ci --no-audit --no-fund --loglevel=error)
fi
