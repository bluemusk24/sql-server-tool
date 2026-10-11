"""Read-only diagnostic query catalog: model, registry, guards, and loaders.

The catalog is the typed, version-controlled list of every diagnostic query
the tool may run. Two guarantees make it safe to approve on the DBA review
screen (plan.md §6.4, §6.6):

* every entry is *known* read-only: the :class:`DiagnosticQuery` model only
  accepts ``read_only=True`` and fails fast otherwise, and
* the SQL itself is *verified* read-only: :func:`assert_read_only` is this
  package's own guard, on top of the connector-level guard in
  ``app.sql.connector``, and rejects any data-modifying statement word.

No entry, and no SQL, is ever built by string interpolation: :func:`query`
must use ``?`` or named parameters, and :func:`assert_no_interpolation`
rejects ``{``/``}`` (f-string/format braces) and ``||`` (concatenation
markers). There is no runtime write path for the catalog.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic.dataclasses import dataclass

#: Default version-controlled catalog data file (one JSON array of entries).
DEFAULT_CATALOG_PATH = Path(__file__).with_name("catalog.json")

#: The eight specific issue areas (groomed spec).
ISSUE_AREAS = (
    "blocking",
    "deadlocks",
    "queries",
    "cpu",
    "agent_jobs",
    "errors",
    "availability",
    "storage",
)

#: Broad evidence categories from plan.md §6 and the issue areas they contain.
EVIDENCE_CATEGORIES = {
    "blocking": ("blocking", "deadlocks"),
    "queries": ("queries", "cpu"),
    "agent_jobs": ("agent_jobs", "errors"),
    "availability": ("availability", "storage"),
}

#: Statement words that modify data or the schema and are never allowed in a
#: catalog query. ``EXEC``/``EXECUTE`` are included: diagnostics must not
#: invoke stored procedures, which may have side effects.
FORBIDDEN_STATEMENT_WORDS = frozenset(
    {
        "ALTER",
        "BACKUP",
        "CREATE",
        "DBCC",
        "DELETE",
        "DENY",
        "DROP",
        "EXEC",
        "EXECUTE",
        "GRANT",
        "INSERT",
        "INTO",
        "KILL",
        "MERGE",
        "RESTORE",
        "REVOKE",
        "SHUTDOWN",
        "TRUNCATE",
        "UPDATE",
        "XP_CMDSHELL",
        "SP_EXECUTESQL",
    }
)

_ID_PATTERN = re.compile(r"\A[a-z][a-z0-9_]*\Z")

# Strip comments, string literals (including N'...'), double-quoted
# identifiers, and bracket-quoted identifiers so words inside them cannot trip
# the read-only guard.
_SQL_SCRUB_RE = re.compile(
    r"--[^\r\n]*"
    r"|/\*.*?\*/"
    r"|N?'(?:''|[^'])*'"
    r'|"(?:""|[^"])*"'
    r"|\[[^\]]*\]",
    re.DOTALL | re.IGNORECASE,
)

_BRACE_RE = re.compile(r"[{}]")
_CONCAT_MARKER = "||"


class CatalogError(Exception):
    """Base class for catalog problems."""


class CatalogValidationError(CatalogError):
    """An entry or the catalog as a whole failed validation."""


class DuplicateQueryError(CatalogError):
    """A query id/key was registered more than once."""


class ReadOnlyQueryError(CatalogError):
    """Catalog SQL contains a data-modifying or non-query statement."""


def assert_read_only(sql: str, *, query_id: str = "") -> None:
    """Reject catalog SQL that contains any data-modifying statement word.

    Strips comments, string literals, and quoted identifiers before scanning,
    so a harmless ``[insert_date]`` column or a ``'exact-text'`` search string
    never triggers a false positive. Raises :class:`ReadOnlyQueryError` with a
    clear message naming the offending words (and the query id when known).
    """
    if not isinstance(sql, str) or not sql.strip():
        label = f" for query {query_id!r}" if query_id else ""
        raise ReadOnlyQueryError(f"catalog SQL{label} must be a non-empty string")

    scrubbed = _SQL_SCRUB_RE.sub(" ", sql)
    present = {word.upper() for word in re.findall(r"[A-Za-z_]+", scrubbed)}
    found = sorted(FORBIDDEN_STATEMENT_WORDS & present)
    if found:
        label = f" for query {query_id!r}" if query_id else ""
        joined = ", ".join(found)
        raise ReadOnlyQueryError(
            f"catalog SQL{label} contains data-modifying or non-query statement word(s): {joined}"
        )


def assert_no_interpolation(sql: str, *, query_id: str = "") -> None:
    """Reject catalog SQL built via f-string/format braces or concatenation.

    Parameters must be ``?`` placeholders or named parameters; catalog SQL is
    never assembled from strings. ``{``/``}`` are f-string/format markers and
    ``||`` is an ANSI concatenation marker (not used by T-SQL).
    """
    label = f" for query {query_id!r}" if query_id else ""
    if _BRACE_RE.search(sql):
        raise CatalogValidationError(
            f"catalog SQL{label} must not contain f-string/format braces; use ? or named parameters"
        )
    if _CONCAT_MARKER in sql:
        raise CatalogValidationError(
            f"catalog SQL{label} must not contain concatenation markers (||); use ? or named parameters"
        )


@dataclass(frozen=True)
class DiagnosticQuery:
    """One entry in the diagnostic query catalog.

    The stored fields follow the source data format: ``sql`` for the SQL text
    and ``description`` for the DBA-facing purpose. Convenience aliases expose
    the API names used by the review screen: ``key`` -> ``id``,
    ``sql_text`` -> ``sql``, ``purpose`` -> ``description``.

    Validation runs on construction (fails fast): the id must be a slug, the
    SQL must be non-empty and start with ``SELECT``, the category/issue_area
    must be known and consistent, and the SQL must pass both guards.
    """

    id: str
    title: str
    description: str
    category: str
    issue_area: str
    sql: str
    expected_impact: str
    versions: tuple[str, ...]
    min_permission: str
    read_only: bool
    params: tuple[str, ...] = ()
    requires_approval: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID_PATTERN.match(self.id):
            raise CatalogValidationError(f"query id {self.id!r} is not a valid slug (a-z, 0-9, _)")
        if not isinstance(self.title, str) or not self.title.strip():
            raise CatalogValidationError(f"query {self.id!r}: title is required")
        if not isinstance(self.description, str) or not self.description.strip():
            raise CatalogValidationError(f"query {self.id!r}: description is required")
        if not isinstance(self.expected_impact, str) or not self.expected_impact.strip():
            raise CatalogValidationError(f"query {self.id!r}: expected_impact is required")
        if not isinstance(self.min_permission, str) or not self.min_permission.strip():
            raise CatalogValidationError(f"query {self.id!r}: min_permission is required")
        if not isinstance(self.requires_approval, bool):
            raise CatalogValidationError(f"query {self.id!r}: requires_approval must be a bool")
        if not isinstance(self.read_only, bool):
            raise CatalogValidationError(f"query {self.id!r}: read_only must be a bool")
        if self.category not in EVIDENCE_CATEGORIES:
            raise CatalogValidationError(
                f"query {self.id!r}: unknown category {self.category!r}; expected one of {sorted(EVIDENCE_CATEGORIES)}"
            )
        if self.issue_area not in ISSUE_AREAS:
            raise CatalogValidationError(
                f"query {self.id!r}: unknown issue_area {self.issue_area!r}; expected one of {sorted(ISSUE_AREAS)}"
            )
        if self.issue_area not in EVIDENCE_CATEGORIES[self.category]:
            raise CatalogValidationError(
                f"query {self.id!r}: issue_area {self.issue_area!r} does not belong to category {self.category!r}"
            )
        if not isinstance(self.params, tuple) or not all(
            isinstance(name, str) and bool(name) for name in self.params
        ):
            raise CatalogValidationError(
                f"query {self.id!r}: params must be a tuple of non-empty parameter names"
            )
        if not isinstance(self.versions, tuple) or not self.versions or not all(
            isinstance(version, str) and bool(version) for version in self.versions
        ):
            raise CatalogValidationError(
                f"query {self.id!r}: versions must be a non-empty tuple of version targets"
            )
        if not isinstance(self.sql, str) or not self.sql.strip():
            raise CatalogValidationError(f"query {self.id!r}: sql is required")
        if not self.sql.lstrip().upper().startswith("SELECT"):
            raise CatalogValidationError(f"query {self.id!r}: sql must start with SELECT")
        if not self.read_only:
            raise CatalogValidationError(
                f"query {self.id!r}: read_only must be True (diagnostics are read-only)"
            )
        assert_read_only(self.sql, query_id=self.id)
        assert_no_interpolation(self.sql, query_id=self.id)

    @property
    def key(self) -> str:
        """Registry key: identical to ``id``."""
        return self.id

    @property
    def sql_text(self) -> str:
        """The SQL text, verbatim (API alias for the review screen)."""
        return self.sql

    @property
    def purpose(self) -> str:
        """DBA-facing purpose (API alias for the review screen)."""
        return self.description

    def to_dict(self) -> dict[str, Any]:
        """All fields plus the API aliases, with ``sql_text`` echoed verbatim."""
        return {
            "id": self.id,
            "key": self.id,
            "title": self.title,
            "description": self.description,
            "purpose": self.description,
            "category": self.category,
            "issue_area": self.issue_area,
            "sql": self.sql,
            "sql_text": self.sql,
            "params": list(self.params),
            "requires_approval": self.requires_approval,
            "expected_impact": self.expected_impact,
            "versions": list(self.versions),
            "min_permission": self.min_permission,
            "read_only": self.read_only,
        }


class Catalog:
    """Ordered registry of catalog queries, keyed by query id.

    Registers queries in order, rejects duplicate keys, and offers the full
    list plus an audit view that reports every query together with its
    read-only and approval flags.
    """

    def __init__(self) -> None:
        self._queries: dict[str, DiagnosticQuery] = {}

    def register(self, query: DiagnosticQuery) -> None:
        """Add ``query`` to the registry, rejecting duplicate ids/keys."""
        if query.id in self._queries:
            raise DuplicateQueryError(f"duplicate catalog query id {query.id!r}")
        self._queries[query.id] = query

    def all(self) -> list[DiagnosticQuery]:
        """The full list of queries, in registration order."""
        return list(self._queries.values())

    def get(self, key: str) -> DiagnosticQuery | None:
        """Return the query registered under ``key``, or ``None``."""
        return self._queries.get(key)

    def audit(self) -> list[dict[str, object]]:
        """Report every query together with its read-only and approval flags."""
        return [
            {
                "id": query.id,
                "category": query.category,
                "issue_area": query.issue_area,
                "requires_approval": query.requires_approval,
                "read_only": query.read_only,
            }
            for query in self._queries.values()
        ]

    def __len__(self) -> int:
        return len(self._queries)

    def __contains__(self, key: object) -> bool:
        return key in self._queries

    def __iter__(self) -> Iterator[DiagnosticQuery]:
        return iter(self._queries.values())


def load_catalog(path: Path | str | None = None) -> Catalog:
    """Load and validate the catalog from version-controlled ``catalog.json``.

    ``path`` defaults to ``DEFAULT_CATALOG_PATH`` next to this module. Each
    entry is validated by the model (missing required fields and ``read_only``
    false fail fast with the entry id), and duplicate ids are rejected. There
    is no runtime write path: this function only reads.
    """
    catalog_path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"cannot read catalog file {catalog_path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog file {catalog_path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise CatalogError(f"catalog file {catalog_path} must contain a JSON array of entries")

    catalog = Catalog()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise CatalogValidationError(f"catalog entry at index {index} is not an object")
        try:
            query = DiagnosticQuery(**item)
        except ValidationError as exc:
            entry_id = item.get("id", f"index {index}")
            raise CatalogValidationError(
                f"catalog entry {entry_id!r} is missing or has invalid required fields: {exc}"
            ) from exc
        try:
            catalog.register(query)
        except DuplicateQueryError as exc:
            raise CatalogValidationError(
                f"catalog file {catalog_path}: duplicate id {query.id!r}"
            ) from exc
    return catalog


def build_catalog() -> Catalog:
    """Build the registry from the Python definitions in ``app.diagnostics.queries``.

    Used by tests (and by the JSON generation/consistency check) so the
    committed ``catalog.json`` and the code-seeded definitions cannot drift
    apart.
    """
    from app.diagnostics.queries import ALL_QUERIES

    catalog = Catalog()
    for query in ALL_QUERIES:
        catalog.register(query)
    return catalog