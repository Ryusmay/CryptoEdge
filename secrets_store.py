# ============================================================
# Bezpieczne przechowywanie kluczy API
# ============================================================
# Plik: logs/secrets.bin – szyfrowany AES-GCM (Fernet) kluczem
# wyprowadzonym z lokalnej, losowej soli (logs/.secrets_salt,
# uprawnienia 0600), NIE z przewidywalnych home/username.
# Wstecznie kompatybilne: stare pliki zapisane starą (słabą)
# obfuskacją XOR są przy pierwszym odczycie odszyfrowywane i od
# razu zapisywane ponownie w nowym, bezpieczniejszym formacie.
# Na starcie aplikowane do config + .env (dla live executora).
# ============================================================

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import config

BASE_DIR = Path(__file__).resolve().parent
SECRETS_FILE = BASE_DIR / "logs" / "secrets.bin"
SALT_FILE = BASE_DIR / "logs" / ".secrets_salt"
ENV_FILE = BASE_DIR / ".env"

# Klucze Blofin (i miejsce na przyszłe giełdy)
SECRET_KEYS = (
    "BLOFIN_API_KEY",
    "BLOFIN_API_SECRET",
    "BLOFIN_API_PASSPHRASE",
)

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _HAS_CRYPTO = True
except Exception:
    _HAS_CRYPTO = False


def _local_salt() -> bytes:
    """
    Losowa sól generowana raz lokalnie (nie z home/username - te są
    trywialnie odczytywalne przez kogokolwiek z dostępem do maszyny).
    Plik z uprawnieniami 0600; bez niego nie da się odtworzyć klucza
    nawet mając kod źródłowy.
    """
    SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SALT_FILE.exists():
        try:
            raw = SALT_FILE.read_bytes()
            if len(raw) >= 16:
                return raw
        except Exception:
            pass
    salt = os.urandom(32)
    try:
        SALT_FILE.write_bytes(salt)
        os.chmod(SALT_FILE, 0o600)
    except Exception as e:
        print(f"[Secrets] salt write: {e}")
    return salt


def _fernet() -> Optional["Fernet"]:
    if not _HAS_CRYPTO:
        return None
    salt = _local_salt()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
    key = base64.urlsafe_b64encode(kdf.derive(b"cryptoedge-secrets"))
    return Fernet(key)


def _encrypt(plain: str) -> str:
    plain = plain or ""
    f = _fernet()
    if f is None:
        # brak biblioteki cryptography – nie powinno się zdarzyć (jest w requirements),
        # ale nie chcemy zostawić kluczy plaintext: sygnalizuj błąd zamiast cichego zapisu.
        raise RuntimeError("cryptography package unavailable – cannot store secrets safely")
    return f.encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt(token: str) -> str:
    if not token:
        return ""
    f = _fernet()
    if f is None:
        return ""
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return ""
    except Exception:
        return ""


# --- Legacy (v1) XOR format: tylko do jednorazowej migracji ---
def _legacy_machine_key() -> bytes:
    seed = f"cryptoedge|{Path.home()}|{os.environ.get('USERNAME') or os.environ.get('USER') or 'u'}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _legacy_deobfuscate(token: str) -> str:
    if not token:
        return ""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        k = _legacy_machine_key()
        plain = bytes(b ^ k[i % len(k)] for i, b in enumerate(raw))
        return plain.decode("utf-8")
    except Exception:
        return ""


def load_secrets() -> Dict[str, str]:
    """Odczyt z secrets.bin (AES-GCM/Fernet); fallback: legacy XOR → migracja; potem config/env/.env."""
    out = {k: "" for k in SECRET_KEYS}
    migrated = False
    try:
        if SECRETS_FILE.exists():
            with open(SECRETS_FILE, "r", encoding="utf-8") as f:
                blob = json.load(f)
            if isinstance(blob, dict):
                version = blob.get("_version")
                vals = blob.get("data", blob)  # legacy pliki nie mają "data"/"_version"
                for k in SECRET_KEYS:
                    raw = vals.get(k)
                    if not raw:
                        continue
                    if version == 2:
                        out[k] = _decrypt(str(raw))
                    else:
                        # legacy v1 XOR – odszyfruj starym kluczem, oznacz do migracji
                        val = _legacy_deobfuscate(str(raw))
                        if val:
                            out[k] = val
                            migrated = True
    except Exception as e:
        print(f"[Secrets] load: {e}")

    # Uzupełnij z env / config jeśli plik pusty
    for k in SECRET_KEYS:
        if out[k]:
            continue
        v = os.environ.get(k) or getattr(config, k, "") or ""
        if v:
            out[k] = str(v)

    if migrated and any(out.values()):
        try:
            print("[Secrets] Migracja starego formatu (XOR) → AES-GCM (Fernet)")
            save_secrets(out)
        except Exception as e:
            print(f"[Secrets] migration save failed: {e}")
    return out


def save_secrets(data: Dict[str, str]) -> None:
    """Zapisuje zaszyfrowane wartości (AES-GCM/Fernet) + synchronizuje .env i config."""
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    vals = {}
    clean = {}
    for k in SECRET_KEYS:
        val = (data.get(k) or "").strip()
        clean[k] = val
        vals[k] = _encrypt(val) if val else ""
    blob = {"_version": 2, "data": vals}
    try:
        with open(SECRETS_FILE, "w", encoding="utf-8") as f:
            json.dump(blob, f, indent=2)
        try:
            os.chmod(SECRETS_FILE, 0o600)
        except Exception:
            pass
    except Exception as e:
        print(f"[Secrets] save file: {e}")
        raise

    apply_secrets(clean)
    _sync_env_file(clean)
    print("[Secrets] Blofin keys zapisane (AES-GCM)")


def apply_secrets(data: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Wpisuje do config + os.environ."""
    data = data if data is not None else load_secrets()
    for k in SECRET_KEYS:
        v = (data.get(k) or "").strip()
        try:
            setattr(config, k, v)
        except Exception:
            pass
        if v:
            os.environ[k] = v
        elif k in os.environ and not v:
            # nie kasuj env jeśli user tylko nie podał w UI pustego – tylko gdy jawny clear
            pass
    return data


def _sync_env_file(clean: Dict[str, str]) -> None:
    """Aktualizuje linie BLOFIN_* w .env (tworzy plik jeśli brak)."""
    lines = []
    if ENV_FILE.exists():
        try:
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []
    keys_done = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in SECRET_KEYS:
            new_lines.append(f"{key}={clean.get(key, '')}")
            keys_done.add(key)
        else:
            new_lines.append(line)
    for k in SECRET_KEYS:
        if k not in keys_done:
            new_lines.append(f"{k}={clean.get(k, '')}")
    try:
        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        try:
            os.chmod(ENV_FILE, 0o600)
        except Exception:
            pass
    except Exception as e:
        print(f"[Secrets] .env sync: {e}")


def mask(value: str, show_last: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= show_last:
        return "*" * len(value)
    return "*" * (len(value) - show_last) + value[-show_last:]


def has_blofin_keys() -> bool:
    s = load_secrets()
    return bool(s.get("BLOFIN_API_KEY") and s.get("BLOFIN_API_SECRET"))


def status_label() -> str:
    s = load_secrets()
    if s.get("BLOFIN_API_KEY") and s.get("BLOFIN_API_SECRET"):
        return f"Blofin: skonfigurowany ({mask(s['BLOFIN_API_KEY'])})"
    if s.get("BLOFIN_API_KEY") or s.get("BLOFIN_API_SECRET"):
        return "Blofin: niekompletny (brak key lub secret)"
    return "Blofin: brak kluczy"
