"""Auth router: login, session, authorize.

``POST /auth/login`` exchanges a username/password for a bearer token.
``GET /auth/session`` reports the caller for the current token (401 when
unauthenticated). ``GET /auth/authorize`` returns a stub IdP URL for the
frontend's SSO entry point.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import CurrentUser, get_current_user, issue_token, verify_password
from app.models import AuthorizationStart, LoginRequest, Session, TokenResponse
from app.store import InMemoryStore, get_store

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    store: InMemoryStore = Depends(get_store),
) -> TokenResponse:
    user = store.users.get(payload.username)
    if user is None or not verify_password(
        payload.password, user.password_salt, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = issue_token(store, user.username)
    return TokenResponse(access_token=token)


@router.get("/session", response_model=Session)
def session(user: CurrentUser = Depends(get_current_user)) -> Session:
    return Session(organization=user.organization, actor=user.actor)


@router.get("/authorize", response_model=AuthorizationStart)
def authorize() -> AuthorizationStart:
    # Stub for the OIDC entry point; the frontend redirects here.
    return AuthorizationStart(authorize_url="https://idp.acme.example/authorize")
