"""Pakowanie zipa z automatycznym SemVer.

  python pack.py              # zip z aktualnym numerem, bez bumpa
  python pack.py --patch      # 17.36.0 -> 17.36.1
  python pack.py --minor      # 17.36.0 -> 17.37.0
  python pack.py --major      # 17.36.0 -> 18.0.0
"""
from __future__ import annotations

import argparse
import importlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT.parent
SKIP_PARTS = {"__pycache__", "logs", ".git"}


def _load_version():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import version
    importlib.reload(version)
    return version


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def pack(version_mod) -> Path:
    version_mod.write_version_txt()
    dest = OUT_DIR / f"{version_mod.zip_stem()}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            if not path.is_file() or _should_skip(path.relative_to(ROOT)):
                continue
            zf.write(path, Path("cryptoedge_bot") / path.relative_to(ROOT))
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="CryptoEdge pack + SemVer")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--patch", action="store_true")
    g.add_argument("--minor", action="store_true")
    g.add_argument("--major", action="store_true")
    g.add_argument("--from-commit", action="store_true",
                   help="bump z ostatniego commita: feat/fix/feat!")
    args = parser.parse_args()
    version = _load_version()
    kind = None
    if args.from_commit:
        from bump import classify, last_commit_body, last_commit_subject
        kind = classify(last_commit_subject(), last_commit_body())
        print(f"[pack] commit bump={kind}")
    elif args.major:
        kind = "major"
    elif args.minor:
        kind = "minor"
    elif args.patch:
        kind = "patch"
    if kind in ("major", "minor", "patch"):
        version.bump(kind)
        version = _load_version()
    dest = pack(version)
    print(f"CryptoEdge {version.display()} -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
