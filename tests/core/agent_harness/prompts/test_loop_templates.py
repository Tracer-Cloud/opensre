"""Shipped loop templates stay short, complete and runnable."""

from __future__ import annotations

import re

import pytest

from core.agent_harness.prompts.loop_templates import (
    MAX_TEMPLATE_SENTENCES,
    count_sentences,
    load_loop_template,
    loop_template_names,
)
from infrastructure.scheduling.scheduler.cron_expression import cap_cron_at_most_hourly
from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODES
from tools.registry import get_tool_descriptors

#: A tool named in a template step, e.g. ``call fix_github_pr_ci``.
_CALLED_TOOL = re.compile(r"\bcall (\w+)", re.IGNORECASE)


@pytest.mark.parametrize("template", loop_template_names())
def test_template_is_at_most_ten_sentences_with_complete_metadata(template: str) -> None:
    loaded = load_loop_template(template)

    assert 0 < count_sentences(loaded.prompt) <= MAX_TEMPLATE_SENTENCES
    assert loaded.name
    assert loaded.description
    assert loaded.mode in LOOP_MODES
    assert cap_cron_at_most_hourly(loaded.cron, "UTC") == loaded.cron


@pytest.mark.parametrize("template", loop_template_names())
def test_agent_templates_end_by_forbidding_a_merge(template: str) -> None:
    loaded = load_loop_template(template)
    last_step = loaded.prompt.splitlines()[-1].lower()

    assert loaded.mode != "agent" or ("never" in last_step and "merge" in last_step)


def test_sentence_count_reads_numbered_steps() -> None:
    assert count_sentences("Do it.\n\n1. List PRs.\n2. Fix one; push it.\n3. Reply?") == 4


@pytest.mark.parametrize("template", loop_template_names())
def test_template_calls_only_registered_tools(template: str) -> None:
    called = set(_CALLED_TOOL.findall(load_loop_template(template).prompt))
    registered = {descriptor.name for descriptor in get_tool_descriptors()}

    assert called <= registered
