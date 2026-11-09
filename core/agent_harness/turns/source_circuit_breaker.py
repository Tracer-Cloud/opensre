"""Per-gather circuit breaker over tools/sources that fail to connect.

Installed as :class:`~core.tool.execution.ToolExecutionHooks` on a bounded tool
loop: the first transport-level failure (connect timeout, connection refused)
marks **that tool** unreachable for the rest of the gather run. When the host
has never answered successfully this run, the **source** is marked too — sibling
tools that share a dead Grafana/Kubernetes endpoint must not each re-pay the
connect timeout.

A success for a tool is sticky for the run and clears the source mark: a
concurrent connectivity failure that finishes after that success must not
recreate a source-wide block (otherwise the next gather iteration blocks a
vendor that just returned evidence). Application / downstream errors still do
not trip the breaker at all.

Each SessionGoal gather constructs a fresh breaker; seed from
:mod:`gather_unreachable` so marks survive across ``run_until_session_goal``
iterations.
"""

from __future__ import annotations

import threading

from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)

# Transport-level failure signatures (host unreachable). Application errors
# from a reachable service — "datasource not found", auth failures, empty
# results — must NOT trip the breaker: the source can still answer other calls.
_CONNECTIVITY_ERROR_MARKERS = (
    "connection refused",
    "connect timeout",
    "connecttimeouterror",
    "connection timed out",
    "max retries exceeded",
    "name or service not known",
    "temporary failure in name resolution",
    "no route to host",
    "network is unreachable",
)

# When these appear with a connectivity marker, the failure is about a target
# the reachable vendor could not reach — not the vendor transport itself.
_DOWNSTREAM_VETO_MARKERS = (
    "datasource",
    "data source",
    "upstream",
    "backend",
    "target connection",
    "peer connection",
)

# Tools with no meaningful source identity — still allow tool-level marks.
_UNBREAKABLE_SOURCES = frozenset({"", "unknown"})

# urllib3/requests signatures for *our* HTTP client failing to reach the vendor.
# When these appear, "datasource" in the same string is usually the URL path
# (e.g. discover ``/api/datasources``) — not a reachable vendor reporting on a
# downstream target — so the veto must not apply.
_DIRECT_HTTP_CLIENT_MARKERS = (
    "httpconnectionpool",
    "connecttimeouterror",
    "max retries exceeded with url",
    "failed to establish a new connection",
    "name or service not known",
    "nodename nor servname provided",
)

_REASON_SUMMARY_CHARS = 160


def _error_text(result: ToolExecutionResult) -> str:
    if isinstance(result.content, str):
        return result.content
    return str(result.details if result.details is not None else result.content)


def _is_connectivity_error(error_text: str) -> bool:
    lowered = error_text.lower()
    if not any(marker in lowered for marker in _CONNECTIVITY_ERROR_MARKERS):
        return False
    if any(marker in lowered for marker in _DIRECT_HTTP_CLIENT_MARKERS):
        return True
    # A reachable service reporting that *its* target is down must not poison
    # tools for the rest of the turn.
    return not any(marker in lowered for marker in _DOWNSTREAM_VETO_MARKERS)


def _summarize(error_text: str) -> str:
    single_line = " ".join(error_text.split())
    if len(single_line) <= _REASON_SUMMARY_CHARS:
        return single_line
    return single_line[:_REASON_SUMMARY_CHARS] + "…"


class SourceCircuitBreaker:
    """Tracks tools/sources that failed at the transport level within one run."""

    def __init__(
        self,
        *,
        seed_tools: dict[str, str] | None = None,
        seed_sources: dict[str, str] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._tool_down: dict[str, str] = dict(seed_tools or {})
        self._source_down: dict[str, str] = dict(seed_sources or {})
        # Sticky for the gather run: once a tool/source succeeds, concurrent or
        # later connectivity failures must not re-mark them as source-wide down.
        self._tool_ok: set[str] = set()
        self._source_ok: set[str] = set()

    def snapshot(self) -> tuple[dict[str, str], dict[str, str]]:
        """Return copies of tool and source unreachable maps for session carry."""
        with self._lock:
            return dict(self._tool_down), dict(self._source_down)

    def hooks(self) -> ToolExecutionHooks:
        """Return execution hooks enforcing the breaker on a tool loop."""
        return ToolExecutionHooks(
            before_tool_call=self._skip_if_unreachable,
            after_tool_call=self._mark_on_connectivity_error,
        )

    def _skip_if_unreachable(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        tool_name = request.tool_call.name
        source = request.source
        with self._lock:
            summary = self._tool_down.get(tool_name)
            if summary is None and source not in _UNBREAKABLE_SOURCES:
                summary = self._source_down.get(source)
                if summary is not None:
                    return BeforeToolCallResult(
                        blocked=True,
                        reason=(
                            f"skipped {tool_name}: source {source!r} is unreachable "
                            f"this session ({summary}). Query a different connected "
                            "source, or state the outage in your findings instead of "
                            "retrying it."
                        ),
                        metadata={
                            "skipped_source": source,
                            "skipped_tool": tool_name,
                            "skipped_by": "source",
                        },
                    )
            if summary is None:
                return None
        return BeforeToolCallResult(
            blocked=True,
            reason=(
                f"skipped {tool_name}: tool is unreachable "
                f"this session ({summary}). Query a different connected source or "
                "tool, or state the outage in your findings instead of retrying it."
            ),
            metadata={
                "skipped_source": source,
                "skipped_tool": tool_name,
                "skipped_by": "tool",
            },
        )

    def _mark_on_connectivity_error(
        self,
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> None:
        tool_name = request.tool_call.name
        source = request.source
        if not result.is_error:
            with self._lock:
                self._tool_ok.add(tool_name)
                self._tool_down.pop(tool_name, None)
                if source not in _UNBREAKABLE_SOURCES:
                    self._source_ok.add(source)
                    self._source_down.pop(source, None)
            return None
        if source in _UNBREAKABLE_SOURCES:
            return None
        error_text = _error_text(result)
        if not _is_connectivity_error(error_text):
            return None
        summary = _summarize(error_text)
        with self._lock:
            # Success is sticky: do not recreate a mark after the tool answered.
            if tool_name in self._tool_ok:
                return None
            self._tool_down.setdefault(tool_name, summary)
            # Host never answered this run → siblings sharing the endpoint
            # would also fail; mark the source. If any tool on the source
            # already succeeded, keep the failure tool-scoped only.
            if source not in self._source_ok:
                self._source_down.setdefault(source, summary)
        return None


__all__ = ["SourceCircuitBreaker"]
