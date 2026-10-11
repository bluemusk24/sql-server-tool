"""Read-only diagnostic query catalog.

The catalog is the approved, version-controlled source of every diagnostic
query the tool is allowed to run. Diagnostics are read-only by design
(plan.md §1, §6): every entry carries ``read_only=True``, validation refuses
anything else at load time, and the SQL itself is additionally guarded by
:func:`app.diagnostics.catalog.assert_read_only`, independent of the
connector-level guard in ``app.sql.connector``.
"""

from app.diagnostics.catalog import (
    EVIDENCE_CATEGORIES,
    ISSUE_AREAS,
    Catalog,
    CatalogError,
    CatalogValidationError,
    DiagnosticQuery,
    DuplicateQueryError,
    ReadOnlyQueryError,
    assert_no_interpolation,
    assert_read_only,
    build_catalog,
    load_catalog,
)

__all__ = [
    "EVIDENCE_CATEGORIES",
    "ISSUE_AREAS",
    "Catalog",
    "CatalogError",
    "CatalogValidationError",
    "DiagnosticQuery",
    "DuplicateQueryError",
    "ReadOnlyQueryError",
    "assert_no_interpolation",
    "assert_read_only",
    "build_catalog",
    "load_catalog",
]