"""Slow and CPU-consuming query evidence (plan.md §6 area 2)."""

from app.diagnostics.catalog import DiagnosticQuery

QUERIES = (
    DiagnosticQuery(
        id="top_elapsed_queries",
        title="Top resource-consuming queries",
        description=(
            "Completed queries in the lookback window with their cumulative CPU, "
            "elapsed, logical-read, and logical-write totals plus the SQL text; the "
            "#14 collector ranks the top five per resource category regardless of "
            "runtime, so no query is dropped for being fast."
        ),
        category="queries",
        issue_area="queries",
        sql=(
            "SELECT qs.total_worker_time,\n"
            "       qs.total_elapsed_time,\n"
            "       qs.total_logical_reads,\n"
            "       qs.total_logical_writes,\n"
            "       qs.execution_count,\n"
            "       st.text AS sql_text\n"
            "FROM sys.dm_exec_query_stats AS qs\n"
            "CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) AS st\n"
            "WHERE qs.last_execution_time >= DATEADD(hour, ?, SYSDATETIME())"
        ),
        params=("lookback_hours",),
        requires_approval=True,
        expected_impact="Low; aggregated DMV reads plus SQL text handles; filtered to the window.",
        versions=("2008+",),
        min_permission="VIEW SERVER STATE",
        read_only=True,
    ),
    DiagnosticQuery(
        id="top_cpu_sessions",
        title="Sessions consuming the most CPU",
        description=(
            "Active sessions ordered by current CPU time, with their running SQL, "
            "for spotting high-CPU demand on the instance."
        ),
        category="queries",
        issue_area="cpu",
        sql=(
            "SELECT TOP (20) s.session_id,\n"
            "       s.login_name,\n"
            "       r.cpu_time,\n"
            "       r.total_elapsed_time,\n"
            "       st.text AS sql_text\n"
            "FROM sys.dm_exec_sessions AS s\n"
            "LEFT JOIN sys.dm_exec_requests AS r ON r.session_id = s.session_id\n"
            "OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS st\n"
            "WHERE s.session_id > 50\n"
            "  AND r.cpu_time IS NOT NULL\n"
            "ORDER BY r.cpu_time DESC"
        ),
        params=(),
        requires_approval=True,
        expected_impact="Low; bounded DMV reads and SQL text handles; session CPU signal only.",
        versions=("2008+",),
        min_permission="VIEW SERVER STATE",
        read_only=True,
    ),
)