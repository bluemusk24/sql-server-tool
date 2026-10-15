"""OpenTelemetry instrumentation (#44).

Auto-instruments the FastAPI app (a root span per request) and its
SQLAlchemy engine (a child span per query), tagged with *who* is
serving it:

    service.name             sql-server ($SDIP_OTEL_SERVICE_NAME)
    deployment.environment   dev | prod | local ($SDIP_ENVIRONMENT)
    service.version          the deployed image ref ($SDIP_IMAGE — baked
                             by CI, so "version" means the exact artifact
                             running; falls back to the baked commit,
                             then the app version string)
    commit.sha               the baked build commit ($SDIP_BUILD_SHA)

Nothing is initialized and nothing is exported until an OTLP endpoint
is configured via ``$OTEL_EXPORTER_OTLP_ENDPOINT`` — local runs, CI and
the compose e2e stay completely silent. Setting the variable (plus any
``$OTEL_EXPORTER_OTLP_HEADERS`` auth) in the Render dashboard turns
telemetry on at the next deploy; no code change needed.

DB query spans are hand-rolled on the stable ``Engine`` events rather
than via ``opentelemetry-instrumentation-sqlalchemy``, which pins
``sqlalchemy < 2.1`` and force-imports ``sqlalchemy.ext.asyncio``
(requiring greenlet) at instrument time — neither matches this app.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from app import __version__

logger = logging.getLogger("sqldiag.telemetry")

# Key for the in-flight span stashed on the connection between the
# before/after cursor-execute events.
_CONNECTION_SPAN_KEY = "sdip_otel_span"


def otel_resource_attributes() -> dict[str, str]:
    """Identify the emitter: service, environment, deployed version."""
    return {
        "service.name": os.getenv("SDIP_OTEL_SERVICE_NAME") or "sql-server",
        "deployment.environment": os.getenv("SDIP_ENVIRONMENT") or "local",
        "service.version": (
            os.getenv("SDIP_IMAGE")
            or os.getenv("SDIP_BUILD_SHA")
            or __version__
        ),
        "commit.sha": os.getenv("SDIP_BUILD_SHA") or "",
    }


def setup_telemetry(app: FastAPI, exporter: object | None = None) -> bool:
    """Instrument ``app`` when an OTLP endpoint is configured (#44).

    Returns True when instrumentation was activated. ``exporter`` is a
    test seam: production always exports OTLP; tests pass an in-memory
    exporter to assert on real spans without a network.
    """
    if exporter is None and not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False  # no backend chosen yet — stay dormant

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    attributes = otel_resource_attributes()
    provider = TracerProvider(resource=Resource.create(attributes))
    provider.add_span_processor(
        BatchSpanProcessor(exporter or OTLPSpanExporter())  # type: ignore[arg-type]
    )
    trace.set_tracer_provider(provider)

    # Root span per request (auto-instrumented ASGI middleware) …
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    # … and a child span per query on the engine the app actually uses.
    _instrument_sqlalchemy(app.state.engine, trace.get_tracer("app.db"))

    logger.info(
        "OpenTelemetry active: %s (%s) exporting to %s",
        attributes["service.name"],
        attributes["deployment.environment"],
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
    return True


def _instrument_sqlalchemy(engine, tracer) -> None:
    """Emit a child span per executed statement on ``engine``.

    Uses the ``before_cursor_execute`` / ``after_cursor_execute`` events
    (with ``handle_error`` so a failed query still closes its span).
    Statements are parameterized throughout the app, so the captured
    text carries placeholders, never values — credentials stay out of
    telemetry by construction.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _start_span(conn, cursor, statement, parameters, context, executemany):
        span = tracer.start_span(statement.lstrip().split(" ", 1)[0])
        span.set_attribute("db.system", engine.dialect.name)
        span.set_attribute("db.statement", statement)
        conn.info[_CONNECTION_SPAN_KEY] = span

    @event.listens_for(engine, "after_cursor_execute")
    def _end_span(conn, cursor, statement, parameters, context, executemany):
        span = conn.info.pop(_CONNECTION_SPAN_KEY, None)
        if span is not None:
            span.end()

    @event.listens_for(engine, "handle_error")
    def _end_failed_span(context):
        connection = getattr(context, "connection", None)
        span = (
            connection.info.pop(_CONNECTION_SPAN_KEY, None)
            if connection is not None
            else None
        )
        if span is not None:
            span.end()
