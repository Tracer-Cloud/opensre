"""Suppress replayed action-tool calls within one turn.

A guarded local action tool that the model
re-emits with identical arguments after it already succeeded this turn is
blocked, so a stuttering model cannot run the same side-effecting command
twice. Kept apart from the turn driver: this is a self-contained hook policy,
not part of driving the loop.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
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
# 203). Directory changes are guarded across provider batches but intentionally
# remain repeatable inside one ordered batch: the same relative path can resolve
# to a different destination after the preceding change.
_DEDUPE_ACTION_TOOL_NAMES: frozenset[str] = frozenset(
    {"slash_invoke", "shell_run", "cli_exec", "set_working_directory"}
)
_SAME_BATCH_DEDUPE_ACTION_TOOL_NAMES: frozenset[str] = frozenset(
    {"slash_invoke", "shell_run", "cli_exec"}
)

# Hashable identity for one guarded call (no hot-path json.dumps).
_ActionCallFingerprint = tuple[Any, ...]


def coerce_fingerprint_quiet(value: Any) -> bool:
    """Match ``shell_run`` quiet coercion so retries compare equal."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _tool_result_succeeded(result: ToolExecutionResult) -> bool:
    """Treat explicit action-level ``ok: false`` payloads as failures."""
    if result.is_error:
        return False
    return not (isinstance(result.details, dict) and result.details.get("ok") is False)


def _action_call_fingerprint(
    name: str,
    args: Any,
    *,
    working_directory: str | None = None,
) -> _ActionCallFingerprint:
    """Stable identity for one guarded action call and its stateful destination."""
    if not isinstance(args, dict):
        args = {}

    if name == "slash_invoke":
        command = str(args.get("command", ""))
        raw = args.get("args")
        argv = tuple(str(item) for item in raw) if isinstance(raw, (list, tuple)) else ()
        return (name, command, argv)

    if name == "cli_exec":
        return (name, str(args.get("payload", "")).strip())

    if name == "set_working_directory":
        path = Path(str(args.get("path", "")).strip()).expanduser()
        if working_directory is not None and not path.is_absolute():
            path = Path(working_directory) / path
        try:
            resolved_path = str(path.resolve())
        except OSError:
            resolved_path = str(path.absolute())
        return (name, resolved_path)

    # shell_run
    return (
        name,
        str(args.get("command", "")),
        coerce_fingerprint_quiet(args.get("quiet", False)),
    )


def with_duplicate_action_call_guard(
    base: ToolExecutionHooks | None = None,
    *,
    working_directory: Callable[[], str] | None = None,
) -> ToolExecutionHooks:
    """Block replaying guarded action calls already covered by the success snapshot.

    Suppress guarded local action tools when the call's fingerprint is in
    ``last_fully_succeeded_batch`` *or* already succeeded earlier in the
    current provider batch (same-batch duplicates). Guarded tools are
    sequential, so ``batch_succeeded`` is visible to the next ``before()`` in
    the batch.

    Snapshot updates at the next batch boundary:

    - Fully successful batch → replace snapshot with that batch (so A → B → A
      still allows the second A after B replaces the snapshot).
    - Mixed batch (suppressed replay + new success) → replace snapshot with
      *only* the newly succeeded fingerprints. That blocks an immediate re-emit
      of the new action without retaining suppressed members (which would block
      a later intentional standalone replay of A after {A suppressed, C ran}).
    - Pure suppress or total failure → leave the snapshot alone (a third
      identical replay stays blocked; failed calls may still retry).

    Limitation (intentional): the same destination batch twice in one turn —
    lone or multi — is also suppressed; accidental replay and “run that again”
    are indistinguishable without parsing the user message. Relative directory
    changes use the live base directory, so different destinations do not
    collide. Ask again next turn to intentionally replay an identical action.
    """
    last_fully_succeeded_batch: frozenset[_ActionCallFingerprint] = frozenset()
    current_batch: set[_ActionCallFingerprint] = set()
    batch_succeeded: set[_ActionCallFingerprint] = set()
    request_fingerprints: dict[str, _ActionCallFingerprint] = {}
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
            requested = frozenset(current_batch)
            if succeeded == requested:
                last_fully_succeeded_batch = requested
            elif succeeded:
                # Mixed suppress/success: snapshot is only what newly ran.
                # Do not retain suppressed members — that would block a later
                # intentional standalone A after {A suppressed, C succeeded}.
                last_fully_succeeded_batch = succeeded
            # else: pure suppress or all-error — leave snapshot intact.
        current_batch = set()
        batch_succeeded = set()
        request_fingerprints.clear()
        has_open_batch = True

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        name = request.tool_call.name
        # Membership across the prior success snapshot *and* earlier successes
        # in this batch. Interleaved A -> B -> A still runs, because a fully
        # successful {B} replaces the snapshot. Same-batch duplicates (two
        # identical cli_exec in one provider response) hit batch_succeeded.
        if name in _DEDUPE_ACTION_TOOL_NAMES:
            current_directory = working_directory() if working_directory is not None else None
            key = _action_call_fingerprint(
                name,
                public_tool_input(request.arguments),
                working_directory=current_directory,
            )
            current_batch.add(key)
            request_fingerprints[request.tool_call.id] = key
            repeated_in_batch = (
                name in _SAME_BATCH_DEDUPE_ACTION_TOOL_NAMES and key in batch_succeeded
            )
            if key in last_fully_succeeded_batch or repeated_in_batch:
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
        if request.tool_call.name in _DEDUPE_ACTION_TOOL_NAMES and _tool_result_succeeded(result):
            current_directory = working_directory() if working_directory is not None else None
            fallback = _action_call_fingerprint(
                request.tool_call.name,
                public_tool_input(request.arguments),
                working_directory=current_directory,
            )
            batch_succeeded.add(request_fingerprints.pop(request.tool_call.id, fallback))
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
