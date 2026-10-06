"""Agent-callable authenticated GitHub CLI tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import CALL_SIDE_EFFECT_LEVEL_KEY, SideEffectLevel
from core.tool_framework import tool
from integrations.github.tools.github_cli.credentials import (
    GITHUB_CLI_INJECTED_PARAMS,
    github_creds,
    github_source_available,
    resolve_github_token,
)
from integrations.github.tools.github_cli.effects import gh_call_only_reads
from integrations.github.tools.github_cli.runner import MAX_GH_OUTPUT_CHARS, run_gh
from integrations.github.tools.github_cli.summary import attach_summary

_ARGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Arguments after the `gh` binary (for example: "
                '["issue", "create", "--title", "Bug", "--body", "…"] or '
                '["issue", "list", "--limit", "10", "--json", "number,title"]). '
                "Do not include `gh` itself. Each list item is one argv entry, passed "
                "to gh verbatim without shell quoting or splitting."
            ),
        },
        "repo": {
            "type": "string",
            "description": (
                "Optional owner/name passed to gh as -R (overrides default repo). "
                "Ignored for `gh repo`, `gh api`, and other commands without -R; "
                "give those the repository positionally, e.g. "
                '["repo", "create", "owner/name", "--private"].'
            ),
        },
        "timeout": {
            "type": "integer",
            "description": "Maximum seconds to wait for gh (default 60, max 120).",
        },
        "github_token": {
            "type": "string",
            "description": "GitHub token injected from the configured integration.",
        },
    },
    "required": ["args"],
}


def _github_cli_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    if gh.get("connection_selection_error"):
        return False
    return bool(
        github_source_available(sources) or resolve_github_token(None) or gh.get("github_token")
    )


def _github_cli_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    params: dict[str, Any] = {}
    if not gh:
        return params
    creds = github_creds(gh)
    if creds.get("github_connection_id"):
        params["github_connection_id"] = creds["github_connection_id"]
    if creds.get("github_token"):
        params["github_token"] = creds["github_token"]
    owner = str(gh.get("owner") or "").strip()
    repo = str(gh.get("repo") or "").strip()
    if owner and repo:
        params["repo"] = f"{owner}/{repo}"
    return params


def _normalize_args(args: list[str] | None) -> list[str]:
    if not args:
        return []
    return [str(a) for a in args]


@tool(
    name="github_cli",
    source="github",
    description=(
        "Run GitHub CLI (`gh`) with OpenSRE-configured auth — reads and writes. "
        "Use for issue/PR create, list, view, assign, label, merge, repo list, "
        "and gh api. Prefer this over shell_run / !gh / raw gh. "
        "Pass args after the gh binary; optional repo as owner/name for -R. "
        "After the call, reply from the result summary — plain prose for simple "
        "confirms; chat-like markdown bullets for multi-item reads (not report "
        "tables/headers). Not raw JSON/GraphQL dumps. For a commit's workflow "
        "run history with attempts and conclusions use "
        "list_github_actions_workflow_runs with head_sha; gh run list does not "
        f"show attempts. Output over {MAX_GH_OUTPUT_CHARS} characters is cut, and cut JSON is "
        "refused: for list and JSON reads always pass a small --limit, only the "
        "--json fields you need, and --jq to select them (for gh api, --jq too). "
        "For gh api graphql pass the whole query as one arg, e.g. "
        '["api", "graphql", "-f", "query=query($o: String!, $n: String!) '
        '{ repository(owner: $o, name: $n) { name } }", "-f", "o=OWNER", '
        '"-f", "n=NAME"], with balanced braces.'
    ),
    use_cases=[
        "Creating a GitHub issue (title/body/assignee/labels) when the user asks",
        "Listing or viewing GitHub issues and pull requests via gh",
        "Inspecting repository metadata or listing accessible repos",
        "Editing, closing, commenting, or merging via gh",
    ],
    anti_examples=[
        "Running gh via shell_run or !gh",
        "Printing or logging the GitHub token",
        "Inventing repo lists without calling github_cli",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    requires_approval=False,
    input_schema=_ARGS_SCHEMA,
    is_available=_github_cli_available,
    extract_params=_github_cli_extract_params,
    injected_params=GITHUB_CLI_INJECTED_PARAMS,
)
def github_cli(
    args: list[str],
    repo: str | None = None,
    timeout: int | None = None,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run an authenticated ``gh`` command (read or write; no approval gate)."""
    normalized = _normalize_args(args)
    payload = attach_summary(
        run_gh(args=normalized, repo=repo, github_token=github_token, timeout=timeout),
        args=normalized,
    )
    if gh_call_only_reads(normalized):
        payload[CALL_SIDE_EFFECT_LEVEL_KEY] = SideEffectLevel.READ_ONLY.value
    return payload


__all__ = ["github_cli"]
