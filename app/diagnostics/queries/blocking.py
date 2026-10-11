"""Blocking and deadlock evidence (plan.md §6 area 1)."""

from app.diagnostics.catalog import DiagnosticQuery

QUERIES = (
    DiagnosticQuery(
        id="blocking_current_chains",
        title="Current blocking chains",
        description=(
            "Sessions currently blocked by another session, with wait type and wait "
            "time, so the DBA can see who holds whom right now."
        ),
        category="blocking",
        issue_area="blocking",
        sql=(
            "SELECT s.session_id,\n"
            "       r.blocking_session_id,\n"
            "       s.login_name,\n"
            "       r.wait_type,\n"
            "       r.wait_time,\n"
            "       r.status,\n"
            "       r.command\n"
            "FROM sys.dm_exec_sessions AS s\n"
            "JOIN sys.dm_exec_requests AS r ON r.session_id = s.session_id\n"
            "WHERE r.blocking_session_id <> 0\n"
            "ORDER BY r.wait_time DESC"
        ),
        params=(),
        requires_approval=True,
        expected_impact="Minimal; short reads over DMVs only, no locks or waits are introduced.",
        versions=("2008+",),
        min_permission="VIEW SERVER STATE",
        read_only=True,
    ),
    DiagnosticQuery(
        id="deadlock_system_health",
        title="Deadlocks from the system_health ring buffer",
        description=(
            "Recent deadlock graphs retained by the system_health extended-events "
            "session, parsed from the ring buffer data as XML."
        ),
        category="blocking",
        issue_area="deadlocks",
        sql=(
            "SELECT xrb.timestamp AS ring_timestamp,\n"
            "       CAST(xrb.ring_buffer_data AS xml) AS ring_data\n"
            "FROM sys.dm_os_ring_buffers AS xrb\n"
            "WHERE xrb.ring_buffer_type = N'RING_BUFFER_SYSTEM_HEALTH'\n"
            "ORDER BY xrb.timestamp DESC"
        ),
        params=(),
        requires_approval=True,
        expected_impact="Minimal; reads ring buffer data; deadlock graphs may be large.",
        versions=("2012+",),
        min_permission="VIEW SERVER STATE",
        read_only=True,
    ),
)