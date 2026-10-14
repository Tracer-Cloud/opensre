"""Shared preparation of generic cron tasks for CLI and terminal input."""

from __future__ import annotations

import click

from core.agent_harness import load_loop_template, pin_recurring_skill, validate_skill_inputs
from infrastructure.scheduling.scheduler.credentials import requires_explicit_chat_id
from infrastructure.scheduling.scheduler.cron_expression import (
    build_cron_trigger,
    cap_cron_at_most_hourly,
)
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_DESCRIPTION_PARAM,
    LOOP_MODE_AGENT,
    LOOP_MODE_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_SKILL_PARAM,
    LOOP_STATELESS_PARAM,
    LOOP_TEMPLATE_PARAM,
)
from infrastructure.scheduling.scheduler.loop_prompt import loop_skill_reference
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


def prepare_cron_task(
    name: str,
    description: str,
    kind: str,
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    window_hours: int,
    prompt: str,
    template: str | None,
    mode: str | None,
    skill_name: str,
    stateless: bool,
    owner: str,
    repo: str,
    branch: str,
    github_connection_id: str,
    pr_number: int | None,
    city: str,
) -> ScheduledTask:
    """Validate and prepare a scheduled task without saving or running it."""
    task_kind = TaskKind(kind)
    if template:
        if task_kind != TaskKind.MANUAL_LOOP:
            raise click.ClickException("--template is only valid with --kind manual_loop.")
        if prompt.strip():
            raise click.ClickException("Use either --template or --prompt, not both.")
        if not (owner.strip() and repo.strip()):
            raise click.UsageError("--template requires --owner and --repo.")
        loop_template = load_loop_template(template)
        prompt = loop_template.prompt
        name = name.strip() or loop_template.name
        cron_expr = cron_expr.strip() or loop_template.cron
        mode = mode or loop_template.mode or None
    if not cron_expr.strip():
        raise click.UsageError("Missing option '--cron'.")
    # Validate cron expression by constructing the APScheduler trigger
    try:
        build_cron_trigger(cron_expr, timezone)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _validate_chat_id_for_provider(provider, chat_id)

    if mode is not None and task_kind != TaskKind.MANUAL_LOOP:
        raise click.ClickException("--mode is only valid with --kind manual_loop.")
    if stateless and (task_kind != TaskKind.MANUAL_LOOP or mode != LOOP_MODE_AGENT):
        raise click.ClickException(
            "--stateless is only valid with --kind manual_loop --mode agent."
        )
    normalized_prompt = prompt.strip()
    loop_skill = _loop_skill(skill_name) if mode == LOOP_MODE_AGENT else ""
    if loop_skill and not normalized_prompt:
        normalized_prompt = f"Run the {loop_skill} skill."
    if task_kind == TaskKind.MANUAL_LOOP:
        if not normalized_prompt:
            raise click.ClickException("--prompt is required when --kind is manual_loop.")
    elif normalized_prompt:
        raise click.ClickException("--prompt is only valid with --kind manual_loop.")
    pinned_name = ""
    pinned_revision = ""
    if task_kind == TaskKind.RECURRING_SKILL:
        if not skill_name.strip():
            raise click.ClickException("--skill is required when --kind is recurring_skill.")
        try:
            pinned_name, pinned_revision = pin_recurring_skill(skill_name)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
    elif skill_name.strip() and not loop_skill:
        raise click.ClickException(
            "--skill is only valid with --kind recurring_skill or --kind manual_loop --mode agent."
        )
    task_params = {LOOP_PROMPT_PARAM: normalized_prompt} if normalized_prompt else {}
    if template:
        task_params[LOOP_TEMPLATE_PARAM] = template
    if description.strip():
        task_params[LOOP_DESCRIPTION_PARAM] = " ".join(description.split())
    if mode == LOOP_MODE_AGENT:
        task_params[LOOP_MODE_PARAM] = mode
    if loop_skill:
        task_params[LOOP_SKILL_PARAM] = loop_skill
    if stateless:
        task_params[LOOP_STATELESS_PARAM] = "true"
    if task_kind is TaskKind.MANUAL_LOOP and mode == LOOP_MODE_AGENT:
        if city.strip():
            raise click.UsageError("--city is only valid for morning briefings.")
        if bool(owner.strip()) != bool(repo.strip()):
            raise click.UsageError("Supply both --owner and --repo for a repository task.")
        if (branch.strip() or pr_number) and not owner.strip():
            raise click.UsageError("--branch and --pr require --owner and --repo.")
        if branch.strip() and pr_number is not None:
            raise click.UsageError("Use either --branch or --pr, not both.")
        if owner.strip():
            task_params.update(owner=owner.strip(), repo=repo.strip())
        if branch.strip():
            task_params["branch"] = branch.strip()
        if pr_number is not None:
            task_params["pr_number"] = str(pr_number)
        skill_inputs = {}
    else:
        skill_inputs = _recurring_skill_inputs(
            pinned_name,
            city=city,
            owner=owner,
            repo=repo,
            branch=branch,
            pr_number=pr_number,
        )
    if task_kind is TaskKind.MANUAL_LOOP:
        cron_expr = cap_cron_at_most_hourly(cron_expr, timezone)

    from integrations.github import github_schedule_inputs

    try:
        skill_inputs = github_schedule_inputs(pinned_name, skill_inputs, github_connection_id)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    return ScheduledTask(
        name=name.strip(),
        kind=task_kind,
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(provider),
        chat_id=chat_id.strip(),
        window_hours=window_hours,
        skill_name=pinned_name,
        skill_revision=pinned_revision,
        skill_inputs=skill_inputs,
        params=task_params,
    )


def _loop_skill(skill_name: str) -> str:
    """The card's canonical name or the installed folder an agent loop follows; "" for none."""
    if not skill_name.strip():
        return ""
    try:
        return loop_skill_reference(skill_name)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def _recurring_skill_inputs(
    skill_name: str,
    *,
    city: str,
    owner: str,
    repo: str,
    branch: str,
    pr_number: int | None,
) -> dict[str, str]:
    """Validate and serialize inputs for the selected recurring skill."""
    normalized_city = city.strip()
    values_supplied = bool(owner.strip() or repo.strip() or branch.strip() or pr_number)
    if skill_name == "delivering-morning-briefings":
        if values_supplied:
            raise click.UsageError(
                "--owner, --repo, --branch, and --pr are only valid with "
                "--kind recurring_skill --skill reporting-github-ci-failures."
            )
        return validate_skill_inputs({"city": normalized_city} if normalized_city else {})
    if normalized_city:
        raise click.UsageError(
            "--city is only valid with --kind recurring_skill --skill delivering-morning-briefings."
        )
    if skill_name != "reporting-github-ci-failures":
        if values_supplied:
            raise click.UsageError(
                "--owner, --repo, --branch, and --pr are only valid with "
                "--kind recurring_skill --skill reporting-github-ci-failures."
            )
        return validate_skill_inputs({})
    if not owner.strip() or not repo.strip():
        raise click.UsageError(
            "--owner and --repo are required for skill reporting-github-ci-failures."
        )
    if branch.strip() and pr_number is not None:
        raise click.UsageError("Use either --branch or --pr, not both.")
    params = {"owner": owner.strip(), "repo": repo.strip()}
    if branch.strip():
        params["branch"] = branch.strip()
    if pr_number is not None:
        params["pr_number"] = str(pr_number)
    return validate_skill_inputs(params)


def _validate_chat_id_for_provider(provider: str, chat_id: str) -> None:
    """Reject a task with no destination the scheduler could deliver to.

    Which providers can resolve a destination on their own is the scheduler's
    knowledge, not the CLI's — see
    :func:`infrastructure.scheduling.scheduler.credentials.requires_explicit_chat_id`.
    """
    if chat_id.strip() or not requires_explicit_chat_id(provider):
        return
    raise click.UsageError(
        f"--chat-id is required for provider {provider}. "
        "This provider has no configured destination to fall back on."
    )
