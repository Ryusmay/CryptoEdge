"""Wybierz poziom SemVer z ostatniego commita (Conventional Commits).

  python bump.py              # wypisz: patch | minor | major | none
  python pack.py --from-commit
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def last_commit_subject() -> str:
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return (out or "").strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def last_commit_body() -> str:
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%b"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out or ""
    except (OSError, subprocess.CalledProcessError):
        return ""


def classify(subject: str, body: str = "") -> str:
    text = f"{subject}\n{body}"
    head = subject.strip()
    if re.search(r"BREAKING CHANGE:", text, re.I):
        return "major"
    if re.match(r"^(feat|fix|perf|refactor)!:", head, re.I):
        return "major"
    if re.match(r"^feat(\(.+\))?:", head, re.I):
        return "minor"
    if re.match(r"^(fix|perf)(\(.+\))?:", head, re.I):
        return "patch"
    return "none"


def main() -> int:
    parser = argparse.ArgumentParser(description="SemVer from last git commit")
    parser.add_argument("--message", default="", help="override commit subject")
    args = parser.parse_args()
    subject = args.message or last_commit_subject()
    body = "" if args.message else last_commit_body()
    kind = classify(subject, body)
    print(kind)
    if not subject:
        print("# brak git / pusty commit — none", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
