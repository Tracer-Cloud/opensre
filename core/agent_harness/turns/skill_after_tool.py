"""Run a skill's ``after_tool`` menus through the same session queue as ``pre_execute``.

The model can skip a mid-flow ``ask_user_choice``. When the active skill
declares a hook, the host opens that menu after the named tool succeeds.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.prompts.skills.after_tool_options import options_from_tool_result
from core.agent_harness.prompts.skills.loader import (
    ActionSkill,
    SkillAfterToolHook,
    list_action_skills,
)
from core.agent_harness.session.pending_choice import PendingUserChoice, question_key
from core.agent_harness.session.terminal_access import session_terminal, set_auto_command
from core.tool.execution import (
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
)

_CHOOSE_COMMAND = "/choose"
_MIN_OPTIONS = 2


def _active_skill(session: Any) -> ActionSkill | None:
    name = getattr(session, "active_skill", None)
    if not isinstance(name, str) or not name:
        return None
    return next((skill for skill in list_action_skills() if skill.name == name), None)


def _hook_key(skill: ActionSkill, hook: SkillAfterToolHook) -> str:
    title = str(hook.call.args.get("title") or hook.after)
    return f"{skill.name}:{hook.after}:{title}"


def _already_fired(session: Any, key: str) -> bool:
    fired = getattr(session, "skill_hooks_fired", None)
    return isinstance(fired, set) and key in fired


def _already_answered(session: Any, hook: SkillAfterToolHook) -> bool:
    """True when the session already holds the user's answer to this hook's question."""
    settled = getattr(session, "questions_already_answered", None)
    if not isinstance(settled, set):
        return False
    return question_key(str(hook.call.args.get("title") or "")) in settled


def _mark_fired(session: Any, key: str) -> None:
    fired = getattr(session, "skill_hooks_fired", None)
    if isinstance(fired, set):
        fired.add(key)
        return
    session.skill_hooks_fired = {key}


def _queue_choice(session: Any, title: str, options: tuple[str, ...], note: str) -> bool:
    if (
        session_terminal(session) is None
        or getattr(session, "pending_user_choice", None) is not None
    ):
        return False
    if len(options) < _MIN_OPTIONS:
        return False
    session.pending_user_choice = PendingUserChoice(title=title, options=options, note=note)
    set_auto_command(session, _CHOOSE_COMMAND)
    terminal = session_terminal(session)
    if terminal is not None:
        terminal.awaiting_handoff_answer = True
    return True


def _run_hook(session: Any, hook: SkillAfterToolHook, result: ToolExecutionResult) -> bool:
    args = dict(hook.call.args)
    built = options_from_tool_result(hook.options_from, result.details, hook.options_extra)
    if built:
        args["options"] = list(built)
    title = str(args.get("title") or "").strip()
    raw_options = args.get("options")
    options = (
        tuple(item.strip() for item in raw_options if isinstance(item, str) and item.strip())
        if isinstance(raw_options, list)
        else ()
    )
    note = str(args.get("note") or "").strip()
    if hook.call.tool != "ask_user_choice" or not title:
        return False
    return _queue_choice(session, title, options, note)


def with_skill_after_tool(
    base: ToolExecutionHooks | None,
    session: Any,
) -> ToolExecutionHooks:
    """Wrap ``base`` so an active skill's ``after_tool`` hooks can queue a menu."""
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def after(
        request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> ToolExecutionPatch | None:
        patch = base_after(request, result) if base_after is not None else None
        if result.is_error:
            return patch
        skill = _active_skill(session)
        if skill is None or getattr(session, "pending_user_choice", None) is not None:
            return patch
        name = request.tool_call.name
        for hook in skill.after_tool:
            if hook.after != name:
                continue
            key = _hook_key(skill, hook)
            if _already_fired(session, key) or _already_answered(session, hook):
                continue
            if not _run_hook(session, hook, result):
                continue
            _mark_fired(session, key)
            break
        return patch

    return ToolExecutionHooks(
        before_tool_call=base_before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=base_batch,
    )


__all__ = ["with_skill_after_tool"]
