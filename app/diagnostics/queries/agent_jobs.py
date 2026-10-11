"""SQL Agent job failures and SQL Server error-log evidence (plan.md §6 area 3)."""

from app.diagnostics.catalog import DiagnosticQuery

QUERIES = (
    DiagnosticQuery(
        id="failed_agent_jobs",
        title="Failed SQL Agent job steps",
        description=(
            "Job steps flagged as failed in the selected window, so the DBA can see "
            "repeated or recent job failures at a glance."
        ),
        category="agent_jobs",
        issue_area="agent_jobs",
        sql=(
            "SELECT sj.name AS job_name,\n"
            "       jh.step_id,\n"
            "       jh.step_name,\n"
            "       jh.run_status,\n"
            "       jh.run_date,\n"
            "       jh.run_time,\n"
            "       jh.message\n"
            "FROM msdb.dbo.sysjobs AS sj\n"
            "JOIN msdb.dbo.sysjobhistory AS jh ON jh.job_id = sj.job_id\n"
            "WHERE jh.run_status = 0\n"
            "  AND jh.run_date >= ?\n"
            "  AND jh.run_time >= ?\n"
            "ORDER BY jh.run_date DESC, jh.run_time DESC"
        ),
        params=("run_date_from", "run_time_from"),
        requires_approval=True,
        expected_impact="Low; reads msdb history tables filtered to the window.",
        versions=("2008+",),
        min_permission="SQLAgentReaderRole (msdb)",
        read_only=True,
    ),
    DiagnosticQuery(
        id="sql_server_error_log",
        title="SQL Server error log entries",
        description=(
            "Error log lines matching a filter, newest first, so the DBA can flag "
            "errors raised in the selected window."
        ),
        category="agent_jobs",
        issue_area="errors",
        sql=(
            "SELECT xe.log_date,\n"
            "       xe.process_info,\n"
            "       xe.text AS error_text\n"
            "FROM sys.dm_os_error_log AS xe\n"
            "WHERE xe.text LIKE N'%' + ? + N'%'\n"
            "ORDER BY xe.log_date DESC"
        ),
        params=("error_filter",),
        requires_approval=True,
        expected_impact="Low; reads the in-memory error log view filtered by text.",
        versions=("2016+",),
        min_permission="VIEW SERVER STATE",
        read_only=True,
    ),
)