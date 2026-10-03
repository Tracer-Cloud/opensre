"""Hold a skill at entry until the host-owned prerequisites it needs are met.

``SKILL_PREREQUISITES`` (config) names the checks a skill needs. Each check is
registered by id in ``infrastructure.harness_providers`` by the layer that can
answer it, and runs on the same resolved integrations the turn's tools receive,
so the gate and the tools it protects never disagree. A check that is not
registered, or raises, counts as met.

An unmet check replaces the skill body: in the shell, a setup menu is queued
and the turn's user message is parked so it can be resubmitted once setup
succeeds; elsewhere the result is a text instruction. Either way the skill is
not activated and nothing about the session's menus moves, so the resubmitted
message enters the skill exactly as the original would have.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from config.account import load_account_record, resolve_account_token
from config.constants.skill_prerequisites import (
    PREREQUISITE_ACTION_PREFIX,
    PREREQUISITE_CONTINUE_ACTION,
    PREREQUISITE_CONTINUE_OPTION,
    PREREQUISITE_CREDENTIAL_MISSING,
    PREREQUISITE_LOCAL_SETUP_OPTION,
    PREREQUISITE_MENU_NOTE,
    PREREQUISITE_MENU_TITLE,
    PREREQUISITE_OPEN_APP_ACTION,
    PREREQUISITE_OPEN_APP_OPTION,
    PREREQUISITE_SKIP_ACTION,
    PREREQUISITE_SKIP_OPTION,
    PREREQUISITE_STILL_MISSING_NOTE,
    SKILL_PREREQUISITES,
    SkillPrerequisite,
    prerequisite_service_label,
)
from core.agent_harness.spi.integrations import resolve_and_cache_integrations
from core.agent_harness.spi.session_state import (
    PendingUserChoice,
    arm_setup_resume,
    set_auto_command,
)
from core.agent_harness.tools import ActionToolScope
from infrastructure.analytics.capture import capture_skill_prerequisite_missing
from infrastructure.harness_providers import (
    integration_setup_command,
    registered_skill_prerequisite_checks,
    skill_prerequisite_met,
    skill_prerequisite_verdict,
)
from tools.interactive_shell.actions.ask_choice import menu_available

_CHOOSE_COMMAND = "/choose"

_QUEUED_INSTRUCTION = (
    "{skill} needs {service} before it starts, and no usable {service} token was "
    "found. A setup menu is queued; once {service} is connected the shell resubmits "
    "this message and the skill starts from its first step. End the turn now "
    "without narrating, without calling ask_user_choice, and without starting the "
    "workflow."
)
_TEXT_INSTRUCTION = (
    "{skill} needs {service} before it starts, and no usable {service} token was "
    "found. Tell the user to connect {service} in the OpenSRE app or run "
    "`opensre integrations setup {service_id}`, then ask again. Do not start the "
    "workflow."
)


def unmet_prerequisite(
    skill_name: str,
    resolved_integrations: Mapping[str, Any],
    *,
    service: str | None = None,
) -> SkillPrerequisite | None:
    """The first prerequisite of ``skill_name`` (for ``service`` when given) that is not met."""
    for prerequisite in SKILL_PREREQUISITES.get(skill_name, ()):
        if service is not None and prerequisite.service != service:
            continue
        if not skill_prerequisite_met(prerequisite.check, resolved_integrations):
            return prerequisite
    return None


def resumable_setup(skill_name: str, service: str) -> bool:
    """True when a registered check can confirm that setting up ``service`` unblocked ``skill_name``.

    Only such a setup parks the turn for replay: a resume needs evidence, and a
    service with no check (Slack for its own demo) would replay a turn even
    after the user cancelled the wizard.
    """
    registered = set(registered_skill_prerequisite_checks())
    return any(
        prerequisite.service == service and prerequisite.check in registered
        for prerequisite in SKILL_PREREQUISITES.get(skill_name, ())
    )


def setup_verdict(
    skill_name: str, service: str, resolved_integrations: Mapping[str, Any]
) -> bool | None:
    """Whether ``service``'s setup made ``skill_name``'s prerequisites hold; None when no check can tell.

    Unlike skill entry, which lets an unregistered or failing check pass, this
    answers True only on a registered check's positive answer.
    """
    verdicts = [
        skill_prerequisite_verdict(prerequisite.check, resolved_integrations)
        for prerequisite in SKILL_PREREQUISITES.get(skill_name, ())
        if prerequisite.service == service
    ]
    if not verdicts or None in verdicts:
        return None
    return all(verdicts)


def gate_skill_entry(
    skill_name: str,
    ctx: Any,
    *,
    resolved_integrations: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the result that replaces ``skill_name``'s body while a prerequisite is unmet.

    None means the skill may start. ``resolved_integrations`` is what the
    turn's tools receive; a host entry with none resolves the session's own.
    """
    if not SKILL_PREREQUISITES.get(skill_name):
        return None
    session = getattr(ctx, "session", None)
    missing = unmet_prerequisite(skill_name, _entry_integrations(session, resolved_integrations))
    if missing is None:
        return None
    if session is None or not isinstance(ctx, ActionToolScope) or not menu_available(ctx):
        _record_missing(skill_name, missing)
        return _blocked_result(skill_name, missing, menu="unavailable", template=_TEXT_INSTRUCTION)
    _hold(session, skill_name, ctx.turn_user_message, missing)
    return _blocked_result(skill_name, missing, menu="queued", template=_QUEUED_INSTRUCTION)


def setup_needed(session: Any, skill_name: str) -> bool:
    """True when ``skill_name`` has a prerequisite the session's integrations do not meet."""
    if not SKILL_PREREQUISITES.get(skill_name):
        return False
    return unmet_prerequisite(skill_name, _entry_integrations(session, None)) is not None


def hold_for_setup(session: Any, skill_name: str, text: str) -> bool:
    """Queue the setup menu and park ``text`` when ``skill_name`` has an unmet prerequisite.

    For a shell host that asks its own questions before the model enters the
    skill, so setup comes first; model entry goes through
    :func:`gate_skill_entry`. False, with nothing parked or queued, when every
    prerequisite holds.
    """
    if not SKILL_PREREQUISITES.get(skill_name):
        return False
    missing = unmet_prerequisite(skill_name, _entry_integrations(session, None))
    if missing is None:
        return False
    _hold(session, skill_name, text, missing)
    return True


def prerequisite_menu(service: str, *, still_missing: bool = False) -> PendingUserChoice:
    """The setup menu for ``service``: the OpenSRE app first when signed in, then local setup."""
    label = prerequisite_service_label(service)
    commands: dict[str, str] = {}
    if _signed_in():
        commands[PREREQUISITE_OPEN_APP_OPTION.format(service=label)] = prerequisite_action(
            PREREQUISITE_OPEN_APP_ACTION, service
        )
    commands[PREREQUISITE_LOCAL_SETUP_OPTION.format(service=label)] = integration_setup_command(
        service
    )
    commands[PREREQUISITE_CONTINUE_OPTION.format(service=label)] = prerequisite_action(
        PREREQUISITE_CONTINUE_ACTION, service
    )
    commands[PREREQUISITE_SKIP_OPTION] = prerequisite_action(PREREQUISITE_SKIP_ACTION, service)
    note = PREREQUISITE_STILL_MISSING_NOTE if still_missing else PREREQUISITE_MENU_NOTE
    return PendingUserChoice(
        title=PREREQUISITE_MENU_TITLE.format(service=label),
        options=tuple(commands),
        note=note.format(service=label),
        commands=commands,
        custom_answer=False,
    )


def queue_prerequisite_menu(session: Any, service: str, *, still_missing: bool = False) -> None:
    """Store the setup menu for ``service`` and queue ``/choose`` so the shell opens it."""
    session.pending_user_choice = prerequisite_menu(service, still_missing=still_missing)
    set_auto_command(session, _CHOOSE_COMMAND)


def prerequisite_action(action: str, service: str) -> str:
    """The ``commands`` value of one host-action row. Not a slash command."""
    return f"{PREREQUISITE_ACTION_PREFIX}{action}:{service}"


def parse_prerequisite_action(command: str) -> tuple[str, str] | None:
    """``(action, service)`` when ``command`` is a setup-menu host action, else None."""
    if not command.startswith(PREREQUISITE_ACTION_PREFIX):
        return None
    action, separator, service = command[len(PREREQUISITE_ACTION_PREFIX) :].partition(":")
    if not separator or not service:
        return None
    return action, service


def is_prerequisite_menu(pending: PendingUserChoice) -> bool:
    """True when ``pending`` is the setup menu this gate queued."""
    return any(parse_prerequisite_action(command) for command in pending.commands.values())


def _hold(session: Any, skill_name: str, text: str, missing: SkillPrerequisite) -> None:
    _record_missing(skill_name, missing)
    arm_setup_resume(session, text, skill=skill_name, service=missing.service)
    queue_prerequisite_menu(session, missing.service)


def _record_missing(skill_name: str, missing: SkillPrerequisite) -> None:
    capture_skill_prerequisite_missing(
        skill=skill_name,
        check=missing.check,
        reason_code=PREREQUISITE_CREDENTIAL_MISSING,
    )


def _entry_integrations(
    session: Any, resolved_integrations: Mapping[str, Any] | None
) -> Mapping[str, Any]:
    if resolved_integrations is not None:
        return resolved_integrations
    if session is None or not hasattr(session, "resolved_integrations_cache"):
        return {}
    return resolve_and_cache_integrations(session)


def _signed_in() -> bool:
    """True when this machine is signed in to an OpenSRE organization."""
    record = load_account_record()
    return (
        record is not None
        and bool(record.organization_id.strip())
        and bool(resolve_account_token())
    )


def _blocked_result(
    skill_name: str, missing: SkillPrerequisite, *, menu: str, template: str
) -> dict[str, Any]:
    label = prerequisite_service_label(missing.service)
    instruction = template.format(skill=skill_name, service=label, service_id=missing.service)
    # ``summary`` is what the user sees; ``content`` replaces the skill body.
    return {
        "ok": True,
        "name": skill_name,
        "summary": f"{label} is needed before {skill_name}",
        "content": instruction,
        "prerequisite": {
            "check": missing.check,
            "service": missing.service,
            "reason_code": PREREQUISITE_CREDENTIAL_MISSING,
            "menu": menu,
        },
    }


__all__ = [
    "gate_skill_entry",
    "hold_for_setup",
    "is_prerequisite_menu",
    "parse_prerequisite_action",
    "prerequisite_action",
    "prerequisite_menu",
    "queue_prerequisite_menu",
    "resumable_setup",
    "setup_needed",
    "setup_verdict",
    "unmet_prerequisite",
]
