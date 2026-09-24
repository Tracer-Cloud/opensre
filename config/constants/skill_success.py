"""Verifiable success criteria for every skill, workflow and tool-usage alike.

Add a skill's name here in the same change that adds its card. Each criterion
names an observable tool result or reply fact in backticks. The loader appends
the section when the card does not already carry ``## Success criteria``.
"""

from __future__ import annotations

SUCCESS_CRITERIA: dict[str, tuple[str, ...]] = {
    "analyzing-github-ci-performance": (
        "`analyze_github_ci_reliability` has returned and the reply shows the metrics table.",
    ),
    "connecting-slack": (
        "Slack verify via `cli_exec` reports connected, or the reply quotes the verify failure.",
    ),
    "delegating-github-ci-repairs": (
        "`ask_hosted_gateway` returns `done` or `failed`, and a `prompt_id` when the loop was accepted.",
    ),
    "delivering-morning-briefings": (
        "`slack_send_message` or `propose_scheduled_delivery` returns `response_text` with the briefing.",
    ),
    "fixing-github-security-alerts": (
        "`fix_github_security_alert` returns a pushed fix, or `error` names why it stopped.",
    ),
    "investigating-incidents-with-runbooks": (
        "The reply lists each `runbook` step and the tool result that satisfied it.",
    ),
    "measuring-github-star-velocity": (
        "`execute_python_code` returns the star-velocity figure the user asked for.",
    ),
    "onboarding-github-ci": (
        "`entry_menu` is `queued`, or `skill_view` has loaded the chosen child skill.",
    ),
    "operating-github-ci-fixer": (
        "`fix_github_pr_ci` returns `checks_state` `passed`, or `error_kind` after repairs stop.",
    ),
    "operating-github-cli": (
        "`github_cli` returns the pull request or issue data the user asked for.",
    ),
    "operating-github-security-fixer": (
        "`fix_github_security_alert` returns a pushed fix, or `error` names why it stopped.",
    ),
    "querying-yandex-cloud": (
        "`execute_yc_operation` returns the asked data, or `find_yc_api` reports it cannot run.",
    ),
    "repair-github-ci": (
        "`fix_github_pr_ci` returns `checks_state` `passed`, or `error_kind` after repairs stop.",
    ),
    "reporting-github-ci-failures": (
        "The reply lists the failing checks, and `fix_github_pr_ci` was not called.",
    ),
    "scheduling-github-ci-repairs": (
        "The repair run reaches a terminal status and the report is shown before `ask_user_choice`.",
    ),
    "summarizing-posthog-analytics": (
        "The reply summarizes the `PostHog` tool result for the asked insight.",
    ),
    "summarizing-sentry-issues": (
        "The reply summarizes the `Sentry` tool result for the asked issues.",
    ),
    "tracking-github-work-status": (
        "The GitHub workflow tool returns the `status` fields the user asked for.",
    ),
}


def success_section(name: str) -> str:
    """Markdown block the host appends so the agent can check the skill is done."""
    items = SUCCESS_CRITERIA.get(name, ())
    if not items:
        return ""
    lines = ["## Success criteria", ""]
    lines.extend(f"- [ ] {item}" for item in items)
    return "\n".join(lines)
