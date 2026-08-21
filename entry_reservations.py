"""Short-lived, thread-safe entry slot reservations."""
import threading
import time


class EntryReservationBook:
    def __init__(self, ttl_seconds=30.0):
        self.ttl_seconds = float(ttl_seconds)
        self._lock = threading.Lock()
        self._rows = {}

    def _prune(self, now):
        for key, expiry in list(self._rows.items()):
            if expiry <= now:
                self._rows.pop(key, None)

    def reserve(self, symbol, engine, open_count, max_positions):
        key = (str(symbol or "").upper(), str(engine or "trend").lower())
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if key in self._rows:
                return False, "ENTRY_ALREADY_RESERVED"
            if int(open_count) + len(self._rows) >= int(max_positions):
                return False, "ENTRY_SLOTS_RESERVED"
            self._rows[key] = now + self.ttl_seconds
        return True, "RESERVED"

    def release(self, symbol, engine):
        key = (str(symbol or "").upper(), str(engine or "trend").lower())
        with self._lock:
            self._rows.pop(key, None)

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return [{"symbol": k[0], "engine": k[1], "ttl_sec": round(v-now, 1)} for k, v in self._rows.items()]
