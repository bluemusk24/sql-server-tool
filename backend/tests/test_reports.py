"""Tests for the reports router (JSON payload + PDF download)."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient


def test_report_requires_auth(client: TestClient) -> None:
    assert client.get("/reports/1").status_code == 401


def test_seeded_report_payload(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/reports/1", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["investigation_id"] == 1
    assert body["organization"] == "Acme Corp"
    assert body["server_host"]
    assert "read_only_disclaimer" in body


def test_unknown_report_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/reports/9999", headers=auth_headers).status_code == 404


def test_pdf_download_is_valid(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/reports/1.pdf", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    data = response.content
    assert data.startswith(b"%PDF-")
    assert data.rstrip().endswith(b"%%EOF")

    # Validate it parses as a PDF using pypdf (dev dependency).
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1


def test_unknown_pdf_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/reports/9999.pdf", headers=auth_headers).status_code == 404
