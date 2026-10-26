"""Helpers for session-scoped reasoning effort overrides."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum


class ReasoningEffort(StrEnum):
    """Closed user-facing reasoning-effort vocabulary for REPL and env overrides."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"  # user-facing alias; runtime maps to xhigh


ReasoningEffortChoice = ReasoningEffort

REASONING_EFFORT_OPTIONS: tuple[ReasoningEffort, ...] = tuple(ReasoningEffort)

_RUNTIME_ENV_KEY = "OPENSRE_REASONING_EFFORT"
_RUNTIME_VALUES = frozenset({"low", "medium", "high", "xhigh"})

_reasoning_effort_session: ContextVar[str | None] = ContextVar(
    "opensre_reasoning_effort_session", default=None
)


def parse_reasoning_effort(value: str | None) -> ReasoningEffort | None:
    """Return the normalized user-facing effort choice, if valid."""
    if value is None:
        return None
    normalized = value.strip().lower()
    try:
        return ReasoningEffort(normalized)
    except ValueError:
        return None


def _coerce_reasoning_effort(choice: ReasoningEffort | str | None) -> ReasoningEffort | None:
    """Normalize session/env plain strings into enum members at API boundaries."""
    if choice is None or isinstance(choice, ReasoningEffort):
        return choice
    return parse_reasoning_effort(choice)


def runtime_reasoning_effort(choice: ReasoningEffort | str | None) -> str | None:
    """Map a user-facing choice to the runtime value sent to model providers."""
    coerced = _coerce_reasoning_effort(choice)
    if coerced is None:
        return None
    return ReasoningEffort.XHIGH.value if coerced is ReasoningEffort.MAX else coerced.value


def display_reasoning_effort(choice: ReasoningEffort | str | None) -> str:
    """Human-readable label for tables and slash-command output."""
    coerced = _coerce_reasoning_effort(choice)
    if coerced is None:
        return "(default)"
    runtime = runtime_reasoning_effort(coerced)
    if runtime and runtime != coerced.value:
        return f"{coerced} (runtime: {runtime})"
    return coerced.value


def _bare_model_id(model: str | None) -> str:
    normalized = (model or "").strip().lower()
    if "/" in normalized:
        return normalized.rsplit("/", 1)[-1]
    return normalized


def model_default_reasoning_effort(model: str | None) -> str | None:
    """Effort sent when neither the session nor the environment sets one.

    GPT-6.1 Sol's product default is extra high. Other models return ``None``
    so the provider keeps its own default.
    """
    if _bare_model_id(model).startswith("gpt-6.1"):
        return ReasoningEffort.XHIGH.value
    return None


def resolve_reasoning_effort(model: str | None = None) -> str | None:
    """Session override, then environment, then the model family's built-in default."""
    active = get_active_reasoning_effort()
    if active is not None:
        return active
    return model_default_reasoning_effort(model)


def get_active_reasoning_effort() -> str | None:
    """Return the runtime reasoning-effort value for this logical context.

    Order: in-REPL session override (``apply_reasoning_effort``), then
    ``OPENSRE_REASONING_EFFORT`` in the process environment. Model-family
    defaults are applied by ``resolve_reasoning_effort``.
    """
    session = _reasoning_effort_session.get()
    if session is not None:
        return session if session in _RUNTIME_VALUES else None
    value = os.getenv(_RUNTIME_ENV_KEY, "").strip().lower()
    if value in _RUNTIME_VALUES:
        return value
    return None


def provider_supports_reasoning_effort(provider: str | None) -> bool:
    """Whether the current provider is wired to consume the REPL effort override."""
    return (provider or "").strip().lower() in {"openai", "codex"}


def infer_reasoning_effort_default(provider: str | None, model: str | None) -> str | None:
    """Best-effort default reasoning level for providers we wire today.

    Returns ``None`` when the provider/model should fall back to its native
    model default and we do not want to overstate a more precise value.
    """
    normalized_provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip().lower()
    if normalized_provider in {"openai", "codex"}:
        model_effort = model_default_reasoning_effort(normalized_model)
        if model_effort is not None:
            return model_effort
    if normalized_provider == "openai":
        if normalized_model.startswith(("gpt-5.1", "gpt-5.2")):
            return "none"
        if normalized_model.startswith("gpt-5-pro"):
            return "high"
        if normalized_model.startswith(("gpt-5", "o1", "o3", "o4")):
            return "medium"
    return None


def describe_reasoning_effort_default(provider: str | None, model: str | None) -> str:
    """Human-readable default behavior for `/effort` when no override is set."""
    normalized_provider = (provider or "").strip().lower() or "unknown"
    visible_model = (model or "").strip() or "provider default"
    if not provider_supports_reasoning_effort(normalized_provider):
        return f"{normalized_provider} does not use reasoning-effort overrides"
    inferred = infer_reasoning_effort_default(normalized_provider, visible_model)
    if inferred is not None:
        return f"{normalized_provider} · {visible_model}: {inferred}"
    return f"{normalized_provider} · {visible_model}: model default"


@contextmanager
def apply_reasoning_effort(choice: ReasoningEffort | str | None) -> Iterator[None]:
    """Temporarily expose a session effort override to downstream model clients.

    ``choice is None`` means defer to shell/env defaults: do not clear
    ``OPENSRE_REASONING_EFFORT`` or mutate the process environment.

    Non-None choices use a :class:`contextvars.ContextVar` so concurrent REPL or
    CLI invocations on different threads/tasks do not race on ``os.environ``.
    """
    coerced = _coerce_reasoning_effort(choice)
    if coerced is None:
        yield
        return
    runtime = runtime_reasoning_effort(coerced)
    token = _reasoning_effort_session.set(runtime)
    try:
        yield
    finally:
        _reasoning_effort_session.reset(token)


__all__ = [
    "REASONING_EFFORT_OPTIONS",
    "ReasoningEffort",
    "ReasoningEffortChoice",
    "apply_reasoning_effort",
    "describe_reasoning_effort_default",
    "display_reasoning_effort",
    "get_active_reasoning_effort",
    "infer_reasoning_effort_default",
    "model_default_reasoning_effort",
    "parse_reasoning_effort",
    "resolve_reasoning_effort",
    "provider_supports_reasoning_effort",
    "runtime_reasoning_effort",
]
