"""Password hashing primitives (PBKDF2-HMAC-SHA256).

Standard library only — no new dependency. This module is a dependency-free
leaf so that ``app.store`` can hash seeded passwords without importing
``app.auth`` (which in turn depends on ``app.store``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 120_000


def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    """Return ``(salt_hex, hash_hex)`` for a password."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return salt, digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    _, candidate = hash_password(password, salt=salt_hex)
    return hmac.compare_digest(candidate, hash_hex)
