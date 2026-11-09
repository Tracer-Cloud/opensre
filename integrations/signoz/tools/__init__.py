"""SigNoz agent tools — one package per tool, re-exported here.

Registration happens by ``@tool`` decorator at import time. The registry
discovers each tool package directly (``tools.registry_discovery`` walks this
package and follows each package's ``TOOL_MODULES`` manifest), so these
re-exports exist for callers and tests that import from
``integrations.signoz.tools``, not to register anything.
"""

from __future__ import annotations

from integrations.signoz.tools.query_signoz_logs_tool import (
    _logs_is_available,
    query_signoz_logs,
)
from integrations.signoz.tools.query_signoz_metrics_tool import (
    _metrics_is_available,
    query_signoz_metrics,
)
from integrations.signoz.tools.query_signoz_traces_tool import (
    _traces_is_available,
    query_signoz_traces,
)

__all__ = [
    "_logs_is_available",
    "_metrics_is_available",
    "_traces_is_available",
    "query_signoz_logs",
    "query_signoz_metrics",
    "query_signoz_traces",
]
