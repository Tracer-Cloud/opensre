"""Template loops: identified by template name, inspected with the text they run."""

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
from infrastructure.scheduling.scheduler.storage.task_store import add_task, list_tasks
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


def _template_task(prompt_copy: str, description: str) -> ScheduledTask:
    return ScheduledTask(
        name="PR CI",
        kind=TaskKind.MANUAL_LOOP,
        cron="28 * * * *",
        provider=Provider.INTERACTIVE_SHELL,
        params={
            LOOP_TEMPLATE_PARAM: "pr-ci",
            LOOP_PROMPT_PARAM: prompt_copy,
            LOOP_DESCRIPTION_PARAM: description,
            LOOP_MODE_PARAM: "agent",
            "owner": "acme",
            "repo": "widgets",
        },
    )


def test_re_adding_a_template_loop_keeps_one_loop_and_takes_the_new_copies(
    tmp_path: Path,
) -> None:
    store = tmp_path / "scheduler_tasks.json"
    first = add_task(_template_task("Release 1 text.", "Release 1 description."), store)

    second = add_task(_template_task("Release 2 text.", "Operator's new description."), store)

    stored = list_tasks(store)
    assert second.id == first.id
    assert len(stored) == 1
    assert stored[0].params[LOOP_PROMPT_PARAM] == "Release 2 text."
    assert stored[0].params[LOOP_DESCRIPTION_PARAM] == "Operator's new description."


def test_inspecting_a_template_loop_shows_the_text_its_ticks_run(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    task = add_task(_template_task("Stale copy from an older release.", "Fixes CI."), store)

    summary, error = resolve_loop_summary(task.id, store_path=store)

    assert summary is not None, error
    assert summary.prompt == load_loop_template("pr-ci").prompt
