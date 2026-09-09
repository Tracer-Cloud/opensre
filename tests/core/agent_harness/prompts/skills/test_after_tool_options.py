"""after_tool option builders read the trigger payload, not user prose."""

from __future__ import annotations

from core.agent_harness.prompts.skills.after_tool_options import options_from_tool_result


def test_scan_builder_keeps_workflow_repos_and_the_extra_fallback() -> None:
    details = {
        "repos": [
            {"github": "acme/one", "has_workflows": True, "commits": 2},
            {"github": "acme/two", "has_workflows": True, "commits": 9},
            {"name": "local-only", "has_workflows": True, "commits": 20},
            {"github": "acme/three", "has_workflows": False, "commits": 100},
        ]
    }

    options = options_from_tool_result(
        "local_git_scan_repos",
        details,
        ("Use the open-source example repository (Tracer-Cloud/opensre)",),
    )

    assert options == (
        "acme/two (9 commits, CI configured)",
        "acme/one (2 commits, CI configured)",
        "Use the open-source example repository (Tracer-Cloud/opensre)",
    )


def test_scan_builder_adds_exit_when_only_the_fallback_remains() -> None:
    options = options_from_tool_result(
        "local_git_scan_repos",
        {"repos": []},
        ("Use the open-source example repository (Tracer-Cloud/opensre)",),
    )

    assert options == (
        "Use the open-source example repository (Tracer-Cloud/opensre)",
        "Exit demo",
    )
