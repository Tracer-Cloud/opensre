"""Ask a PR's people for a merge decision once per head, and find that request later.

The comment carries the marker the PR doctor loop already posts, so its earlier
requests keep a head skipped too. A new head means new commits: a later call
tries the merge again. Only comments from people with write access count, so
anyone else cannot stop the repairs of a pull request by pasting the marker.
"""

from __future__ import annotations

import json
import logging
import re

from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.errors import GitHubCiFixError
from integrations.github.tools.ci_fix.gh import run_gh_text

logger = logging.getLogger(__name__)

_MARKER_KEY = "opensre-pr-doctor:blocked:"
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
#: GitHub ``author_association`` values of people with write access to the repository.
_TRUSTED_AUTHORS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
#: Enough of a reported decision to name it in a tool result.
_SUMMARY_MAX_CHARS = 600


def _marker(head_sha: str) -> str:
    return f"<!-- {_MARKER_KEY}{head_sha} -->"


def _comments_endpoint(ctx: CiFixContext) -> str:
    return f"repos/{ctx.owner}/{ctx.repo}/issues/{ctx.number}/comments"


def reported_decision(ctx: CiFixContext, *, github_token: str | None) -> str | None:
    """The request already on the PR for its current head, or ``None``.

    A comment listing that fails is treated as no request: the merge is then
    attempted, which costs a coding-agent run but never skips a PR silently.
    """
    try:
        raw = run_gh_text(
            [
                "api",
                "--paginate",
                _comments_endpoint(ctx),
                "--jq",
                ".[] | {body, author_association} | @json",
            ],
            repo=f"{ctx.owner}/{ctx.repo}",
            github_token=github_token,
            repo_flag=False,
        )
    except GitHubCiFixError as exc:
        logger.warning("Could not read merge-decision comments on %s: %s", ctx.url, exc.kind)
        return None
    key = f"{_MARKER_KEY}{ctx.head_sha}"
    for line in raw.splitlines():
        try:
            comment = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(comment, dict) or comment.get("author_association") not in (
            _TRUSTED_AUTHORS
        ):
            continue
        body = comment.get("body")
        if isinstance(body, str) and key in body:
            summary = " ".join(_HTML_COMMENT_RE.sub(" ", body).split())
            return summary[:_SUMMARY_MAX_CHARS]
    return None


def report_decision(ctx: CiFixContext, message: str, *, github_token: str | None) -> bool:
    """Comment the decision a person must make on the PR, marked with its head.

    Returns whether the comment was posted; without it the next call simply
    attempts the merge again.
    """
    body = (
        f"OpenSRE could not bring this branch up to date with `{ctx.base_branch}` "
        f"without a decision from you.\n\n{message}\n\n"
        "It will try again after new commits land on this branch.\n\n"
        f"{_marker(ctx.head_sha)}"
    )
    try:
        run_gh_text(
            ["api", "-X", "POST", _comments_endpoint(ctx), "-f", f"body={body}"],
            repo=f"{ctx.owner}/{ctx.repo}",
            github_token=github_token,
            repo_flag=False,
        )
    except GitHubCiFixError as exc:
        logger.warning("Could not post the merge decision on %s: %s", ctx.url, exc.kind)
        return False
    return True


__all__ = ["report_decision", "reported_decision"]
