"""Read-only SQL Server access via ``pyodbc`` (ODBC Driver 18).

The connector is deliberately thin: it wraps the ODBC driver, builds the
connection string from plain host/port/database/username/password values
(the shape the credential store hands out), and maps raw ODBC failures onto
categorized exceptions so callers never see a driver exception. Diagnostics
are read-only: connections advertise ``ApplicationIntent=ReadOnly`` and
:func:`query` refuses anything that is not a ``SELECT`` unless the caller
explicitly passes ``allow_write=True``.

``pyodbc`` is imported lazily inside :func:`connect` so that importing this
module (and running the unit tests, which inject a fake driver) never requires
the ODBC Driver 18 shared library to be installed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Self

MASK = "****"

DRIVER_NAME = "ODBC Driver 18 for SQL Server"
DEFAULT_LOGIN_TIMEOUT = 15
DEFAULT_QUERY_TIMEOUT = 30

_SELECT_PREFIX = re.compile(
    r"\A\s*(?:--[^\r\n]*(?:\r?\n|$)\s*|/\*.*?\*/\s*)*SELECT\b",
    re.IGNORECASE | re.DOTALL,
)


class ConnectionError(Exception):
    """Base class for connection failures (refused, timeout, auth)."""


class ConnectionRefused(ConnectionError):
    """The SQL Server could not be reached or refused the connection."""


class ConnectionTimeout(ConnectionError):
    """The connection attempt exceeded the login timeout."""


class ConnectionAuthFailed(ConnectionError):
    """SQL authentication was rejected by the server."""


class PermissionError(Exception):
    """The login or the query was denied by the server (permissions)."""


class QueryError(Exception):
    """A query could not be executed (bad SQL, timeout, or other failure)."""


class ReadOnlyViolation(Exception):
    """A non-SELECT statement was attempted without ``allow_write=True``."""


class Row:
    """One result row: values are accessible by column name and by index.

    ``fields`` holds the column names in query order; ``values`` holds the
    values. ``row["name"]`` and ``row[0]`` both work, as does iteration and
    :meth:`_asdict`.
    """

    __slots__ = ("_fields", "_values")

    def __init__(
        self,
        fields: Iterable[str],
        values: Iterable[object],
    ) -> None:
        self._fields = tuple(fields)
        self._values = tuple(values)

    @property
    def field_names(self) -> tuple[str, ...]:
        """The column names for this row, in query order."""
        return self._fields

    def __getitem__(self, key: int | slice | str) -> object:
        if isinstance(key, (int, slice)):
            return self._values[key]
        if isinstance(key, str):
            try:
                index = self._fields.index(key)
            except ValueError:
                raise KeyError(key) from None
            return self._values[index]
        raise TypeError(f"row indices must be integers, slices, or strings, not {type(key).__name__}")

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        rendered = ", ".join(repr(value) for value in self._values)
        return f"Row({rendered})"

    def _asdict(self) -> dict[str, object]:
        return dict(zip(self._fields, self._values))

    def keys(self) -> tuple[str, ...]:
        return self._fields

    def values(self) -> tuple[object, ...]:
        return self._values


class Connection:
    """Thin wrapper around a ``pyodbc`` connection.

    Holds the per-connection default query timeout and the secrets that must be
    redacted from any surfaced error message. The wrapped driver connection is
    private; callers only use :class:`Connection` with :func:`query`.
    """

    __slots__ = ("_driver", "_secrets", "query_timeout")

    def __init__(
        self,
        driver_connection: object,
        *,
        query_timeout: int,
        secrets: Iterable[str] = (),
    ) -> None:
        self._driver = driver_connection
        self._secrets = tuple(secrets)
        self.query_timeout = query_timeout

    def close(self) -> None:
        """Close the underlying driver connection."""
        self._driver.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _quote(value: object) -> str:
    """Wrap ``value`` in ODBC braces, doubling internal braces literally."""
    return "{" + str(value).replace("{", "{{").replace("}", "}}") + "}"


def _build_connection_string(
    *,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    read_only: bool,
) -> str:
    parts = [
        f"DRIVER={_quote(DRIVER_NAME)}",
        f"SERVER=tcp:{host},{port}",
        f"DATABASE={database}",
        f"UID={username}",
        f"PWD={_quote(password)}",
    ]
    if read_only:
        parts.append("ApplicationIntent=ReadOnly")
    return ";".join(parts)


def _redact(message: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if secret:
            message = message.replace(secret, MASK)
    return message


def _sqlstate(exc: Exception) -> str | None:
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], str) and len(args[0]) == 5:
        return args[0].upper()
    return None


def _odbc_message(exc: Exception) -> str:
    args = getattr(exc, "args", None)
    if args:
        parts = [str(part) for part in args if str(part)]
        if parts:
            return " ".join(parts)
    return str(exc)


def _map_connect_error(exc: Exception) -> Exception:
    """Categorize a failure raised while opening the connection."""
    state = _sqlstate(exc)
    message = _odbc_message(exc)
    lowered = message.lower()

    if state == "HYT00" or "timeout" in lowered or "timed out" in lowered:
        return ConnectionTimeout(f"Could not connect: {message}")
    if "cannot open database" in lowered or "cannot open user default database" in lowered:
        return PermissionError(f"Login denied for the requested database: {message}")
    if state == "28000" or "login failed" in lowered or "authentication failed" in lowered:
        return ConnectionAuthFailed(f"SQL authentication failed: {message}")
    if state == "08004" or "rejected the connection" in lowered:
        return ConnectionAuthFailed(f"SQL authentication failed: {message}")
    if state == "08001" or "refused" in lowered or "cannot open server" in lowered:
        return ConnectionRefused(f"Could not connect to the SQL Server: {message}")
    return ConnectionRefused(f"Could not connect to the SQL Server: {message}")


def _map_query_error(exc: Exception) -> Exception:
    """Categorize a failure raised while preparing or running a query."""
    state = _sqlstate(exc)
    message = _odbc_message(exc)
    lowered = message.lower()

    if "permission" in lowered and ("denied" in lowered or "deny " in lowered):
        return PermissionError(f"Permission denied while running the query: {message}")
    if state == "HYT00" or "query timeout" in lowered:
        return QueryError(f"Query timed out: {message}")
    return QueryError(f"Query failed: {message}")


def connect(
    host: str,
    database: str,
    username: str,
    password: str,
    *,
    port: int = 1433,
    login_timeout: int = DEFAULT_LOGIN_TIMEOUT,
    query_timeout: int = DEFAULT_QUERY_TIMEOUT,
    read_only: bool = True,
) -> Connection:
    """Open a read-only SQL Server connection with SQL authentication.

    ``host``, ``database``, ``username``, and ``password`` are required (the
    shape the credential store hands back); ``database`` is the default
    database the connection opens against. Login timeout defaults to
    ``DEFAULT_LOGIN_TIMEOUT`` (15s) and the query timeout to
    ``DEFAULT_QUERY_TIMEOUT`` (30s); both can be overridden per call.

    Returns a :class:`Connection` wrapping the driver connection.
    """
    if not host or not host.strip():
        raise ValueError("host is required")
    if not database or not database.strip():
        raise ValueError("a default database is required")
    if not username:
        raise ValueError("username is required")
    if password is None:
        raise ValueError("password is required")
    if port < 1 or port > 65535:
        raise ValueError("port must be between 1 and 65535")

    connection_string = _build_connection_string(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        read_only=read_only,
    )

    try:
        import pyodbc
    except ImportError as exc:
        raise ConnectionError(
            f"{DRIVER_NAME} driver unavailable on this system (pyodbc could not be imported)"
        ) from exc

    try:
        driver_connection = pyodbc.connect(connection_string, timeout=login_timeout)
    except Exception as exc:
        mapped = _map_connect_error(exc)
        raise type(mapped)(_redact(str(mapped), (password,))) from exc

    driver_connection.timeout = query_timeout
    return Connection(
        driver_connection,
        query_timeout=query_timeout,
        secrets=(password,),
    )


def query(
    conn: Connection,
    sql: str,
    *,
    params: Sequence[object] | None = None,
    allow_write: bool = False,
    timeout: int | None = None,
) -> list[Row]:
    """Run ``sql`` on ``conn`` and return the rows as plain :class:`Row` objects.

    Only ``SELECT`` statements are allowed unless ``allow_write=True`` is
    passed explicitly (the diagnostics engine never does). ``timeout``
    overrides the connection's default query timeout for this call only.

    ``params`` binds the catalog's positional ``?`` placeholders (an ordered
    sequence); values are passed straight to the driver, never interpolated
    into the SQL text. Passing ``None`` or an empty sequence leaves the
    statement unparameterised.
    """
    if not _SELECT_PREFIX.match(sql) and not allow_write:
        raise ReadOnlyViolation(
            "Only SELECT statements are allowed; pass allow_write=True to execute non-SELECT statements"
        )

    cursor = conn._driver.cursor()
    try:
        cursor.timeout = conn.query_timeout if timeout is None else timeout
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        description = cursor.description or ()
        fields = tuple(column[0] for column in description)
        rows = [Row(fields, values) for values in cursor.fetchall()]
    except Exception as exc:
        cursor.close()
        mapped = _map_query_error(exc)
        raise type(mapped)(_redact(str(mapped), conn._secrets)) from exc
    cursor.close()
    return rows