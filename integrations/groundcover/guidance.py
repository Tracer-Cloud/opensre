"""gcQL query guidance and defaults for groundcover tools."""

from __future__ import annotations

# Default row cap embedded in seed/example queries. The model can override it,
# but every gcQL example must carry an explicit ``| limit N``.
DEFAULT_ROW_LIMIT = 50

# Default seed queries: cheap, recent, bounded. Used when the alert payload does
# not carry an explicit query. gcQL leads with the filter directly (no `| filter`
# pipe — that pipe is for post-aggregation conditions on computed aliases), and
# projects with `| fields ...` rather than a bare select-all: the public endpoint
# rejects raw `<filter> | limit N` row pulls that return all columns.
DEFAULT_LOGS_QUERY = "level:error | fields _time, workload, instance, content | limit 50"
DEFAULT_TRACES_QUERY = (
    "status:error | fields _time, workload, span_name, status_code, duration_seconds | limit 50"
)

# Reusable query-guidance preamble embedded in every gcQL tool description.
# This is deliberately redundant with the upstream gcQL reference so OpenSRE
# ships efficient query behavior even when the model never calls the reference.
GCQL_GUIDANCE = (
    "Time range is controlled by start/end/period parameters, NOT in the query. "
    "Keep the window as narrow as the question allows: start with the last 1h (default) and "
    "widen only after an empty/inconclusive result. Wide multi-day scans with selective filters "
    "can time out — '| limit N' caps rows RETURNED, not data SCANNED, so for wide ranges prefer "
    "stats/aggregations over raw row pulls. Queries must start with the filter directly "
    "(e.g. 'level:error | fields _time, content | limit 50') or '*' for match-all — never a bare "
    "'|', and the '| filter' pipe is only for post-aggregation conditions on computed aliases. "
    "Always include '| limit N'. For raw rows, project the fields you need with '| fields ...' "
    "rather than returning all columns; otherwise aggregate with '| stats ...'. "
    "Discover fields before guessing. "
    "Call get_groundcover_query_reference once per session before composing non-trivial gcQL."
)
