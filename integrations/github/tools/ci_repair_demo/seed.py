"""Create or reuse the private CI repair demo and wait for its failing run."""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from collections.abc import Callable
from http import HTTPStatus
from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_repair_loop.fixture import object_response

FAILING_BRANCH = "demo/failing-ci"
_POLL_SECONDS = 2.0
_FAILURE_WAIT_SECONDS = 180.0
_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+")
_EMPTY_ROOT = frozenset({"README.md", ".gitignore"})
_MARKER_NAME = ".opensre-demo.json"

PASSING_CALCULATOR = "def add(a, b):\n    return a + b\n"
FAILING_CALCULATOR = "def add(a, b):\n    return a - b\n"
TEST_CALCULATOR = (
    "import unittest\n"
    "from calculator import add\n\n"
    "class CalculatorTest(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n"
)
WORKFLOW = (
    "name: Demo calculator CI\n"
    "on:\n"
    "  push:\n"
    "  pull_request:\n"
    "permissions:\n"
    "  contents: read\n"
    "jobs:\n"
    "  test:\n"
    "    runs-on: ubuntu-latest\n"
    "    timeout-minutes: 1\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - run: python3 -m unittest -v\n"
)
MARKER = '{"kind":"opensre-ci-repair-demo","version":1}\n'
AGENTS = "Fix calculator.py so the unit test passes. Do not change the test or the workflow.\n"
PR_TITLE = "Demo: calculator subtracts instead of adding"
PR_BODY = "This pull request is a demo. Do not merge.\n"
_NOT_A_DEMO = "The repository is not an OpenSRE CI repair demo."
_DEMO_REPO_PREFIX = "opensre-ci-repair-demo-"
_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_SUFFIX_LENGTH = 4
_REPLACEMENT_ATTEMPTS = 5


class DemoRefused(ValueError):
    """A demo request the caller can correct; ``user_message`` is the reply."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def github_component(value: str) -> str:
    """One GitHub owner or repository name, or a refusal the caller can show."""
    cleaned = value.strip()
    if not cleaned or _COMPONENT.fullmatch(cleaned) is None or cleaned in {".", ".."}:
        raise DemoRefused("Use an explicit GitHub owner and repository name.")
    return cleaned


def baseline_files() -> dict[str, str]:
    """The passing ``main`` fixture. The failing branch changes only ``calculator.py``."""
    return {
        "calculator.py": PASSING_CALCULATOR,
        "test_calculator.py": TEST_CALCULATOR,
        ".github/workflows/test.yml": WORKFLOW,
        _MARKER_NAME: MARKER,
        "AGENTS.md": AGENTS,
    }


def fresh_demo_repo_name() -> str:
    """A new private demo name: ``opensre-ci-repair-demo-`` plus 4 letters or digits."""
    suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(_SUFFIX_LENGTH))
    return f"{_DEMO_REPO_PREFIX}{suffix}"


def _fresh_demo_repo_name() -> str:
    """A new private demo name that does not reuse a repository the caller named."""
    return fresh_demo_repo_name()


def seed_demo(
    client: GitHubRestClient,
    owner: str,
    repo: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Create the private demo when absent, reuse an open failing PR, then wait.

    A 404 from the repository read is the only signal to create. Any other
    status stops. A repository that is not a demo is left unchanged, and a new
    ``opensre-ci-repair-demo-`` name on the same owner is seeded instead. The
    wait ends on a failed pull-request Actions run.
    """
    owner = github_component(owner)
    repo = github_component(repo)
    try:
        return _seed_named(client, owner, repo, sleep=sleep, now=now)
    except DemoRefused as exc:
        if exc.user_message != _NOT_A_DEMO:
            raise
        return _seed_on_fresh_name(client, owner, refused_repo=repo, sleep=sleep, now=now)


def _seed_on_fresh_name(
    client: GitHubRestClient,
    owner: str,
    *,
    refused_repo: str,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> dict[str, Any]:
    """Seed a new demo name. The refused repository is not written."""
    last = DemoRefused(_NOT_A_DEMO)
    for _attempt in range(_REPLACEMENT_ATTEMPTS):
        candidate = _fresh_demo_repo_name()
        if candidate.casefold() == refused_repo.casefold():
            continue
        try:
            seeded = _seed_named(client, owner, candidate, sleep=sleep, now=now)
        except DemoRefused as exc:
            if exc.user_message != _NOT_A_DEMO:
                raise
            last = exc
            continue
        seeded["requested_repo"] = refused_repo
        seeded["response_text"] = (
            f"{owner}/{refused_repo} is not an OpenSRE CI repair demo and was left "
            f"unchanged. Seeded {owner}/{candidate}. Continue this plan with "
            f"{owner}/{candidate}. Do not ask the user."
        )
        return seeded
    raise last


def _seed_named(
    client: GitHubRestClient,
    owner: str,
    repo: str,
    *,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> dict[str, Any]:
    """Seed one named repository. Caller has already checked the name."""
    owner = github_component(owner)
    repo = github_component(repo)
    path = f"repos/{owner}/{repo}"
    repository, created = _load_repository(client, owner, repo)
    default_branch = str(repository.get("default_branch") or "main")
    if not _demo_initialized(client, path):
        parent = _branch_sha(client, path, default_branch, sleep=sleep)
        baseline = _commit_files(client, path, parent, baseline_files(), "Seed healthy CI demo")
        _advance_ref(client, path, default_branch, baseline, force=False)
    pull = _open_demo_pull(client, path, owner)
    reused = pull is not None
    if pull is None:
        if _branch_exists(client, path, FAILING_BRANCH):
            raise DemoRefused(
                f"{FAILING_BRANCH} already has commits and no open pull request. "
                "This tool will not rewrite that branch."
            )
        parent = _branch_sha(client, path, default_branch, sleep=sleep)
        head_sha = _commit_files(
            client,
            path,
            parent,
            {"calculator.py": FAILING_CALCULATOR},
            "Demo: expose an addition bug with a real test",
        )
        _advance_ref(client, path, FAILING_BRANCH, head_sha, force=False)
        pull = object_response(
            client.request(
                "POST",
                f"{path}/pulls",
                body={
                    "title": PR_TITLE,
                    "head": FAILING_BRANCH,
                    "base": default_branch,
                    "body": PR_BODY,
                },
            )
        )
    else:
        head_sha = _pull_head_sha(pull)
    number = int(pull["number"])
    failed_run_id = _await_failed_run(client, path, head_sha, sleep=sleep, now=now)
    return {
        "owner": owner,
        "repo": repo,
        "pr_url": f"https://github.com/{owner}/{repo}/pull/{number}",
        "pr_number": number,
        "head_sha": head_sha,
        "failed_run_id": failed_run_id,
        "repository_url": f"https://github.com/{owner}/{repo}",
        "created_repository": created,
        "reused": reused,
    }


def _load_repository(
    client: GitHubRestClient, owner: str, repo: str
) -> tuple[dict[str, Any], bool]:
    path = f"repos/{owner}/{repo}"
    try:
        repository = object_response(client.request("GET", path))
        created = False
    except GitHubApiError as exc:
        if exc.status_code != HTTPStatus.NOT_FOUND:
            raise
        repository = _create_repository(client, owner, repo)
        created = True
    _require_demo_repository(repository, owner, repo, created=created)
    return repository, created


def _create_repository(client: GitHubRestClient, owner: str, repo: str) -> dict[str, Any]:
    user = object_response(client.request("GET", "user"))
    login = str(user.get("login") or "")
    if not login:
        raise DemoRefused("GitHub did not return the authenticated user.")
    target = "user/repos" if owner.casefold() == login.casefold() else f"orgs/{owner}/repos"
    return object_response(
        client.request(
            "POST",
            target,
            body={
                "name": repo,
                "private": True,
                "auto_init": True,
                "description": "OpenSRE CI repair onboarding demo",
            },
        )
    )


def _require_demo_repository(
    repository: dict[str, Any], owner: str, repo: str, *, created: bool
) -> None:
    full_name = f"{owner}/{repo}"
    if str(repository.get("full_name") or "").casefold() != full_name.casefold():
        raise DemoRefused("GitHub returned a different repository than the one requested.")
    if repository.get("private") is not True or repository.get("fork"):
        raise DemoRefused("The demo needs a private repository that is not a fork.")
    permissions = repository.get("permissions")
    if created:
        return
    if not isinstance(permissions, dict) or not permissions.get("push"):
        raise DemoRefused("The demo needs permission to push to that repository.")


def _demo_initialized(client: GitHubRestClient, path: str) -> bool:
    names = _root_names(client, path)
    if _MARKER_NAME not in names:
        if names <= _EMPTY_ROOT:
            return False
        raise DemoRefused(_NOT_A_DEMO)
    payload = object_response(client.request("GET", f"{path}/contents/{_MARKER_NAME}"))
    try:
        parsed = json.loads(_file_text(payload))
    except json.JSONDecodeError as exc:
        raise DemoRefused(_NOT_A_DEMO) from exc
    if not isinstance(parsed, dict) or parsed.get("kind") != "opensre-ci-repair-demo":
        raise DemoRefused(_NOT_A_DEMO)
    return True


def _root_names(client: GitHubRestClient, path: str) -> set[str]:
    try:
        entries = client.request("GET", f"{path}/contents")
    except GitHubApiError as exc:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            return set()
        raise
    if not isinstance(entries, list):
        raise DemoRefused("GitHub returned an unexpected repository listing.")
    return {str(item.get("name")) for item in entries if isinstance(item, dict)}


def _file_text(payload: dict[str, Any]) -> str:
    raw = str(payload.get("content") or "")
    if payload.get("encoding") == "base64":
        return base64.b64decode(raw).decode()
    return raw


def _branch_sha(
    client: GitHubRestClient, path: str, branch: str, *, sleep: Callable[[float], None]
) -> str:
    last: GitHubApiError | None = None
    for _attempt in range(5):
        try:
            ref = object_response(client.request("GET", f"{path}/branches/{branch}"))
            return str(ref["commit"]["sha"])
        except GitHubApiError as exc:
            if exc.status_code != HTTPStatus.NOT_FOUND:
                raise
            last = exc
            sleep(_POLL_SECONDS)
    if last is not None:
        raise last
    raise DemoRefused("The repository's default branch was not available.")


def _commit_files(
    client: GitHubRestClient,
    path: str,
    parent: str,
    files: dict[str, str],
    message: str,
) -> str:
    git = f"{path}/git"
    original = object_response(client.request("GET", f"{git}/commits/{parent}"))
    tree = object_response(
        client.request(
            "POST",
            f"{git}/trees",
            body={
                "base_tree": original["tree"]["sha"],
                "tree": [
                    {"path": name, "mode": "100644", "type": "blob", "content": content}
                    for name, content in files.items()
                ],
            },
        )
    )
    commit = object_response(
        client.request(
            "POST",
            f"{git}/commits",
            body={"message": message, "tree": tree["sha"], "parents": [parent]},
        )
    )
    return str(commit["sha"])


def _branch_exists(client: GitHubRestClient, path: str, branch: str) -> bool:
    try:
        client.request("GET", f"{path}/branches/{branch}")
    except GitHubApiError as exc:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            return False
        raise
    return True


def _reference_is_missing(exc: GitHubApiError) -> bool:
    """GitHub's update-a-reference call returns 422, not 404, for a new branch."""
    if exc.status_code == HTTPStatus.NOT_FOUND:
        return True
    return (
        exc.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        and "Reference does not exist" in exc.message
    )


def _advance_ref(
    client: GitHubRestClient, path: str, branch: str, sha: str, *, force: bool
) -> None:
    try:
        client.request(
            "PATCH",
            f"{path}/git/refs/heads/{branch}",
            body={"sha": sha, "force": force},
        )
    except GitHubApiError as exc:
        if not _reference_is_missing(exc):
            raise
        client.request(
            "POST",
            f"{path}/git/refs",
            body={"ref": f"refs/heads/{branch}", "sha": sha},
        )


def _open_demo_pull(client: GitHubRestClient, path: str, owner: str) -> dict[str, Any] | None:
    pulls = client.request(
        "GET",
        f"{path}/pulls",
        params={"state": "open", "head": f"{owner}:{FAILING_BRANCH}"},
    )
    if not isinstance(pulls, list):
        raise DemoRefused("GitHub returned an unexpected pull request listing.")
    for pull in pulls:
        if isinstance(pull, dict):
            return pull
    return None


def _pull_head_sha(pull: dict[str, Any]) -> str:
    head = pull.get("head")
    sha = head.get("sha") if isinstance(head, dict) else ""
    if not sha:
        raise DemoRefused("The open demo pull request has no head commit.")
    return str(sha)


def _await_failed_run(
    client: GitHubRestClient,
    path: str,
    sha: str,
    *,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    deadline = now() + _FAILURE_WAIT_SECONDS
    while True:
        payload = object_response(
            client.request(
                "GET",
                f"{path}/actions/runs",
                params={"head_sha": sha, "per_page": 20},
            )
        )
        runs = payload.get("workflow_runs")
        matched = [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []
        matched = [
            run for run in matched if not run.get("head_sha") or str(run.get("head_sha")) == sha
        ]
        pull_failures = [
            run
            for run in matched
            if run.get("event") == "pull_request" and run.get("conclusion") == "failure"
        ]
        if pull_failures and pull_failures[0].get("id") is not None:
            return int(pull_failures[0]["id"])
        passed = any(
            run.get("conclusion") == "success" and run.get("event") == "pull_request"
            for run in matched
        )
        if passed:
            raise DemoRefused(
                "The demo pull request's checks passed, so the calculator change did not fail CI."
            )
        if now() >= deadline:
            raise DemoRefused(
                "The demo pull request did not report a failed Actions run before the wait ended."
            )
        sleep(_POLL_SECONDS)
