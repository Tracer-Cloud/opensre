"""Check a pull request out into a clone OpenSRE owns, never into the user's checkout."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.constants.paths import opensre_home
from integrations.git import (
    GitCommandError,
    clone_repository,
    current_branch,
    merge_in_progress,
    origin_url,
)
from integrations.github.identity import (
    is_github_repository_part,
    workspace_public_repository_source,
)
from integrations.github.tools.ci_fix.errors import ERR_INVALID_INPUT, GitHubCiFixError
from integrations.github.tools.ci_fix.gh import run_gh_text

_URL_SELECTOR = re.compile(
    r"github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s#]+)/pull/(?P<number>\d+)"
)
_SHORT_SELECTOR = re.compile(r"^(?:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+))?#?(?P<number>\d+)$")

Clone = Callable[..., None]
RunGh = Callable[..., str]


@dataclass(frozen=True)
class PullRequestCheckout:
    """A pull request head checked out in a workspace of its own."""

    workspace: str
    owner: str
    repo: str
    number: int
    head_branch: str
    reused: bool

    @property
    def label(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


def parse_pull_request(selector: str, *, cwd: str) -> tuple[str, str, int]:
    """``(owner, repo, number)`` from a URL, ``owner/repo#N``, ``#N`` or ``N``.

    A bare number names a pull request of the repository *cwd*'s origin points at.
    """
    text = selector.strip()
    match = _URL_SELECTOR.search(text) or _SHORT_SELECTOR.match(text)
    if match is None:
        raise GitCommandError(
            ERR_INVALID_INPUT, f"{selector!r} is not a pull request number, owner/repo#N or URL."
        )
    number = int(match.group("number"))
    owner, repo = match.group("owner"), match.group("repo")
    if owner and repo:
        repo = repo.removesuffix(".git")
        _require_repository_parts(owner, repo, selector)
        return owner, repo, number
    owner, repo = _repository_of(cwd)
    if not owner:
        raise GitCommandError(
            ERR_INVALID_INPUT,
            f"#{number} names no repository: pass owner/repo#{number} or the pull request URL.",
        )
    _require_repository_parts(owner, repo, selector)
    return owner, repo, number


def checkout_pull_request(
    selector: str,
    *,
    cwd: str,
    token: str | None = None,
    root: Path | None = None,
    clone: Clone = clone_repository,
    run_gh: RunGh = run_gh_text,
) -> PullRequestCheckout:
    """Clone the pull request's repository under OpenSRE's home and check its head out there.

    The clone lives at a fixed path per pull request. One left with that pull
    request's merge still in progress is reused, so a later turn continues the
    same merge; otherwise it is replaced by a fresh clone. ``gh pr checkout``
    sets the push remote, so a fork's branch is pushed back to the fork.
    """
    owner, repo, number = parse_pull_request(selector, cwd=cwd)
    path = _workspace_path(root or opensre_home() / "workspaces" / "merges", owner, repo, number)
    if path.is_dir() and _merge_in_progress(path):
        return PullRequestCheckout(
            str(path), owner, repo, number, current_branch(str(path)), reused=True
        )
    shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    clone(f"https://github.com/{owner}/{repo}.git", str(path), token=token)
    try:
        run_gh(
            ["pr", "checkout", str(number)],
            repo=f"{owner}/{repo}",
            github_token=token,
            cwd=str(path),
        )
    except GitHubCiFixError as exc:
        raise GitCommandError(exc.kind, exc.message) from exc
    return PullRequestCheckout(
        str(path), owner, repo, number, current_branch(str(path)), reused=False
    )


def _require_repository_parts(owner: str, repo: str, selector: str) -> None:
    if is_github_repository_part(owner) and is_github_repository_part(repo):
        return
    raise GitCommandError(
        ERR_INVALID_INPUT,
        f"{selector!r} is not a pull request number, owner/repo#N or URL.",
    )


def _workspace_path(root: Path, owner: str, repo: str, number: int) -> Path:
    """``<root>/<owner>/<repo>/<number>``, or raise if that path leaves *root*.

    Separate segments keep ``a/b-c#7`` and ``a-b/c#7`` from sharing a directory.
    The containment check is what ``rmtree`` relies on if a name ever slipped
    through validation (a ``..`` owner, a symlink, a Windows separator).
    """
    base = root.resolve()
    path = (base / owner / repo / str(number)).resolve()
    if path == base or base not in path.parents:
        raise GitCommandError(
            ERR_INVALID_INPUT,
            f"{owner}/{repo}#{number} is not a usable pull request workspace.",
        )
    return path


def _merge_in_progress(path: Path) -> bool:
    try:
        return merge_in_progress(str(path))
    except GitCommandError:
        return False


def _repository_of(cwd: str) -> tuple[str, str]:
    try:
        url = origin_url(cwd)
    except GitCommandError:
        return "", ""
    identity: dict[str, Any] = workspace_public_repository_source({"workspace_repo": url})
    github = identity.get("github", {})
    return str(github.get("owner") or ""), str(github.get("repo") or "")


__all__ = ["PullRequestCheckout", "checkout_pull_request", "parse_pull_request"]
