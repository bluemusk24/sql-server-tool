"""Authentication: password hashing and bearer-token guard.

Hashing lives in :mod:`app.security` (a dependency-free leaf) so that
:mod:`app.store` can hash seeded passwords without a circular import; the
functions are re-exported here for callers that import them from
``app.auth``. Bearer tokens are opaque random strings persisted in the
database, so sessions survive a backend restart.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security import hash_password, verify_password
from app.store import DatabaseStore, get_store

__all__ = [
    "CurrentUser",
    "get_current_user",
    "hash_password",
    "verify_password",
]

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller attached to protected requests."""

    username: str
    organization: str
    actor: str


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    store: DatabaseStore = Depends(get_store),
) -> CurrentUser:
    """FastAPI dependency: resolve the bearer token to a user or raise 401."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = store.resolve_token(credentials.credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Every authenticated request counts the user as active (#46);
    # a no-op when telemetry is dormant.
    request.app.state.metrics.touch_user(user.actor)
    return CurrentUser(
        username=user.username,
        organization=user.organization,
        actor=user.actor,
    )
