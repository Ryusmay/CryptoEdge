import json
import unittest
from unittest.mock import MagicMock, patch

import grpc_service
from grpc_service import GrpcServer, _json_deserializer, _json_serializer


class TestJsonSerialization(unittest.TestCase):
    def test_serialize_then_deserialize_roundtrip(self):
        obj = {"cycle": 5, "equity": 100.5, "running": True, "note": "ąćę"}
        raw = _json_serializer(obj)
        self.assertIsInstance(raw, bytes)
        self.assertEqual(obj, _json_deserializer(raw))

    def test_deserialize_empty_bytes_returns_empty_dict(self):
        self.assertEqual({}, _json_deserializer(b""))

    def test_serialize_non_json_native_value_uses_str_fallback(self):
        class Weird:
            def __str__(self):
                return "weird-value"
        raw = _json_serializer({"x": Weird()})
        self.assertEqual({"x": "weird-value"}, json.loads(raw))


class TestGrpcServerAvailability(unittest.TestCase):
    def test_start_returns_false_when_grpcio_not_installed(self):
        with patch.object(grpc_service, "_GRPC_AVAILABLE", False):
            server = GrpcServer(snapshot_provider=lambda: {"cycle": 1})
            self.assertFalse(server.available)
            self.assertFalse(server.start())
            self.assertFalse(server.is_running())

    def test_stop_without_start_does_not_raise(self):
        server = GrpcServer(snapshot_provider=lambda: {})
        server.stop()  # nie powinno rzucic, mimo ze nigdy nie wystartowal


class TestGrpcServerHandleGetSnapshot(unittest.TestCase):
    """Testuje logike handlera bezposrednio (bez przechodzenia przez cala
    warstwe grpc.server() - grpcio nie jest zainstalowany w tym srodowisku),
    czyli dokladnie to, co realnie wykona sie na kazde wywolanie klienta."""

    def test_handle_get_snapshot_returns_provider_result_as_dict(self):
        server = GrpcServer(snapshot_provider=lambda: {"cycle": 7, "equity": 123.45})
        result = server.handle_get_snapshot({}, None)
        self.assertEqual({"cycle": 7, "equity": 123.45}, result)

    def test_handle_get_snapshot_returns_empty_dict_when_provider_returns_none(self):
        server = GrpcServer(snapshot_provider=lambda: None)
        self.assertEqual({}, server.handle_get_snapshot({}, None))

    def test_handle_get_snapshot_catches_provider_exception_and_sets_context(self):
        def boom():
            raise RuntimeError("provider padł")
        server = GrpcServer(snapshot_provider=boom)
        fake_grpc = MagicMock()
        fake_grpc.StatusCode.INTERNAL = "INTERNAL"
        context = MagicMock()
        with patch.object(grpc_service, "grpc", fake_grpc):
            result = server.handle_get_snapshot({}, context)
        self.assertIn("error", result)
        context.set_code.assert_called_once_with("INTERNAL")
        context.set_details.assert_called_once()

    def test_handle_get_snapshot_without_context_does_not_raise_on_error(self):
        def boom():
            raise RuntimeError("boom")
        server = GrpcServer(snapshot_provider=boom)
        result = server.handle_get_snapshot({}, None)
        self.assertIn("error", result)


class TestGrpcServerGenericHandlerRouting(unittest.TestCase):
    """Testuje routing metod (_generic_handler) - kluczowe dla poprawnosci
    gRPC-nad-JSON, gdzie nazwa metody jest odczytywana z pelnej sciezki."""

    def _fake_grpc_module(self):
        fake = MagicMock()
        fake.unary_unary_rpc_method_handler.side_effect = lambda fn, **kw: ("HANDLER", fn, kw)
        return fake

    def test_known_method_returns_handler_tuple(self):
        server = GrpcServer(snapshot_provider=lambda: {"ok": True})
        details = MagicMock()
        details.method = f"/{grpc_service.SERVICE_NAME}/{grpc_service.METHOD_GET_SNAPSHOT}"
        with patch.object(grpc_service, "grpc", self._fake_grpc_module()):
            result = server._generic_handler(details)
        self.assertIsNotNone(result)
        self.assertEqual("HANDLER", result[0])
        self.assertEqual(server.handle_get_snapshot, result[1])

    def test_unknown_method_returns_none(self):
        server = GrpcServer(snapshot_provider=lambda: {})
        details = MagicMock()
        details.method = f"/{grpc_service.SERVICE_NAME}/SomeOtherMethod"
        with patch.object(grpc_service, "grpc", self._fake_grpc_module()):
            result = server._generic_handler(details)
        self.assertIsNone(result)


class TestGrpcServerStartupWithFakeGrpc(unittest.TestCase):
    """Symuluje pelny cykl start/stop z fake grpc.server(), zeby zweryfikowac
    ze GrpcServer poprawnie woluje add_generic_rpc_handlers/add_insecure_port/
    start w oczekiwanej kolejnosci."""

    def test_start_configures_and_starts_fake_server(self):
        fake_server_instance = MagicMock()
        fake_grpc = MagicMock()
        fake_grpc.server.return_value = fake_server_instance
        server = GrpcServer(snapshot_provider=lambda: {}, port=50099)
        with patch.object(grpc_service, "_GRPC_AVAILABLE", True), \
             patch.object(grpc_service, "grpc", fake_grpc):
            ok = server.start()
        self.assertTrue(ok)
        self.assertTrue(server.is_running())
        fake_server_instance.add_generic_rpc_handlers.assert_called_once()
        fake_server_instance.add_insecure_port.assert_called_once_with("[::]:50099")
        fake_server_instance.start.assert_called_once()

    def test_start_failure_returns_false_and_does_not_raise(self):
        fake_grpc = MagicMock()
        fake_grpc.server.side_effect = RuntimeError("port zajęty")
        server = GrpcServer(snapshot_provider=lambda: {})
        with patch.object(grpc_service, "_GRPC_AVAILABLE", True), \
             patch.object(grpc_service, "grpc", fake_grpc):
            ok = server.start()
        self.assertFalse(ok)
        self.assertFalse(server.is_running())

    def test_second_start_is_noop_when_already_running(self):
        fake_server_instance = MagicMock()
        fake_grpc = MagicMock()
        fake_grpc.server.return_value = fake_server_instance
        server = GrpcServer(snapshot_provider=lambda: {})
        with patch.object(grpc_service, "_GRPC_AVAILABLE", True), \
             patch.object(grpc_service, "grpc", fake_grpc):
            server.start()
            server.start()
        self.assertEqual(1, fake_grpc.server.call_count)

    def test_stop_calls_server_stop_with_grace_period(self):
        fake_server_instance = MagicMock()
        fake_grpc = MagicMock()
        fake_grpc.server.return_value = fake_server_instance
        server = GrpcServer(snapshot_provider=lambda: {})
        with patch.object(grpc_service, "_GRPC_AVAILABLE", True), \
             patch.object(grpc_service, "grpc", fake_grpc):
            server.start()
            server.stop(grace=2.5)
        fake_server_instance.stop.assert_called_once_with(2.5)
        self.assertFalse(server.is_running())


if __name__ == "__main__":
    unittest.main()
