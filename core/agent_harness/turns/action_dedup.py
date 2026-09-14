"""Suppress replayed action-tool calls within one turn.

A guarded tool (``slash_invoke`` / ``shell_run`` / ``cli_exec``) that the model
re-emits with identical arguments after it already succeeded this turn is
blocked, so a stuttering model cannot run the same side-effecting command
twice. Kept apart from the turn driver: this is a self-contained hook policy,
not part of driving the loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.llm.types import ToolCall
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
    public_tool_input,
)

# Local REPL tools covered by consecutive identical-batch suppress (oracle 202 /
# 203): slash_invoke, shell_run, cli_exec. One rule — no per-tool carve-outs.
_DEDUPE_ACTION_TOOL_NAMES: frozenset[str] = frozenset({"slash_invoke", "shell_run", "cli_exec"})

# Hashable identity for one guarded call (no hot-path json.dumps).
_ActionCallFingerprint = tuple[Any, ...]


def coerce_fingerprint_quiet(value: Any) -> bool:
    """Match ``shell_run`` quiet coercion so retries compare equal."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _action_call_fingerprint(name: str, args: Any) -> _ActionCallFingerprint:
    """Stable identity for one guarded action call (schema fields only)."""
    if not isinstance(args, dict):
        args = {}

    if name == "slash_invoke":
        command = str(args.get("command", ""))
        raw = args.get("args")
        argv = tuple(str(item) for item in raw) if isinstance(raw, (list, tuple)) else ()
        return (name, command, argv)

    if name == "cli_exec":
        return (name, str(args.get("payload", "")).strip())

    # shell_run
    return (
        name,
        str(args.get("command", "")),
        coerce_fingerprint_quiet(args.get("quiet", False)),
    )


def with_duplicate_action_call_guard(
    base: ToolExecutionHooks | None = None,
) -> ToolExecutionHooks:
    """Block replaying guarded action calls already covered by the success snapshot.

    Suppress ``slash_invoke`` / ``shell_run`` / ``cli_exec`` when the call's
    fingerprint is in ``last_fully_succeeded_batch``. Two guarded calls in one
    response never reach this hook: the executor rejects a response with more
    than one action before any hook runs.

    Snapshot updates at the next batch boundary:

    - Fully successful batch → replace snapshot with that batch (so A → B → A
      still allows the second A after B replaces the snapshot).
    - Mixed batch (suppressed replay + new success) → replace snapshot with
      *only* the newly succeeded fingerprints. That blocks an immediate re-emit
      of the new action without retaining suppressed members (which would block
      a later intentional standalone replay of A after {A suppressed, C ran}).
    - Pure suppress or total failure → leave the snapshot alone (a third
      identical replay stays blocked; failed calls may still retry).

    Limitation (intentional): the same batch twice in one turn — lone or
    multi — is also suppressed; accidental replay and “run that again” are
    indistinguishable without parsing the user message. Ask again next turn.
    """
    last_fully_succeeded_batch: frozenset[_ActionCallFingerprint] = frozenset()
    current_batch: frozenset[_ActionCallFingerprint] = frozenset()
    batch_succeeded: set[_ActionCallFingerprint] = set()
    has_open_batch = False
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def before_batch(tool_calls: Sequence[ToolCall]) -> None:
        nonlocal last_fully_succeeded_batch, current_batch, batch_succeeded, has_open_batch
        if base_batch is not None:
            base_batch(tool_calls)
        if has_open_batch and current_batch:
            succeeded = frozenset(batch_succeeded)
            if succeeded == current_batch:
                last_fully_succeeded_batch = current_batch
            elif succeeded:
                # Mixed suppress/success: snapshot is only what newly ran.
                # Do not retain suppressed members — that would block a later
                # intentional standalone A after {A suppressed, C succeeded}.
                last_fully_succeeded_batch = succeeded
            # else: pure suppress or all-error — leave snapshot intact.
        keys: list[_ActionCallFingerprint] = []
        for tool_call in tool_calls:
            if tool_call.name not in _DEDUPE_ACTION_TOOL_NAMES:
                continue
            keys.append(
                _action_call_fingerprint(tool_call.name, public_tool_input(tool_call.input))
            )
        current_batch = frozenset(keys)
        batch_succeeded = set()
        has_open_batch = True

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        name = request.tool_call.name
        # Interleaved A -> B -> A still runs, because a fully successful {B}
        # replaces the snapshot.
        if name in _DEDUPE_ACTION_TOOL_NAMES:
            key = _action_call_fingerprint(name, public_tool_input(request.arguments))
            if key in last_fully_succeeded_batch:
                return BeforeToolCallResult(
                    blocked=True,
                    reason=(
                        f"Already ran {name} with identical arguments "
                        "this turn. Do not repeat it; finish with no further tool calls."
                    ),
                    metadata={"suppressed_duplicate": True},
                )
        if base_before is not None:
            return base_before(request)
        return None

    def after(
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> ToolExecutionPatch | None:
        if request.tool_call.name in _DEDUPE_ACTION_TOOL_NAMES and not result.is_error:
            batch_succeeded.add(
                _action_call_fingerprint(
                    request.tool_call.name, public_tool_input(request.arguments)
                )
            )
        if base_after is not None:
            return base_after(request, result)
        return None

    return ToolExecutionHooks(
        before_tool_call=before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=before_batch,
    )


__all__ = ["coerce_fingerprint_quiet", "with_duplicate_action_call_guard"]
