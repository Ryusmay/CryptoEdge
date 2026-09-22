"""Nieblokujace ujscie zdarzen: hot path oddaje zdarzenie, zapis robi kto inny.

Dzis kazdy wiersz decision_telemetry, CSV loggera, stan ochrony i kalibracji
jest pisany synchronicznie w watku `bot` (open('a') pod globalnym lockiem,
json.dump stanu). Wzorzec z jev-trader wart przejecia to rozdzial "decyzja
i zlecenie w petli, potwierdzenia/ksiegowanie/logi poza nia" - ale jev-trader
sam go lamie: `appendFileSync("data/events.jsonl")` jest wolany wewnatrz
petli bloku, przed zwolnieniem flagi `busy`. Tu zapis jest wylacznie w
watku sinka.

Zasady:
- `emit()` nigdy nie czeka: `put_nowait`; pelna kolejka -> zdarzenie
  odrzucone i POLICZONE (`dropped`), nie zgubione po cichu.
- blad writera nie wraca do wolajacego (obserwacja nie moze zmieniac
  decyzji) - liczony w `write_errors`, ostatni zapamietany.
- kolejnosc zachowana dla jednego sinka (jeden watek zapisujacy).

Modul nie jest jeszcze wpiety w runtime. Wpiecie decision_telemetry zmienia
moment zapisu wiersza (test czytajacy plik zaraz po zapisie musi zrobic
`flush()`), wiec idzie osobnym krokiem, za flaga, z pomiarem.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

_STOP = object()


class NonBlockingEventSink:
    def __init__(self, writer: Callable[[Any], None], *, max_queue: int = 10_000,
                 name: str = "telemetry-sink"):
        if max_queue <= 0:
            raise ValueError("max_queue must be positive")
        self._writer = writer
        self._queue: queue.Queue = queue.Queue(maxsize=int(max_queue))
        self._lock = threading.Lock()
        self._accepted = 0
        self._written = 0
        self._dropped = 0
        self._write_errors = 0
        self._last_error: Optional[str] = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def emit(self, event: Any) -> bool:
        if self._closed:
            with self._lock:
                self._dropped += 1
            return False
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False
        with self._lock:
            self._accepted += 1
        return True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                try:
                    self._writer(item)
                    with self._lock:
                        self._written += 1
                except Exception as exc:  # noqa: BLE001 - obserwacja nie przerywa handlu
                    with self._lock:
                        self._write_errors += 1
                        self._last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        """Czeka, az wszystko przyjete zostanie przetworzone. Dla testow,
        zamkniecia procesu i narzedzi - nie dla hot path."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.005)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout: float = 5.0) -> bool:
        if self._closed:
            return not self._thread.is_alive()
        self._closed = True
        self.flush(timeout)
        try:
            self._queue.put(_STOP, timeout=max(0.0, timeout))
        except queue.Full:
            return False
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def stats(self) -> dict:
        with self._lock:
            return {"accepted": self._accepted, "written": self._written,
                    "dropped": self._dropped, "write_errors": self._write_errors,
                    "last_error": self._last_error, "queued": self._queue.qsize(),
                    "closed": self._closed}


class JsonlWriter:
    """Writer dla sinka: jeden obiekt -> jedna linia JSON. Obiekty z
    `to_legacy()` (DomainEvent i kontrakty domeny) sa serializowane przez nie."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def __call__(self, event: Any) -> None:
        row = event.to_legacy() if hasattr(event, "to_legacy") else event
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
