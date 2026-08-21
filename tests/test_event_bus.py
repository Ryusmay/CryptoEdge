import unittest
from unittest.mock import MagicMock, patch

import event_bus
from event_bus import EventBus, NullBackend, RedisStreamsBackend, build_event_bus


class TestNullBackend(unittest.TestCase):
    def test_publish_returns_false_without_raising(self):
        backend = NullBackend()
        self.assertFalse(backend.publish("stream", {"a": 1}))
        self.assertFalse(backend.available())


class TestRedisStreamsBackend(unittest.TestCase):
    def test_available_false_when_redis_package_not_installed(self):
        with patch.object(event_bus, "_REDIS_AVAILABLE", False):
            backend = RedisStreamsBackend()
            self.assertFalse(backend.available())

    def test_publish_returns_false_when_redis_not_installed(self):
        with patch.object(event_bus, "_REDIS_AVAILABLE", False):
            backend = RedisStreamsBackend()
            self.assertFalse(backend.publish("stream", {"a": 1}))

    def test_connects_lazily_and_pings_once(self):
        fake_client = MagicMock()
        fake_redis_lib = MagicMock()
        fake_redis_lib.Redis.from_url.return_value = fake_client
        with patch.object(event_bus, "_REDIS_AVAILABLE", True), \
             patch.object(event_bus, "_redis_lib", fake_redis_lib):
            backend = RedisStreamsBackend("redis://fake:6379/0")
            self.assertTrue(backend.available())
            self.assertTrue(backend.available())  # drugie wywolanie nie laczy sie ponownie
        fake_client.ping.assert_called_once()

    def test_publish_calls_xadd_with_maxlen_and_json_encodes_non_str_values(self):
        fake_client = MagicMock()
        fake_redis_lib = MagicMock()
        fake_redis_lib.Redis.from_url.return_value = fake_client
        with patch.object(event_bus, "_REDIS_AVAILABLE", True), \
             patch.object(event_bus, "_redis_lib", fake_redis_lib):
            backend = RedisStreamsBackend()
            ok = backend.publish("cryptoedge:cycles", {"cycle": 5, "symbol": "BTC", "extra": {"a": 1}})
        self.assertTrue(ok)
        fake_client.xadd.assert_called_once()
        args, kwargs = fake_client.xadd.call_args
        self.assertEqual("cryptoedge:cycles", args[0])
        flat = args[1]
        self.assertEqual("5", flat["cycle"])  # json.dumps(5) == "5"
        self.assertEqual("BTC", flat["symbol"])  # str przechodzi bez zmian
        self.assertEqual(event_bus.MAX_STREAM_LEN, kwargs.get("maxlen"))
        self.assertTrue(kwargs.get("approximate"))

    def test_connection_failure_is_cached_not_retried_every_publish(self):
        fake_redis_lib = MagicMock()
        fake_redis_lib.Redis.from_url.side_effect = ConnectionError("brak serwera")
        with patch.object(event_bus, "_REDIS_AVAILABLE", True), \
             patch.object(event_bus, "_redis_lib", fake_redis_lib):
            backend = RedisStreamsBackend()
            backend.publish("s", {"a": 1})
            backend.publish("s", {"a": 2})
        self.assertEqual(1, fake_redis_lib.Redis.from_url.call_count)

    def test_publish_exception_returns_false_not_raise(self):
        fake_client = MagicMock()
        fake_client.xadd.side_effect = RuntimeError("boom")
        fake_redis_lib = MagicMock()
        fake_redis_lib.Redis.from_url.return_value = fake_client
        with patch.object(event_bus, "_REDIS_AVAILABLE", True), \
             patch.object(event_bus, "_redis_lib", fake_redis_lib):
            backend = RedisStreamsBackend()
            self.assertFalse(backend.publish("s", {"a": 1}))

    def test_reset_connection_forces_new_ping(self):
        fake_client = MagicMock()
        fake_redis_lib = MagicMock()
        fake_redis_lib.Redis.from_url.return_value = fake_client
        with patch.object(event_bus, "_REDIS_AVAILABLE", True), \
             patch.object(event_bus, "_redis_lib", fake_redis_lib):
            backend = RedisStreamsBackend()
            backend.available()
            backend.reset_connection()
            backend.available()
        self.assertEqual(2, fake_client.ping.call_count)


class TestEventBusFacade(unittest.TestCase):
    def test_publish_cycle_adds_timestamp_and_delegates_to_backend(self):
        backend = MagicMock()
        backend.publish.return_value = True
        bus = EventBus(backend)
        ok = bus.publish_cycle(cycle=10, universe_size=136)
        self.assertTrue(ok)
        stream_arg, payload_arg = backend.publish.call_args[0]
        self.assertEqual(event_bus.CYCLE_STREAM, stream_arg)
        self.assertEqual(10, payload_arg["cycle"])
        self.assertIn("ts", payload_arg)

    def test_publish_reject_delegates_to_backend_with_reject_stream(self):
        backend = MagicMock()
        bus = EventBus(backend)
        bus.publish_reject(symbol="BTC", reason="DAY_QUALITY_LOW")
        stream_arg, payload_arg = backend.publish.call_args[0]
        self.assertEqual(event_bus.REJECT_STREAM, stream_arg)
        self.assertEqual("BTC", payload_arg["symbol"])

    def test_default_backend_is_null_when_none_given(self):
        bus = EventBus()
        self.assertIsInstance(bus.backend, NullBackend)
        self.assertFalse(bus.publish_cycle(cycle=1))


class TestBuildEventBus(unittest.TestCase):
    def test_disabled_by_default_gives_null_backend(self):
        fake_config = type("_c", (), {"EVENT_BUS_ENABLED": False})
        bus = build_event_bus(fake_config)
        self.assertIsInstance(bus.backend, NullBackend)

    def test_enabled_gives_redis_backend_with_configured_url(self):
        fake_config = type("_c", (), {"EVENT_BUS_ENABLED": True, "EVENT_BUS_REDIS_URL": "redis://custom:1234/2"})
        bus = build_event_bus(fake_config)
        self.assertIsInstance(bus.backend, RedisStreamsBackend)
        self.assertEqual("redis://custom:1234/2", bus.backend._url)

    def test_missing_config_attrs_default_to_disabled(self):
        fake_config = type("_c", (), {})
        bus = build_event_bus(fake_config)
        self.assertIsInstance(bus.backend, NullBackend)


if __name__ == "__main__":
    unittest.main()
