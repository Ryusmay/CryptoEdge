import json
import socket
import unittest
from contextlib import closing

from market_stream_server import MarketStreamServer, envelope


class TestMarketStreamServer(unittest.TestCase):
    @staticmethod
    def _port():
        with closing(socket.socket()) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _server(self, **kwargs):
        server = MarketStreamServer(
            kwargs.pop("snapshot_factory", lambda: {"ok": True}),
            kwargs.pop("delta_factory", lambda: {"prices": {"BTC": 1}}),
            port=self._port(), interval_s=0.1, **kwargs,
        ).start()
        self.addCleanup(server.stop)
        self.assertFalse(server.error)
        return server

    def test_envelope_is_compact_and_monotonic_fields_are_explicit(self):
        payload = json.loads(envelope("session", 7, "heartbeat", {}))
        self.assertEqual(payload["session_id"], "session")
        self.assertEqual(payload["sequence_id"], 7)
        self.assertEqual(payload["kind"], "heartbeat")
        self.assertIsInstance(payload["emitted_at_ms"], int)

    def test_forces_loopback_host(self):
        server = MarketStreamServer(lambda: {}, lambda: {}, host="0.0.0.0")
        self.assertEqual(server.host, "127.0.0.1")

    def test_real_server_snapshot_then_delta_and_graceful_stop(self):
        from websockets.sync.client import connect
        server = self._server()
        with connect(f"ws://127.0.0.1:{server.port}/api/stream", origin="http://tauri.localhost", open_timeout=2) as client:
            first = json.loads(client.recv(timeout=2))
            second = json.loads(client.recv(timeout=2))
        self.assertEqual(first["kind"], "snapshot")
        self.assertEqual(second["kind"], "delta")
        self.assertEqual((first["sequence_id"], second["sequence_id"]), (1, 2))

    def test_rejects_wrong_path_and_wrong_token(self):
        from websockets.exceptions import ConnectionClosedError
        from websockets.sync.client import connect
        server = self._server(token="secret")
        for path in ("/wrong?token=secret", "/api/stream?token=wrong"):
            with self.subTest(path=path):
                with connect(f"ws://127.0.0.1:{server.port}{path}", origin="http://tauri.localhost", open_timeout=2) as client:
                    with self.assertRaises(ConnectionClosedError) as caught:
                        client.recv(timeout=2)
                    self.assertEqual(caught.exception.rcvd.code, 1008)

    def test_rejects_untrusted_origin_during_handshake(self):
        from websockets.sync.client import connect
        server = self._server()
        with self.assertRaises(Exception):
            connect(f"ws://127.0.0.1:{server.port}/api/stream", origin="https://evil.example", open_timeout=2)

    def test_unchanged_delta_becomes_heartbeat_with_valid_envelope(self):
        from websockets.sync.client import connect
        server = self._server(delta_factory=lambda: {"prices": {"ETH": 2}})
        with connect(f"ws://127.0.0.1:{server.port}/api/stream", origin="http://tauri.localhost", open_timeout=2) as client:
            messages = [json.loads(client.recv(timeout=2)) for _ in range(3)]
        self.assertEqual([item["kind"] for item in messages], ["snapshot", "delta", "heartbeat"])
        self.assertEqual([item["sequence_id"] for item in messages], [1, 2, 3])
        self.assertEqual(messages[-1]["payload"], {})
        self.assertTrue(all(item["session_id"] == messages[0]["session_id"] for item in messages))

    def test_reconnect_gets_new_session_and_sequence_restarts(self):
        from websockets.sync.client import connect
        server = self._server()
        url = f"ws://127.0.0.1:{server.port}/api/stream"
        with connect(url, origin="http://tauri.localhost", open_timeout=2) as client:
            first = json.loads(client.recv(timeout=2))
        with connect(url, origin="http://tauri.localhost", open_timeout=2) as client:
            second = json.loads(client.recv(timeout=2))
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual((first["sequence_id"], second["sequence_id"]), (1, 1))

    def test_client_limit_closes_excess_connection(self):
        from websockets.exceptions import ConnectionClosedError
        from websockets.sync.client import connect
        server = self._server(max_clients=1)
        url = f"ws://127.0.0.1:{server.port}/api/stream"
        with connect(url, origin="http://tauri.localhost", open_timeout=2) as first:
            json.loads(first.recv(timeout=2))
            with connect(url, origin="http://tauri.localhost", open_timeout=2) as second:
                with self.assertRaises(ConnectionClosedError) as caught:
                    second.recv(timeout=2)
                self.assertEqual(caught.exception.rcvd.code, 1013)


if __name__ == "__main__":
    unittest.main()
