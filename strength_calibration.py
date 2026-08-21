# ============================================================
# 12. Kalibracja strength → expected R
# Empiryczna mapa (priory + aktualizacja z closed trades)
# ============================================================

from __future__ import annotations
import json
import os
from typing import Dict, List, Optional, Any

# Prior (start) – do wymiany gdy zbierzemy historię
# strength bucket midpoint → expected R (reward/risk)
DEFAULT_CURVE = [
    # (strength_lo, strength_hi, expected_R, n_obs)
    (0.50, 0.55, 0.15, 0),
    (0.55, 0.60, 0.25, 0),
    (0.60, 0.65, 0.40, 0),
    (0.65, 0.70, 0.55, 0),
    (0.70, 0.75, 0.70, 0),
    (0.75, 0.80, 0.85, 0),
    (0.80, 0.85, 1.00, 0),
    (0.85, 0.90, 1.15, 0),
    (0.90, 0.95, 1.30, 0),
    (0.95, 1.01, 1.45, 0),
]


class StrengthCalibrator:
    def __init__(self, path: str = None):
        self.path = path or os.path.join("logs", "strength_calibration.json")
        self.buckets: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.buckets = data.get("buckets") or []
            except Exception:
                self.buckets = []
        if not self.buckets:
            self.buckets = [
                {"lo": a, "hi": b, "expected_r": r, "n": n, "sum_r": 0.0}
                for a, b, r, n in DEFAULT_CURVE
            ]

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"buckets": self.buckets}, f, indent=2)
        except Exception as e:
            print(f"[Calib] save: {e}")

    def expected_r(self, strength: float) -> float:
        s = float(strength or 0)
        for b in self.buckets:
            if b["lo"] <= s < b["hi"]:
                return float(b["expected_r"])
        if s >= 1.0:
            return float(self.buckets[-1]["expected_r"])
        return float(self.buckets[0]["expected_r"]) if self.buckets else 0.3

    def calibration_meta(self, strength: float) -> Dict[str, Any]:
        import config
        s = float(strength or 0)
        bucket = next((b for b in self.buckets if b["lo"] <= s < b["hi"]), None)
        if bucket is None and self.buckets:
            bucket = self.buckets[-1] if s >= 1.0 else self.buckets[0]
        n = int((bucket or {}).get("n") or 0)
        min_obs = int(getattr(config, "EXPECTED_R_MIN_CALIBRATION_OBS", 30) or 30)
        if n <= 0:
            status = "PRIOR_ONLY"
        elif n < min_obs:
            status = "LOW_SAMPLE"
        else:
            status = "CALIBRATED"
        return {
            "status": status, "n": n, "min_obs": min_obs,
            "bucket_lo": (bucket or {}).get("lo"),
            "bucket_hi": (bucket or {}).get("hi"),
        }

    def is_strong(self, strength: float, min_expected_r: float = None) -> bool:
        """0.80 nie jest automatycznie 'bardzo mocny' – liczy się expected R."""
        import config
        thr = min_expected_r
        if thr is None:
            thr = float(getattr(config, "MIN_EXPECTED_R", 0.7))
        return self.expected_r(strength) >= thr

    def label(self, strength: float) -> str:
        er = self.expected_r(strength)
        if er >= 1.2:
            return "VERY_STRONG"
        if er >= 0.9:
            return "STRONG"
        if er >= 0.55:
            return "MODERATE"
        if er >= 0.3:
            return "WEAK"
        return "VERY_WEAK"

    def annotate(self, signal: dict) -> dict:
        s = float(signal.get("strength") or 0)
        er = self.expected_r(s)
        lab = self.label(s)
        signal["expected_r"] = round(er, 3)
        signal["strength_label"] = lab
        meta = self.calibration_meta(s)
        signal["expected_r_calibration"] = meta
        signal["expected_r_status"] = meta["status"]
        return signal

    def update_from_trade(self, strength: float, realized_r: float):
        """realized_r = pnl / risk_usd (lub pnl_pct / |sl_pct|)."""
        s = float(strength or 0)
        r = float(realized_r)
        for b in self.buckets:
            if b["lo"] <= s < b["hi"]:
                n = int(b.get("n") or 0)
                sum_r = float(b.get("sum_r") or 0.0) + r
                n += 1
                # blend prior with empirical (Bayesian-ish)
                prior = float(b.get("expected_r") or 0.5)
                empirical = sum_r / n
                # im więcej obs, tym większa waga empirii
                # n=1 nie może przeważyć prioru (BTW -1.5R zamykało bucket 0.80)
                w = 0.0 if n < 8 else min(0.85, n / (n + 20.0))
                b["expected_r"] = (1 - w) * prior + w * empirical
                b["n"] = n
                b["sum_r"] = sum_r
                self.save()
                return
        self.save()


_CALIB: Optional[StrengthCalibrator] = None


def get_calibrator() -> StrengthCalibrator:
    global _CALIB
    if _CALIB is None:
        _CALIB = StrengthCalibrator()
    return _CALIB
