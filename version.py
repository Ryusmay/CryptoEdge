# ============================================================
# CryptoEdge SemVer — jedno zrodlo prawdy
# PATCH = naprawa, MINOR = nowa zasada, MAJOR = inny kontrakt
# ============================================================
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

MAJOR = 20
MINOR = 19
PATCH = 0
BUILT = "2026-08-26T13:57:48Z"

ROOT = Path(__file__).resolve().parent


def tag() -> str:
    return f"v{MAJOR}.{MINOR}.{PATCH}"


def display() -> str:
    return f"{MAJOR}.{MINOR}.{PATCH}"


def zip_stem() -> str:
    return f"cryptoedge_bot_{tag()}"


def as_dict() -> dict:
    return {
        "major": MAJOR,
        "minor": MINOR,
        "patch": PATCH,
        "version": display(),
        "tag": tag(),
        "built": BUILT,
    }


def write_version_txt() -> Path:
    path = ROOT / "VERSION.txt"
    path.write_text(
        f"{display()}\n"
        f"tag={tag()}\n"
        f"built={BUILT}\n"
        f"zip={zip_stem()}.zip\n",
        encoding="utf-8",
    )
    return path


def bump(kind: str = "patch") -> tuple[int, int, int]:
    """Podnosi numer w tym pliku. kind: patch | minor | major."""
    kind = (kind or "patch").lower()
    major, minor, patch = MAJOR, MINOR, PATCH
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = Path(__file__).read_text(encoding="utf-8")
    repl = {
        "MAJOR": major,
        "MINOR": minor,
        "PATCH": patch,
        "BUILT": built,
    }
    out = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        changed = False
        for key, val in repl.items():
            if stripped.startswith(f"{key} ="):
                if key == "BUILT":
                    out.append(f'{key} = "{val}"\n')
                else:
                    out.append(f"{key} = {val}\n")
                changed = True
                break
        if not changed:
            out.append(line)
    Path(__file__).write_text("".join(out), encoding="utf-8")
    return major, minor, patch
