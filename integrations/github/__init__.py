"""GitHub integration package.

Other tiers import GitHub behavior through this module, not the files inside it.
The client names load eagerly; the rest are re-exported lazily through
``__getattr__`` so importing the package does not pull heavier submodules (the
MCP transport and repair workers) until a caller actually needs them.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token

#: Public name -> the submodule that defines it, imported on first access.
_LAZY_EXPORTS: dict[str, str] = {
    "github_workspace_git_token": "integrations.github.workspace_credentials",
    "workspace_public_repository_source": "integrations.github.identity",
    "missing_token_envelope": "integrations.github.envelope",
    "filter_github_connected_services": "integrations.github.connections",
    "github_setup_url": "integrations.github.app_connection",
    "github_schedule_inputs": "integrations.github.app_connection",
    "refreshed_github_token": "integrations.github.app_connection",
    "setup_github": "integrations.github.cli_setup",
    "run_ci_repair_worker": "integrations.github.tools.ci_repair_loop.worker",
    "effective_github_token": "integrations.github.tools.ci_repair_loop.credentials",
    "count_ci_fixes": "integrations.github.tools.ci_fix.ledger",
    "get_ci_fix_counter": "integrations.github.tools.ci_fix.ledger",
    "github_creds": "integrations.github.helpers",
    "github_rest_token": "integrations.github.rest_token",
    "has_github_rest_token": "integrations.github.rest_token",
    "saved_github_username": "integrations.github.identity",
    "fresh_demo_repo_name": "integrations.github.tools.ci_repair_demo.seed",
    "PullRequestCheckout": "integrations.github.pull_request_checkout",
    "checkout_pull_request": "integrations.github.pull_request_checkout",
    "parse_pull_request": "integrations.github.pull_request_checkout",
    "CHECKS_NOT_WATCHED": "integrations.github.pull_request_checks",
    "ChecksOutcome": "integrations.github.pull_request_checks",
    "watch_pull_request_checks": "integrations.github.pull_request_checks",
    "ERR_GITHUB_TOKEN": "integrations.github.pull_requests",
    "GitHubPullRequestError": "integrations.github.pull_requests",
    "PullRequest": "integrations.github.pull_requests",
    "open_pull_request": "integrations.github.pull_requests",
    "resolve_repo_scope": "integrations.github.pull_requests",
    "DEFAULT_GITHUB_MCP_MODE": "integrations.github.mcp",
    "DEFAULT_GITHUB_MCP_URL": "integrations.github.mcp",
    "GitHubMCPValidationResult": "integrations.github.mcp",
    "GitHubMcpDisplayDetailLevel": "integrations.github.mcp",
    "build_github_mcp_config": "integrations.github.mcp",
    "format_github_mcp_validation_cli_report": "integrations.github.mcp",
    "print_github_mcp_validation_report": "integrations.github.mcp",
    "validate_github_mcp_config": "integrations.github.mcp",
    "disconnect_personal_github": "integrations.github.personal_account",
    "Analysis": "integrations.github.tools.ci_analytics.analysis",
    "analyze_repository": "integrations.github.tools.ci_analytics.analysis",
    "ci_report_headline": "integrations.github.tools.ci_analytics.render",
    "prefetch_ci_analysis": "integrations.github.tools.ci_analytics.tool",
    "DEFAULT_LOOP_TIME": "integrations.github.tools.ci_analytics.loop",
    "LoopCard": "integrations.github.tools.ci_analytics.loop",
    "ScheduledLoop": "integrations.github.tools.ci_analytics.loop",
    "local_timezone": "integrations.github.tools.ci_analytics.loop",
    "loop_card": "integrations.github.tools.ci_analytics.loop",
    "report_looks_complete": "integrations.github.tools.ci_analytics.loop",
    "schedule_ci_reliability_loop": "integrations.github.tools.ci_analytics.loop",
}


def __getattr__(name: str) -> object:
    """Resolve a lazily re-exported name to its submodule attribute (PEP 562).

    Resolved on every access rather than cached in module globals, so a test that
    patches the owning submodule's attribute is reflected here.
    """
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_path), name)


if TYPE_CHECKING:
    from integrations.github.app_connection import (
        github_schedule_inputs,
        github_setup_url,
        refreshed_github_token,
    )
    from integrations.github.cli_setup import setup_github
    from integrations.github.connections import filter_github_connected_services
    from integrations.github.envelope import missing_token_envelope
    from integrations.github.helpers import github_creds
    from integrations.github.identity import (
        saved_github_username,
        workspace_public_repository_source,
    )
    from integrations.github.mcp import (
        DEFAULT_GITHUB_MCP_MODE,
        DEFAULT_GITHUB_MCP_URL,
        GitHubMcpDisplayDetailLevel,
        GitHubMCPValidationResult,
        build_github_mcp_config,
        format_github_mcp_validation_cli_report,
        print_github_mcp_validation_report,
        validate_github_mcp_config,
    )
    from integrations.github.personal_account import disconnect_personal_github
    from integrations.github.pull_request_checkout import (
        PullRequestCheckout,
        checkout_pull_request,
        parse_pull_request,
    )
    from integrations.github.pull_request_checks import (
        CHECKS_NOT_WATCHED,
        ChecksOutcome,
        watch_pull_request_checks,
    )
    from integrations.github.pull_requests import (
        ERR_GITHUB_TOKEN,
        GitHubPullRequestError,
        PullRequest,
        open_pull_request,
        resolve_repo_scope,
    )
    from integrations.github.rest_token import github_rest_token, has_github_rest_token
    from integrations.github.tools.ci_analytics.analysis import Analysis, analyze_repository
    from integrations.github.tools.ci_analytics.loop import (
        DEFAULT_LOOP_TIME,
        LoopCard,
        ScheduledLoop,
        local_timezone,
        loop_card,
        report_looks_complete,
        schedule_ci_reliability_loop,
    )
    from integrations.github.tools.ci_analytics.render import ci_report_headline
    from integrations.github.tools.ci_analytics.tool import prefetch_ci_analysis
    from integrations.github.tools.ci_fix.ledger import count_ci_fixes, get_ci_fix_counter
    from integrations.github.tools.ci_repair_demo.seed import fresh_demo_repo_name
    from integrations.github.tools.ci_repair_loop.credentials import effective_github_token
    from integrations.github.tools.ci_repair_loop.worker import run_ci_repair_worker
    from integrations.github.workspace_credentials import github_workspace_git_token


__all__ = [
    "github_workspace_git_token",
    "filter_github_connected_services",
    "workspace_public_repository_source",
    "missing_token_envelope",
    "github_setup_url",
    "github_schedule_inputs",
    "refreshed_github_token",
    "setup_github",
    "PullRequestCheckout",
    "checkout_pull_request",
    "parse_pull_request",
    "CHECKS_NOT_WATCHED",
    "DEFAULT_GITHUB_MCP_MODE",
    "DEFAULT_GITHUB_MCP_URL",
    "Analysis",
    "ChecksOutcome",
    "DEFAULT_LOOP_TIME",
    "ERR_GITHUB_TOKEN",
    "GitHubApiError",
    "GitHubMCPValidationResult",
    "GitHubMcpDisplayDetailLevel",
    "GitHubPullRequestError",
    "GitHubRestClient",
    "LoopCard",
    "PullRequest",
    "ScheduledLoop",
    "analyze_repository",
    "build_github_mcp_config",
    "ci_report_headline",
    "count_ci_fixes",
    "disconnect_personal_github",
    "effective_github_token",
    "fresh_demo_repo_name",
    "format_github_mcp_validation_cli_report",
    "get_ci_fix_counter",
    "github_creds",
    "github_rest_token",
    "has_github_rest_token",
    "local_timezone",
    "loop_card",
    "open_pull_request",
    "prefetch_ci_analysis",
    "print_github_mcp_validation_report",
    "report_looks_complete",
    "resolve_github_token",
    "resolve_repo_scope",
    "run_ci_repair_worker",
    "saved_github_username",
    "schedule_ci_reliability_loop",
    "validate_github_mcp_config",
    "watch_pull_request_checks",
]
