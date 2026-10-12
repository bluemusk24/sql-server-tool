"""Request and response schemas for the investigation-create endpoint.

The allowed lookback windows are defined once here as
:data:`ALLOWED_LOOKBACK_HOURS` and exported from :mod:`app.investigations` so
that the frontend confirmation step (#23) reuses the same single source of
truth rather than restating the list.

No credential field exists on :class:`InvestigationCreate` and unknown fields
are rejected, so a password can never be accepted (and therefore never stored)
on this endpoint. Credentials arrive separately through the session-only
credential store keyed by investigation id (#5).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The lookback windows (hours) an investigation may be created with.
ALLOWED_LOOKBACK_HOURS: tuple[int, ...] = (1, 4, 8, 24)

#: Default SQL Server TCP port.
DEFAULT_SERVER_PORT = 1433

#: Characters allowed in a server host: hostname, FQDN, IPv4, bracketed IPv6,
#: or a ``host\\instance`` named instance. Anything else is malformed.
_HOST_PATTERN = re.compile(r"\A[A-Za-z0-9._:\\\-\[\]]+\Z")


class AuthType(str, Enum):
    """SQL Server authentication mode for an investigation."""

    sql = "sql"
    ad = "ad"


class ScopeKind(str, Enum):
    """Whether an investigation covers the whole instance or named databases."""

    instance = "instance"
    databases = "databases"


class InvestigationCreate(BaseModel):
    """The DBA's inputs for a new investigation (no credentials)."""

    model_config = ConfigDict(extra="forbid")

    server_host: str
    server_port: int = Field(default=DEFAULT_SERVER_PORT, ge=1, le=65535)
    auth_type: AuthType = AuthType.sql
    databases: list[str] | None = None
    lookback_hours: int

    @field_validator("server_host")
    @classmethod
    def _validate_host(cls, value: str) -> str:
        host = value.strip()
        if not host:
            raise ValueError("server_host is required")
        if len(host) > 255:
            raise ValueError("server_host must be at most 255 characters")
        if not _HOST_PATTERN.match(host):
            raise ValueError(f"server_host {value!r} is not a valid host name")
        return host

    @field_validator("databases")
    @classmethod
    def _validate_databases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError(
                "databases must be a non-empty list of database names, or null "
                "for whole-instance scope"
            )
        cleaned: list[str] = []
        for name in value:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("database names must be non-empty strings")
            cleaned.append(name.strip())
        return cleaned

    @field_validator("lookback_hours")
    @classmethod
    def _validate_lookback(cls, value: int) -> int:
        if value not in ALLOWED_LOOKBACK_HOURS:
            raise ValueError(
                f"lookback_hours must be one of {list(ALLOWED_LOOKBACK_HOURS)}, got {value!r}"
            )
        return value


class InvestigationResponse(BaseModel):
    """Fixed response shape for a created investigation."""

    id: int
    status: str
    catalog: list[dict[str, Any]]
