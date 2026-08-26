import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import disk_cache
from data_feeder import DataFeeder


class TestWsStatusVisibleInSourcesStatus(unittest.TestCase):
    """Regresja na realny problem z uploadu logow: przy braku pakietu
    websocket-client (lub przy nieudanym polaczeniu) DataFeeder.sources_status()
    milczal na temat Public WS - w calej ~13-minutowej sesji nie bylo ani
    jednej linii "[BlofinWS]" w konsoli, mimo ze DayTradingEngineV2.generate()
    cicho spadal z pelnego uniwersum do limitu top-N (DAYTRADING_V2_MAX_CANDIDATES)
    wlasnie z powodu braku polaczenia WS. sources_status() jest drukowane co
    cykl ("Zrodla: ...") i wystawiane do UI (DataAdapter.feed_status()), wiec
    to jest jedyne miejsce, ktore realnie mogloby to pokazac na biezaco."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = patch.object(disk_cache, "CACHE_DIR", Path(self._tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.feeder = DataFeeder()

    def test_shows_missing_package_when_ws_client_not_installed(self):
        # "available" jest properties bez settera (odzwierciedla staly stan
        # instalacji pakietu) - patchujemy zrodlowa flage modulu, ktora ta
        # wlasciwosc zwraca, zamiast probowac podmienic sam property.
        with patch("blofin_ws._WS_AVAILABLE", False):
            status = self.feeder.sources_status()
        self.assertIn("WS:brak pakietu websocket-client", status)

    def test_shows_connected_when_ws_is_up(self):
        with patch("blofin_ws._WS_AVAILABLE", True), \
                patch("blofin_ws.PUBLIC_WS.is_connected", return_value=True):
            status = self.feeder.sources_status()
        self.assertIn("WS:OK", status)

    def test_shows_cf_403_when_handshake_rejected(self):
        with patch("blofin_ws._WS_AVAILABLE", True), \
                patch("blofin_ws.PUBLIC_WS.is_connected", return_value=False), \
                patch("blofin_ws.PUBLIC_WS.is_cf_blocked", return_value=True):
            status = self.feeder.sources_status()
        self.assertIn("WS:CF-403", status)
        self.assertNotIn("WS:rozlaczony", status)

    def test_never_raises_even_if_ws_module_state_is_unavailable(self):
        with patch("blofin_ws.PUBLIC_WS.is_connected", side_effect=RuntimeError("boom")), \
                patch("blofin_ws._WS_AVAILABLE", True):
            status = self.feeder.sources_status()
        self.assertIn("WS:?", status)


if __name__ == "__main__":
    unittest.main()
