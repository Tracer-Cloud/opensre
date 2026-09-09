"""Session-scoped token accounting and LLM run metadata for the agent harness.

Callers accumulate token usage per session via ``record_llm_turn`` (or
the higher-level ``record_invoke_response`` / ``build_llm_run_info``
wrappers), passing exact provider-reported counts when available and
falling back to the ``estimate_tokens`` heuristic otherwise. Any
session object is accepted as long as it exposes a ``tokens`` attribute
with a compatible ``record``/``totals`` interface. ``format_token_total``
then renders the accumulated totals for display.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.llm.types import LLMResponse

_CHARS_PER_TOKEN = 4


@dataclass(frozen=True, slots=True)
class LlmRunInfo:
    """Best-effort metadata from one visible LLM response.

    Note: named ``LlmRunInfo`` (not ``LLMRunInfo``) to match an existing
    naming convention elsewhere in the codebase — intentional, not a
    typo; do not rename.

    All fields are optional: callers may lack a ``client`` to resolve
    model/provider from, a ``started`` timestamp for latency, etc.
    Instances are produced by ``build_llm_run_info``.
    """

    model: str | None = None
    provider: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    response_text: str | None = None
    final_system_prompt: str | None = None


def estimate_tokens(text: str) -> int:
    """Roughly estimate the token count of ``text`` when no provider count is available.

    Uses a fixed ``_CHARS_PER_TOKEN`` (4) chars-per-token heuristic — not
    an exact tokenizer count. Returns an ``int`` (integer division, so it
    rounds down). Use this only as a fallback; prefer provider-reported
    token counts when they exist.
    """
    return len(text) // _CHARS_PER_TOKEN


def resolve_model_name(client: object) -> str | None:
    """Best-effort read of an LLM client's model name.

    Reads the private ``_model`` attribute off ``client``. Returns the
    value only if it's a non-empty ``str``; returns ``None`` if the
    attribute is missing, empty, or not a string (no exceptions raised).
    """
    value = getattr(client, "_model", None)
    return value if isinstance(value, str) and value else None


def resolve_provider_name(client: object) -> str | None:
    """Best-effort identification of the provider backing ``client``.

    Prefers an explicit ``_provider_label`` attribute on ``client``
    (lowercased, spaces replaced with underscores). Falls back to
    pattern-matching the client's class name for known substrings
    (``openai``, ``bedrock``, ``cli``, ``anthropic``/``llmclient``).
    Returns ``None`` if no match is found (no exceptions raised).
    """
    provider_label = getattr(client, "_provider_label", None)
    if isinstance(provider_label, str) and provider_label:
        return provider_label.strip().lower().replace(" ", "_")
    name = type(client).__name__.lower()
    if "openai" in name:
        return "openai"
    if "bedrock" in name:
        return "bedrock"
    if "cli" in name:
        return "cli"
    if "anthropic" in name or "llmclient" in name:
        return "anthropic"
    return None


def record_llm_turn(
    session: Any,
    *,
    prompt: str,
    response: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> tuple[int, int, bool]:
    """Accumulate one LLM call's token usage onto ``session.tokens``.

    ``session`` may be any object exposing a ``tokens`` attribute whose
    ``record(input_tokens, output_tokens, estimated)`` method will be
    called; if ``session.tokens`` is absent or non-callable, this is a
    no-op (no exception is raised).

    If both ``input_tokens`` and ``output_tokens`` are given, they are
    used as-is and treated as exact provider-reported counts. Otherwise
    both are derived from ``prompt``/``response`` via
    ``estimate_tokens`` and treated as estimates. Partial overrides
    (only one of the two args) are not supported — either both are
    provided or neither is.

    Returns ``(input_tokens, output_tokens, estimated)`` where
    ``estimated`` is ``True`` iff the values were derived via
    ``estimate_tokens`` rather than passed in.
    """
    if input_tokens is not None and output_tokens is not None:
        inp, out, estimated = input_tokens, output_tokens, False
    else:
        inp = estimate_tokens(prompt)
        out = estimate_tokens(response)
        estimated = True
    tokens = getattr(session, "tokens", None)
    if tokens is not None and callable(getattr(tokens, "record", None)):
        tokens.record(input_tokens=inp, output_tokens=out, estimated=estimated)
    return inp, out, estimated


def record_provider_usage(session: Any | None, *, input_tokens: int, output_tokens: int) -> None:
    """Accumulate one model call's provider-reported usage onto ``session.tokens``.

    Only exact counts are recorded: a call whose provider reported nothing adds
    nothing rather than an estimate. This is what ``/cost`` and the ``/goal``
    token delta read.
    """
    if session is None or (input_tokens <= 0 and output_tokens <= 0):
        return
    tokens = getattr(session, "tokens", None)
    if tokens is not None and callable(getattr(tokens, "record", None)):
        tokens.record(input_tokens=input_tokens, output_tokens=output_tokens, estimated=False)


def tap_provider_usage(inner: Callable[[Any], None] | None, session: Any) -> Callable[[Any], None]:
    """Wrap a runtime-event callback so every finished model call lands on ``session.tokens``.

    Recording per call, not per run, keeps the spend of a run that raises on a
    later call.
    """

    def _callback(event: Any) -> None:
        if getattr(event, "type", "") == "provider_request_end":
            data = getattr(event, "data", None) or {}
            record_provider_usage(
                session,
                input_tokens=int(data.get("input_tokens", 0) or 0),
                output_tokens=int(data.get("output_tokens", 0) or 0),
            )
        if inner is not None:
            inner(event)

    return _callback


def record_invoke_response(
    session: Any | None,
    *,
    prompt: str,
    response: LLMResponse,
) -> str:
    """Record token usage for an ``invoke()``-style call and return its text.

    Reads ``response.input_tokens``/``response.output_tokens`` (exact,
    provider-reported counts, not estimates) and records them via
    ``record_llm_turn`` against ``session`` — unless ``session`` is
    ``None``, in which case no recording happens.

    Returns ``response.content`` with leading/trailing whitespace
    stripped.
    """
    content = response.content.strip()
    if session is not None:
        record_llm_turn(
            session,
            prompt=prompt,
            response=content,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
    return content


def build_llm_run_info(
    *,
    session: Any,
    prompt: str,
    response_text: str,
    started: float | None = None,
    client: object | None = None,
    model: str | None = None,
    provider: str | None = None,
    final_system_prompt: str | None = None,
) -> LlmRunInfo:
    """Record token usage for a turn and assemble an ``LlmRunInfo`` for logging.

    Always records the turn on ``session`` via ``record_llm_turn`` using
    ``prompt``/``response_text`` (token counts are estimated from text,
    since no explicit counts are accepted here).

    ``latency_ms`` is computed from ``started`` (a ``time.monotonic()``
    timestamp) if given, else defaults to ``0``.

    ``model``/``provider`` are used as-is if given; otherwise, if
    ``client`` is provided, they're resolved via ``resolve_model_name``/
    ``resolve_provider_name``; otherwise they're left as ``None``.

    Returns a fully populated ``LlmRunInfo``, including the given
    ``response_text`` and ``final_system_prompt`` verbatim.
    """
    inp, out, _estimated = record_llm_turn(session, prompt=prompt, response=response_text)
    latency_ms = 0 if started is None else int((time.monotonic() - started) * 1000)
    return LlmRunInfo(
        model=model or (resolve_model_name(client) if client is not None else None),
        provider=provider or (resolve_provider_name(client) if client is not None else None),
        latency_ms=latency_ms,
        input_tokens=inp,
        output_tokens=out,
        response_text=response_text,
        final_system_prompt=final_system_prompt,
    )


def format_token_total(session: Any, *, direction: str) -> tuple[str, str]:
    """Format a session's accumulated token count for display.

    ``direction`` must be ``"input"`` or ``"output"`` — it's used to
    look up ``session.tokens.totals[direction]``,
    ``f"{direction}_measured"``, and ``f"{direction}_estimated"``
    (each defaulting to ``0`` if absent).

    Returns ``(row_label, formatted_value)``:
    - If both measured and estimated counts are present, the label is
      unchanged and the value shows the breakdown, e.g.
      ``"1,234 (1,000 provider + 234 est.)"``.
    - If only estimated counts are present, the label gets an
      ``" (est.)"`` suffix and the value is just the total.
    - If only measured counts are present, the label is unchanged and
      the value is just the total.
    """
    usage = session.tokens.totals
    measured = usage.get(f"{direction}_measured", 0)
    estimated = usage.get(f"{direction}_estimated", 0)
    total = usage.get(direction, 0)
    label = f"{direction} tokens"
    if estimated and measured:
        return (
            label,
            f"{total:,} ({measured:,} provider + {estimated:,} est.)",
        )
    if estimated:
        return (f"{label} (est.)", f"{total:,}")
    return (label, f"{total:,}")


__all__ = [
    "LlmRunInfo",
    "build_llm_run_info",
    "estimate_tokens",
    "format_token_total",
    "record_provider_usage",
    "tap_provider_usage",
    "record_invoke_response",
    "record_llm_turn",
    "resolve_model_name",
    "resolve_provider_name",
]
