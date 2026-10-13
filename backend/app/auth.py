"""Authentication: password hashing and bearer-token guard.

Uses only the standard library for hashing (PBKDF2-HMAC-SHA256) so no new
dependency is required. Tokens are opaque random strings held in the
in-memory store; a real deployment would swap this for signed JWTs without
changing the HTTP contract.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_PBKDF2_ITERATIONS = 120_000
_bearer_scheme = HTTPBearer(auto_error=False)

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


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller attached to protected requests."""

    username: str
    organization: str
    actor: str


def issue_token(store, username: str) -> str:
    """Create a bearer token for a user and record it in the store."""
    token = secrets.token_urlsafe(32)
    store.tokens[token] = username
    return token


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """FastAPI dependency: resolve the bearer token to a user or raise 401."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    store = request.app.state.store
    username = store.tokens.get(credentials.credentials)
    user = store.users.get(username) if username else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(
        username=user.username,
        organization=user.organization,
        actor=user.actor,
    )
