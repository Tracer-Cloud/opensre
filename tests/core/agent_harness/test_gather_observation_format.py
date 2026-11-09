"""Structured gather-observation helpers used by L0 / SessionGoal bookkeeping."""

from __future__ import annotations

from core.agent_harness.turns.gather_observation import (
    GatheredEvidence,
    count_gather_tool_successes,
)
from core.tool_framework.utils.tool_availability import tool_unavailable


def test_count_gather_tool_successes_skips_unavailable() -> None:
    assert count_gather_tool_successes(None) == 0
    evidence = GatheredEvidence(
        observation="x",
        tool_results=(
            ("list_posthog_tools", {"tools": []}),
            ("call_posthog_tool", tool_unavailable("posthog", "auth failed")),
            ("call_posthog_tool", "windows|272"),
        ),
    )
    # list_* roster probes do not count; unavailable does not count.
    assert count_gather_tool_successes(evidence) == 1


def test_count_gather_tool_successes_falls_back_to_observation_blocks() -> None:
    """Empty tool_results still counts Tool: blocks in the rendered observation."""
    observation = (
        "Tool: list_posthog_tools\nArguments: {}\nResult: []\n\n"
        "Tool: call_posthog_tool\nArguments: {}\nResult: windows|272"
    )
    evidence = GatheredEvidence(observation=observation, tool_results=())
    assert count_gather_tool_successes(evidence) == 1


def test_count_gather_tool_successes_observation_fallback_skips_unavailable() -> None:
    """Rendered ``tool_unavailable`` envelopes must not count as gather evidence."""
    from core.tool_framework.utils.tool_availability import tool_unavailable

    observation = (
        "Tool: call_posthog_tool\nArguments: {}\nResult: "
        f"{tool_unavailable('posthog', 'auth failed')}"
    )
    evidence = GatheredEvidence(observation=observation, tool_results=())
    assert count_gather_tool_successes(evidence) == 0
