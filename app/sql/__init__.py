"""SQL Server access: read-only connection via pyodbc and categorized errors.

This package wraps the ODBC Driver 18 for SQL Server. Diagnostics are always
read-only; see :mod:`app.sql.connector` for the connector contract.
"""

from app.sql.connector import (
    Connection,
    ConnectionAuthFailed,
    ConnectionError,
    ConnectionRefused,
    ConnectionTimeout,
    PermissionError,
    QueryError,
    ReadOnlyViolation,
    Row,
    connect,
    query,
)

__all__ = [
    "Connection",
    "ConnectionAuthFailed",
    "ConnectionError",
    "ConnectionRefused",
    "ConnectionTimeout",
    "PermissionError",
    "QueryError",
    "ReadOnlyViolation",
    "Row",
    "connect",
    "query",
]