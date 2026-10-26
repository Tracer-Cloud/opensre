"""GitHub action-agent prompt fragment — routes `gh` CLI requests to tools.

Registered with :func:`infrastructure.harness_providers.register_action_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def github_action_prompt_fragment() -> str:
    return """GITHUB CLI REQUESTS USE GITHUB TOOLS:
When the user asks to create, list, view, edit, close, comment, assign, label,
merge, or search GitHub issues/PRs/repos *as the primary request* (including
github.com/owner/repo URLs and follow-ups like "from this info create an issue on GitHub"),
call github_cli directly. Prefer github_cli over shell_run / !gh / raw gh.
Exception: GitHub issue/PR/repo create/list/view/merge/comment as a *standalone*
request via `gh` uses github_cli, as above.
Do NOT use github_cli when the user is diagnosing a crash/failure/outage and
names GitHub among other sources to query (e.g. sentry + github issues +
posthog) — use the matching chat tools for every source, then answer.
Do NOT use github_cli (or shell_run / gh api stargazers) for star history,
day-by-day stars, stars gained, or star velocity — use get_github_star_history
(paginated gh scans routinely undercount).
Pass args after the `gh` binary; optional repo as owner/name for -R.
Examples:
* "create an issue titled X with body Y"
  → github_cli(args=["issue", "create", "--title", "X", "--body", "Y"])
* "list open PRs" → github_cli(args=["pr", "list", "--state", "open"])
* "merge PR 45 with squash auto"
  → github_cli(args=["pr", "merge", "45", "--squash", "--auto"])
* "figure out why OpenSRE is crashing on Windows — query sentry, github issues,
  and posthog" → query all three matching chat tools, then answer
After the tool returns, reply briefly from the result summary."""


__all__ = ["github_action_prompt_fragment"]
