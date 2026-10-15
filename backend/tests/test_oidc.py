"""Tests for the OIDC authorization-code flow (#30).

The issuer legs (discovery, token exchange, JWKS) are monkeypatched at
the module boundary; the id_token itself is exercised for real with
Authlib JOSE and locally minted RS256 tokens.
"""

from __future__ import annotations

import base64
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import import_key

from app import oidc

ISSUER = "https://idp.test.example"
CLIENT = "sdip-client"

METADATA: dict[str, Any] = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": f"{ISSUER}/jwks",
}

CLAIMS: dict[str, Any] = {
    "iss": ISSUER,
    "aud": CLIENT,
    "sub": "jdoe",
    "preferred_username": "jdoe",
    "email": "jdoe@acme.example",
}


# ── JOSE helpers (real RS256, minted locally) ────────────────────────
def _b64url_uint(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key) -> dict[str, Any]:
    numbers = key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": "test-key",
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }


def _sign(key, claims: dict[str, Any]) -> str:
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return jwt.encode(
        {"alg": "RS256", "kid": "test-key"}, claims, import_key(pem)
    )


def _claims(**overrides) -> dict[str, Any]:
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": CLIENT,
        "sub": "jdoe",
        "iat": now,
        "exp": now + 300,
        "preferred_username": "jdoe",
        "email": "jdoe@acme.example",
    }
    base.update(overrides)
    return base


@pytest.fixture
def rsa_key():
    return _rsa_key()


# ── Environment + issuer-leg fixtures ────────────────────────────────
@pytest.fixture
def oidc_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "shhh")
    monkeypatch.setenv("FRONTEND_URL", "http://app.test.example")
    monkeypatch.setattr(oidc, "discover", lambda issuer: METADATA)


def _wire_token_legs(monkeypatch: pytest.MonkeyPatch, claims=None) -> None:
    monkeypatch.setattr(
        oidc, "exchange_code", lambda *a, **k: {"id_token": "fake-token"}
    )
    monkeypatch.setattr(
        oidc,
        "verify_id_token",
        lambda *a, **k: dict(CLAIMS if claims is None else claims),
    )


def _begin_callback(
    client: TestClient, state: str = "test-state", code: str = "the-code"
):
    client.cookies.set("sdip_oidc_state", state)
    return client.get(
        f"/auth/callback?code={code}&state={state}", follow_redirects=False
    )


# ── GET /auth/authorize ──────────────────────────────────────────────
def test_authorize_redirects_to_the_issuer_and_sets_state(
    client: TestClient, oidc_env
) -> None:
    response = client.get("/auth/authorize", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(f"{ISSUER}/authorize?")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == [CLIENT]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid profile email"]
    assert query["redirect_uri"] == ["http://testserver/auth/callback"]
    assert query["state"][0]  # a state value was minted
    assert "sdip_oidc_state" in response.headers["set-cookie"]


def test_authorize_surfaces_discovery_failures(
    client: TestClient, oidc_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(issuer):
        raise oidc.OidcUnavailable("issuer unreachable")

    monkeypatch.setattr(oidc, "discover", _boom)
    response = client.get("/auth/authorize", follow_redirects=False)
    assert response.status_code == 503


# ── GET /auth/callback ───────────────────────────────────────────────
def test_callback_requires_code_and_state(client: TestClient, oidc_env) -> None:
    assert client.get("/auth/callback", follow_redirects=False).status_code == 400


def test_callback_reports_issuer_errors(
    client: TestClient, oidc_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.get(
        "/auth/callback?error=access_denied&state=x", follow_redirects=False
    )
    assert response.status_code == 400
    assert "access_denied" in response.json()["detail"]


def test_callback_rejects_a_state_mismatch(
    client: TestClient, oidc_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_token_legs(monkeypatch)
    client.cookies.set("sdip_oidc_state", "expected")
    response = client.get(
        "/auth/callback?code=the-code&state=other", follow_redirects=False
    )
    assert response.status_code == 400
    assert "state mismatch" in response.json()["detail"]


def test_callback_completes_the_flow_and_identifies_the_caller(
    client: TestClient, auth_headers: dict[str, str], oidc_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_token_legs(monkeypatch)
    # Trailing slash in the stored issuer must not matter (#30).
    created = client.post(
        "/orgs",
        headers=auth_headers,
        json={"name": "Globex", "issuer": f"{ISSUER}/"},
    )
    assert created.status_code == 201

    response = _begin_callback(client)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://app.test.example/auth/callback?token=")
    token = parse_qs(urlparse(location).query)["token"][0]

    # The session token exposes the caller's identity everywhere (#30).
    session = client.get(
        "/auth/session", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert session == {"organization": "Globex", "actor": "jdoe@acme.example"}
    inv = client.get(
        "/investigations", headers={"Authorization": f"Bearer {token}"}
    )
    assert inv.status_code == 200


def test_callback_refuses_an_unprovisioned_organization(
    client: TestClient, oidc_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_token_legs(monkeypatch)
    response = _begin_callback(client)
    assert response.status_code == 403
    assert "not provisioned" in response.json()["detail"]


def test_callback_rejects_a_bad_id_token(
    client: TestClient, auth_headers: dict[str, str], oidc_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client.post(
        "/orgs", headers=auth_headers, json={"name": "Globex", "issuer": ISSUER}
    )
    monkeypatch.setattr(
        oidc, "exchange_code", lambda *a, **k: {"id_token": "forged"}
    )

    def _reject(*a, **k):
        raise oidc.IdTokenRejected("bad signature")

    monkeypatch.setattr(oidc, "verify_id_token", _reject)
    response = _begin_callback(client)
    assert response.status_code == 400
    assert "bad signature" in response.json()["detail"]


def test_sso_login_refreshes_one_user_row(
    client: TestClient, auth_headers: dict[str, str], oidc_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_token_legs(monkeypatch)
    client.post(
        "/orgs", headers=auth_headers, json={"name": "Globex", "issuer": ISSUER}
    )
    first = _begin_callback(client)
    second = _begin_callback(client)
    assert first.status_code == second.status_code == 302

    # One user, still able to authenticate, no duplicated identity.
    users = client.get("/orgs", headers=auth_headers)  # orgs endpoint sanity
    assert users.status_code == 200
    for response in (first, second):
        token = parse_qs(urlparse(response.headers["location"]).query)["token"][0]
        session = client.get(
            "/auth/session", headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert session["organization"] == "Globex"


# ── verify_id_token (real JOSE) ──────────────────────────────────────
def test_verify_id_token_accepts_a_signed_token(
    rsa_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oidc, "fetch_jwks", lambda metadata: _jwks(rsa_key))
    claims = oidc.verify_id_token(
        _sign(rsa_key, _claims()), METADATA, client_id=CLIENT, issuer=ISSUER
    )
    assert claims["preferred_username"] == "jdoe"


def test_verify_id_token_rejects_a_wrong_issuer(
    rsa_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oidc, "fetch_jwks", lambda metadata: _jwks(rsa_key))
    with pytest.raises(oidc.IdTokenRejected, match="issuer"):
        oidc.verify_id_token(
            _sign(rsa_key, _claims(iss="https://other.example")),
            METADATA,
            client_id=CLIENT,
            issuer=ISSUER,
        )


def test_verify_id_token_rejects_the_wrong_audience(
    rsa_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oidc, "fetch_jwks", lambda metadata: _jwks(rsa_key))
    with pytest.raises(oidc.IdTokenRejected, match="audience"):
        oidc.verify_id_token(
            _sign(rsa_key, _claims(aud="someone-else")),
            METADATA,
            client_id=CLIENT,
            issuer=ISSUER,
        )


def test_verify_id_token_rejects_an_expired_token(
    rsa_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oidc, "fetch_jwks", lambda metadata: _jwks(rsa_key))
    now = int(time.time())
    with pytest.raises(oidc.IdTokenRejected):
        oidc.verify_id_token(
            _sign(rsa_key, _claims(iat=now - 660, exp=now - 600)),
            METADATA,
            client_id=CLIENT,
            issuer=ISSUER,
        )


def test_verify_id_token_rejects_a_signature_from_another_key(
    rsa_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oidc, "fetch_jwks", lambda metadata: _jwks(rsa_key))
    other = _rsa_key()
    with pytest.raises(oidc.IdTokenRejected):
        oidc.verify_id_token(
            _sign(other, _claims()), METADATA, client_id=CLIENT, issuer=ISSUER
        )
