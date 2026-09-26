"""Passwörter und Tokens: nur Standardbibliothek, keine zusätzliche Abhängigkeit."""

from __future__ import annotations

import hashlib
import hmac
import secrets

ITERATIONS = 600_000  # PBKDF2-HMAC-SHA256, Empfehlung der OWASP (Stand 2023)
SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"{ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        iterations, salt_hex, digest_hex = stored.split("$")
        expected = bytes.fromhex(digest_hex)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # Tokens sind zufällig und lang; ein schneller Hash genügt (anders als bei Passwörtern).
    return hashlib.sha256(token.encode()).hexdigest()
