from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "cryptoedge"

EXPECTED_PACKAGES = {
    "apps",
    "domain",
    "market_data",
    "strategy",
    "risk",
    "execution",
    "portfolio",
    "replay",
    "telemetry",
    "services",
    "infrastructure",
}

DOMAIN_FORBIDDEN_ROOTS = {
    "config",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "socket",
    "websockets",
    "PySide6",
    "PyQt6",
    "tkinter",
    "frontend",
    "blofin_feed",
    "blofin_ws",
    "binance_feed",
    "binance_ws",
    "engine_api",
}


def python_files(path: Path):
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class ArchitectureBoundariesTests(unittest.TestCase):
    """Guards only the new package; legacy root modules remain migration inputs."""

    def test_modular_package_skeleton_exists(self):
        self.assertTrue(PACKAGE.is_dir(), "Brak nowego pakietu cryptoedge/")
        missing = sorted(name for name in EXPECTED_PACKAGES if not (PACKAGE / name).is_dir())
        self.assertEqual([], missing, f"Brak modułów architektury: {missing}")

    def test_domain_is_pure_and_has_no_outward_dependencies(self):
        domain = PACKAGE / "domain"
        self.assertTrue(domain.is_dir(), "Brak cryptoedge/domain")
        violations: list[str] = []
        for path in python_files(domain):
            for imported in imports(path):
                root = imported.split(".", 1)[0]
                if root in DOMAIN_FORBIDDEN_ROOTS:
                    violations.append(f"{path.relative_to(ROOT)} -> {imported}")
                if imported.startswith("cryptoedge.") and not imported.startswith("cryptoedge.domain"):
                    violations.append(f"{path.relative_to(ROOT)} -> {imported}")
        self.assertEqual([], violations, "Domena zależy od warstwy zewnętrznej:\n" + "\n".join(violations))

    def test_python_backend_does_not_import_frontend(self):
        violations: list[str] = []
        for path in python_files(PACKAGE):
            for imported in imports(path):
                if imported == "frontend" or imported.startswith("frontend."):
                    violations.append(f"{path.relative_to(ROOT)} -> {imported}")
        self.assertEqual([], violations, "Backend importuje frontend:\n" + "\n".join(violations))

    def test_frontend_does_not_reach_into_python_sources(self):
        frontend = ROOT / "frontend" / "src"
        if not frontend.exists():
            self.skipTest("Frontend React nie istnieje w tym wariancie projektu")
        violations: list[str] = []
        for path in frontend.rglob("*"):
            if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if ".py'" in text or '.py"' in text or "../cryptoedge/" in text or "..\\cryptoedge\\" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual([], violations, "Frontend sięga do źródeł Python:\n" + "\n".join(violations))

    def test_runtime_and_replay_share_decision_pipeline(self):
        runtime = PACKAGE / "apps" / "runtime.py"
        replay = PACKAGE / "apps" / "replay.py"
        pipeline = PACKAGE / "services" / "decision_pipeline.py"
        for path in (runtime, replay, pipeline):
            self.assertTrue(path.is_file(), f"Brak kontraktu architektury: {path.relative_to(ROOT)}")

        expected = "cryptoedge.services.decision_pipeline"
        runtime_imports = imports(runtime)
        replay_imports = imports(replay)
        self.assertIn(expected, runtime_imports, "Runtime nie używa wspólnego DecisionPipeline")
        self.assertIn(expected, replay_imports, "Replay nie używa wspólnego DecisionPipeline")


if __name__ == "__main__":
    unittest.main()
