"""Fixed rule thresholds (#17) -- the single source of truth.

Every number a rule compares against lives here exactly once; the rule modules
and their tests both import these names, so a threshold cannot drift. The
thresholds are deliberately not tunable in the POC (plan.md §4): the same
built-in numbers are used for every instance and there is no cross-investigation
baseline.

Where a threshold already exists as a named constant in a #13-#16 collector it
is imported and re-exported here rather than written a second time, so the
number itself appears once in the codebase.
"""

from __future__ import annotations

from app.evidence.errors import HIGH_SEVERITY, MEDIUM_SEVERITY
from app.evidence.storage import LOW_FREE_SPACE_RATIO

# Blocking (plan.md §4 area 1): a strictly positive wait is a signal; it is
# ``medium`` at or above 5s and ``high`` at or above 30s. A zero or unknown wait
# is not a signal.
BLOCKING_HIGH_MS = 30_000.0
BLOCKING_MEDIUM_MS = 5_000.0

# Queries (plan.md §4 area 2): only the rank-1 CPU or duration query is
# considered, and its severity is chosen by the cumulative metric magnitude
# (``sys.dm_exec_query_stats`` counters are microseconds).
QUERY_TOP_RANK = 1
QUERY_HIGH_METRIC_US = 60_000_000
QUERY_MEDIUM_METRIC_US = 10_000_000

# SQL Server errors (plan.md §4 area 3): severity >= 17 is ``high``, 11-16 is
# ``medium``, everything lower (and unknown) is ``low``. Shared with the #15
# collector so 17 and 11 each appear once.
ERROR_SEVERITY_HIGH = HIGH_SEVERITY
ERROR_SEVERITY_MEDIUM = MEDIUM_SEVERITY

# Storage (plan.md §4 area 4): below 10% free is ``high``; the documented extra
# band below 20% free is ``low``. The 10% value is shared with the #16
# collector's low-free-space indicator.
STORAGE_FREE_HIGH_RATIO = LOW_FREE_SPACE_RATIO
STORAGE_FREE_LOW_RATIO = 0.20
