"""PostHog MCP owns HogQL draft fences and signup-event identity checks.

Core's metric floor decides *when* to append a draft and when a SessionGoal
needs cohort identity. This package supplies the PostHog dialect (HogQL) and
how to tell a real signup event from a login stand-in in gather observations.

Parsing prefers structured tool payloads and JSON/literal argument dicts —
no regex over rendered observation text.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from typing import Any

from core.agent_harness.ports import GatheredEvidence
from core.agent_harness.spi.prompt_chrome import reply_reports_cohort_unverified
from core.agent_harness.tools import coerce_gathered_evidence
from infrastructure.harness_providers import (
    register_discovery_targets,
    register_metric_cohort_resolver,
    register_metric_query_draft,
    register_metric_query_tools,
)

# PostHog MCP bridge targets: which run a query, which only read schema.
_METRIC_QUERY_TOOLS = frozenset({"execute-sql", "query-trends", "query-run"})
_DISCOVERY_TARGETS = (
    "docs-search",
    "event-definitions",
    "property-definitions",
    "property-values",
)
# Only event-definitions lists event names the project actually has.
_SCHEMA_EVENT_TOOLS = frozenset({"event-definitions"})

_DRAFT_HOGQL_COUNT = """```sql
-- Draft HogQL: confirm event name and property filters, then run in PostHog.
-- This is not a live count.
SELECT uniq(person_id)
FROM events
WHERE timestamp >= now() - INTERVAL 7 DAY
```"""

_DRAFT_HOGQL_COHORT = """```sql
-- Draft HogQL: replace <signup_event> with your project's signup event
-- (not user_signed_in / login). This is not a live retention percentage.
SELECT
  count() AS eligible,
  countIf(dateDiff('day', signup_at, activity_at) = 7) AS retained_d7
FROM (
  SELECT
    person_id,
    min(timestamp) AS signup_at
  FROM events
  WHERE event = '<signup_event>'
    AND properties.$os = 'Windows'
    AND timestamp >= now() - INTERVAL 30 DAY
  GROUP BY person_id
) AS signups
LEFT JOIN (
  SELECT person_id, timestamp AS activity_at
  FROM events
) AS activity USING (person_id)
```"""

# Substrings that mark an event as the one a person signs up with. Login and
# identify events carry none of them, so they can never stand in for signup.
_SIGNUP_EVENT_MARKERS = (
    "signup",
    "sign_up",
    "signed_up",
    "registered",
    "registration",
    "account_created",
    "created_account",
)

_EVENT_NAME_KEYS = ("name", "event", "event_name")
# event-definitions envelopes nest the roster under these keys — never scrape
# free-form string fields (description, tags, …) as event names.
_SCHEMA_LIST_KEYS = ("results", "events", "data", "items", "definitions")


def _parse_jsonish(raw: str) -> Any | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None


def _parse_arguments(raw: str) -> dict[str, Any]:
    parsed = _parse_jsonish(raw)
    return parsed if isinstance(parsed, dict) else {}


def _parse_result_payload(raw: str | Any) -> Any:
    if not isinstance(raw, str):
        return raw
    parsed = _parse_jsonish(raw)
    return parsed if parsed is not None else raw


def _iter_observation_blocks(
    observation: str,
) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Yield ``(tool_name, arguments, result_text)`` via partition — not regex."""
    for block in (observation or "").split("\n\n"):
        if not block.startswith("Tool: "):
            continue
        name = block.split("\n", 1)[0][len("Tool: ") :].strip()
        _, _, rest = block.partition("\nArguments: ")
        args_text, _, result = rest.partition("\nResult: ")
        if not rest:
            _, _, result = block.partition("\nResult: ")
            args_text = ""
        yield name, _parse_arguments(args_text), result


def _iter_tool_calls(
    evidence: GatheredEvidence,
) -> Iterator[tuple[str, dict[str, Any], Any]]:
    """Prefer structured ``tool_results`` payloads; keep args from observation."""
    blocks = list(_iter_observation_blocks(evidence.observation))
    structured = evidence.tool_results
    if structured and len(structured) == len(blocks):
        for (name, arguments, _result_text), (_structured_name, payload) in zip(
            blocks,
            structured,
            strict=True,
        ):
            yield name, arguments, payload
        return
    if structured and not blocks:
        for name, payload in structured:
            yield str(name or ""), {}, payload
        return
    for name, arguments, result_text in blocks:
        yield name, arguments, _parse_result_payload(result_text)


def _bridge_target(tool_name: str, arguments: dict[str, Any]) -> str:
    raw = arguments.get("tool_name")
    if raw is None:
        nested = arguments.get("arguments")
        if isinstance(nested, dict):
            raw = nested.get("tool_name")
    if raw is not None:
        return str(raw).strip().lower()
    return tool_name.strip().lower()


def _is_placeholder_event(event: str) -> bool:
    text = event.strip()
    return (not text) or "<" in text or ">" in text or text == "signup_event"


def _names_a_signup_event(event: str) -> bool:
    if _is_placeholder_event(event):
        return False
    lowered = event.casefold()
    return any(marker in lowered for marker in _SIGNUP_EVENT_MARKERS)


def _query_text(arguments: dict[str, Any]) -> str:
    nested = arguments.get("arguments")
    if isinstance(nested, dict) and nested.get("query") is not None:
        return str(nested["query"])
    if arguments.get("query") is not None:
        return str(arguments["query"])
    return ""


def _events_from_query(query: str) -> frozenset[str]:
    """Pull ``event = '…'`` literals from a HogQL string without regex.

    Scans for the ``event`` token, then ``=``, then a quoted literal. Only the
    query field is searched — never the whole observation.
    """
    found: set[str] = set()
    haystack = query or ""
    search = haystack.casefold()
    pos = 0
    while True:
        idx = search.find("event", pos)
        if idx < 0:
            break
        before = search[idx - 1] if idx > 0 else " "
        after = search[idx + 5] if idx + 5 < len(search) else " "
        # Skip identifiers like ``event_name`` / ``pre_event``.
        if before.isalnum() or before == "_" or after.isalnum() or after == "_":
            pos = idx + 5
            continue
        rest = haystack[idx + 5 :].lstrip()
        if not rest.startswith("="):
            pos = idx + 5
            continue
        rest = rest[1:].lstrip()
        if not rest or rest[0] not in "'\"":
            pos = idx + 5
            continue
        quote = rest[0]
        end = rest.find(quote, 1)
        if end > 1:
            name = rest[1:end].strip()
            if name and not _is_placeholder_event(name):
                found.add(name)
        pos = idx + 5
    return frozenset(found)


def _add_schema_event_name(found: set[str], value: Any) -> None:
    if not isinstance(value, str):
        return
    name = value.strip()
    if name and not _is_placeholder_event(name):
        found.add(name)


def _events_from_schema_payload(payload: Any) -> frozenset[str]:
    """Collect event names from an event-definitions payload.

    Accepts only:
    * bare string entries in a list (``["user_signed_up", "$pageview"]``)
    * ``name`` / ``event`` / ``event_name`` on a definition object
    * those same shapes nested under known roster keys (``results``, …)

    Does **not** promote arbitrary nested strings (descriptions, tags, docs)
    to event names — that would let metadata confirm a guessed signup event.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            _add_schema_event_name(found, node)
            return
        if isinstance(node, dict):
            took_name = False
            for key in _EVENT_NAME_KEYS:
                value = node.get(key)
                if isinstance(value, str):
                    _add_schema_event_name(found, value)
                    took_name = True
            if took_name:
                # Definition row: ignore description / tags / other strings.
                return
            for key in _SCHEMA_LIST_KEYS:
                nested = node.get(key)
                if nested is not None:
                    walk(nested)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                if isinstance(item, str):
                    _add_schema_event_name(found, item)
                else:
                    walk(item)

    walk(payload)
    return frozenset(found)


def _sql_events_from_metric_queries(evidence: GatheredEvidence) -> frozenset[str]:
    """Event names referenced in live metric / SQL tool calls only."""
    found: set[str] = set()
    for tool_name, arguments, _payload in _iter_tool_calls(evidence):
        target = _bridge_target(tool_name, arguments)
        if target not in _METRIC_QUERY_TOOLS:
            continue
        found.update(_events_from_query(_query_text(arguments)))
    return frozenset(found)


def _events_listed_in_schema(evidence: GatheredEvidence) -> frozenset[str]:
    """Event names returned by event-definitions (schema), never from SQL."""
    found: set[str] = set()
    for tool_name, arguments, payload in _iter_tool_calls(evidence):
        target = _bridge_target(tool_name, arguments)
        if target not in _SCHEMA_EVENT_TOOLS:
            continue
        found.update(_events_from_schema_payload(payload))
    return frozenset(found)


def posthog_signup_cohort_resolved(evidence: Any, reply: str) -> bool:
    """True when schema confirmed a signup event that the live query used.

    A signup-shaped name inside HogQL alone is not enough — the model can guess
    ``user_signed_up`` without ever seeing it in event-definitions. Identity is
    resolved only when a signup-named event appears in both the schema listing
    and a metric query, and the reply is a live percentage.
    """
    if reply_reports_cohort_unverified(reply):
        return False
    gathered = coerce_gathered_evidence(evidence)
    if gathered is None:
        return False
    signup_in_sql = frozenset(
        event for event in _sql_events_from_metric_queries(gathered) if _names_a_signup_event(event)
    )
    if not signup_in_sql:
        return False
    confirmed = signup_in_sql & _events_listed_in_schema(gathered)
    if not confirmed:
        return False
    lower = (reply or "").casefold()
    return "unavailable" not in lower and "unverified" not in lower


def register_posthog_mcp_metric_drafts() -> None:
    """Opt PostHog MCP into the unformed-metric draft floor."""
    register_metric_query_draft(
        "posthog_mcp",
        count_draft=_DRAFT_HOGQL_COUNT,
        cohort_draft=_DRAFT_HOGQL_COHORT,
        priority=10,
    )
    register_metric_cohort_resolver("posthog_mcp", posthog_signup_cohort_resolved)
    register_metric_query_tools("posthog_mcp", _METRIC_QUERY_TOOLS)
    register_discovery_targets("posthog_mcp", _DISCOVERY_TARGETS)


__all__ = [
    "posthog_signup_cohort_resolved",
    "register_posthog_mcp_metric_drafts",
]
