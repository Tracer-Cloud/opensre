"""Handle follow-up questions by grounding them against the previous investigation."""

from __future__ import annotations

import json
import logging
from typing import Any

from rich.console import Console
from rich.markup import escape

from app.cli.repl.session import ReplSession

_logger = logging.getLogger(__name__)


def _summarize_evidence(evidence: Any) -> list[str]:
    """Render a short evidence preview for the follow-up prompt.

    ``AgentState.evidence`` is a ``dict[str, Any]`` keyed by evidence id, but
    we accept list/other shapes defensively so an unexpected value doesn't
    silently drop all grounding context.
    """
    if isinstance(evidence, dict):
        sample_keys = list(evidence)[:3]
        sample = {key: evidence[key] for key in sample_keys}
        return [
            f"Evidence items: {len(evidence)}",
            "Evidence keys: " + ", ".join(map(str, sample_keys)),
            "Sample evidence:\n" + json.dumps(sample, indent=2, default=str)[:1500],
        ]
    if isinstance(evidence, list):
        return [
            f"Evidence items: {len(evidence)}",
            "Sample evidence:\n" + json.dumps(evidence[:3], indent=2, default=str)[:1500],
        ]
    return [
        f"Evidence type: {type(evidence).__name__}",
        f"Evidence summary:\n{str(evidence)[:1500]}",
    ]


def _summarize_last_state(state: dict[str, Any]) -> str:
    """Produce a compact text summary of the previous investigation for grounding."""
    parts: list[str] = []
    alert_name = state.get("alert_name")
    if alert_name:
        parts.append(f"Alert: {alert_name}")
    root_cause = state.get("root_cause")
    if root_cause:
        parts.append(f"Root cause: {root_cause}")
    problem_md = state.get("problem_md") or ""
    if problem_md:
        parts.append(f"Problem summary:\n{problem_md[:2000]}")
    slack_message = state.get("slack_message") or ""
    if slack_message:
        parts.append(f"Report:\n{slack_message[:2000]}")
    evidence = state.get("evidence")
    if evidence:
        try:
            parts.extend(_summarize_evidence(evidence))
        except (TypeError, ValueError) as exc:
            # Serialization can fail on exotic evidence values; tell the LLM
            # the context was withheld rather than silently dropping it.
            _logger.warning("could not serialize evidence for follow-up: %s", exc)
            parts.append("(evidence present but could not be serialized for grounding)")
    return "\n\n".join(parts) or "(no prior investigation details available)"


def answer_follow_up(question: str, session: ReplSession, console: Console) -> None:
    """Answer a follow-up question about the previous investigation."""
    if session.last_state is None:
        console.print(
            "[yellow]no prior investigation in this session.[/yellow] "
            "describe an alert first, then ask follow-up questions about it."
        )
        return

    try:
        from app.services.llm_client import get_llm_for_reasoning
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]LLM client unavailable:[/red] {exc}")
        return

    context = _summarize_last_state(session.last_state)
    prompt = (
        "You are an SRE assistant answering a follow-up question about a prior "
        "incident investigation that you just completed. Use only the provided "
        "investigation context. If the context does not contain the answer, say so "
        "plainly. Keep the answer concise and concrete.\n\n"
        f"--- Prior investigation ---\n{context}\n\n"
        f"--- Follow-up question ---\n{question}"
    )

    try:
        client = get_llm_for_reasoning()
        response = client.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]follow-up failed:[/red] {escape(str(exc))}")
        return

    text = getattr(response, "content", None) or str(response)
    # LLM output routinely contains brackets ([ERROR], [OOMKilled], ISO
    # timestamps, service names) that Rich would interpret as markup tags and
    # silently drop.  Escape everything that isn't our own markup.
    console.print()
    console.print(f"[bold cyan]answer:[/bold cyan] {escape(text)}")
    console.print()


__all__ = ["answer_follow_up"]
