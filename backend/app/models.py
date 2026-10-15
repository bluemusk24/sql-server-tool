"""Pydantic request/response models mirroring ``openapi.yaml``.

These are the single source of truth for the HTTP contract on the server
side. Field names use snake_case to match the spec (which the frontend's
TypeScript types already expect).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ── Enums ────────────────────────────────────────────────────────────
class AuthType(str, Enum):
    sql = "sql"
    ad = "ad"


LookbackHours = Literal[1, 4, 8, 24]
Decision = Literal["approved", "rejected"]
ApprovalState = Literal["pending", "approved", "rejected"]
JobState = Literal["queued", "running", "succeeded", "failed", "canceled"]
Severity = Literal["high", "medium", "low", "none"]
ConfidenceLabel = Literal["low", "medium", "high"]
SourceStatus = Literal["collected", "partial", "unavailable"]


# ── Auth ─────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class Session(BaseModel):
    organization: str
    actor: str


# ── Health ───────────────────────────────────────────────────────────
class HealthStatus(BaseModel):
    status: str
    version: str
    # Served commit (#38): sourced from RENDER_GIT_COMMIT on Render so
    # the CI deploy gate can verify the *new* code is live, not merely
    # that some instance answers 200. Null where the runtime doesn't
    # supply it (local compose, plain uvicorn).
    commit: str | None = None


# ── Catalog ──────────────────────────────────────────────────────────
class CatalogEntry(BaseModel):
    id: str
    key: str
    title: str
    description: str
    purpose: str
    category: str
    issue_area: str
    sql: str
    sql_text: str
    params: list[str]
    requires_approval: bool
    expected_impact: str
    versions: list[str]
    min_permission: str
    read_only: bool


# ── Server host validation (#37) ────────────────────────────────────
# Mirrors `isValidHost` in the frontend: hostname, FQDN, IPv4, IPv6 —
# or the DBA-style `HOST\INSTANCE`, which the run resolves through the
# SQL Browser service. Server-side because the frontend can be bypassed.
_IPV4_RE = re.compile(
    r"^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$"
)
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_IPV6_RE = re.compile(r"^[0-9a-fA-F:]+$")
#: The `\INSTANCE` suffix: alnum-initial, then alnum / `_` / `-`.
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _valid_host_name(host: str) -> bool:
    if host == "" or len(host) > 253:
        return False
    if _IPV4_RE.fullmatch(host):
        return True
    if ":" in host:
        return _IPV6_RE.fullmatch(host) is not None
    return all(
        _HOST_LABEL_RE.fullmatch(label)
        for label in host.removesuffix(".").split(".")
    )


def valid_server_host(value: str) -> bool:
    """True for `host`, FQDN, IP, or a single-slash `HOST\\INSTANCE`."""
    host = value.strip()
    if host == "":
        return False
    slash = host.find("\\")
    if slash == -1:
        return _valid_host_name(host)
    if host.find("\\", slash + 1) != -1:
        return False  # exactly one backslash
    return _valid_host_name(host[:slash]) and bool(
        _INSTANCE_RE.fullmatch(host[slash + 1 :])
    )


# ── Investigations ───────────────────────────────────────────────────
class CreateInvestigationRequest(BaseModel):
    server_host: str = Field(min_length=1)
    server_port: int = Field(default=1433, ge=1, le=65535)
    auth_type: AuthType
    databases: list[str] | None = None
    lookback_hours: LookbackHours

    @field_validator("server_host")
    @classmethod
    def _server_host_format(cls, value: str) -> str:
        if not valid_server_host(value):
            raise ValueError(
                "Enter a valid hostname, IP address, or HOST\\INSTANCE."
            )
        return value.strip()


class InvestigationSummary(BaseModel):
    """List row: enough to show *which* SQL Server each investigation
    targets (#34) without fetching the detail."""

    id: int
    status: str
    server_host: str
    server_port: int
    auth_type: AuthType
    databases: list[str] | None = None
    lookback_hours: int
    created_at: str


class InvestigationResponse(BaseModel):
    id: int
    status: str
    server_host: str
    server_port: int
    auth_type: AuthType
    databases: list[str] | None = None
    lookback_hours: int
    created_at: str
    catalog: list[CatalogEntry]


# ── Approvals ────────────────────────────────────────────────────────
class ApprovalRequest(BaseModel):
    decisions: dict[str, Decision]
    note: str | None = None


class ApprovalQuery(BaseModel):
    catalog_id: str
    decision: Decision | None = None
    actor: str | None = None
    note: str | None = None
    created_at: str | None = None


class ApprovalResponse(BaseModel):
    investigation_id: int
    state: ApprovalState
    queries: list[ApprovalQuery]


# ── Jobs ─────────────────────────────────────────────────────────────
class RunAccepted(BaseModel):
    job_id: str


class CredentialsRequest(BaseModel):
    """Per-investigation SQL credentials (#31).

    Session-only by design (spec §6): the request model excludes the
    password from reprs, and the run path never persists either field —
    they die with the request frame after the connection closes.
    """

    username: str | None = None
    password: str | None = Field(default=None, repr=False)


class StartRunRequest(BaseModel):
    """Optional body on ``POST /investigations/{id}/run``.

    Supplying ``credentials`` switches the run to live collection against
    the investigation's instance; omitting it keeps the seeded demo path.
    """

    credentials: CredentialsRequest | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobState
    investigation_id: int | None = None
    result: Any | None = None
    error: str | None = None


# ── Report (loosely typed; the payload is assembled by the store) ────
class ReportPayload(BaseModel):
    investigation_id: int | None
    organization: str
    server_host: str
    server_port: int
    database_scope: str
    databases: list[str]
    lookback_hours: int
    investigation_time: datetime
    data_sources: list[dict[str, Any]]
    findings_by_area: list[dict[str, Any]]
    primary_cause: dict[str, Any]
    alternatives: list[dict[str, Any]]
    confidence_percent: int
    confidence_label: ConfidenceLabel
    severity: Severity
    rationale: str
    inconclusive: bool
    coverage_gaps: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    validation_checks: list[str]
    remediation: list[dict[str, Any]]
    read_only_disclaimer: str
    # #26: True for the deterministic sample the no-credentials path
    # produces — the UI labels such reports so sample data is never
    # mistaken for collected evidence.
    synthetic: bool = False


# ── Orgs ─────────────────────────────────────────────────────────────
class Organization(BaseModel):
    id: int
    name: str
    issuer: str


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1)
    issuer: str
