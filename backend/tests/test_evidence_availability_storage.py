"""Cross-cutting test for #16: unavailable *and* low-space is two rows (#16).

The groomed spec requires a database that is both unavailable and low on space
to surface both findings as separate rows, never merged. The availability and
storage collectors own separate sources and produce separate
:class:`~app.evidence.models.EvidenceRow` records; this test pins that the two
findings coexist for the same database name.
"""

from __future__ import annotations

from app.diagnostics.catalog import build_catalog
from app.evidence.availability import SOURCE_ID as AVAILABILITY_SOURCE
from app.evidence.availability import AvailabilityCollector
from app.evidence.collectors import SourceEvidence
from app.evidence.models import SourceStatus
from app.evidence.registry import EvidenceRegistry
from app.evidence.storage import SOURCE_ID as STORAGE_SOURCE
from app.evidence.storage import DatabaseVolumeCollector
from app.sql.connector import Row


def _row(**values) -> Row:
    return Row(tuple(values), tuple(values.values()))


def test_database_unavailable_and_low_space_surfaces_both_rows_separately():
    registry = EvidenceRegistry(build_catalog())

    availability_rows = [
        _row(
            database_name="Orders",
            state=6,
            state_desc="OFFLINE",
            user_access_desc="MULTI_USER",
            is_in_standby=0,
            recovery_model_desc="FULL",
        )
    ]
    storage_rows = [
        _row(
            database_name="Orders",
            physical_name="O:\\data\\orders.mdf",
            logical_volume_name="O",
            total_bytes=1000,
            available_bytes=50,
            free_percent=5.0,
        )
    ]

    availability_findings = AvailabilityCollector().collect(
        SourceEvidence(source_id=AVAILABILITY_SOURCE, rows=availability_rows), registry
    )
    storage_findings = DatabaseVolumeCollector().collect(
        SourceEvidence(source_id=STORAGE_SOURCE, rows=storage_rows), registry
    )

    combined = [*availability_findings, *storage_findings]
    assert len(combined) == 2
    assert {finding.area for finding in combined} == {"availability", "storage"}
    assert all(finding.details["database_name"] == "Orders" for finding in combined)
    assert registry.get(AVAILABILITY_SOURCE).status is SourceStatus.collected
    assert registry.get(STORAGE_SOURCE).status is SourceStatus.collected
