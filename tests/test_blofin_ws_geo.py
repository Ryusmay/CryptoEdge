import unittest

from blofin_ws import (
    BlofinPublicWebSocket,
    looks_like_geo_block,
    summarize_handshake_error,
    ws_handshake_header_sets,
    _headers_are_ws_safe,
)


class TestBlofinWsHandshake(unittest.TestCase):
    def test_detects_restricted_countries_html(self):
        html = "Handshake status 403 Forbidden We noticed that your IP address is from one of BloFin's restricted countries"
        self.assertTrue(looks_like_geo_block(html))

    def test_detects_bare_handshake_403(self):
        self.assertTrue(looks_like_geo_block("Handshake status 403 Forbidden"))

    def test_ignores_timeout(self):
        self.assertFalse(looks_like_geo_block("timed out"))

    def test_mark_sets_cf_flag(self):
        ws = BlofinPublicWebSocket()
        ws._mark_geo_blocked("Handshake status 403 Forbidden")
        self.assertTrue(ws.is_cf_blocked())
        self.assertTrue(ws.is_geo_blocked())
        self.assertFalse(ws.is_connected())

    def test_handshake_variants_never_send_rest_connection_header(self):
        for headers in ws_handshake_header_sets():
            self.assertTrue(_headers_are_ws_safe(headers), headers)
            blob = "\n".join(headers).lower()
            self.assertNotIn("connection:", blob)
            self.assertNotIn("application/json", blob)
            self.assertTrue(any(h.lower().startswith("user-agent:") for h in headers))

    def test_rest_waf_headers_are_not_ws_safe(self):
        rest = [
            "User-Agent: x",
            "Accept: application/json, text/plain, */*",
            "Connection: keep-alive",
            "Origin: https://blofin.com",
        ]
        self.assertFalse(_headers_are_ws_safe(rest))

    def test_summarize_extracts_cf_ray_not_html(self):
        raw = (
            "Handshake status 403 Forbidden -+-+- "
            "{'cf-ray': 'a2f565d00ea9eeaf-WAW', 'server': 'cloudflare'} "
            "-+-+- b'<!DOCTYPE html> ... 5000 bytes ...'"
        )
        out = summarize_handshake_error(raw)
        self.assertIn("HTTP 403", out)
        self.assertIn("a2f565d00ea9eeaf-WAW", out)
        self.assertNotIn("DOCTYPE", out)


class TestBlofinWsErrorReporting(unittest.TestCase):
    """25.08.2026: log pokazywal 'HTTP ? cf-ray=?' dla bledow, ktore nie mialy
    nic wspolnego z handshake'iem - prawdziwa tresc byla wyrzucana i diagnoza
    szla w strone Cloudflare zamiast w strone realnej przyczyny."""

    def test_close_frame_error_keeps_real_text(self):
        from blofin_ws import summarize_ws_error

        out = summarize_ws_error("fin=1 opcode=8 data=b'\\x03\\xe8'")
        self.assertIn("opcode=8", out)
        self.assertNotIn("cf-ray=?", out)

    def test_exception_error_keeps_type_and_text(self):
        from blofin_ws import summarize_ws_error

        out = summarize_ws_error(ConnectionResetError("connection reset by peer"))
        self.assertIn("ConnectionResetError", out)
        self.assertIn("connection reset by peer", out)

    def test_handshake_html_is_still_shortened(self):
        from blofin_ws import summarize_ws_error

        raw = (
            "Handshake status 403 Forbidden -+-+- "
            "{'cf-ray': 'a2f565d00ea9eeaf-WAW', 'server': 'cloudflare'} "
            "-+-+- b'<!DOCTYPE html> ... 5000 bytes ...'"
        )
        out = summarize_ws_error(raw)
        self.assertIn("HTTP 403", out)
        self.assertIn("a2f565d00ea9eeaf-WAW", out)
        self.assertNotIn("DOCTYPE", out)

    def test_empty_error_does_not_crash(self):
        from blofin_ws import summarize_ws_error

        self.assertTrue(summarize_ws_error(None))

    def test_backoff_resets_after_long_lived_connection(self):
        from pathlib import Path

        source = Path(__file__).resolve().parents[1].joinpath("blofin_ws.py").read_text(encoding="utf-8")
        self.assertIn("_BACKOFF_RESET_AFTER_S", source)
        self.assertIn("attempt_started", source)
        run_forever = source[source.index("def _run_forever"):source.index("def _connect_once")]
        self.assertIn("backoff = 1.0", run_forever.split("while self._running:", 1)[1])
