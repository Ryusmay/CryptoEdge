"""Persistent empirical TP-event calibration for the daytrading lifecycle."""

from __future__ import annotations

import json
import os
import tempfile


class DayExpectancyCalibrator:
    def __init__(self, path="logs/day_expectancy_calibration.json"):
        self.path = path
        self.data = {"n": 0, "tp1": 0, "tp2": 0, "profiles": {}, "regimes": {}}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            for key in ("n", "tp1", "tp2"):
                self.data[key] = max(0, int(loaded.get(key, 0)))
            for key in ("profiles", "regimes"):
                self.data[key] = loaded.get(key) if isinstance(loaded.get(key), dict) else {}
        except Exception:
            pass

    @staticmethod
    def _snapshot_row(row: dict, segment: str = "global") -> dict:
        n, tp1, tp2 = (max(0, int(row.get(k, 0))) for k in ("n", "tp1", "tp2"))
        return {
            "n": n,
            "p_tp1": tp1 / n if n else 0.0,
            "p_tp2_given_tp1": tp2 / tp1 if tp1 else 0.0,
            "segment": segment,
        }

    def snapshot(self, profile: str = None, regime: str = None) -> dict:
        # Profil major/alt jest wazniejszy od globalnej sredniej. Segment
        # o malej probie nadal zostanie oznaczony LOW_SAMPLE przez consumer.
        profile_key = str(profile or "").upper()
        if profile_key and profile_key in self.data["profiles"]:
            return self._snapshot_row(self.data["profiles"][profile_key], f"profile:{profile_key}")
        regime_key = str(regime or "").upper()
        if regime_key and regime_key in self.data["regimes"]:
            return self._snapshot_row(self.data["regimes"][regime_key], f"regime:{regime_key}")
        return self._snapshot_row(self.data)

    @staticmethod
    def _increment(row: dict, hit_tp1: bool, hit_tp2: bool) -> None:
        row["n"] = max(0, int(row.get("n", 0))) + 1
        row["tp1"] = max(0, int(row.get("tp1", 0))) + int(bool(hit_tp1))
        row["tp2"] = max(0, int(row.get("tp2", 0))) + int(bool(hit_tp1 and hit_tp2))

    def record(self, hit_tp1: bool, hit_tp2: bool,
               profile: str = None, regime: str = None) -> None:
        self._increment(self.data, hit_tp1, hit_tp2)
        profile_key = str(profile or "").upper()
        if profile_key:
            row = self.data["profiles"].setdefault(profile_key, {"n": 0, "tp1": 0, "tp2": 0})
            self._increment(row, hit_tp1, hit_tp2)
        regime_key = str(regime or "").upper()
        if regime_key:
            row = self.data["regimes"].setdefault(regime_key, {"n": 0, "tp1": 0, "tp2": 0})
            self._increment(row, hit_tp1, hit_tp2)
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="day_calib_", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


_CALIBRATOR = None


def get_day_calibrator() -> DayExpectancyCalibrator:
    global _CALIBRATOR
    if _CALIBRATOR is None:
        _CALIBRATOR = DayExpectancyCalibrator()
    return _CALIBRATOR
