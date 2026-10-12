"""Database availability and storage pressure evidence (plan.md §6 area 4)."""

from app.diagnostics.catalog import DiagnosticQuery

QUERIES = (
    DiagnosticQuery(
        id="non_online_databases",
        title="Databases not ONLINE",
        description=(
            "Databases that are offline, suspect, recovering, or otherwise not "
            "available, with their state, so the DBA can flag availability gaps."
        ),
        category="availability",
        issue_area="availability",
        sql=(
            "SELECT d.name AS database_name,\n"
            "       d.state,\n"
            "       d.state_desc,\n"
            "       d.user_access_desc,\n"
            "       d.is_in_standby,\n"
            "       d.recovery_model_desc\n"
            "FROM sys.databases AS d\n"
            "WHERE d.state_desc <> N'ONLINE'\n"
            "   OR d.is_in_standby = 1\n"
            "ORDER BY d.name"
        ),
        params=(),
        requires_approval=True,
        expected_impact="Minimal; single catalog-table read.",
        versions=("2008+",),
        min_permission="VIEW ANY DATABASE",
        read_only=True,
    ),
    DiagnosticQuery(
        id="database_volume_space",
        title="Free space on database file volumes",
        description=(
            "Free space percentage per database file's volume, lowest first, so "
            "the DBA can flag storage pressure below 10% free."
        ),
        category="availability",
        issue_area="storage",
        sql=(
            "SELECT d.name AS database_name,\n"
            "       mf.physical_name,\n"
            "       vs.logical_volume_name,\n"
            "       vs.total_bytes,\n"
            "       vs.available_bytes,\n"
            "       CAST(ROUND(100.0 * vs.available_bytes / vs.total_bytes, 2) AS decimal(8, 2)) AS free_percent\n"
            "FROM sys.master_files AS mf\n"
            "JOIN sys.databases AS d ON d.database_id = mf.database_id\n"
            "CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) AS vs\n"
            "ORDER BY free_percent ASC"
        ),
        params=(),
        requires_approval=True,
        expected_impact="Low; one volume-stat call per database file; no writes.",
        versions=("2008+",),
        min_permission="VIEW ANY DATABASE",
        read_only=True,
    ),
)