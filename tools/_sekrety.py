"""Skan sledzonych plikow pod katem sekretow. Odpalany recznie przed publikacja.

Nie udaje, ze jest kompletny - zaden skan regexowy nie jest. Sprawdza to, co
faktycznie moglo tu wyciec: klucze BloFin, sekrety w .env, klucze prywatne
i dlugie ciagi wygladajace na token. Brak trafien NIE znaczy "czysto",
znaczy "te wzorce nie wystapily".
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = r"C:\Program Files\Git\bin\git.exe"

WZORCE = [
    ("klucz prywatny", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("BLOFIN_ z wartoscia", re.compile(r"BLOFIN_[A-Z_]*\s*=\s*['\"]?[A-Za-z0-9]{12,}")),
    ("api secret/passphrase", re.compile(
        r"(api[_-]?secret|passphrase|private[_-]?key)\s*[:=]\s*['\"][^'\"]{12,}['\"]",
        re.IGNORECASE)),
    # Prefiksy MUSZA miec podkreslnik/myslnik i dlugi ogon. Pierwsza wersja
    # tego wzorca ("ghp|gho|...") trafiala w slowo "ghost" i w "skip" - a skaner,
    # ktory krzyczy na proze, przestaje byc czytany po trzecim razie.
    ("token GitHub/OpenAI", re.compile(
        r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|sk-[A-Za-z0-9]{20,})")),
]

# Pliki, ktore Z DEFINICJI zawieraja przykladowe wartosci albo dane rynkowe.
POMIJANE_SUFIKSY = (".json", ".jsonl", ".png", ".ico", ".svg", ".zip")


def sledzone() -> list[str]:
    out = subprocess.run([GIT, "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    return [line for line in out.stdout.splitlines() if line.strip()]


def main() -> int:
    trafienia = []
    zbadane = 0
    for wzgledna in sledzone():
        if wzgledna.lower().endswith(POMIJANE_SUFIKSY):
            continue
        sciezka = ROOT / wzgledna
        if not sciezka.is_file():
            continue
        try:
            tekst = sciezka.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        zbadane += 1
        for numer, linia in enumerate(tekst.splitlines(), 1):
            for etykieta, wzorzec in WZORCE:
                if wzorzec.search(linia):
                    trafienia.append((wzgledna, numer, etykieta, linia.strip()[:120]))

    print(f"[sekrety] przeskanowano {zbadane} sledzonych plikow tekstowych")
    if not trafienia:
        print("[sekrety] BRAK TRAFIEN dla sprawdzanych wzorcow.")
        print("[sekrety] To nie jest dowod czystosci - tylko brak tych wzorcow.")
        return 0
    for plik, numer, etykieta, fragment in trafienia:
        print(f"[sekrety] {plik}:{numer}  {etykieta}  {fragment}")
    print(f"[sekrety] TRAFIEN: {len(trafienia)} - przejrzyj je zanim cokolwiek opublikujesz")
    return 1


if __name__ == "__main__":
    sys.exit(main())
