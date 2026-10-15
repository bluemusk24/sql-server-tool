"""OIDC authorization-code flow for organization SSO (#30).

The identity token is validated with joserfc — the Authlib ecosystem's
JOSE library (authlib's own ``jose`` leaf is deprecated in favour of it)
— checking the signature against the issuer's JWKS, the expiry, the
issuer, and the audience. httpx performs the HTTP legs (Authlib's
httpx/requests integrations are client-side helpers; these are the
server-side legs of the flow and the app's endpoints are synchronous).

Configuration (environment variables, the same pattern as the rest of
the app's settings):

    OIDC_ISSUER         e.g. https://login.example.com/<tenant>/v2.0
    OIDC_CLIENT_ID      client id registered at the issuer
    OIDC_CLIENT_SECRET  client secret at the issuer
    OIDC_REDIRECT_URI   defaults to this app's ``/auth/callback``
    FRONTEND_URL        where the SPA lives; the final hop carries the
                        session token to ``<FRONTEND_URL>/auth/callback``

Flow (routers.auth):

    GET /auth/authorize → 302 to the issuer's authorization_endpoint
    GET /auth/callback  → verify state cookie, exchange the code, verify
                          the id_token, resolve the caller's organization
                          by the token issuer, provision the local user,
                          issue a session token, redirect to the SPA.

Organizations are provisioned by an admin (#27): an unknown issuer is
refused with 403 — never auto-provisioned.
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet

DEFAULT_TIMEOUT_SECONDS = 10.0

# Discovery documents and JWKS are immutable in practice; cache them for
# the process lifetime (tests monkeypatch around these functions anyway).
_DISCOVERY: dict[str, dict[str, Any]] = {}
_JWKS: dict[str, dict[str, Any]] = {}


class OidcError(RuntimeError):
    """Base class for OIDC wiring problems."""


class OidcNotConfigured(OidcError):
    """The OIDC_* environment variables are not all set."""


class OidcUnavailable(OidcError):
    """The issuer could not be reached (discovery, JWKS, token endpoint)."""


class OidcProtocolError(OidcError):
    """The issuer's responses are missing what the flow needs."""


class IdTokenRejected(OidcError):
    """The id_token failed signature/claims validation."""


# ── Configuration ────────────────────────────────────────────────────
def configured() -> bool:
    return bool(os.getenv("OIDC_ISSUER") and os.getenv("OIDC_CLIENT_ID"))


def require_configuration() -> tuple[str, str, str]:
    issuer = os.getenv("OIDC_ISSUER", "").rstrip("/")
    client = os.getenv("OIDC_CLIENT_ID", "")
    secret = os.getenv("OIDC_CLIENT_SECRET", "")
    if not (issuer and client):
        raise OidcNotConfigured(
            "SSO is not configured (OIDC_ISSUER and OIDC_CLIENT_ID are required)"
        )
    return issuer, client, secret


def redirect_uri(fallback: str) -> str:
    """Registered redirect URI: OIDC_REDIRECT_URI, else the given fallback."""
    return os.getenv("OIDC_REDIRECT_URI", "").rstrip("/") or fallback


def frontend_url() -> str:
    """Where the SPA finishes the login; defaults to the Vite dev server."""
    return os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")


# ── Discovery & endpoints ────────────────────────────────────────────
def discover(base_issuer: str) -> dict[str, Any]:
    """Fetch (and cache) the issuer's openid-configuration."""
    cached = _DISCOVERY.get(base_issuer)
    if cached is not None:
        return cached
    url = f"{base_issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        response = httpx.get(url, timeout=DEFAULT_TIMEOUT_SECONDS)
        response.raise_for_status()
        metadata = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcUnavailable(f"could not read OIDC discovery at {url}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise OidcProtocolError(f"discovery document at {url} is not an object")
    _DISCOVERY[base_issuer] = metadata
    return metadata


def authorization_url(
    metadata: dict[str, Any],
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    endpoint = metadata.get("authorization_endpoint")
    if not endpoint:
        raise OidcProtocolError("discovery document has no authorization_endpoint")
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{endpoint}?{query}"


def exchange_code(
    metadata: dict[str, Any],
    code: str,
    *,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Exchange the authorization code at the token endpoint."""
    endpoint = metadata.get("token_endpoint")
    if not endpoint:
        raise OidcProtocolError("discovery document has no token_endpoint")
    try:
        response = httpx.post(
            endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcUnavailable(f"token exchange failed: {exc}") from exc
    if not isinstance(payload, dict) or "id_token" not in payload:
        raise OidcProtocolError("token response carries no id_token")
    return payload


def fetch_jwks(metadata: dict[str, Any]) -> dict[str, Any]:
    url = metadata.get("jwks_uri")
    if not url:
        raise OidcProtocolError("discovery document has no jwks_uri")
    cached = _JWKS.get(url)
    if cached is not None:
        return cached
    try:
        response = httpx.get(url, timeout=DEFAULT_TIMEOUT_SECONDS)
        response.raise_for_status()
        jwks = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcUnavailable(f"could not read JWKS at {url}: {exc}") from exc
    if not isinstance(jwks, dict):
        raise OidcProtocolError(f"JWKS at {url} is not an object")
    _JWKS[url] = jwks
    return jwks


def verify_id_token(
    id_token: str,
    metadata: dict[str, Any],
    *,
    client_id: str,
    issuer: str,
) -> dict[str, Any]:
    """Validate the id_token (joserfc JOSE) and return its claims.

    Checks the signature against the issuer's JWKS, the expiry, the
    issuer, and the audience. Anything else fails closed.
    """
    key_set = KeySet.import_key_set(fetch_jwks(metadata))
    try:
        token = jwt.decode(id_token, key_set)  # verifies the signature
    except Exception as exc:  # joserfc raises a family of JOSE errors
        raise IdTokenRejected(f"id_token rejected: {exc}") from exc
    claims = dict(token.claims)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        raise IdTokenRejected("id_token is expired or carries no exp")
    if str(claims.get("iss")) != issuer:
        raise IdTokenRejected("id_token issuer does not match OIDC_ISSUER")
    audience = claims.get("aud")
    allowed = audience if isinstance(audience, list) else [audience]
    if client_id not in allowed:
        raise IdTokenRejected("id_token audience does not include this client")
    return claims


__all__ = [
    "IdTokenRejected",
    "OidcError",
    "OidcNotConfigured",
    "OidcProtocolError",
    "OidcUnavailable",
    "authorization_url",
    "configured",
    "discover",
    "exchange_code",
    "fetch_jwks",
    "frontend_url",
    "redirect_uri",
    "require_configuration",
    "verify_id_token",
]
