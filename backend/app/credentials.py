"""Session-only, in-memory credential storage.

Credentials supplied for an investigation live only in memory, keyed by the
investigation id, and are removed when the investigation ends. Nothing here is
ever serialized to disk, included in a report payload, or written to the
repository: the only non-secret view exposed is :func:`credential_summary`,
which the reporting and persistence layers use.

The store is an ordinary object rather than a process-global singleton so that
callers (and tests) can create and discard independent stores without leaking
state between them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

MASK = "****"


@dataclass(frozen=True, repr=False)
class SqlCredentials:
    """SQL Server authentication credentials (session only)."""

    username: str
    password: str = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "SqlCredentials(username=<redacted>, password=<redacted>)"


@dataclass(frozen=True)
class AdContext:
    """Reference to an external AD/Kerberos context (no secret material)."""

    keytab_path: str
    principal: str
    spn: str


Credential = SqlCredentials | AdContext


class CredentialStore:
    """In-memory credentials keyed by investigation id."""

    def __init__(self) -> None:
        self._entries: dict[str, Credential] = {}

    def store(self, investigation_id: str, credentials: Credential) -> None:
        """Store the credentials for ``investigation_id``, overwriting any previous entry."""
        self._entries[investigation_id] = credentials

    def get(self, investigation_id: str) -> Credential | None:
        """Return the live credential object reference, or ``None`` if absent."""
        return self._entries.get(investigation_id)

    def clear(self, investigation_id: str) -> None:
        """Drop the credentials for ``investigation_id``; safe to call twice."""
        self._entries.pop(investigation_id, None)

    def end_investigation(self, investigation_id: str) -> None:
        """Alias for :meth:`clear`; called when an investigation ends."""
        self.clear(investigation_id)

    def investigation_ids(self) -> tuple[str, ...]:
        """Return the ids that currently hold credentials."""
        return tuple(self._entries)

    def __contains__(self, investigation_id: object) -> bool:
        return investigation_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"CredentialStore(investigation_ids={self.investigation_ids()!r})"


def credential_summary(credentials: Credential | None) -> Mapping[str, str]:
    """Return only non-secret fields, safe for reports, persistence, and logs.

    The store hands out full credential *references* for the connection layer,
    but this summary is the only shape the report builder or repository writer
    ever sees, so neither can accidentally carry a username or password.
    """
    if credentials is None:
        return {}
    if isinstance(credentials, SqlCredentials):
        return {"auth_mode": "sql"}
    return {"auth_mode": "ad"}
