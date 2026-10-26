"""Template loops: identified by template name, shown with the text they run."""

from __future__ import annotations

from pathlib import Path

from core.agent_harness import load_loop_template
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_DESCRIPTION_PARAM,
    LOOP_MODE_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_TEMPLATE_PARAM,
)
from infrastructure.scheduling.scheduler.loops import resolve_loop_summary
from infrastructure.scheduling.scheduler.registry_telemetry import registry_entry
from infrastructure.scheduling.scheduler.storage.task_store import add_task, list_tasks
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


def _template_task(prompt_copy: str, description: str = "") -> ScheduledTask:
    params = {
        LOOP_TEMPLATE_PARAM: "pr-ci",
        LOOP_PROMPT_PARAM: prompt_copy,
        LOOP_MODE_PARAM: "agent",
        "owner": "acme",
        "repo": "widgets",
    }
    if description:
        params[LOOP_DESCRIPTION_PARAM] = description
    return ScheduledTask(
        name="PR CI",
        kind=TaskKind.MANUAL_LOOP,
        cron="28 * * * *",
        provider=Provider.INTERACTIVE_SHELL,
        params=params,
    )


def test_re_adding_a_template_loop_keeps_one_loop_and_takes_the_new_text(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    first = add_task(_template_task("Release 1 text.", "Old description."), store)

    second = add_task(_template_task("Release 2 text.", "New description."), store)

    stored = list_tasks(store)
    assert second.id == first.id
    assert len(stored) == 1
    assert stored[0].params[LOOP_PROMPT_PARAM] == "Release 2 text."
    assert stored[0].params[LOOP_DESCRIPTION_PARAM] == "New description."


def test_re_adding_without_a_description_keeps_the_operators_own(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    add_task(_template_task("Release 1 text.", "Operator's description."), store)

    add_task(_template_task("Release 2 text."), store)

    assert list_tasks(store)[0].params[LOOP_DESCRIPTION_PARAM] == "Operator's description."


def test_inspection_and_telemetry_show_the_template_text_ticks_run(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    task = add_task(_template_task("Stale copy from an older release."), store)
    template = load_loop_template("pr-ci")

    summary, error = resolve_loop_summary(task.id, store_path=store)
    reported = registry_entry(task)["params"]

    assert summary is not None, error
    assert (summary.prompt, summary.description) == (template.prompt, template.description)
    assert reported[LOOP_PROMPT_PARAM] == template.prompt
    assert reported[LOOP_DESCRIPTION_PARAM] == template.description
    assert reported[LOOP_TEMPLATE_PARAM] == "pr-ci"
