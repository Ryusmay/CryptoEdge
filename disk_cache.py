"""Prosty cache na dysku (JSON), przetrwa restart procesu.

Zasada: "przestarzałe dane != brak danych - stara lista jest lepsza niż
pusta po limicie". Bez tego każdy restart bota startuje z zerowym cache i
od razu robi burst zapytań, żeby cokolwiek uzyskać - dokładnie ten wzorzec,
który wywoływał rate-limit spiral 19-20.08.

Używane dla: listy instrumentów Blofin i świec 4H/1D/1W (długie interwały -
nie ma sensu persistować 5m/15m/1h, zmieniają się za szybko, żeby coś dać po
restarcie poza mylącym poczuciem świeżości).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

CACHE_DIR = Path(__file__).resolve().parent / "data" / "disk_cache"


def load(key: str) -> Optional[dict]:
    """Zwraca {'ts': float, 'data': Any} albo None (brak pliku / uszkodzony)."""
    path = CACHE_DIR / f"{key}.json"
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "ts" in payload and "data" in payload:
            return payload
    except (OSError, ValueError, UnicodeError):
        pass
    return None


def save(key: str, data: Any) -> None:
    """Zapis atomowy (tmp + rename) - crash w trakcie zapisu nie psuje pliku."""
    path = CACHE_DIR / f"{key}.json"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "data": data}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
