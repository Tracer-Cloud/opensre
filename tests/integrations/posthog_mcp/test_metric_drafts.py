"""PostHog MCP metric-draft registration and signup-cohort identity."""

from __future__ import annotations

import pytest

from core.agent_harness.turns.gather_observation import GatheredEvidence
from infrastructure.harness_providers import (
    clear_metric_query_drafts,
    metric_query_draft_for,
)
from integrations.posthog_mcp.metric_drafts import (
    posthog_signup_cohort_resolved,
    register_posthog_mcp_metric_drafts,
)


@pytest.fixture(autouse=True)
def _register_drafts() -> None:
    clear_metric_query_drafts()
    register_posthog_mcp_metric_drafts()
    yield
    clear_metric_query_drafts()


def test_posthog_registers_count_and_cohort_hogql_drafts() -> None:
    count = metric_query_draft_for(("posthog_mcp",), cohort_goal=False)
    cohort = metric_query_draft_for(("posthog_mcp",), cohort_goal=True)
    assert count is not None and "```sql" in count
    assert "uniq(person_id)" in (count or "")
    assert cohort is not None and "<signup_event>" in cohort


def _evidence(
    *,
    event: str,
    schema_lists: tuple[str, ...] | list[dict[str, str]] | None = None,
    use_structured_tool_results: bool = False,
) -> GatheredEvidence:
    blocks: list[str] = []
    tool_results: list[tuple[str, object]] = []
    if schema_lists is not None:
        if isinstance(schema_lists, list):
            schema_payload: object = schema_lists
            listed = str(schema_lists)
        else:
            schema_payload = list(schema_lists)
            listed = "[" + ", ".join(f"'{name}'" for name in schema_lists) + "]"
        blocks.append(
            "Tool: call_posthog_tool\n"
            'Arguments: {"tool_name": "event-definitions"}\n'
            f"Result: {listed}"
        )
        tool_results.append(("call_posthog_tool", schema_payload))
    query = f"SELECT count() FROM events WHERE event = '{event}'"
    blocks.append(
        "Tool: call_posthog_tool\n"
        'Arguments: {"tool_name": "execute-sql", '
        f'"arguments": {{"query": "{query}"}}}}\n'
        "Result: eligible=120 retained_d7=49"
    )
    tool_results.append(("call_posthog_tool", {"eligible": 120, "retained_d7": 49}))
    return GatheredEvidence(
        observation="\n\n".join(blocks),
        tool_results=tuple(tool_results) if use_structured_tool_results else (),
    )


def test_schema_confirmed_signup_event_resolves_signup_identity() -> None:
    evidence = _evidence(event="user_signed_up", schema_lists=("user_signed_up", "$pageview"))
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is True


def test_structured_schema_dicts_resolve_without_scraping_result_text() -> None:
    evidence = _evidence(
        event="user_signed_up",
        schema_lists=[{"name": "user_signed_up"}, {"name": "$pageview"}],
        use_structured_tool_results=True,
    )
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is True


def test_schema_metadata_strings_do_not_confirm_a_guessed_signup_event() -> None:
    """Descriptions / tags must not satisfy the schema ∩ SQL check."""
    evidence = _evidence(
        event="user_signed_up",
        schema_lists=[
            {
                "name": "$pageview",
                "description": "user_signed_up",
                "tags": ["user_signed_up", "signup"],
            }
        ],
        use_structured_tool_results=True,
    )
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is False


def test_schema_results_envelope_uses_name_fields_only() -> None:
    evidence = GatheredEvidence(
        observation=(
            "Tool: call_posthog_tool\n"
            'Arguments: {"tool_name": "event-definitions"}\n'
            'Result: {"results": [{"name": "user_signed_up", "description": "x"}]}\n\n'
            "Tool: call_posthog_tool\n"
            'Arguments: {"tool_name": "execute-sql", '
            '"arguments": {"query": "SELECT count() FROM events '
            "WHERE event = 'user_signed_up'\"}}\n"
            "Result: eligible=120"
        ),
        tool_results=(
            (
                "call_posthog_tool",
                {"results": [{"name": "user_signed_up", "description": "user_signed_up docs"}]},
            ),
            ("call_posthog_tool", {"eligible": 120}),
        ),
    )
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is True


def test_guessed_signup_in_sql_without_schema_does_not_resolve() -> None:
    """A signup-shaped name in HogQL alone must not suppress the cohort floor."""
    evidence = _evidence(event="user_signed_up", schema_lists=None)
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is False


def test_signup_queried_but_absent_from_schema_does_not_resolve() -> None:
    evidence = _evidence(event="user_signed_up", schema_lists=("$pageview", "login"))
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is False


@pytest.mark.parametrize("event", ["user_signed_in", "login", "$identify"])
def test_a_login_event_query_does_not_resolve_signup_identity(event: str) -> None:
    evidence = _evidence(event=event, schema_lists=(event, "user_signed_up"))
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is False


def test_an_unrelated_event_query_does_not_resolve_signup_identity() -> None:
    evidence = _evidence(event="$pageview", schema_lists=("$pageview", "user_signed_up"))
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is False


def test_placeholder_signup_event_in_sql_does_not_resolve() -> None:
    evidence = _evidence(event="<signup_event>", schema_lists=("<signup_event>",))
    assert posthog_signup_cohort_resolved(evidence, "D7 retention was 41%") is False
