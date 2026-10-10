"""Unit tests for the SQL Server connector with a faked ``pyodbc``.

These tests never need the real ODBC Driver 18: the connector imports
``pyodbc`` lazily inside :func:`connect`, and a fake module is injected into
``sys.modules`` so the code under test resolves it for connection-string
building, execute, and error mapping.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.sql.connector import (
    DEFAULT_LOGIN_TIMEOUT,
    DEFAULT_QUERY_TIMEOUT,
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

PASSWORD = "s3cret-P@ss'w0rd{9}"
HOST = "db.example.com"
DATABASE = "tickets"


class FakeDriverError(Exception):
    """Duck-typed stand-in for pyodbc.Error (state, message) args."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(state, message)
        self.state = state


class FakeCursor:
    def __init__(
        self,
        *,
        description: list[tuple] | None = None,
        rows: list[tuple] = (),
        execute_exc: Exception | None = None,
    ) -> None:
        self.description = description
        self._rows = rows
        self._execute_exc = execute_exc
        self.timeout: int | None = None
        self.executed: list[str] = []
        self.closed = False

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if self._execute_exc is not None:
            raise self._execute_exc

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def close(self) -> None:
        self.closed = True


class FakeDriverConnection:
    def __init__(self, **cursor_kwargs) -> None:
        self.timeout: int | None = None
        self._cursor_kwargs = cursor_kwargs
        self.closed = False
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(**self._cursor_kwargs)
        self.cursors.append(cursor)
        return cursor

    def close(self) -> None:
        self.closed = True


def install_fake_pyodbc(monkeypatch, *, connect_exc: Exception | None = None, **cursor_kwargs):
    """Inject a fake ``pyodbc`` module and record the connect() call."""
    calls: dict[str, object] = {}

    def fake_connect(connection_string: str, *, timeout: int | None = None):
        calls["connection_string"] = connection_string
        calls["timeout"] = timeout
        if connect_exc is not None:
            raise connect_exc
        return FakeDriverConnection(**cursor_kwargs)

    module = SimpleNamespace(connect=fake_connect, Error=FakeDriverError)
    monkeypatch.setitem(sys.modules, "pyodbc", module)
    return calls


def _success_connection(monkeypatch):
    rows = [("alice@example.com", 3, "2026-01-02"), ("bob@example.com", 7, "2026-01-04")]
    description = [
        ("email", None, None, 255, None, None),
        ("open_tickets", None, None, None, 10, 0),
        ("resolved_on", None, None, 19, None, None),
    ]
    calls = install_fake_pyodbc(monkeypatch, description=description, rows=rows)
    conn = connect(HOST, DATABASE, "sa", PASSWORD)
    return conn, calls, rows


def test_connect_returns_connection_with_fake_driver(monkeypatch):
    _success_connection(monkeypatch)


def test_success_query_returns_rows_by_name_and_index(monkeypatch):
    conn, _calls, _rows = _success_connection(monkeypatch)

    results = query(conn, "SELECT email, open_tickets FROM dbo.queue")

    assert [r["email"] for r in results] == ["alice@example.com", "bob@example.com"]
    assert [r[1] for r in results] == [3, 7]
    assert results[0][0] == "alice@example.com"
    assert results[0]["open_tickets"] == 3
    assert len(results) == 2
    assert list(results[0].field_names) == ["email", "open_tickets", "resolved_on"]
    assert results[0]._asdict()["open_tickets"] == 3


def test_query_with_no_rows_returns_empty_list(monkeypatch):
    install_fake_pyodbc(
        monkeypatch,
        description=[("email", None, None, 255, None, None)],
        rows=[],
    )
    conn = connect(HOST, DATABASE, "sa", PASSWORD)

    results = query(conn, "SELECT email FROM dbo.queue WHERE 1 = 0")

    assert results == []


def test_row_supports_iteration_and_missing_key_raises_keyerror(monkeypatch):
    conn, _calls, _rows = _success_connection(monkeypatch)

    row = query(conn, "SELECT email, open_tickets FROM dbo.queue")[0]

    assert tuple(row) == ("alice@example.com", 3, "2026-01-02")
    with pytest.raises(KeyError):
        row["nonexistent"]


def test_connection_string_contains_host_database_driver_and_intent(monkeypatch):
    _conn, calls, _rows = _success_connection(monkeypatch)

    connection_string = str(calls["connection_string"])
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in connection_string
    assert f"SERVER=tcp:{HOST},1433" in connection_string
    assert f"DATABASE={DATABASE}" in connection_string
    assert "UID=sa" in connection_string
    assert "ApplicationIntent=ReadOnly" in connection_string
    assert connection_string.endswith(";ApplicationIntent=ReadOnly")
    assert PASSWORD.replace("{", "{{").replace("}", "}}") in connection_string


def test_connection_string_omits_read_only_intent_when_disabled(monkeypatch):
    calls = install_fake_pyodbc(monkeypatch)
    connect(HOST, DATABASE, "sa", PASSWORD, read_only=False)

    connection_string = str(calls["connection_string"])
    assert "ApplicationIntent=ReadOnly" not in connection_string


def test_default_login_and_query_timeouts(monkeypatch):
    conn, calls, _rows = _success_connection(monkeypatch)

    assert calls["timeout"] == DEFAULT_LOGIN_TIMEOUT == 15
    assert conn.query_timeout == DEFAULT_QUERY_TIMEOUT == 30


def test_timeouts_can_be_overridden(monkeypatch):
    calls = install_fake_pyodbc(monkeypatch, description=[("one", None, None, None, None, None)], rows=[(1,)])
    conn = connect(HOST, DATABASE, "sa", PASSWORD, login_timeout=7, query_timeout=12)

    assert calls["timeout"] == 7

    driver_conn = conn._driver
    assert driver_conn.timeout == 12
    assert conn.query_timeout == 12

    rows = query(conn, "SELECT 1 AS one")
    assert rows[0][0] == 1
    assert driver_conn.cursors[-1].timeout == 12

    query(conn, "SELECT 1 AS one", timeout=5)
    assert driver_conn.cursors[-1].timeout == 5


def test_connection_refused_maps_to_connection_refused(monkeypatch):
    refused = FakeDriverError("08001", "[Microsoft][ODBC Driver 18 for SQL Server]Cannot open server 'x'")
    install_fake_pyodbc(monkeypatch, connect_exc=refused)

    with pytest.raises(ConnectionRefused) as excinfo:
        connect(HOST, DATABASE, "sa", PASSWORD)

    assert isinstance(excinfo.value, ConnectionError)
    assert excinfo.value.__cause__ is refused
    assert "Cannot open server" in str(excinfo.value)


def test_connection_timeout_maps_to_connection_timeout(monkeypatch):
    timed_out = FakeDriverError("HYT00", "[Microsoft][ODBC Driver 18 for SQL Server]Login timeout expired")
    install_fake_pyodbc(monkeypatch, connect_exc=timed_out)

    with pytest.raises(ConnectionTimeout) as excinfo:
        connect(HOST, DATABASE, "sa", PASSWORD)

    assert isinstance(excinfo.value, ConnectionError)
    assert excinfo.value.__cause__ is timed_out
    assert "timeout expired" in str(excinfo.value)


def test_auth_failure_maps_to_connection_auth_failed(monkeypatch):
    auth_error = FakeDriverError("28000", "[Microsoft][ODBC Driver 18 for SQL Server]Login failed for user 'sa'.")
    install_fake_pyodbc(monkeypatch, connect_exc=auth_error)

    with pytest.raises(ConnectionAuthFailed) as excinfo:
        connect(HOST, DATABASE, "sa", PASSWORD)

    assert isinstance(excinfo.value, ConnectionError)
    assert excinfo.value.__cause__ is auth_error


def test_query_permission_error_maps_to_permission_error(monkeypatch):
    denied = FakeDriverError(
        "42000",
        "The SELECT permission was denied on the object 'queue', database 'tickets', schema 'dbo'.",
    )
    calls = install_fake_pyodbc(monkeypatch, execute_exc=denied)
    conn = connect(HOST, DATABASE, "sa", PASSWORD)

    with pytest.raises(PermissionError) as excinfo:
        query(conn, "SELECT * FROM dbo.queue")

    assert excinfo.value.__cause__ is denied
    assert "permission was denied" in str(excinfo.value)
    assert isinstance(calls["connection_string"], str)


def test_query_timeout_maps_to_query_error(monkeypatch):
    timed_out = FakeDriverError("HYT00", "[Microsoft][ODBC Driver 18 for SQL Server]Query timeout expired")
    install_fake_pyodbc(monkeypatch, execute_exc=timed_out)
    conn = connect(HOST, DATABASE, "sa", PASSWORD)

    with pytest.raises(QueryError) as excinfo:
        query(conn, "SELECT COUNT(*) FROM dbo.queue")

    assert excinfo.value.__cause__ is timed_out
    assert "Query timed out" in str(excinfo.value)


def test_bad_sql_maps_to_query_error(monkeypatch):
    bad_sql = FakeDriverError("42000", "Invalid object name 'dbonope'.")
    install_fake_pyodbc(monkeypatch, execute_exc=bad_sql)
    conn = connect(HOST, DATABASE, "sa", PASSWORD)

    with pytest.raises(QueryError) as excinfo:
        query(conn, "SELECT wrong FROM definitely_not_a_table")

    assert excinfo.value.__cause__ is bad_sql
    assert "Invalid object name" in str(excinfo.value)


def test_write_statement_raises_read_only_violation(monkeypatch):
    install_fake_pyodbc(monkeypatch)
    conn = connect(HOST, DATABASE, "sa", PASSWORD)

    with pytest.raises(ReadOnlyViolation):
        query(conn, "UPDATE dbo.queue SET open_tickets = 1 WHERE id = 5")

    assert len(conn._driver.cursors) == 0


def test_write_statement_requires_explicit_allow_write(monkeypatch):
    install_fake_pyodbc(monkeypatch, description=[("id", None, None, None, None, None)], rows=[(1,)])
    conn = connect(HOST, DATABASE, "sa", PASSWORD)

    assert query(conn, "UPDATE dbo.queue SET open_tickets = 1", allow_write=True)[0][0] == 1
    assert conn._driver.cursors[-1].executed == ["UPDATE dbo.queue SET open_tickets = 1"]


def test_select_with_allow_write_false_is_allowed(monkeypatch):
    _conn, _calls, rows_expected = _success_connection(monkeypatch)

    rows = query(_conn, "SELECT email FROM dbo.queue", allow_write=False)

    assert [r["email"] for r in rows] == [rows_expected[0][0], rows_expected[1][0]]


def test_connection_without_database_is_invalid(monkeypatch):
    install_fake_pyodbc(monkeypatch)

    with pytest.raises(TypeError):
        connect(HOST, "sa", PASSWORD)
    with pytest.raises(ValueError, match="default database"):
        connect(HOST, "  ", "sa", PASSWORD)


def test_password_is_never_in_surfaced_error_messages(monkeypatch):
    leaked = FakeDriverError("08001", f"cannot open server; secret='{PASSWORD}'")
    install_fake_pyodbc(monkeypatch, connect_exc=leaked)

    with pytest.raises(ConnectionRefused) as excinfo:
        connect(HOST, DATABASE, "sa", PASSWORD)

    assert PASSWORD not in str(excinfo.value)
    assert PASSWORD not in repr(excinfo.value)
    assert "secret='****'" in str(excinfo.value)


def test_query_errors_redact_password_from_connection_secrets(monkeypatch):
    leaked = FakeDriverError("42000", f"bad query referenced password '{PASSWORD}'")
    install_fake_pyodbc(monkeypatch, execute_exc=leaked)
    conn = connect(HOST, DATABASE, "sa", PASSWORD)

    with pytest.raises(QueryError) as excinfo:
        query(conn, "SELECT * FROM dbo.queue")

    assert PASSWORD not in str(excinfo.value)


def test_error_types_are_object_identity_stable(monkeypatch):
    assert ConnectionError.__name__ == "ConnectionError"
    assert ConnectionRefused.__mro__[1] is ConnectionError
    assert ConnectionTimeout.__mro__[1] is ConnectionError
    assert ConnectionAuthFailed.__mro__[1] is ConnectionError


def test_missing_pyodbc_maps_to_connection_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyodbc", None)

    with pytest.raises(ConnectionError, match="driver unavailable") as excinfo:
        connect(HOST, DATABASE, "sa", PASSWORD)

    assert isinstance(excinfo.value.__cause__, ImportError)


def test_port_validation(monkeypatch):
    install_fake_pyodbc(monkeypatch)

    with pytest.raises(ValueError, match="port"):
        connect(HOST, DATABASE, "sa", PASSWORD, port=0)


def test_row_repr_does_not_expose_field_order_junk(monkeypatch):
    _success_connection(monkeypatch)
    row = Row(["a", "b"], (1, "x"))
    assert repr(row) == "Row(1, 'x')"
    assert row.keys() == ("a", "b")
    assert row.values() == (1, "x")