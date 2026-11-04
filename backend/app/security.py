"""Passwords, session tokens, virtual keys and provider-key encryption."""
import hashlib
import hmac
import os
import secrets

from cryptography.fernet import Fernet

from .config import DATA_DIR

_PBKDF2_ROUNDS = 240_000


# ---------- passwords ----------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------- opaque tokens (sessions + virtual keys) ----------

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def new_session_token() -> str:
    return "kls_" + secrets.token_urlsafe(32)


def new_virtual_key() -> str:
    return "kl_live_" + secrets.token_urlsafe(24)


def hint(token: str) -> str:
    return token[:12] + "…" + token[-4:]


# ---------- provider key encryption ----------

def _fernet() -> Fernet:
    key = os.environ.get("KEYLENS_FERNET_KEY", "").strip()
    if not key:
        path = DATA_DIR / "secret.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(Fernet.generate_key())
        key = path.read_text().strip()
    return Fernet(key.encode())


def encrypt_key(raw: str) -> bytes:
    return _fernet().encrypt(raw.encode())


def decrypt_key(blob: bytes) -> str:
    return _fernet().decrypt(blob).decode()


def fingerprint(raw: str) -> str:
    """Stable id for a key that reveals nothing about it."""
    return sha256(raw)[:16]


def mask(raw: str) -> str:
    head = raw[:7] if len(raw) > 14 else raw[:3]
    return f"{head}…{raw[-4:]}"
