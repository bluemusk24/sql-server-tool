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

Besides traces, #46 adds **application metrics** (see ``AppMetrics``)
and, once a MeterProvider exists, the auto-instrumented HTTP request
metrics (request rate, latency) come for free.

DB query spans are hand-rolled on the stable ``Engine`` events rather
than via ``opentelemetry-instrumentation-sqlalchemy``, which pins
``sqlalchemy < 2.1`` and force-imports ``sqlalchemy.ext.asyncio``
(requiring greenlet) at instrument time — neither matches this app.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI

from app import __version__

logger = logging.getLogger("sqldiag.telemetry")

# Key for the in-flight span stashed on the connection between the
# before/after cursor-execute events.
_CONNECTION_SPAN_KEY = "sdip_otel_span"

# How recently a user must have made a request to count as active.
ACTIVE_USER_WINDOW = 300.0


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


class AppMetrics:
    """Application metrics (#46): the owner's brief, mapped to this domain.

    - ``sdip.investigations.created`` — investigations created      ("rooms")
    - ``sdip.users.active``           — distinct authenticated users seen in
                                        the last ``ACTIVE_USER_WINDOW``
                                                                  ("participants")
    - ``sdip.reports.generated``      — report exports          ("elements")
    - ``sdip.creations.failed``       — creations that raised ("failures")

    Every point inherits ``deployment.environment`` and
    ``service.version`` from the resource, so metrics are labelled with
    the environment and the exact deployed image. A meter-less instance
    (telemetry dormant) no-ops every method.
    """

    def __init__(self, meter: object | None = None) -> None:
        self._enabled = meter is not None
        self._last_seen: dict[str, float] = {}
        if meter is None:
            self._investigations_created = None
            self._reports_generated = None
            self._creations_failed = None
            return
        self._investigations_created = meter.create_counter(
            "sdip.investigations.created",
            unit="{investigation}",
            description="Investigations successfully created.",
        )
        self._reports_generated = meter.create_counter(
            "sdip.reports.generated",
            unit="{report}",
            description="Report artifacts exported (pdf/json).",
        )
        self._creations_failed = meter.create_counter(
            "sdip.creations.failed",
            unit="{failure}",
            description="Domain-object creations that raised an error.",
        )
        # Establish a zero baseline immediately. Sync counters export
        # nothing until their first add(), so without this the first
        # event after every (free-tier) cold start lands on a series
        # whose first sample is already 1 — rate()/increase() over any
        # window then sees a flat line and the dashboard panels and
        # alert rules report 0 (prod, 2026-10-20: investigation #7 was
        # created and reported, yet "Investigations created" showed 0
        # because prod had cold-started minutes earlier). The gauge
        # below has callbacks and exports from the first cycle; the
        # counters must match it.
        self._investigations_created.add(0)
        self._reports_generated.add(0)
        self._creations_failed.add(0)
        meter.create_observable_gauge(
            "sdip.users.active",
            callbacks=[self._observe_active_users],
            unit="{user}",
            description="Distinct authenticated users active in the last 5 minutes.",
        )

    def investigation_created(self, actor: str) -> None:
        """Count a created investigation.

        ``actor`` is deliberately **not** a metric label: open-ended
        labels split the series, and every label combination's first
        post-boot event would again be invisible to rate() (its series
        starts flat at 1 — the bug the zero baseline fixes). The actor
        is recorded in the router's log line instead.
        """
        if self._enabled:
            self._investigations_created.add(1)

    def report_generated(self, fmt: str) -> None:
        """Count an exported report artifact (``pdf``/``json`` detail
        lives in the route/log, not as a metric label — see
        :meth:`investigation_created` for why)."""
        if self._enabled:
            self._reports_generated.add(1)

    def creation_failed(self, object_type: str) -> None:
        """Count a failed creation (``investigation``/``report`` detail
        lives in the raised error's log, not as a metric label — see
        :meth:`investigation_created` for why)."""
        if self._enabled:
            self._creations_failed.add(1)

    def touch_user(self, actor: str) -> None:
        """Record a request from ``actor``; feeds the active-users gauge."""
        if self._enabled:
            self._last_seen[actor] = time.monotonic()

    def _observe_active_users(self, options: object) -> list:
        from opentelemetry.metrics import Observation

        cutoff = time.monotonic() - ACTIVE_USER_WINDOW
        self._last_seen = {
            actor: seen for actor, seen in self._last_seen.items() if seen >= cutoff
        }
        return [Observation(len(self._last_seen))]


def setup_telemetry(
    app: FastAPI,
    exporter: object | None = None,
    metric_reader: object | None = None,
) -> bool:
    """Instrument ``app`` when an OTLP endpoint is configured (#44, #46).

    Returns True when instrumentation was activated. ``exporter`` /
    ``metric_reader`` are test seams: production always exports OTLP;
    tests pass in-memory implementations to assert on real telemetry
    without a network.
    """
    if (
        exporter is None
        and metric_reader is None
        and not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    ):
        app.state.metrics = AppMetrics()  # disabled — no-ops everywhere
        return False  # no backend chosen yet — stay dormant

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    attributes = otel_resource_attributes()
    resource = Resource.create(attributes)

    # Traces (#44): a root span per request, child spans per query.
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(exporter or OTLPSpanExporter())  # type: ignore[arg-type]
    )
    trace.set_tracer_provider(provider)
    # Exposed so tests can flush deterministically even when another
    # test file already claimed the (one-shot) global provider slot.
    app.state.otel_tracer_provider = provider

    # Metrics (#46): same endpoint, same identity — every point carries
    # environment + deployed version. A MeterProvider being set also
    # wakes the auto-instrumented HTTP request metrics (rate, latency).
    if metric_reader is None:
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(), export_interval_millis=60_000
        )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    app.state.metrics = AppMetrics(
        meter_provider.get_meter("sdip.app", version=attributes["service.version"])
    )

    # Root span per request (auto-instrumented ASGI middleware) …
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    # … and a child span per query on the engine the app actually uses.
    # Bound to the local provider (never the global proxy) so tests stay
    # deterministic regardless of which test claimed the global slot.
    _instrument_sqlalchemy(app.state.engine, provider.get_tracer("app.db"))

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
