"""MCP server (#51): expose SDIP's own store to AI assistants.

Additive by design — mounted at ``/mcp`` by :mod:`app.main` beside the
existing routers. Nothing here changes the REST API, the UI, or any
existing behavior: the tools call the same store methods the routes use,
under the same opaque bearer tokens (``store.resolve_token``), and they
touch only SDIP's own database. A tool never opens a connection to a
monitored SQL Server — that stays the web UI's job, behind its human
approval gate.

Transport: streamable HTTP, **stateless with JSON responses**, so every
``POST /mcp`` is one independent JSON-RPC exchange (no SSE stream to
hold open; plain HTTP clients can test it).

Guard rails mirrored from the product's own rules:

- ``create_investigation`` carries no credentials by construction — the
  password only ever enters at run time (``StartRunRequest.credentials``).
- MCP deliberately ships **no approval tool**: reviewing ``sql_text`` and
  approving the gate stays human-only (the web UI), so an AI client can
  prepare and start runs but never bypass review.
- ``start_investigation_run`` accepts credentials only as optional
  arguments; its description warns that anything passed there transits
  the AI client's transcript — the web UI remains the recommended path
  for live SQL authentication.

Per-request binding: a thin ASGI wrapper (``_BearerProtect``) validates
the bearer token exactly like ``get_current_user`` does, then places the
``CurrentUser``, a request-scoped store/session, and the metrics handle
in a :class:`contextvars.ContextVar` that the tools read. Contextvars
survive into the SDK's worker threads (``anyio.to_thread`` copies the
context), so sync tools see it too.

Note for maintainers: ``server`` is a process-wide singleton, and
``streamable_http_app()`` builds a fresh session manager each call — the
``MCPServer.session_manager`` property therefore tracks the most recent
``create_app``. Sequential app lifecycles (tests, one production app)
line up; interleaving two live apps in one process would not.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import live
from app.auth import CurrentUser
from app.models import AuthType, CreateInvestigationRequest
from app.routers.investigations import _approval_state, _execute_run, _inv_target
from app.store import CATALOG, DatabaseStore

logger = logging.getLogger(__name__)

server = MCPServer(
    name="sql-server-diagnostic-assistant",
    instructions=(
        "Read-only diagnostics assistant for SQL Server incident triage "
        "(SDIP). Tools answer from the application's own store: the "
        "diagnostic data-source catalog, investigations, findings-bearing "
        "reports, and the org's fleet summary. Tools never touch a SQL "
        "Server directly. To investigate a server: list_data_sources to "
        "see what would run, create_investigation to prepare it, then a "
        "human must review and approve the SQL in the SDIP web UI before "
        "start_investigation_run may execute it. Reports carry the "
        "findings, primary cause, and confidence."
    ),
)


@dataclass(frozen=True)
class _ToolContext:
    """Everything a tool call needs, bound per request by the wrapper."""

    user: CurrentUser
    store: DatabaseStore
    metrics: Any
    session: Session
    session_factory: Any


_tool_ctx: ContextVar[_ToolContext | None] = ContextVar("sdip_mcp", default=None)


def _ctx() -> _ToolContext:
    ctx = _tool_ctx.get()
    if ctx is None:  # the wrapper guarantees this never happens
        raise ToolError("Not authenticated")
    return ctx


# ── Tool surface ─────────────────────────────────────────────────────


_READ = ToolAnnotations(readOnlyHint=True)


@server.tool(annotations=_READ)
def list_data_sources() -> list[dict[str, Any]]:
    """The diagnostic catalog: every query SDIP can run against a server.

    Each entry carries what it collects (purpose, category, issue_area),
    what it needs (min_permission, versions), whether it needs approval
    (requires_approval), and the exact SQL a human reviews before a run
    (sql_text). Use this to explain a planned investigation.
    """
    return [entry.model_dump() for entry in CATALOG]


@server.tool(annotations=_READ)
def list_investigations() -> list[dict[str, Any]]:
    """Every investigation: id, target server, status, and created_at.

    Statuses: draft/pending_approval (waiting for human approval),
    running/succeeded/failed/canceled, completed (report ready).
    """
    return _ctx().store.list_investigations()


@server.tool(annotations=_READ)
def get_investigation(investigation_id: int) -> dict[str, Any]:
    """One investigation in full: target, scope, catalog, and status."""
    inv = _ctx().store.get_investigation(investigation_id)
    if inv is None:
        raise ToolError(f"Investigation {investigation_id} not found")
    return {
        "id": inv.id,
        "status": inv.status,
        "catalog": [entry.model_dump() for entry in inv.catalog],
        **_inv_target(inv),
    }


@server.tool(annotations=_READ)
def get_report(investigation_id: int) -> dict[str, Any]:
    """The diagnostic report for an investigation: findings, primary
    cause, severity, confidence, per-area evidence, and guidance.

    Returns the JSON payload the web UI and PDF render from. Raises if
    the investigation has no report yet (check list_investigations for
    status first)."""
    ctx = _ctx()
    report = ctx.store.get_report(investigation_id)
    if report is None:
        raise ToolError(f"No report for investigation {investigation_id}")
    ctx.metrics.report_generated("json")  # parity with GET /reports/{id}
    return report


@server.tool(annotations=_READ)
def get_fleet_summary() -> dict[str, Any]:
    """Failure summary for the caller's organization: findings by
    severity, per-area breakdown, latest primary cause per server —
    the 'which server needs attention' answer."""
    ctx = _ctx()
    return ctx.store.fleet_summary(ctx.user.organization)


@server.tool(
    description=(
        "Prepare a new investigation (draft) against a SQL Server. Never "
        "takes credentials — they only enter later, at run time. A human "
        "must then review and approve the collected SQL in the SDIP web "
        "UI; start_investigation_run refuses to run before that."
    )
)
def create_investigation(
    server_host: str,
    auth_type: str = "sql",
    server_port: int = 1433,
    databases: list[str] | None = None,
    lookback_hours: int = 8,
) -> dict[str, Any]:
    """Create a draft investigation. Validation matches the REST API
    (hostname/FQDN/IP/HOST\\INSTANCE format, port range, lookback in
    {1, 4, 8, 24}, auth_type 'sql' or 'ad')."""
    ctx = _ctx()
    try:
        payload = CreateInvestigationRequest(
            server_host=server_host,
            server_port=server_port,
            auth_type=AuthType(auth_type),
            databases=databases,
            lookback_hours=lookback_hours,
        )
    except (ValidationError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    try:
        inv = ctx.store.create_investigation(
            server_host=payload.server_host,
            server_port=payload.server_port,
            auth_type=payload.auth_type.value,
            databases=payload.databases,
            lookback_hours=payload.lookback_hours,
        )
        # The REST request commit lives in get_db's teardown; tools own
        # theirs explicitly so the row survives this request.
        ctx.session.commit()
    except Exception as exc:
        ctx.metrics.creation_failed("investigation")
        raise ToolError(f"Could not create the investigation: {exc}") from exc
    ctx.metrics.investigation_created(ctx.user.actor)
    logger.info(
        "investigation %s opened for %s:%s (auth=%s, lookback=%sh, scope=%s) by %s [mcp]",
        inv.id,
        inv.server_host,
        inv.server_port,
        inv.auth_type,
        inv.lookback_hours,
        "all databases" if inv.databases is None else ", ".join(inv.databases),
        ctx.user.actor,
    )
    return {
        "id": inv.id,
        "status": inv.status,
        "server_host": inv.server_host,
        "server_port": inv.server_port,
        "auth_type": inv.auth_type,
        "databases": inv.databases,
        "lookback_hours": inv.lookback_hours,
        "created_at": inv.created_at,
        "catalog": [entry.model_dump() for entry in inv.catalog],
        "next_step": (
            "A human must approve every catalog query in the SDIP web UI "
            "before start_investigation_run will execute it."
        ),
    }


@server.tool(
    description=(
        "Start an approved investigation's run and get back a job_id to "
        "poll (GET /jobs/{job_id}). Refuses if the approval gate is not "
        "satisfied — approval is deliberately human-only, done in the web "
        "UI. With username/password it runs live collection against the "
        "server; without, it produces the seeded demo report. WARNING: "
        "anything passed as username/password transits this AI client's "
        "transcript — prefer starting live runs from the web UI."
    )
)
async def start_investigation_run(
    investigation_id: int,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """Start a run (mirrors POST /investigations/{id}/run). Live mode
    requires credentials for auth_type 'sql'; omit them for demo mode."""
    ctx = _ctx()
    inv = ctx.store.get_investigation(investigation_id)
    if inv is None:
        raise ToolError(f"Investigation {investigation_id} not found")
    if inv.status not in ("draft", "pending_approval"):
        raise ToolError(f"Run already started (status: {inv.status})")
    decisions = ctx.store.get_approvals(investigation_id) or {}
    if _approval_state(decisions) != "approved":
        raise ToolError(
            "Approval gate not satisfied: a human must review the "
            "collected SQL in the SDIP web UI and approve every query "
            "first. MCP cannot approve on its own."
        )

    has_credentials = username is not None or password is not None
    if has_credentials and inv.auth_type == "sql" and (
        not username or password is None
    ):
        raise ToolError("SQL authentication requires a username and password.")

    # Credentials present → live collection (#31), mirroring the router:
    # the password is handed to the driver's ConnectionSettings and
    # never stored or logged.
    entries: list[dict[str, Any]] | None = None
    settings: live.ConnectionSettings | None = None
    if has_credentials:
        approved_ids = {
            cid
            for cid, decision in decisions.items()
            if decision.decision == "approved"
        }
        entries = [
            {
                "id": entry.id,
                "title": entry.title,
                "category": entry.category,
                "issue_area": entry.issue_area,
                "sql": entry.sql,
            }
            for entry in inv.catalog
            if entry.id in approved_ids
        ]
        settings = live.ConnectionSettings(
            server_host=inv.server_host,
            server_port=inv.server_port,
            auth_type=inv.auth_type,
            username=username,
            password=password,
            timeout_seconds=live.query_timeout_seconds(),
        )

    job = ctx.store.create_job(investigation_id)
    # Commit the job row before the worker thread opens its own database
    # session (#29), same as the REST handler.
    ctx.session.commit()
    logger.info(
        "run started for investigation %s on %s:%s (%s) by %s [mcp]",
        inv.id,
        inv.server_host,
        inv.server_port,
        "live collection" if has_credentials else "demo report",
        ctx.user.actor,
    )
    # The worker owns a fresh session and can outlive this request (#29).
    # Running the sync worker on the default executor keeps it off the
    # request path exactly like FastAPI's BackgroundTasks does.
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        functools.partial(
            _execute_run,
            session_factory=ctx.session_factory,
            job_id=job.job_id,
            inv_id=inv.id,
            organization=ctx.user.organization,
            entries=entries,
            settings=settings,
            lookback_hours=inv.lookback_hours,
            databases=inv.databases,
        ),
    )
    return {
        "job_id": job.job_id,
        "mode": "live" if has_credentials else "demo",
        "investigation_id": inv.id,
        "next_step": "Poll GET /jobs/{job_id} until status is terminal.",
    }


# ── Transport: bearer-guarded streamable HTTP ────────────────────────


async def _send_unauthorized(send: Any) -> None:
    body = b'{"detail":"Not authenticated"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _BearerProtect:
    """ASGI wrapper: same opaque bearer tokens as the REST API.

    Resolves the token through ``store.resolve_token`` (so sessions are
    the DB-persisted kind and shared with REST), counts the user as
    active (#46), and binds the per-request context the tools read.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope["headers"]
        }
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            await _send_unauthorized(send)
            return

        # Starlette's Mount hands the inner app the remainder path; for
        # an exact "/mcp" request that is "" and the inner router would
        # answer 307 → "/mcp/". Normalize so one POST lands directly and
        # MCP clients that don't follow redirects still work.
        if not scope.get("path"):
            scope["path"] = "/"

        root_app = scope.get("app")
        if root_app is None:  # unreachable behind FastAPI/Starlette
            await _send_unauthorized(send)
            return

        # One session per MCP request, like get_db gives REST: tools that
        # write commit explicitly; the wrapper rolls back on failure and
        # always closes.
        session = root_app.state.SessionLocal()
        store = DatabaseStore(session)
        user = store.resolve_token(token)
        if user is None:
            session.close()
            await _send_unauthorized(send)
            return
        root_app.state.metrics.touch_user(user.actor)

        token_var = _tool_ctx.set(
            _ToolContext(
                user=CurrentUser(
                    username=user.username,
                    organization=user.organization,
                    actor=user.actor,
                ),
                store=store,
                metrics=root_app.state.metrics,
                session=session,
                session_factory=root_app.state.SessionLocal,
            )
        )
        succeeded = False
        try:
            await self.app(scope, receive, send)
            succeeded = True
        finally:
            _tool_ctx.reset(token_var)
            if not succeeded:
                session.rollback()
            session.close()


class _CanonicalMcpPath:
    """Outer-app middleware: send the bare ``/mcp`` path to ``/mcp/``.

    Starlette's ``Mount("/mcp")`` only matches slash-suffixed paths, so
    an exact ``POST /mcp`` would get a 307 from the router *before* any
    authentication runs — and MCP clients that don't follow redirects
    would fail on the very URL they configure. Rewriting the path
    pre-routing (exact bare path only) removes the redirect entirely.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope["path"] = "/mcp/"
        await self.app(scope, receive, send)


def mcp_http_app() -> _BearerProtect:
    """Build the ``/mcp`` sub-app (fresh per app instance).

    Stateless + JSON so each POST is an independent exchange, and DNS
    rebinding protection off deliberately: the transport is guarded by
    bearer auth instead, and the service is reached by public Render
    hostnames the SDK's loopback allowlist would reject.
    """
    sub = server.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    return _BearerProtect(sub)
