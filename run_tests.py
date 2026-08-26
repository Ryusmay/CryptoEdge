#!/usr/bin/env python3
# Uruchom: python run_tests.py
import sys
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def _run_shared() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def _run_isolated() -> int:
    """Run each test module in a clean interpreter.

    CryptoEdge intentionally has process-wide runtime singletons (market store,
    rate-limit buckets and mutable exchange caches). Running all modules inside
    one interpreter lets one module's synthetic market state contaminate later
    strategy tests. Module isolation matches a fresh bot process and makes the
    regression result deterministic.
    """
    total = failures = errors = failed_modules = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        module = f"tests.{path.stem}"
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", module, "-q"],
            cwd=str(ROOT), text=True, capture_output=True,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        ran = re.findall(r"Ran (\d+) tests?", output)
        total += int(ran[-1]) if ran else 0
        summary = re.findall(r"FAILED \(([^)]*)\)", output)
        if summary:
            failures += sum(int(x) for x in re.findall(r"failures=(\d+)", summary[-1]))
            errors += sum(int(x) for x in re.findall(r"errors=(\d+)", summary[-1]))
        if proc.returncode:
            failed_modules += 1
            print(f"\n--- {module} FAILED ---")
            print(output.rstrip())
    print(f"\nRan {total} tests in isolated modules")
    if failed_modules:
        print(f"FAILED (failures={failures}, errors={errors}, modules={failed_modules})")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    # --shared pozostaje do diagnozowania wycieków globalnego stanu.
    sys.exit(_run_shared() if "--shared" in sys.argv else _run_isolated())
