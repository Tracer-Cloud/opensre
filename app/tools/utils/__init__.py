"""Tool utilities - data validation, evidence compaction, and log deduplication."""

from app.tools.utils.compaction import (
    DEFAULT_ERROR_LOG_LIMIT,
    DEFAULT_LOG_LIMIT,
    DEFAULT_MESSAGE_CHARS,
    DEFAULT_METRICS_LIMIT,
    DEFAULT_TRACE_LIMIT,
    compact_invocations,
    compact_logs,
    compact_metrics,
    compact_traces,
    summarize_counts,
    truncate_list,
    truncate_log_entry,
    truncate_message,
)
from app.tools.utils.data_validation import validate_host_metrics
from app.tools.utils.log_compaction import (
    build_error_taxonomy,
    deduplicate_logs,
)
from app.tools.utils.log_compaction import (
    compact_logs as compact_logs_dedup,
)

__all__ = [
    # Data validation
    "validate_host_metrics",
    # Compaction utilities
    "compact_logs",
    "compact_traces",
    "compact_metrics",
    "compact_invocations",
    "summarize_counts",
    "truncate_list",
    "truncate_message",
    "truncate_log_entry",
    # Log deduplication and taxonomy
    "deduplicate_logs",
    "build_error_taxonomy",
    "compact_logs_dedup",
    # Constants
    "DEFAULT_LOG_LIMIT",
    "DEFAULT_ERROR_LOG_LIMIT",
    "DEFAULT_TRACE_LIMIT",
    "DEFAULT_METRICS_LIMIT",
    "DEFAULT_MESSAGE_CHARS",
]
