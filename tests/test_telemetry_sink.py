"""NonBlockingEventSink: emit() nie czeka na I/O i liczy to, co odrzuca."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cryptoedge.domain import DomainEvent, EventType
from cryptoedge.telemetry import JsonlWriter, NonBlockingEventSink


class NonBlockingEventSinkTests(unittest.TestCase):
    def test_emit_does_not_wait_for_a_blocked_writer(self):
        gate = threading.Event()
        written = []

        def slow_writer(item):
            gate.wait(5)
            written.append(item)

        sink = NonBlockingEventSink(slow_writer, max_queue=3)
        try:
            t0 = time.perf_counter()
            results = [sink.emit(i) for i in range(10)]
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 0.5)
            # 1 w rece writera + 3 w kolejce; reszta odrzucona i policzona
            self.assertGreaterEqual(results.count(False), 6)
            self.assertEqual(sink.stats()["dropped"], results.count(False))
        finally:
            gate.set()
            self.assertTrue(sink.close())
        self.assertEqual(len(written), results.count(True))
        self.assertEqual(written, sorted(written))  # kolejnosc zachowana

    def test_writer_errors_are_counted_not_raised(self):
        def broken(_):
            raise OSError("disk full")

        sink = NonBlockingEventSink(broken)
        self.assertTrue(sink.emit({"a": 1}))
        self.assertTrue(sink.flush())
        stats = sink.stats()
        self.assertEqual(stats["write_errors"], 1)
        self.assertIn("disk full", stats["last_error"])
        sink.close()

    def test_emit_after_close_is_dropped(self):
        sink = NonBlockingEventSink(lambda _: None)
        sink.close()
        self.assertFalse(sink.emit(1))
        self.assertEqual(sink.stats()["dropped"], 1)

    def test_jsonl_writer_serializes_domain_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            sink = NonBlockingEventSink(JsonlWriter(path))
            sink.emit(DomainEvent(EventType.EXECUTION, 1, "router", {"validity": "LATE"},
                                  decision_id="d1"))
            sink.emit({"plain": True})
            self.assertTrue(sink.close())
            rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows[0]["event_type"], "EXECUTION")
        self.assertEqual(rows[0]["decision_id"], "d1")
        self.assertEqual(rows[1], {"plain": True})


if __name__ == "__main__":
    unittest.main()
