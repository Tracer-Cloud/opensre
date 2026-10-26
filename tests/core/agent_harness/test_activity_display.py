"""Compact tool activity for the shell's hosted-gateway feed."""

from __future__ import annotations

from config.constants.gateway import PROMPT_PROGRESS_KIND_PLAN, PROMPT_PROGRESS_KIND_TOOL
from core.agent_harness.activity_display import format_hosted_activity
from core.agent_harness.task_plan.progress import task_plan_from_checklist


def test_github_activity_compacts_jq_and_drops_a_secret_header() -> None:
    activity = format_hosted_activity(
        "github_cli",
        {
            "args": [
                "api",
                "user",
                "--include",
                "-H",
                "Authorization: Bearer should-not-render",
                "--jq",
                ".[] | [.createdAt] | @tsv",
            ],
            "repo": "Tracer-Cloud/opensre",
        },
    )

    assert activity is not None
    assert activity.kind == PROMPT_PROGRESS_KIND_TOOL
    assert activity.text.startswith("GitHub CLI · gh -R Tracer-Cloud/opensre api user")
    assert "--include" in activity.text
    assert "-H …" in activity.text
    assert "--jq …" in activity.text
    assert "should-not-render" not in activity.text
    assert ".createdAt" not in activity.text
    assert "{'" not in activity.text


def test_update_plan_is_a_checklist_not_a_dict() -> None:
    activity = format_hosted_activity(
        "update_plan",
        {
            "plan": [
                {"step": "Inspect authenticated user", "status": "completed"},
                {
                    "step": "Check repository creation permission",
                    "status": "in_progress",
                    "verifies": True,
                },
            ]
        },
    )

    assert activity is not None
    assert activity.kind == PROMPT_PROGRESS_KIND_PLAN
    assert "{'step'" not in activity.text
    assert "status" not in activity.text.splitlines()[1]
    assert "✓ Inspect authenticated user" in activity.text
    assert "● Check repository creation permission (verify)" in activity.text
    restored = task_plan_from_checklist(activity.text)
    assert restored is not None
    assert restored.steps[1].verifies is True
    assert restored.steps[1].step == "Check repository creation permission"


def test_ask_user_and_the_private_chooser_are_not_progress_rows() -> None:
    assert format_hosted_activity("ask_user_choice", {"title": "Which repo?"}) is None
    assert format_hosted_activity("slash_invoke", {"command": "/choose", "args": []}) is None


def test_generic_activity_omits_secrets_and_does_not_dump_dicts() -> None:
    activity = format_hosted_activity(
        "custom_registry_tool",
        {
            "query": "x" * 400,
            "api_token": "secret-token",
            "timeout": 30,
            "payload": {"step": "Inspect authenticated user", "status": "completed"},
        },
    )

    assert activity is not None
    assert activity.kind == PROMPT_PROGRESS_KIND_TOOL
    assert activity.text.startswith("custom registry tool · ")
    assert activity.text.endswith("…")
    assert "secret-token" not in activity.text
    assert "timeout" not in activity.text
    assert "{'step'" not in activity.text
    assert "fields step, status" in activity.text
