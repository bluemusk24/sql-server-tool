"""FastAPI dependencies for the investigation endpoints.

Authentication is not built yet (#30). Until it is, :func:`get_caller` is a
deliberate stub: it reads the caller's SSO issuer and actor from request
headers, falling back to a fixture identity so the endpoint is usable in tests
and local development. It does no OIDC verification and must be replaced by the
real #30 flow.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.diagnostics.catalog import Catalog
from app.investigations.repository import (
    AuditWriter,
    InvestigationRepository,
    NullAuditWriter,
    SqlAlchemyInvestigationRepository,
)

#: Fixture SSO issuer used when the caller does not supply one (pre-#30).
FIXTURE_SSO_ISSUER = "https://issuer.example.com/"

#: Fixture actor used when the caller does not supply one (pre-#30).
FIXTURE_ACTOR = "fixture-dba"


@dataclass(frozen=True)
class Caller:
    """The authenticated caller, stubbed until the #30 auth layer lands."""

    sso_issuer: str
    actor: str


def get_caller(request: Request) -> Caller:
    """Return the caller's SSO issuer and actor (stub, see module docstring)."""
    issuer = request.headers.get("X-SSO-Issuer") or FIXTURE_SSO_ISSUER
    actor = request.headers.get("X-Actor") or FIXTURE_ACTOR
    return Caller(sso_issuer=issuer, actor=actor)


def get_catalog(request: Request) -> Catalog:
    """Return the process catalog loaded by ``create_app``."""
    return request.app.state.catalog


def get_investigation_repository(request: Request) -> InvestigationRepository:
    """Return the repository, created once per app and cached on app state."""
    repository = getattr(request.app.state, "investigation_repository", None)
    if repository is None:
        repository = SqlAlchemyInvestigationRepository()
        request.app.state.investigation_repository = repository
    return repository


def get_audit_writer(request: Request) -> AuditWriter:
    """Return the audit writer; defaults to the deferring no-op (#21)."""
    return getattr(request.app.state, "audit_writer", None) or NullAuditWriter()
