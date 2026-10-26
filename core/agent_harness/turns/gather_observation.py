"""Gather observation blocks: structured outcome + plain Tool/Result parsing.

Gather renders executed tools as ``\\n\\n``-joined paragraphs, **newest
first** (so head truncation keeps late count/SQL results)::

    Tool: <name>
    Arguments: …
    Result: …

Callers that only have the rendered string should split with
:func:`iter_tool_result_blocks` (same shape as
``pending_offer._compact_evidence_lines``) — not regex. Prefer
:class:`GatheredEvidence.tool_results` when the gather loop still has the
raw ``(tool_name, payload)`` pairs (those stay chronological).
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from core.tool_framework.utils.tool_availability import is_tool_unavailable_envelope


@dataclass(frozen=True, slots=True)
class GatheredEvidence:
    """One gather pass: prompt text plus structured tool payloads.

    ``observation`` is what the answer path injects into the prompt.
    ``tool_results`` are ``(tool_name, raw_output)`` pairs from the gather
    loop — used for typed checks (e.g. ``tool_unavailable``) without
    scraping the rendered string.
    """

    observation: str
    tool_results: tuple[tuple[str, Any], ...] = ()
    #: The gather loop stopped at its iteration cap instead of concluding, so
    #: these results are whatever it had reached, not a finished answer.
    truncated: bool = False


def _is_roster_probe(name: str) -> bool:
    stripped = str(name or "").strip()
    return stripped.startswith("list_") and stripped.endswith("_tools")


def _payload_from_observation_result(result: str) -> Any:
    """Best-effort typed payload from a rendered ``Result:`` body.

    Structured ``tool_results`` already carry dict envelopes. The observation
    fallback only has text — parse JSON or a Python dict repr so
    ``tool_unavailable`` still does not count. Ordinary result strings stay
    strings (not regex / phrase scans).
    """
    text = (result or "").strip()
    if not text or text[0] != "{":
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text
    return parsed


def _counts_as_gather_success(name: str, payload: Any) -> bool:
    if is_tool_unavailable_envelope(payload):
        return False
    return not _is_roster_probe(name)


def count_gather_tool_successes(evidence: GatheredEvidence | None) -> int:
    """How many gather tools returned a usable payload (not ``tool_unavailable``).

    SessionGoal evidence must count successful gather work.

    Roster probes (``list_*_tools``) do not count — listing alone previously
    looked like evidence after every MCP call failed, which could false-close
    a host-owned goal on a "query failed" draft.

    When ``tool_results`` is empty but ``observation`` still has ``Tool:``
    blocks (legacy string gather / name-less tool calls), count those blocks
    with the same roster and unavailable exclusions so SessionGoal still sees
    live work without treating a failed envelope as evidence.
    """
    if evidence is None:
        return 0
    n = 0
    for name, payload in evidence.tool_results:
        if _counts_as_gather_success(name, payload):
            n += 1
    if n > 0:
        return n
    # Fallback: structured pairs missing, but rendered observation has tools.
    for name, result in iter_tool_result_blocks(evidence.observation):
        if _counts_as_gather_success(name, _payload_from_observation_result(result)):
            n += 1
    return n


def iter_tool_result_blocks(observation: str) -> Iterator[tuple[str, str]]:
    """Yield ``(tool_line, result_text)`` for each Tool/Result paragraph.

    Matches rendered ``Tool:`` / ``Arguments:`` / ``Result:`` paragraphs:
    blocks separated by a blank line, each starting with ``Tool: ``, result
    after ``\\nResult: `` (``Arguments:`` may sit between).
    """
    text = (observation or "").strip()
    if not text:
        return
    for block in text.split("\n\n"):
        if not block.startswith("Tool: "):
            continue
        name = block.split("\n", 1)[0][len("Tool: ") :].strip()
        _, _, result = block.partition("\nResult: ")
        yield name, result


def coerce_gathered_evidence(
    raw: str | GatheredEvidence | None,
) -> GatheredEvidence | None:
    """Normalize gather return values (legacy ``str`` or :class:`GatheredEvidence`)."""
    if raw is None:
        return None
    if isinstance(raw, GatheredEvidence):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        return GatheredEvidence(observation=text) if text else None
    return None


def tool_results_from_executed(
    executed: Sequence[tuple[Any, Any]],
) -> tuple[tuple[str, Any], ...]:
    """Project gather ``(tool_call, output)`` pairs to ``(name, output)``."""
    out: list[tuple[str, Any]] = []
    for tc, output in executed:
        name = str(getattr(tc, "name", "") or "").strip()
        if not name:
            continue
        out.append((name, output))
    return tuple(out)


__all__ = [
    "GatheredEvidence",
    "coerce_gathered_evidence",
    "count_gather_tool_successes",
    "iter_tool_result_blocks",
    "tool_results_from_executed",
]
