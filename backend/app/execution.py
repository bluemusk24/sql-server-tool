"""Diagnostic execution engine (#12).

Given an investigation id, :func:`run_diagnostic` iterates the approved
catalog queries in catalog order, connects read-only to each target database
through the #6 connector, runs each query with its catalog parameters, and
collects raw evidence per query/database. It is isolated by construction: one
query's failure (error or timeout) is recorded and the run continues.

The engine composes the merged modules and reimplements none of them:

* the approval gate is the #10 :func:`app.investigations.service.can_start`,
* the query catalog and read-only SQL are #8,
* connections, rows, and categorized errors are #6, invoked ``read_only=True``
  and ``allow_write=False`` always, and
* credentials come from the session-only #5 store, keyed by investigation id.

Nothing credential-shaped reaches the evidence: error text is redacted before
it is stored, and a run never accepts credentials beyond the live store.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from app.credentials import CredentialStore, SqlCredentials
from app.diagnostics.catalog import Catalog, DiagnosticQuery
from app.evidence.models import (
    DiagnosticRunResult,
    QueryEvidence,
    QueryStatus,
    SourceStatus,
)
from app.evidence.registry import EvidenceRegistry, is_applicable
from app.investigations.repository import (
    ApprovalRepository,
    InvestigationRecord,
    InvestigationRepository,
)
from app.investigations.service import can_start
from app.logredact import redact_text
from app.sql import connector as default_connector
from app.sql.connector import (
    DEFAULT_LOGIN_TIMEOUT,
    DEFAULT_QUERY_TIMEOUT,
    MASK,
    QueryError,
)
from app.sql.connector import (
    PermissionError as SqlPermissionError,
)

#: Database a whole-instance investigation connects to for instance-wide DMVs.
DEFAULT_TARGET_DATABASE = "master"

#: Raw rows kept per query result; larger results are recorded but truncated.
MAX_EVIDENCE_ROWS = 100_000

#: Human-readable note prefixed to a source whose query could not be collected.
_CONNECTION_NOTE = "Server/database unreachable; evidence not collected."

logger = logging.getLogger(__name__)


class RunNotApproved(RuntimeError):
    """The investigation is missing or its approval state is not ``approved``."""


class MissingCredentials(RuntimeError):
    """No session credentials were stored for the investigation."""


class UnknownParameter(RuntimeError):
    """A catalog placeholder had no value the engine knows how to bind."""


@runtime_checkable
class Connector(Protocol):
    """The #6 connector surface the engine depends on (injectable for tests)."""

    def connect(
        self,
        host: str,
        database: str,
        username: str,
        password: str,
        *,
        port: int = ...,
        login_timeout: int = ...,
        query_timeout: int = ...,
        read_only: bool = ...,
    ) -> Any:
        """Open a read-only connection to ``database``."""

    def query(
        self,
        conn: Any,
        sql: str,
        *,
        params: Sequence[object] | None = ...,
        allow_write: bool = ...,
        timeout: int | None = ...,
    ) -> list[Any]:
        """Run ``sql`` on ``conn`` and return the rows."""


@dataclass(frozen=True)
class _Outcome:
    """Per-execution result used to aggregate a source's inventory status."""

    query_status: QueryStatus
    source_status: SourceStatus
    note: str


def _sanitize(message: str, credentials: SqlCredentials) -> str:
    """Strip both credential fields from ``message`` and mask secret shapes."""
    for secret in (credentials.password, credentials.username):
        if secret:
            message = message.replace(secret, MASK)
    return redact_text(message)


def _is_timeout(exc: BaseException) -> bool:
    """#6 maps query timeouts onto :class:`QueryError`; recognise the message."""
    return isinstance(exc, QueryError) and "timed out" in str(exc).lower()


def _classify_query_error(exc: BaseException) -> tuple[QueryStatus, SourceStatus]:
    """Map a #6 query-phase error onto the two status dimensions."""
    if isinstance(exc, SqlPermissionError):
        return QueryStatus.error, SourceStatus.unavailable_permission
    if _is_timeout(exc):
        return QueryStatus.timeout, SourceStatus.unavailable_setting
    return QueryStatus.error, SourceStatus.unavailable_setting


def _classify_connection_error(exc: BaseException) -> SourceStatus:
    """Map a #6 connection-phase error onto the source inventory status."""
    if isinstance(exc, SqlPermissionError):
        return SourceStatus.unavailable_permission
    return SourceStatus.unavailable_connection


def _resolve_parameter(name: str, investigation: InvestigationRecord) -> object:
    """Resolve one catalog placeholder name to a bound value."""
    if name == "lookback_hours":
        return investigation.lookback_hours
    if name == "error_filter":
        return ""
    if name in ("run_date_from", "run_time_from"):
        since = datetime.now(UTC) - timedelta(hours=investigation.lookback_hours)
        if name == "run_date_from":
            return int(since.strftime("%Y%m%d"))
        return int(since.strftime("%H%M%S"))
    raise UnknownParameter(f"no value is defined for catalog parameter {name!r}")


def _parameter_values(
    query: DiagnosticQuery, investigation: InvestigationRecord
) -> tuple[object, ...]:
    """Bind a query's named params to a positional tuple for the driver."""
    return tuple(_resolve_parameter(name, investigation) for name in query.params)


def _target_databases(investigation: InvestigationRecord) -> list[str]:
    """The databases to connect to: the named scope, or ``master`` for the instance."""
    if investigation.scope_kind == "databases" and investigation.databases:
        return list(investigation.databases)
    return [DEFAULT_TARGET_DATABASE]


def _approved_ids(
    investigation_id: int, approval_repository: ApprovalRepository
) -> set[str]:
    """The catalog ids with an ``approved`` decision for the investigation."""
    latest = approval_repository.latest_decisions(investigation_id)
    return {
        catalog_id
        for catalog_id, record in latest.items()
        if record.decision == "approved"
    }


def _execute_query(
    *,
    connector: Connector,
    conn: Any,
    query: DiagnosticQuery,
    database: str,
    investigation: InvestigationRecord,
    credentials: SqlCredentials,
    approved_ids: set[str],
    query_timeout: int | None,
) -> tuple[_Outcome, QueryEvidence]:
    """Run one query against one database, recording success or a categorized error."""
    if query.requires_approval and query.id not in approved_ids:
        return (
            _Outcome(
                QueryStatus.skipped,
                SourceStatus.unavailable_setting,
                "Not approved for this run; skipped.",
            ),
            QueryEvidence(
                query_id=query.id, database=database, status=QueryStatus.skipped
            ),
        )

    start = time.perf_counter()
    try:
        params = _parameter_values(query, investigation)
        rows = connector.query(
            conn,
            query.sql,
            params=params,
            allow_write=False,
            timeout=query_timeout,
        )
    except Exception as exc:  # noqa: BLE001 - isolation: record, never abort
        duration_ms = (time.perf_counter() - start) * 1000.0
        message = _sanitize(str(exc), credentials)
        query_status, source_status = _classify_query_error(exc)
        note = message
        return (
            _Outcome(query_status, source_status, note),
            QueryEvidence(
                query_id=query.id,
                database=database,
                status=query_status,
                duration_ms=duration_ms,
                error=message,
            ),
        )

    duration_ms = (time.perf_counter() - start) * 1000.0
    row_count = len(rows)
    truncated = row_count > MAX_EVIDENCE_ROWS
    stored = rows[:MAX_EVIDENCE_ROWS] if truncated else rows
    row_dicts = [row._asdict() for row in stored]

    if is_applicable(query, rows):
        source_status = SourceStatus.collected
        note = f"Collected {row_count} row(s) in {duration_ms:.0f} ms."
    else:
        source_status = SourceStatus.not_applicable
        note = "Server retained no evidence for this source; not applicable."

    return (
        _Outcome(QueryStatus.ok, source_status, note),
        QueryEvidence(
            query_id=query.id,
            database=database,
            status=QueryStatus.ok,
            row_count=row_count,
            duration_ms=duration_ms,
            rows=row_dicts,
            truncated=truncated,
        ),
    )


def _finalize_source(outcomes: list[_Outcome]) -> tuple[SourceStatus, str]:
    """Aggregate one query's per-database outcomes into its source status.

    ``collected`` wins whenever any target yielded applicable evidence, so a
    source is never recorded unavailable because one of several databases
    failed. Otherwise the most informative failure is reported.
    """
    if not outcomes:
        return SourceStatus.unavailable_setting, "Not collected during this run."
    for candidate in (
        SourceStatus.collected,
        SourceStatus.unavailable_permission,
        SourceStatus.unavailable_connection,
        SourceStatus.unavailable_setting,
        SourceStatus.not_applicable,
    ):
        for outcome in outcomes:
            if outcome.source_status is candidate:
                return candidate, outcome.note
    return SourceStatus.unavailable_setting, "Not collected during this run."


def run_diagnostic(
    investigation_id: int,
    *,
    investigation_repository: InvestigationRepository,
    approval_repository: ApprovalRepository,
    catalog: Catalog,
    credential_store: CredentialStore,
    connector: Connector | None = None,
    query_timeout: int | None = DEFAULT_QUERY_TIMEOUT,
    login_timeout: int = DEFAULT_LOGIN_TIMEOUT,
) -> DiagnosticRunResult:
    """Execute the approved catalog queries and return evidence plus inventory.

    Aborts early with :class:`RunNotApproved` when the investigation is unknown
    or the #10 approval gate is not satisfied, and with
    :class:`MissingCredentials` when no SQL credentials are stored for it.
    Otherwise it connects once per target database and runs every query in
    catalog order, isolating failures. An unreachable server marks every source
    unavailable and the run still completes.
    """
    active_connector: Connector = connector or default_connector  # type: ignore[assignment]

    investigation = investigation_repository.get_investigation(investigation_id)
    if investigation is None:
        raise RunNotApproved(f"no investigation with id {investigation_id}")

    if not can_start(
        investigation_id,
        investigation_repository=investigation_repository,
        approval_repository=approval_repository,
        catalog=catalog,
    ):
        raise RunNotApproved(
            f"investigation {investigation_id} is not approved: every required "
            "catalog query must be approved before a run can start"
        )

    credentials = credential_store.get(str(investigation_id))
    if credentials is None:
        raise MissingCredentials(
            f"no session credentials are stored for investigation {investigation_id}"
        )
    if not isinstance(credentials, SqlCredentials):
        raise MissingCredentials(
            f"investigation {investigation_id} has non-SQL credentials; AD/Kerberos "
            "authentication is not supported yet"
        )

    queries = catalog.all()
    approved_ids = _approved_ids(investigation_id, approval_repository)
    registry = EvidenceRegistry(catalog)
    evidence: list[QueryEvidence] = []
    outcomes: dict[str, list[_Outcome]] = {query.id: [] for query in queries}

    for database in _target_databases(investigation):
        try:
            conn = active_connector.connect(
                investigation.server_host,
                database,
                credentials.username,
                credentials.password,
                port=investigation.server_port,
                login_timeout=login_timeout,
                query_timeout=query_timeout or DEFAULT_QUERY_TIMEOUT,
                read_only=True,
            )
        except Exception as exc:  # noqa: BLE001 - record, never abort the run
            source_status = _classify_connection_error(exc)
            message = _sanitize(str(exc), credentials)
            note = f"{_CONNECTION_NOTE} {message}"
            for query in queries:
                evidence.append(
                    QueryEvidence(
                        query_id=query.id,
                        database=database,
                        status=QueryStatus.error,
                        error=message,
                    )
                )
                outcomes[query.id].append(_Outcome(QueryStatus.error, source_status, note))
            continue

        try:
            for query in queries:
                outcome, record = _execute_query(
                    connector=active_connector,
                    conn=conn,
                    query=query,
                    database=database,
                    investigation=investigation,
                    credentials=credentials,
                    approved_ids=approved_ids,
                    query_timeout=query_timeout,
                )
                evidence.append(record)
                outcomes[query.id].append(outcome)
        finally:
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001 - closing must never mask evidence
                logger.debug("failed to close diagnostic connection: %s", exc)

    for query in queries:
        status, note = _finalize_source(outcomes[query.id])
        registry.record(query.id, status, note)

    return DiagnosticRunResult(
        investigation_id=investigation_id,
        status="succeeded",
        evidence=evidence,
        sources=registry.all(),
    )
