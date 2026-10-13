"""Tests for the organizations router."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_orgs_require_auth(client: TestClient) -> None:
    assert client.get("/orgs").status_code == 401


def test_list_includes_seeded_org(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/orgs", headers=auth_headers)
    assert response.status_code == 200
    names = [row["name"] for row in response.json()]
    assert "Acme Corp" in names


def test_create_org(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/orgs",
        headers=auth_headers,
        json={"name": "Globex", "issuer": "https://idp.globex.example"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Globex"
    assert body["issuer"] == "https://idp.globex.example"


def test_delete_org(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/orgs",
        headers=auth_headers,
        json={"name": "Initech", "issuer": "https://idp.initech.example"},
    ).json()
    response = client.delete(f"/orgs/{created['id']}", headers=auth_headers)
    assert response.status_code == 204

    # Now gone.
    orgs = client.get("/orgs", headers=auth_headers).json()
    assert created["id"] not in [row["id"] for row in orgs]


def test_delete_unknown_org_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.delete("/orgs/9999", headers=auth_headers).status_code == 404
