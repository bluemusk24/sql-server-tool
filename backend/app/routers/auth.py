"""Auth router: login, session, and the OIDC entry points (#30).

``POST /auth/login`` exchanges a username/password for a bearer token.
``GET /auth/session`` reports the caller for the current token (401 when
unauthenticated). ``GET /auth/authorize`` redirects the browser to the
organization's issuer; ``GET /auth/callback`` completes the
authorization-code flow — it verifies the state cookie and id_token,
resolves the caller's organization by the token issuer (provisioned
up front by an admin, #27), refreshes the local user, and redirects to
the SPA carrying a session token. Every authenticated endpoint then
sees the caller through the ordinary bearer guard.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app import oidc
from app.auth import CurrentUser, get_current_user, verify_password
from app.models import LoginRequest, Session, TokenResponse
from app.store import DatabaseStore, get_store

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_STATE_COOKIE = "sdip_oidc_state"
_STATE_MAX_AGE = 600  # seconds — long enough to finish, short-lived otherwise


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    store: DatabaseStore = Depends(get_store),
) -> TokenResponse:
    user = store.get_user(payload.username)
    if user is None or not verify_password(
        payload.password, user.password_salt, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = store.issue_token(user.username)
    return TokenResponse(access_token=token)


@router.get("/session", response_model=Session)
def session(user: CurrentUser = Depends(get_current_user)) -> Session:
    return Session(organization=user.organization, actor=user.actor)


@router.get("/authorize", status_code=status.HTTP_302_FOUND)
def authorize(request: Request) -> RedirectResponse:
    """Send the browser to the issuer's authorization endpoint (#30)."""
    try:
        issuer, client, _secret = oidc.require_configuration()
    except oidc.OidcNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    try:
        metadata = oidc.discover(issuer)
        state = secrets.token_urlsafe(32)
        url = oidc.authorization_url(
            metadata,
            client_id=client,
            redirect_uri=oidc.redirect_uri(str(request.url_for("oidc_callback"))),
            state=state,
        )
    except oidc.OidcError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=_STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/auth/callback",
    )
    return response


@router.get("/callback", name="oidc_callback", status_code=status.HTTP_302_FOUND)
def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    store: DatabaseStore = Depends(get_store),
) -> RedirectResponse:
    """Finish the authorization-code flow and hand the SPA a session."""
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"the issuer reported: {error}",
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="missing code or state"
        )
    cookie = request.cookies.get(_STATE_COOKIE, "")
    if not cookie or not hmac.compare_digest(cookie, state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="state mismatch; start again from /auth/authorize",
        )

    try:
        issuer, client, secret = oidc.require_configuration()
        metadata = oidc.discover(issuer)
        tokens = oidc.exchange_code(
            metadata,
            code,
            redirect_uri=oidc.redirect_uri(str(request.url_for("oidc_callback"))),
            client_id=client,
            client_secret=secret,
        )
        claims = oidc.verify_id_token(
            tokens["id_token"], metadata, client_id=client, issuer=issuer
        )
    except oidc.OidcNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except oidc.IdTokenRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except oidc.OidcError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # The verified token issuer resolves the organization; unknown
    # issuers are refused, never auto-provisioned (#27, #30).
    org = store.get_organization_by_issuer(str(claims.get("iss", "")))
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your organization is not provisioned; ask an administrator",
        )

    username = str(
        claims.get("preferred_username") or claims.get("email") or claims.get("sub") or ""
    )
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="the identity token carries no usable user name",
        )
    actor = str(claims.get("email") or claims.get("name") or username)
    store.upsert_sso_user(
        username=username, organization=org["name"], actor=actor
    )
    token = store.issue_token(username)
    logger.info("SSO login for %s (%s) via OIDC", username, org["name"])

    target = f"{oidc.frontend_url()}/auth/callback?token={token}"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)
