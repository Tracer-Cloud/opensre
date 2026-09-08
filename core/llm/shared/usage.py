"""Process-wide LLM usage hook for token and cost accounting."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from core.llm.types import LLMResponse

logger = logging.getLogger(__name__)

UsageHook = Callable[[str, int, int], object]
_usage_hook: UsageHook | None = None


def set_usage_hook(hook: UsageHook | None) -> None:
    """Register or clear the process-wide usage observer (``model, tokens_in, tokens_out``)."""
    global _usage_hook
    if hook is not None and _usage_hook is not None:
        raise RuntimeError(
            "A usage hook is already registered. Either the previous owner "
            "failed to clear it (call set_usage_hook(None) in a finally), or "
            "two concurrent users of llm_client are conflicting. See the "
            "set_usage_hook docstring for the contract."
        )
    _usage_hook = hook


def emit_usage(model: str, tokens_in: int | None, tokens_out: int | None) -> None:
    """Notify the registered hook (if any). No-op when no hook or token counts missing."""
    hook = _usage_hook
    if hook is None:
        return
    if tokens_in is None and tokens_out is None:
        return
    hook(model, int(tokens_in or 0), int(tokens_out or 0))


def coerce_usage_tokens(
    usage: Any,
    *,
    input_key: str,
    output_key: str,
) -> tuple[int | None, int | None]:
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        raw_in = usage.get(input_key)
        raw_out = usage.get(output_key)
    else:
        raw_in = getattr(usage, input_key, None)
        raw_out = getattr(usage, output_key, None)
    inp = int(raw_in) if isinstance(raw_in, (int, float)) else None
    out = int(raw_out) if isinstance(raw_out, (int, float)) else None
    return inp, out


def extract_cache_tokens(usage: Any) -> tuple[int | None, int | None]:
    """Prompt-cache (read, write) token counts from a provider usage payload.

    Anthropic reports flat ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens``. OpenAI nests both counts under
    ``prompt_tokens_details`` (Chat Completions) or ``input_tokens_details``
    (Responses), as ``cached_tokens`` and ``cache_write_tokens``; older models
    omit the write-side key.
    ``None`` means the provider sent nothing — distinct from a measured 0.
    """
    if usage is None:
        return None, None

    def _field(container: Any, key: str) -> Any:
        if isinstance(container, dict):
            return container.get(key)
        return getattr(container, key, None)

    read = _field(usage, "cache_read_input_tokens")
    write = _field(usage, "cache_creation_input_tokens")
    if read is None or write is None:
        # OpenAI nests both counts: Chat Completions under
        # prompt_tokens_details, the Responses API under input_tokens_details.
        for details_key in ("prompt_tokens_details", "input_tokens_details"):
            details = _field(usage, details_key)
            if details is None:
                continue
            if read is None:
                read = _field(details, "cached_tokens")
            if write is None:
                write = _field(details, "cache_write_tokens")
            if read is not None or write is not None:
                break

    def _to_int(value: Any) -> int | None:
        return int(value) if isinstance(value, (int, float)) else None

    return _to_int(read), _to_int(write)


def _log_usage(
    model: str,
    inp: int | None,
    out: int | None,
    cache_read: int | None,
    cache_write: int | None,
) -> None:
    """One greppable line per call so cache hit rate is verifiable from logs."""
    if cache_read is not None or cache_write is not None:
        logger.debug(
            "[llm-usage] model=%s input=%s output=%s cache_read=%s cache_write=%s",
            model,
            inp,
            out,
            cache_read,
            cache_write,
        )
    else:
        logger.debug("[llm-usage] model=%s input=%s output=%s", model, inp, out)


def emit_provider_usage(
    model: str,
    usage: Any,
    *,
    input_key: str,
    output_key: str,
) -> tuple[int | None, int | None]:
    """Emit provider-reported usage from an arbitrary usage payload (agent clients).

    Returns the ``(input, output)`` counts so the caller can put them on the
    response it builds.
    """
    inp, out = coerce_usage_tokens(usage, input_key=input_key, output_key=output_key)
    _log_usage(model, inp, out, *extract_cache_tokens(usage))
    emit_usage(model, inp, out)
    return inp, out


def llm_response_with_usage(
    content: str,
    model: str,
    usage: Any,
    *,
    input_key: str,
    output_key: str,
) -> LLMResponse:
    inp, out = coerce_usage_tokens(usage, input_key=input_key, output_key=output_key)
    cache_read, cache_write = extract_cache_tokens(usage)
    _log_usage(model, inp, out, cache_read, cache_write)
    emit_usage(model, inp, out)
    return LLMResponse(
        content=content,
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_write,
    )
