"""Persistent empirical TP-event calibration for the daytrading lifecycle."""

from __future__ import annotations

import json
import os
import tempfile


class DayExpectancyCalibrator:
    def __init__(self, path="logs/day_expectancy_calibration.json"):
        self.path = path
        self.data = {"n": 0, "tp1": 0, "tp2": 0}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            for key in self.data:
                self.data[key] = max(0, int(loaded.get(key, 0)))
        except Exception:
            pass

    def snapshot(self) -> dict:
        n, tp1, tp2 = self.data["n"], self.data["tp1"], self.data["tp2"]
        return {
            "n": n,
            "p_tp1": tp1 / n if n else 0.0,
            "p_tp2_given_tp1": tp2 / tp1 if tp1 else 0.0,
        }

    def record(self, hit_tp1: bool, hit_tp2: bool) -> None:
        self.data["n"] += 1
        self.data["tp1"] += int(bool(hit_tp1))
        self.data["tp2"] += int(bool(hit_tp1 and hit_tp2))
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
