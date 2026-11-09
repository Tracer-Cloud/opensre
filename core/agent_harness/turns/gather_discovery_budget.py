"""Limit how many schema-exploring MCP calls one evidence gather may spend.

MCP bridges typically expose ``list_*_tools`` plus a ``call_*_tool`` exec
surface where the model explores with ``search`` / ``info`` / ``schema`` and
meta ``call read-data-schema`` before any real metric query. Some bridges take
a structured ``tool_name`` + ``arguments`` shape, not only an ``exec`` command
string. Those exploring calls succeed, or fail with taxonomy errors, so
:class:`SourceCircuitBreaker` never trips: a gather can spend every iteration
exploring and return no data.

This hook:

* Matches bridge tools by naming shape (``list_*_tools`` / ``call_*_tool``),
  not a vendor allow-list.
* Treats structured ``tool_name`` targets as discovery unless they are a
  registered metric query tool (vendors opt in via
  :func:`~infrastructure.harness_providers.register_metric_query_tools`).
* Dedupes exact discovery fingerprints for the gather turn (command text /
  tool target; ``context`` prose is ignored).
* Caps how many discovery-style calls may run (including failed ones);
  further discovery is blocked with a reason that steers toward one real
  query or stop.
* Still allows metric ``call_*_tool`` calls after the budget is spent.

Fingerprints use hashable tuples of known fields — never ``json.dumps``.
"""

from __future__ import annotations

from typing import Any

from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from infrastructure.harness_providers import (
    registered_discovery_targets,
    registered_metric_query_tools,
)

DEFAULT_MAX_DISCOVERY_CALLS = 4

_DISCOVERY_PREFIXES = frozenset({"search", "info", "schema"})
# Meta ``call <tool>`` targets that only explore schemas / skills — not metrics.
_DISCOVERY_CALL_TARGETS = frozenset({"read-data-schema", "skill-get"})

# Structured ``tool_name=…`` values that explore rather than fetch. Exact names
# because a bridge's fetch tools are open-ended (``issue_get``,
# ``search_tweets``, ``conversations_get``) and must not be caught by a prefix.
_DISCOVERY_TARGETS = _DISCOVERY_CALL_TARGETS | frozenset(
    {
        "docs-search",
        "event-definitions",
        "property-definitions",
        "property-values",
        "list-tools",
        "schema",
    }
)
# Scalar keys worth including in a fingerprint without dumping the whole blob.
_FINGERPRINT_ARG_KEYS = ("entity", "kind", "event", "name", "type", "query")

DiscoveryFingerprint = tuple[str, str, str]


def is_mcp_list_tools(tool_name: str) -> bool:
    """True for MCP roster tools named ``list_<vendor>_tools``."""
    name = tool_name.strip()
    return name.startswith("list_") and name.endswith("_tools") and len(name) > len("list__tools")


def is_mcp_exec_bridge(tool_name: str) -> bool:
    """True for MCP exec bridges named ``call_<vendor>_tool``."""
    name = tool_name.strip()
    return name.startswith("call_") and name.endswith("_tool") and len(name) > len("call__tool")


def _nested_exec_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    inner = arguments.get("arguments")
    if isinstance(inner, dict):
        return inner
    return arguments


def _exec_command(arguments: dict[str, Any]) -> str:
    nested = _nested_exec_arguments(arguments)
    raw = nested.get("command")
    if raw is None:
        raw = arguments.get("command")
    return str(raw or "").strip()


def _call_target(command: str) -> str:
    parts = command.split(None, 2)
    if len(parts) < 2 or parts[0].lower() != "call":
        return ""
    return parts[1].split("{", 1)[0].strip().lower()


def bridge_tool_target(arguments: dict[str, Any]) -> str:
    """MCP tool selected via structured ``tool_name`` (PostHog MCP shape)."""
    raw = arguments.get("tool_name")
    if raw is None:
        nested = _nested_exec_arguments(arguments)
        raw = nested.get("tool_name")
    return str(raw or "").strip().lower()


def is_mcp_discovery_target(target: str) -> bool:
    """True for MCP targets that explore the schema rather than fetch evidence.

    A closed set on purpose: mistaking a fetch for discovery can reject a
    gather that already has its answer, while mistaking discovery for a fetch
    only spends iterations.
    """
    name = target.strip().lower()
    if not name:
        return False
    # Exact names. Prefix matching would read ``search_tweets`` as the probe
    # verb ``search`` and bill a data fetch to the discovery budget.
    return name in _DISCOVERY_TARGETS or name in registered_discovery_targets()


def is_mcp_metric_target(target: str) -> bool:
    """True for MCP tools registered as live metric / query fetches.

    Vendors declare names via
    :func:`~infrastructure.harness_providers.register_metric_query_tools`. Core must not
    hard-code PostHog ``execute-sql`` / ``query-*`` conventions here.
    """
    name = target.strip().lower()
    return bool(name) and name in registered_metric_query_tools()


def is_live_metric_query_call(tool_name: str, arguments: dict[str, Any]) -> bool:
    """True when this call is a live metric/SQL/PromQL fetch — not discovery.

    Narrower than ``not is_gather_discovery_call``: Sentry ``issue_get``, X
    ``search_tweets``, and other non-discovery fetches must not count as a
    formed metric query (that would suppress the draft-query floor).

    Matching is exact against the vendor-registered closed set — no substring
    scans and no hyphen/underscore dual checks. Alternate spellings must be
    registered explicitly if both are real tool names.
    """
    name = tool_name.strip()
    if not name or is_mcp_list_tools(name):
        return False
    if is_mcp_exec_bridge(name):
        target = bridge_tool_target(arguments)
        if target:
            return is_mcp_metric_target(target)
        command = _exec_command(arguments)
        if not command:
            return False
        verb = command.split(None, 1)[0].lower()
        if verb == "call":
            return is_mcp_metric_target(_call_target(command))
        return False
    return name.lower() in registered_metric_query_tools()


def _fingerprint_arg_hint(arguments: dict[str, Any]) -> str:
    nested = _nested_exec_arguments(arguments)
    for key in _FINGERPRINT_ARG_KEYS:
        if key in nested and nested[key] is not None:
            return f"{key}={nested[key]}"
        if key in arguments and arguments[key] is not None:
            return f"{key}={arguments[key]}"
    return ""


def is_gather_discovery_call(tool_name: str, arguments: dict[str, Any]) -> bool:
    """Whether this tool call is schema/skill discovery rather than a metric query."""
    name = tool_name.strip()
    if is_mcp_list_tools(name):
        return True
    if not is_mcp_exec_bridge(name):
        return False
    # Structured selection (``tool_name=…``). Discovery is the closed set; a
    # target outside it fetches evidence. Defaulting the other way made every
    # non-PostHog fetch — Sentry ``issue_get`` or X ``search_tweets`` — spend
    # the discovery budget, so a gather that had
    # already answered the question could be rejected for overspending.
    target = bridge_tool_target(arguments)
    if target:
        return is_mcp_discovery_target(target)
    command = _exec_command(arguments)
    if not command:
        # Bridge call with neither tool_name nor command — treat as discovery
        # so an empty probe still burns budget instead of looping forever.
        return True
    verb = command.split(None, 1)[0].lower()
    if verb in _DISCOVERY_PREFIXES:
        return True
    if verb == "call":
        call_target = _call_target(command)
        if is_mcp_metric_target(call_target):
            return False
        if call_target in _DISCOVERY_CALL_TARGETS:
            return True
        # Unknown ``call <tool>`` — still discovery (schema / skill probes).
        return bool(call_target)
    return False


def discovery_fingerprint(tool_name: str, arguments: dict[str, Any]) -> DiscoveryFingerprint:
    """Stable identity for discovery dedupe (ignores free-text ``context``)."""
    name = tool_name.strip()
    if is_mcp_list_tools(name):
        filt = str(arguments.get("name_filter") or "")
        schema = "1" if arguments.get("include_schema") else "0"
        return (name, filt, schema)
    target = bridge_tool_target(arguments)
    if target:
        return (name, target, _fingerprint_arg_hint(arguments))
    command = _exec_command(arguments)
    # Drop trailing JSON payload noise for call-target identity when present.
    verb_and_target = command.split("{", 1)[0].strip().lower()
    bridge = str(arguments.get("name") or arguments.get("tool") or "exec")
    return (name, bridge, verb_and_target)


def with_gather_discovery_budget(
    base: ToolExecutionHooks | None = None,
    *,
    max_discovery_calls: int = DEFAULT_MAX_DISCOVERY_CALLS,
) -> ToolExecutionHooks:
    """Compose discovery budget + exact-fingerprint dedupe onto gather hooks."""
    budget = max(1, int(max_discovery_calls))
    seen: set[DiscoveryFingerprint] = set()
    discovery_count = 0
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        nonlocal discovery_count
        tool_name = request.tool_call.name
        arguments = dict(request.arguments or {})
        if is_gather_discovery_call(tool_name, arguments):
            key = discovery_fingerprint(tool_name, arguments)
            if key in seen:
                return BeforeToolCallResult(
                    blocked=True,
                    reason=(
                        f"Already ran discovery {tool_name} with the same command "
                        "this gather turn. Do not repeat it — run one real metric "
                        "query (or stop gathering)."
                    ),
                    metadata={"suppressed_duplicate_discovery": True},
                )
            if discovery_count >= budget:
                return BeforeToolCallResult(
                    blocked=True,
                    reason=(
                        f"Discovery budget exhausted ({budget} search/info/schema/"
                        "list calls this gather turn). Stop exploring MCP schemas — "
                        "execute one concrete metric query now, or conclude with "
                        "what you have so the assistant can draft a query + a setup CTA."
                    ),
                    metadata={
                        "discovery_budget_exhausted": True,
                        "max_discovery_calls": budget,
                    },
                )
        if base_before is not None:
            return base_before(request)
        return None

    def after(
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> Any:
        nonlocal discovery_count
        # Count failed discovery too: taxonomy/schema errors still burn the
        # explore budget and must not be retriable forever under the same key.
        _ = result
        tool_name = request.tool_call.name
        arguments = dict(request.arguments or {})
        if is_gather_discovery_call(tool_name, arguments):
            key = discovery_fingerprint(tool_name, arguments)
            if key not in seen:
                seen.add(key)
                discovery_count += 1
        if base_after is not None:
            return base_after(request, result)
        return None

    return ToolExecutionHooks(
        before_tool_call=before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=base_batch,
    )


__all__ = [
    "DEFAULT_MAX_DISCOVERY_CALLS",
    "is_mcp_discovery_target",
    "bridge_tool_target",
    "discovery_fingerprint",
    "is_gather_discovery_call",
    "is_live_metric_query_call",
    "is_mcp_exec_bridge",
    "is_mcp_list_tools",
    "is_mcp_metric_target",
    "with_gather_discovery_budget",
]
