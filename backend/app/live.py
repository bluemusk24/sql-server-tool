"""Live, read-only SQL Server collection (#31).

Turns an approved investigation into real evidence against the DBA's
instance, honouring the product's hard rules (docs/spec.md):

- **Read-only**: connections carry ``ApplicationIntent=ReadOnly`` and only
  the seeded catalog's own SQL can ever run — the executor refuses any
  source whose SQL is not byte-for-byte one of the vetted catalog queries,
  and lookback/database parameters are bound, never interpolated raw.
- **Credentials are session-only** (§6): they live in the request frame,
  are passed to the driver as keyword arguments (never inside a connection
  string that could be logged), are excluded from reprs, and are never
  written to the investigation, job, report, or any log.
- **Partial evidence is honest** (§3.8): a failed connection or query marks
  that source ``unavailable`` with a coverage gap. Nothing unavailable is
  ever presented as checked, and no findings are fabricated.

The module is import-light: ``pyodbc`` loads lazily, so the app (and the
whole test suite) runs on hosts without the ODBC driver. ``run_collection``
takes an injectable ``connect_fn`` — the unit tests drive it with a fake
cursor, and the real driver is exercised only where an actual instance is
reachable (see ``tests/test_live.py`` for the env-gated integration test).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_QUERY_TIMEOUT_SECONDS = 30

#: Overall run budget (#29) so an abandoned run can't consume the instance.
DEFAULT_RUN_TIMEOUT_SECONDS = 600

#: Cap the evidence kept per source so one runaway query can't bloat a report.
MAX_EVIDENCE_ROWS = 100

#: Statement-text redaction (#52). DMV collectors can return full customer
#: SQL — string literals, PII, secrets — which would then sit at rest in
#: reports, exports, and backups. Read-only protects the server; this
#: protects the data. Any outcome column named like statement text is
#: truncated at the collection boundary, fail-safe for future collectors
#: too (Darling's PerformanceMonitor withholds the same class of text).
STATEMENT_TEXT_LIMIT = 120
STATEMENT_TEXT_COLUMNS = frozenset({"text", "statement", "query_text", "command"})
_TRUNCATED_MARK = " … [truncated: {total} chars total]"

#: A database name may only be a plain identifier before it enters SQL.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_$]{1,128}$")


class LiveUnavailableError(RuntimeError):
    """The driver or the server cannot serve evidence right now."""


@dataclass
class ConnectionSettings:
    """Everything needed to open a session — and nothing persisted."""

    server_host: str
    server_port: int
    auth_type: str  # "sql" | "ad"
    username: str | None = None
    # repr=False: a password must never appear in logs or error dumps.
    password: str | None = field(default=None, repr=False)
    timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS
    trust_server_certificate: bool = False


@dataclass
class SourceOutcome:
    """Per-source collection result (maps 1:1 onto the report's shapes)."""

    source_id: str
    title: str
    category: str
    issue_area: str
    status: str  # "collected" | "partial" | "unavailable"
    note: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "category": self.category,
            "issue_area": self.issue_area,
            "status": self.status,
            "note": self.note,
            "evidence": self.evidence,
            "row_count": self.row_count,
        }


class _Cursor(Protocol):
    description: Any

    def execute(self, sql: str) -> Any: ...

    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...


class _Connection(Protocol):
    timeout: int

    def cursor(self) -> _Cursor: ...

    def close(self) -> None: ...


def query_timeout_seconds() -> int:
    """Per-query timeout from ``$LIVE_QUERY_TIMEOUT_SECONDS`` (default 30)."""
    raw = os.environ.get("LIVE_QUERY_TIMEOUT_SECONDS", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_QUERY_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_QUERY_TIMEOUT_SECONDS


def run_timeout_seconds() -> int:
    """Overall run budget from ``$LIVE_RUN_TIMEOUT_SECONDS`` (default 600)."""
    raw = os.environ.get("LIVE_RUN_TIMEOUT_SECONDS", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RUN_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_RUN_TIMEOUT_SECONDS


def driver_available() -> bool:
    """True when pyodbc (and thus a driver) can be imported on this host."""
    try:
        import pyodbc  # noqa: F401
    except Exception:  # noqa: BLE001 - a missing system lib raises OSError, not just ImportError
        return False
    return True


def server_address(settings: ConnectionSettings) -> str:
    """Value for the ODBC ``SERVER`` key.

    A named instance (``HOST\\INSTANCE``) is passed without a port so the
    driver can resolve it through the SQL Browser service; a plain host
    keeps the explicit ``host,port`` it was given (#37). The two are
    alternative addressing modes — a known port makes the instance name
    redundant for TDS, so the API never mixes them.
    """
    if "\\" in settings.server_host:
        return settings.server_host
    return f"{settings.server_host},{settings.server_port}"


def display_target(settings: ConnectionSettings) -> str:
    """Human-readable target for error messages — never ``HOST\\INST:1433``."""
    if "\\" in settings.server_host:
        return settings.server_host
    return f"{settings.server_host}:{settings.server_port}"


def build_connection_string(settings: ConnectionSettings) -> str:
    """Connection string for ODBC Driver 18 — credentials deliberately absent.

    UID/PWD never appear here; they are handed to the driver as keyword
    arguments (see `open_connection`) so no log or exception that echoes the
    connection string can leak them.
    """
    parts = [
        "DRIVER={ODBC Driver 18 for SQL Server}",
        f"SERVER={server_address(settings)}",
        "DATABASE=master",
        f"Connection Timeout={settings.timeout_seconds}",
        "ApplicationIntent=ReadOnly",
        "Encrypt=yes" if not settings.trust_server_certificate else "Encrypt=no",
        f"TrustServerCertificate={'yes' if settings.trust_server_certificate else 'no'}",
    ]
    if settings.auth_type == "ad":
        parts.append("Authentication=ActiveDirectoryIntegrated")
    return ";".join(parts) + ";"


def open_connection(settings: ConnectionSettings) -> _Connection:
    """Open a session to the target instance, or explain why it's impossible."""
    try:
        import pyodbc
    except Exception as exc:  # pragma: no cover - depends on the host's libs
        raise LiveUnavailableError(
            "pyodbc / the ODBC Driver 18 is not installed on this host"
        ) from exc

    kwargs: dict[str, Any] = {"autocommit": True}
    if settings.auth_type == "sql":
        if not settings.username or settings.password is None:
            raise LiveUnavailableError(
                "SQL authentication requires a username and password"
            )
        kwargs["uid"] = settings.username
        kwargs["pwd"] = settings.password
    # For "ad", Integrated/Kerberos auth uses the host's own identity.

    try:
        return pyodbc.connect(build_connection_string(settings), **kwargs)
    except Exception as exc:
        raise LiveUnavailableError(
            f"could not connect to {display_target(settings)}: {exc}"
        ) from exc


def render_sql(
    sql: str, lookback_hours: int, databases: list[str] | None
) -> str:
    """Bind a catalog query's declared parameters — never string-splice input.

    ``:lookback_hours`` becomes the integer window; ``:database_list``
    becomes a quoted, identifier-validated list. A database name that is
    not a plain identifier is refused outright.
    """
    rendered = sql.replace(":lookback_hours", str(int(lookback_hours)))
    if ":database_list" in rendered:
        names = databases or []
        for name in names:
            if not _IDENTIFIER_RE.match(name):
                raise LiveUnavailableError(
                    f"refused: {name!r} is not a valid database identifier"
                )
        if not names:
            raise LiveUnavailableError(
                "refused: this query needs a database scope but none was given"
            )
        rendered = rendered.replace(
            ":database_list", ", ".join(f"[{name}]" for name in names)
        )
    return rendered


def _jsonable(value: Any) -> Any:
    """Coerce a driver value into something a JSON report can hold."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def redact_statement_text(value: Any, limit: int = STATEMENT_TEXT_LIMIT) -> Any:
    """Shorten statement text to a readable query-shape prefix (#52).

    Non-strings pass through untouched; text at or under the limit is
    kept exactly as collected. Longer text keeps its first ``limit``
    characters plus a marker naming the original length — the DBA can
    still identify the query while the literals tail, where PII and
    secrets live, is gone before anything is stored.
    """
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit] + _TRUNCATED_MARK.format(total=len(value))


class RunTimeout(RuntimeError):
    """The overall run budget ran out (#29).

    Carries the outcomes collected before the deadline so a timed-out
    run can still publish its partial evidence.
    """

    def __init__(self, outcomes: list[SourceOutcome]) -> None:
        super().__init__("run exceeded the overall timeout")
        self.outcomes = outcomes


def mark_uncollected(
    entries: Iterable[dict[str, Any]],
    outcomes: list[SourceOutcome],
    note: str,
) -> list[SourceOutcome]:
    """Honesty pad for a stopped run (#29).

    Sources the run never reached become explicit ``unavailable``
    outcomes, so the report lists them as coverage gaps instead of
    silently omitting them (spec §3.8).
    """
    seen = {outcome.source_id for outcome in outcomes}
    padded = list(outcomes)
    for entry in entries:
        if entry["id"] in seen:
            continue
        padded.append(
            SourceOutcome(
                source_id=entry["id"],
                title=entry["title"],
                category=entry["category"],
                issue_area=entry["issue_area"],
                status="unavailable",
                note=note,
            )
        )
    return padded


def run_collection(
    entries: Iterable[dict[str, Any]],
    settings: ConnectionSettings,
    lookback_hours: int,
    databases: list[str] | None = None,
    *,
    connect_fn: Callable[[ConnectionSettings], _Connection] | None = None,
    deadline_seconds: float | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> list[SourceOutcome]:
    """Collect evidence for each entry; never raises for server problems.

    `entries` must be the catalog rows the DBA approved for this run — the
    caller resolves them from the seeded catalog, and anything not in that
    set is refused here as defense in depth.

    ``should_abort`` is consulted between sources: a true return stops the
    run early and whatever was collected is returned (cancellation, #29).
    ``deadline_seconds`` caps the whole run; exceeding it raises
    :class:`RunTimeout` carrying the partial outcomes. Both leave the
    sources they skipped for the caller to mark as uncollected.
    """
    entry_list = list(entries)
    connect = connect_fn or open_connection  # late bind: tests can patch it

    try:
        conn = connect(settings)
    except LiveUnavailableError as exc:
        return [
            SourceOutcome(
                source_id=entry["id"],
                title=entry["title"],
                category=entry["category"],
                issue_area=entry["issue_area"],
                status="unavailable",
                note=str(exc),
            )
            for entry in entry_list
        ]

    started = time.monotonic()
    outcomes: list[SourceOutcome] = []
    try:
        conn.timeout = settings.timeout_seconds
        for entry in entry_list:
            if should_abort is not None and should_abort():
                break
            if deadline_seconds is not None and (
                time.monotonic() - started >= deadline_seconds
            ):
                raise RunTimeout(outcomes)
            outcomes.append(_collect_one(conn, entry, lookback_hours, databases))
        return outcomes
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup; the run's result matters, not the close
            pass


def _collect_one(
    conn: _Connection,
    entry: dict[str, Any],
    lookback_hours: int,
    databases: list[str] | None,
) -> SourceOutcome:
    base = {
        "source_id": entry["id"],
        "title": entry["title"],
        "category": entry["category"],
        "issue_area": entry["issue_area"],
    }
    try:
        sql = render_sql(entry["sql"], lookback_hours, databases)
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = (
            [column[0] for column in cursor.description]
            if cursor.description
            else []
        )
        rows = cursor.fetchmany(MAX_EVIDENCE_ROWS)
        # Redact at the boundary (#52): raw statement text never leaves
        # this function, so it can't reach generate_report payloads.
        evidence = []
        for row in rows:
            record: dict[str, Any] = {}
            for column, value in zip(columns, row):
                cell = _jsonable(value)
                if column.lower() in STATEMENT_TEXT_COLUMNS:
                    cell = redact_statement_text(cell)
                record[column] = cell
            evidence.append(record)
        note = "" if len(rows) < MAX_EVIDENCE_ROWS else (
            f"showing the first {MAX_EVIDENCE_ROWS} rows"
        )
        return SourceOutcome(
            **base,
            status="collected",
            note=note,
            evidence=evidence,
            row_count=len(rows),
        )
    except Exception as exc:  # noqa: BLE001 - any driver error becomes an honest per-source gap
        # Permission denied, timeout, broken object… — honest gap, no
        # fabricated rows, and the other sources still run.
        return SourceOutcome(**base, status="unavailable", note=f"query failed: {exc}")


__all__ = [
    "ConnectionSettings",
    "LiveUnavailableError",
    "RunTimeout",
    "SourceOutcome",
    "build_connection_string",
    "driver_available",
    "mark_uncollected",
    "open_connection",
    "query_timeout_seconds",
    "render_sql",
    "run_collection",
    "run_timeout_seconds",
]
