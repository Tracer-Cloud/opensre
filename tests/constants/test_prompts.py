"""Structural assertions for the prompt templates in app.constants.prompts.

These tests pin the safety guidance that hardens GENERAL_SYSTEM_PROMPT against
borderline RCA requests (issue #658). They do not call an LLM; they assert the
prompt itself carries the instructions that produce safe behaviour.
"""

from __future__ import annotations

from app.constants.prompts import (
    GENERAL_SYSTEM_PROMPT,
    ROUTER_PROMPT,
    SYSTEM_PROMPT,
)


def test_general_prompt_states_no_tools_or_live_data() -> None:
    text = GENERAL_SYSTEM_PROMPT.lower()
    assert "general chat mode" in text
    assert "do not have access to tools" in text
    for source in ("logs", "metrics", "datadog", "grafana", "eks"):
        assert source in text, f"GENERAL_SYSTEM_PROMPT should mention {source!r}"


def test_general_prompt_preserves_conceptual_q_and_a() -> None:
    text = GENERAL_SYSTEM_PROMPT.lower()
    assert "best practices" in text
    assert "answer directly" in text


def test_general_prompt_lists_incident_shape_signals() -> None:
    text = GENERAL_SYSTEM_PROMPT.lower()
    for marker in (
        "alertname",
        "severity",
        "kube_pod",
        "db_instance",
        "exit code",
        "stack trace",
        "root cause",
    ):
        assert marker in text, (
            f"GENERAL_SYSTEM_PROMPT should reference {marker!r} as an incident-shape signal"
        )


def test_general_prompt_forbids_speculative_rca() -> None:
    text = GENERAL_SYSTEM_PROMPT.lower()
    assert "do not produce a root cause" in text
    assert "do not invent" in text
    assert "speculation" in text


def test_general_prompt_requires_evidence_pointer_and_handoff() -> None:
    text = GENERAL_SYSTEM_PROMPT
    assert "opensre investigate" in text, (
        "GENERAL_SYSTEM_PROMPT should redirect users to the investigation surface"
    )
    lower = text.lower()
    assert "evidence" in lower
    assert "replicalag" in lower or "replication lag" in lower, (
        "Prompt should give a concrete RDS-class evidence example"
    )
    assert "oomkilled" in lower or "crashloopbackoff" in lower, (
        "Prompt should give a concrete EKS-class evidence example"
    )


def test_general_prompt_pins_four_section_response_headings() -> None:
    """The refusal branch commits to four section headings in a fixed order.

    The four-section response template is the load-bearing structural
    contract of the refusal: a future edit that renames or removes a
    heading while preserving the surrounding keyword text would silently
    break the response shape. Pinning the headings (and their ordering)
    makes that failure mode caught by CI.
    """
    text = GENERAL_SYSTEM_PROMPT.lower()
    headings = (
        "what you can see in the input",
        "why a root cause cannot be given here",
        "what evidence would be required",
        "how to get a real investigation",
    )
    positions: list[int] = []
    for heading in headings:
        idx = text.find(heading)
        assert idx >= 0, f"GENERAL_SYSTEM_PROMPT must contain heading {heading!r}"
        positions.append(idx)
    assert positions == sorted(positions), (
        "Four-section headings must appear in order "
        f"{list(headings)}, but found ordering "
        f"{[h for _, h in sorted(zip(positions, headings, strict=True))]}"
    )


def test_other_prompts_unchanged_in_shape() -> None:
    """SYSTEM_PROMPT and ROUTER_PROMPT should remain investigation-mode oriented."""
    assert "Tracer" in SYSTEM_PROMPT
    assert "tracer_data" in ROUTER_PROMPT
    assert "general" in ROUTER_PROMPT
