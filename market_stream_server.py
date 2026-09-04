"""Loopback-only UI market stream using the maintained websockets server."""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


PayloadFactory = Callable[[], dict[str, Any]]


def envelope(session_id: str, sequence_id: int, kind: str, payload: dict) -> str:
    return json.dumps({
        "session_id": session_id,
        "sequence_id": sequence_id,
        "emitted_at_ms": int(time.time() * 1000),
        "kind": kind,
        "payload": payload,
    }, ensure_ascii=False, default=str, separators=(",", ":"))


class MarketStreamServer:
    def __init__(self, snapshot_factory: PayloadFactory, delta_factory: PayloadFactory,
                 host: str = "127.0.0.1", port: int = 47822, token: str = "",
                 interval_s: float = 1.0, max_clients: int = 4):
        self.snapshot_factory = snapshot_factory
        self.delta_factory = delta_factory
        self.host = host if host in ("127.0.0.1", "localhost", "::1") else "127.0.0.1"
        self.port = int(port)
        self.token = token
        self.interval_s = max(0.1, float(interval_s))
        self.max_clients = max(1, int(max_clients))
        self.server = None
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error = ""
        self._clients = 0
        self._lock = threading.Lock()

    def start(self) -> "MarketStreamServer":
        if self.thread and self.thread.is_alive():
            return self
        self.thread = threading.Thread(target=self._run, daemon=True, name="market-stream")
        self.thread.start()
        self.ready.wait(timeout=3)
        return self

    def stop(self) -> None:
        server = self.server
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
        self.server = None

    def _run(self) -> None:
        try:
            from websockets.sync.server import serve
            origins = [None, "http://127.0.0.1:1420", "http://localhost:1420",
                       "http://tauri.localhost", "tauri://localhost"]
            with serve(self._handle, self.host, self.port, origins=origins,
                       ping_interval=20, ping_timeout=20, close_timeout=5,
                       open_timeout=5, max_size=16 * 1024, max_queue=4,
                       compression=None, server_header=None) as server:
                self.server = server
                self.ready.set()
                server.serve_forever()
        except Exception as exc:
            self.error = str(exc)[:240]
            self.ready.set()

    def _authorized(self, connection) -> bool:
        request = getattr(connection, "request", None)
        path = str(getattr(request, "path", "") or "")
        parsed = urlparse(path)
        if parsed.path != "/api/stream":
            return False
        if not self.token:
            return True
        supplied = (parse_qs(parsed.query).get("token") or [""])[0]
        return supplied == self.token

    def _handle(self, connection) -> None:
        if not self._authorized(connection):
            connection.close(1008, "unauthorized stream")
            return
        with self._lock:
            if self._clients >= self.max_clients:
                connection.close(1013, "too many clients")
                return
            self._clients += 1
        session_id = uuid.uuid4().hex
        sequence = 1
        try:
            connection.send(envelope(session_id, sequence, "snapshot", self.snapshot_factory()))
            previous = None
            while True:
                payload = self.delta_factory()
                encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
                sequence += 1
                if encoded == previous:
                    connection.send(envelope(session_id, sequence, "heartbeat", {}))
                else:
                    connection.send(envelope(session_id, sequence, "delta", payload))
                    previous = encoded
                time.sleep(self.interval_s)
        except Exception:
            return
        finally:
            with self._lock:
                self._clients = max(0, self._clients - 1)
