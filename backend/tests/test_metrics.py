"""Application metrics (#46): the owner's brief, mapped to this domain.

Counters/gauge for investigations created, active users, reports
generated, and creation failures — each point labelled with the
environment and the deployed image via the shared resource.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.main import create_app
from app.telemetry import AppMetrics, setup_telemetry

_IMAGE = "ghcr.io/bluemusk24/sql-server-tool:20260818-163457-83242da"
_DBA = ("dba", "dba-password")  # seeded demo login (app/store.py)
_INVESTIGATION = {
    "server_host": "sql02.acme.example",
    "server_port": 1433,
    "auth_type": "sql",
    "databases": None,
    "lookback_hours": 8,
}


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


def _metrics_by_name(reader: InMemoryMetricReader) -> tuple[dict, dict]:
    data = reader.get_metrics_data()
    by_name: dict = {}
    resource: dict = {}
    for rm in data.resource_metrics:
        resource = dict(rm.resource.attributes)
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                by_name[metric.name] = metric
    return by_name, resource


def _sum(metric) -> float:
    # NumberDataPoint aggregates into `.value` (counters included).
    return sum(dp.value for dp in metric.data.data_points)


def _login_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/login", json={"username": _DBA[0], "password": _DBA[1]}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_disabled_metrics_noop_everything() -> None:
    metrics = AppMetrics()
    metrics.investigation_created("dba")
    metrics.report_generated("pdf")
    metrics.creation_failed("investigation")
    metrics.touch_user("dba")  # nothing raises


def test_routers_stay_correct_with_dormant_metrics() -> None:
    """Telemetry off (the default) must not change any behavior."""
    app = create_app("sqlite:///:memory:")
    assert setup_telemetry(app) is False
    with TestClient(app) as client:
        headers = _login_headers(client)
        assert client.get("/auth/session", headers=headers).status_code == 200
        created = client.post("/investigations", headers=headers, json=_INVESTIGATION)
        assert created.status_code == 201


def test_metrics_carry_environment_and_deployed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDIP_ENVIRONMENT", "prod")
    monkeypatch.setenv("SDIP_IMAGE", _IMAGE)

    app = create_app("sqlite:///:memory:")
    reader = InMemoryMetricReader()
    assert (
        setup_telemetry(app, exporter=InMemorySpanExporter(), metric_reader=reader)
        is True
    )

    with TestClient(app) as client:
        headers = _login_headers(client)
        # An authenticated request marks the user active (#46).
        assert client.get("/auth/session", headers=headers).status_code == 200
        created = client.post("/investigations", headers=headers, json=_INVESTIGATION)
        assert created.status_code == 201

    # The failure path needs a broken store — call the seam directly.
    app.state.metrics.creation_failed("report")

    by_name, resource = _metrics_by_name(reader)
    # The brief: environment + deployed version on every metric.
    assert resource["service.name"] == "sql-server"
    assert resource["deployment.environment"] == "prod"
    assert resource["service.version"] == _IMAGE

    created_metric = by_name["sdip.investigations.created"]
    assert _sum(created_metric) == 1
    # No per-event labels: open-ended label values (actor, format,
    # object type) split the series and make its first post-boot event
    # invisible to rate() again — the zero baseline below is the fix.
    assert not created_metric.data.data_points[0].attributes

    active = by_name["sdip.users.active"].data.data_points[0]
    assert active.value == 1  # exactly the logged-in dba

    failed = by_name["sdip.creations.failed"]
    assert _sum(failed) == 1
    assert not failed.data.data_points[0].attributes


def test_counters_export_a_zero_baseline_before_any_event() -> None:
    """A fresh boot must publish 0-valued counter samples immediately.

    Sync counters are otherwise silent until their first add(), so the
    first event after a cold start appears as a series whose first
    sample is already 1 — increase()/rate() then computes no change and
    dashboards and alert rules read 0. Observed on prod 2026-10-20:
    investigation #7 existed (and its report too), but the dashboard
    showed 0 because prod had cold-started minutes earlier.
    """
    app = create_app("sqlite:///:memory:")
    reader = InMemoryMetricReader()
    assert (
        setup_telemetry(app, exporter=InMemorySpanExporter(), metric_reader=reader)
        is True
    )

    by_name, _ = _metrics_by_name(reader)
    for name in (
        "sdip.investigations.created",
        "sdip.reports.generated",
        "sdip.creations.failed",
    ):
        assert name in by_name, f"{name} missing its zero baseline"
        assert _sum(by_name[name]) == 0


def test_a_scoped_creation_is_not_counted_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A creation with a database scope succeeds, so it must not raise the
    failure counter the "Repeated creation failures" alert watches."""
    monkeypatch.setenv("SDIP_ENVIRONMENT", "dev")

    app = create_app("sqlite:///:memory:")
    reader = InMemoryMetricReader()
    setup_telemetry(app, exporter=InMemorySpanExporter(), metric_reader=reader)

    with TestClient(app) as client:
        headers = _login_headers(client)
        created = client.post(
            "/investigations",
            headers=headers,
            json={**_INVESTIGATION, "databases": ["Sales", "Orders"]},
        )
        assert created.status_code == 201, created.text

    by_name, _ = _metrics_by_name(reader)
    # The baseline makes the failure counter *exist* at boot — the
    # assertion is that a successful scoped creation leaves it at 0.
    assert "sdip.creations.failed" in by_name
    assert _sum(by_name["sdip.creations.failed"]) == 0
    assert _sum(by_name["sdip.investigations.created"]) == 1
