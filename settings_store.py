# ============================================================
# Ustawienia użytkownika (JSON) – nadpisują config w runtime
# ============================================================

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import config

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "logs" / "settings.json"

# Klucze edytowalne z UI + domyślne
DEFAULTS: Dict[str, Any] = {
    "PAPER_TRADING": True,           # True=DEMO/paper, False=LIVE (giełda)
    "STRATEGY_MODE": "DAYTRADING_V2",  # V2 glowny
    "STARTING_CAPITAL": 100.0,       # startowe saldo DEMO (paper)

    "ALERTS_ENABLED": True,
    "ALERT_ON_OPEN": False,
    "ALERT_ON_CLOSE": True,
    "ALERT_ON_HALT": True,
    "ALERT_ON_MARGIN_CALL": True,
    "ALERT_ON_FEED_FAIL": True,
    "ALERT_SOUND": True,
    "ALERT_PUSH": True,          # Windows toast / balloon
    "BLOCK_OB_THIN": True,
    "BLOCK_PUMP_CHASE_PCT": 22.0,
    "BLOCK_RANGE_REGIME": False,
    "BLOCK_STRAT_NA_IN_RANGE": True,
    "REQUIRE_PRIMARY_STRATEGY": True,
    "AGGRESSIVE_MODE": False,
    "MIN_SIGNAL_STRENGTH": 0.48,
}


def load_settings() -> Dict[str, Any]:
    data = dict(DEFAULTS)
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in DEFAULTS:
                        data[k] = v
    except Exception as e:
        print(f"[Settings] load: {e}")
    return data


def save_settings(data: Dict[str, Any]) -> bool:
    """Atomically persist public settings and verify the written payload."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        out = {k: data.get(k, DEFAULTS[k]) for k in DEFAULTS}
        mode = str(out.get("STRATEGY_MODE") or "DAYTRADING").upper()
        out["STRATEGY_MODE"] = mode if mode in ("SWING", "DAYTRADING", "DAYTRADING_V2") else "DAYTRADING_V2"
        temporary = SETTINGS_FILE.with_suffix(".json.tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
            f.flush()
        temporary.replace(SETTINGS_FILE)
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            verified = json.load(f)
        return verified.get("STRATEGY_MODE") == out["STRATEGY_MODE"]
    except Exception as e:
        print(f"[Settings] save: {e}")
        return False


def apply_settings(data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Wpisuje wartości do modułu config (runtime)."""
    data = data if data is not None else load_settings()
    if not SETTINGS_FILE.exists():
        save_settings(data)
    for k, v in data.items():
        if hasattr(config, k) or k in DEFAULTS:
            try:
                if k == "PAPER_TRADING":
                    # Granica systemu: JSON moze przyniesc "false", a bool("false")
                    # to True. Bez tej konwersji czesc systemu widzialaby PAPER,
                    # a czesc LIVE. Nierozpoznana wartosc => papier.
                    from cryptoedge.domain.trading_mode import coerce_paper_flag
                    v = coerce_paper_flag(v)
                if k == "STARTING_CAPITAL":
                    v = float(v)
                    if v < 1:
                        v = 1.0
                if k == "MIN_SIGNAL_STRENGTH":
                    v = float(v)
                if k == "STRATEGY_MODE":
                    v = str(v or "DAYTRADING_V2").upper()
                    if v == "DAYTRADING" and getattr(config, "DAYTRADING_V2_ENABLED", False):
                        v = "DAYTRADING_V2"
                    if v not in ("SWING", "DAYTRADING", "DAYTRADING_V2"):
                        v = "DAYTRADING_V2"
                setattr(config, k, v)
            except Exception:
                pass
    return data


def update_setting(key: str, value: Any) -> Dict[str, Any]:
    data = load_settings()
    if key not in DEFAULTS:
        raise KeyError(key)
    data[key] = value
    if not save_settings(data):
        raise OSError(f"Nie udało się zapisać ustawienia {key}")
    apply_settings(data)
    return data
