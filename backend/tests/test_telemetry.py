"""OpenTelemetry instrumentation (#44).

Resource identity on every span (service name, environment, deployed
version) and the dormant-until-configured activation model: without an
OTLP endpoint nothing is initialized, so local runs and CI are silent.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app import __version__
from app.main import create_app
from app.telemetry import otel_resource_attributes, setup_telemetry

_IMAGE = "ghcr.io/bluemusk24/sql-server-tool:20260818-163457-83242da"
_SHA = "83242da111111111111111111111111111111111"


@pytest.fixture(autouse=True)
def _clean_otel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SDIP_OTEL_SERVICE_NAME",
        "SDIP_ENVIRONMENT",
        "SDIP_IMAGE",
        "SDIP_BUILD_SHA",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        monkeypatch.delenv(var, raising=False)


def test_resource_attributes_default_to_local_and_app_version() -> None:
    attrs = otel_resource_attributes()
    assert attrs["service.name"] == "sql-server"
    assert attrs["deployment.environment"] == "local"
    assert attrs["service.version"] == __version__
    assert attrs["commit.sha"] == ""


def test_resource_attributes_prefer_the_deployed_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDIP_ENVIRONMENT", "prod")
    monkeypatch.setenv("SDIP_IMAGE", _IMAGE)
    monkeypatch.setenv("SDIP_BUILD_SHA", _SHA)

    attrs = otel_resource_attributes()
    # "deployed version" = the exact artifact serving, not the code's
    # version string — the same tag the deploy gates verify.
    assert attrs["service.version"] == _IMAGE
    assert attrs["deployment.environment"] == "prod"
    assert attrs["commit.sha"] == _SHA


def test_resource_attributes_fall_back_to_the_baked_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDIP_BUILD_SHA", _SHA)
    assert otel_resource_attributes()["service.version"] == _SHA


def test_setup_stays_dormant_without_an_endpoint() -> None:
    app = create_app("sqlite:///:memory:")
    assert setup_telemetry(app) is False
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_requests_produce_spans_carrying_the_required_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDIP_ENVIRONMENT", "prod")
    monkeypatch.setenv("SDIP_IMAGE", _IMAGE)

    app = create_app("sqlite:///:memory:")
    exporter = InMemorySpanExporter()
    assert setup_telemetry(app, exporter=exporter) is True

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        # Wrong credentials still query the users table — enough to
        # produce a DB child span without needing seeded users.
        response = client.post(
            "/auth/login", json={"username": "nobody", "password": "nope"}
        )
        assert response.status_code == 401

    trace.get_tracer_provider().force_flush()
    spans = exporter.get_finished_spans()
    # Exact match: ASGI send/receive children are named "GET /health http
    # send" etc. — only the root span is named exactly "GET /health".
    root = next(span for span in spans if span.name == "GET /health")
    attrs = {**dict(root.attributes or {}), **dict(root.resource.attributes)}
    assert attrs["service.name"] == "sql-server"
    assert attrs["deployment.environment"] == "prod"
    assert attrs["service.version"] == _IMAGE
    # The auto-instrumented span describes the request itself.
    assert attrs["http.method"] == "GET"
    assert attrs["http.status_code"] == 200

    # The login query ran as a child span of the login request, tagged
    # with the database system it executed against. (Siblings include
    # ASGI send/receive event spans — only query spans carry db.system.)
    login = next(span for span in spans if span.name == "POST /auth/login")
    queries = [
        span
        for span in spans
        if span.parent is not None
        and span.parent.span_id == login.context.span_id
        and span.attributes
        and "db.system" in span.attributes
    ]
    assert queries, "expected a child span for the users-table query"
    assert queries[0].attributes["db.system"] in {"sqlite", "postgresql"}
    assert "db.statement" in queries[0].attributes
