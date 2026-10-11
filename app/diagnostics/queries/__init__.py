"""Seed diagnostic query definitions, one module per plan.md §6 evidence category.

Each submodule exposes ``QUERIES`` (a tuple of :class:`DiagnosticQuery`
entries). ``ALL_QUERIES`` aggregates them in category order; it is the base
used by :func:`app.diagnostics.catalog.build_catalog` and the checks that keep
``catalog.json`` in sync with these definitions.
"""

from . import agent_jobs, availability, blocking, slow_queries
from .agent_jobs import QUERIES as AGENT_JOB_QUERIES
from .availability import QUERIES as AVAILABILITY_QUERIES
from .blocking import QUERIES as BLOCKING_QUERIES
from .slow_queries import QUERIES as SLOW_QUERIES

ALL_QUERIES = (
    *BLOCKING_QUERIES,
    *SLOW_QUERIES,
    *AGENT_JOB_QUERIES,
    *AVAILABILITY_QUERIES,
)

__all__ = [
    "ALL_QUERIES",
    "agent_jobs",
    "availability",
    "blocking",
    "slow_queries",
]