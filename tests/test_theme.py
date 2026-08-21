import unittest

import theme


class TestGateTone(unittest.TestCase):
    def test_open_maps_to_long_color(self):
        text, bg, border = theme.gate_tone("OPEN")
        self.assertEqual(theme.LONG, text)

    def test_wait_maps_to_wait_color(self):
        text, bg, border = theme.gate_tone("WAIT")
        self.assertEqual(theme.WAIT, text)

    def test_block_maps_to_short_color(self):
        text, bg, border = theme.gate_tone("BLOCK")
        self.assertEqual(theme.SHORT, text)

    def test_lowercase_input_still_matches(self):
        text, _bg, _border = theme.gate_tone("open")
        self.assertEqual(theme.LONG, text)

    def test_unknown_gate_falls_back_to_wait_not_crash(self):
        text, _bg, _border = theme.gate_tone("SOMETHING_UNKNOWN")
        self.assertEqual(theme.WAIT, text)

    def test_none_gate_falls_back_to_wait_not_crash(self):
        text, _bg, _border = theme.gate_tone(None)
        self.assertEqual(theme.WAIT, text)

    def test_returns_three_distinct_values(self):
        text, bg, border = theme.gate_tone("OPEN")
        self.assertNotEqual(text, bg)
        self.assertNotEqual(bg, border)


class TestSideColor(unittest.TestCase):
    def test_long_short_are_distinct(self):
        self.assertNotEqual(theme.side_color("LONG"), theme.side_color("SHORT"))

    def test_unknown_side_falls_back_to_muted(self):
        self.assertEqual(theme.MUTED, theme.side_color("SIDEWAYS"))

    def test_lowercase_input_still_matches(self):
        self.assertEqual(theme.side_color("LONG"), theme.side_color("long"))


class TestRegimeColor(unittest.TestCase):
    def test_panic_and_trend_up_are_distinct(self):
        self.assertNotEqual(theme.regime_color("PANIC"), theme.regime_color("TREND_UP"))

    def test_unknown_regime_falls_back_to_muted(self):
        self.assertEqual(theme.MUTED, theme.regime_color("SOMETHING_NEW"))


class TestRegimeLabel(unittest.TestCase):
    def test_panic_does_not_say_panic_to_the_user(self):
        # PANIC to tylko wewnetrzny klucz progu ATR/RVOL (regime_model.py),
        # nie faktyczna panika rynku - user nie powinien widziec tego slowa.
        label = theme.regime_label("PANIC")
        self.assertNotIn("PANIC", label.upper())

    def test_panic_reads_as_strong_move(self):
        self.assertEqual("STRONG MOVE", theme.regime_label("PANIC"))

    def test_lowercase_input_still_matches(self):
        self.assertEqual(theme.regime_label("PANIC"), theme.regime_label("panic"))

    def test_unlabeled_regime_falls_back_to_raw_uppercase_key(self):
        self.assertEqual("TREND_UP", theme.regime_label("trend_up"))

    def test_none_or_empty_does_not_crash(self):
        self.assertEqual("", theme.regime_label(None))
        self.assertEqual("", theme.regime_label(""))


class TestQss(unittest.TestCase):
    def test_qss_is_non_empty_string(self):
        css = theme.qss()
        self.assertIsInstance(css, str)
        self.assertGreater(len(css), 100)

    def test_qss_references_all_v2_object_names(self):
        css = theme.qss()
        for object_name in ("V2Card", "V2CardTitle", "V2TopBar", "V2Nav", "V2StatePill", "V2RegimePill", "V2CloseAll", "V2Table"):
            self.assertIn(object_name, css, f"brak QSS dla #{object_name}")

    def test_qss_uses_2px_radius_not_rounded_corners(self):
        # Spec: "2px radius, hairline border, zero cieni" - nie stary,
        # bardziej zaokraglony styl (10px) uzywany przez pozostaly UI.
        css = theme.qss()
        self.assertIn("border-radius:2px", css)
        self.assertNotIn("border-radius:10px", css)

    def test_qss_has_no_box_shadow(self):
        self.assertNotIn("shadow", theme.qss().lower())


class TestColorConstants(unittest.TestCase):
    def test_all_top_level_colors_are_valid_hex(self):
        for name in ("BG", "SIDE", "PANEL", "PANEL2", "LINE", "LINE2", "TEXT", "MUTED",
                     "LONG", "SHORT", "WAIT", "CYAN", "PURPLE"):
            value = getattr(theme, name)
            self.assertTrue(value.startswith("#"), f"{name}={value} nie zaczyna sie od #")
            self.assertIn(len(value), (4, 7), f"{name}={value} ma nietypowa dlugosc")

    def test_mono_font_is_jetbrains_mono(self):
        self.assertEqual("JetBrains Mono", theme.MONO)


if __name__ == "__main__":
    unittest.main()
