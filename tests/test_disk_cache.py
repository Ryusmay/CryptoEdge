import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import disk_cache


class TestDiskCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = patch.object(disk_cache, "CACHE_DIR", Path(self._tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_load_missing_key_returns_none(self):
        self.assertIsNone(disk_cache.load("nie_istnieje"))

    def test_save_then_load_roundtrip(self):
        disk_cache.save("instrumenty", {"a": 1, "b": [1, 2, 3]})
        result = disk_cache.load("instrumenty")
        self.assertIsNotNone(result)
        self.assertEqual({"a": 1, "b": [1, 2, 3]}, result["data"])
        self.assertGreater(result["ts"], 0)

    def test_save_creates_cache_dir_if_missing(self):
        self.assertFalse(Path(self._tmpdir.name).exists() and any(Path(self._tmpdir.name).iterdir()))
        disk_cache.save("x", [1])
        self.assertTrue((Path(self._tmpdir.name) / "x.json").exists())

    def test_write_is_atomic_no_tmp_file_left_behind(self):
        disk_cache.save("y", {"ok": True})
        files = list(Path(self._tmpdir.name).glob("*"))
        self.assertEqual(1, len(files))
        self.assertEqual("y.json", files[0].name)

    def test_load_corrupted_file_returns_none_instead_of_raising(self):
        Path(self._tmpdir.name).mkdir(parents=True, exist_ok=True)
        (Path(self._tmpdir.name) / "zly.json").write_text("{niepoprawny json", encoding="utf-8")
        self.assertIsNone(disk_cache.load("zly"))

    def test_load_file_missing_expected_shape_returns_none(self):
        Path(self._tmpdir.name).mkdir(parents=True, exist_ok=True)
        (Path(self._tmpdir.name) / "zle_pole.json").write_text('{"foo": "bar"}', encoding="utf-8")
        self.assertIsNone(disk_cache.load("zle_pole"))

    def test_save_overwrites_previous_value_for_same_key(self):
        disk_cache.save("k", [1])
        disk_cache.save("k", [2, 3])
        self.assertEqual([2, 3], disk_cache.load("k")["data"])


if __name__ == "__main__":
    unittest.main()
