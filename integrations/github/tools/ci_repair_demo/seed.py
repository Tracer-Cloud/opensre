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
from integrations.github.tools.ci_repair_loop.credentials import account_id
from integrations.github.tools.ci_repair_loop.responses import object_response
from integrations.github.tools.ci_repair_loop.seeded import remember_seeded_pull
from integrations.github.tools.ci_repair_loop.storage import RepairStore

FAILING_BRANCH = "demo/failing-ci"
#: Interval between reads of this one repository's Actions runs while the
#: failure is awaited: at most ~180 requests, a few percent of the hourly REST limit.
_POLL_SECONDS = 1.0
_FAILURE_WAIT_SECONDS = 180.0
#: Wait between reads of a just-created repository's default branch.
_BRANCH_RETRY_SECONDS = 2.0
_BRANCH_ATTEMPTS = 5
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
#: Keeps test bytecode out of git status, which the calculator.py-only repair scope reads.
GITIGNORE = "__pycache__/\n.pytest_cache/\n"
PR_TITLE = "Demo: calculator subtracts instead of adding"
PR_BODY = "This pull request is a demo. Do not merge.\n"
#: What the failing commit changes and why the test fails, for the outcome report.
SEEDED_FAULT = (
    "changed `add()` in `calculator.py` from `return a + b` to `return a - b`, "
    "so `add(2, 3)` returned -1 and `test_add`, which expects 5, failed."
)
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
        ".gitignore": GITIGNORE,
    }


def is_seeded_ci_repair_demo(repo: str) -> bool:
    """True for a repository this seeder creates, ``opensre-ci-repair-demo-`` plus a suffix."""
    return repo.startswith(_DEMO_REPO_PREFIX)


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
    store: RepairStore | None = None,
) -> dict[str, Any]:
    """Create the private demo when absent, or reuse its open PR, then wait.

    A 404 from the repository read is the only signal to create. Any other
    status stops. A repository that is not a demo is left unchanged, and a new
    ``opensre-ci-repair-demo-`` name on the same owner is seeded instead. An
    open demo PR whose repair already landed first gets one new failing
    commit (``rearmed``), unless a repair of it is still running in ``store``.
    The wait ends on a failed pull-request Actions run.
    The returned pull request is remembered for this account at its head
    commit, so scheduling it before anything else changes it repairs it as
    the demo.
    """
    owner = github_component(owner)
    repo = github_component(repo)
    # The seeding account is read once, before anything is written.
    user = object_response(client.request("GET", "user"))
    account = account_id(user)
    login = str(user.get("login") or "")
    try:
        seeded = _seed_named(client, owner, repo, login=login, sleep=sleep, now=now, store=store)
    except DemoRefused as exc:
        if exc.user_message != _NOT_A_DEMO:
            raise
        seeded = _seed_on_fresh_name(
            client, owner, refused_repo=repo, login=login, sleep=sleep, now=now
        )
    remember_seeded_pull(
        account, seeded["owner"], seeded["repo"], seeded["pr_number"], seeded["head_sha"]
    )
    return seeded


def _seed_on_fresh_name(
    client: GitHubRestClient,
    owner: str,
    *,
    refused_repo: str,
    login: str,
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
            seeded = _seed_named(client, owner, candidate, login=login, sleep=sleep, now=now)
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
    login: str,
    sleep: Callable[[float], None],
    now: Callable[[], float],
    store: RepairStore | None = None,
) -> dict[str, Any]:
    """Seed one named repository. Caller has already checked the name."""
    owner = github_component(owner)
    repo = github_component(repo)
    path = f"repos/{owner}/{repo}"
    repository, created = _load_repository(client, owner, repo, login)
    default_branch = str(repository.get("default_branch") or "main")
    if created:
        pull, head_sha = _seed_created(client, path, default_branch, sleep=sleep)
        reused = rearmed = False
    else:
        pull, head_sha, reused, rearmed = _seed_existing(
            client, path, owner, default_branch, sleep=sleep, store=store
        )
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
        "rearmed": rearmed,
    }


def _seed_created(
    client: GitHubRestClient, path: str, default_branch: str, *, sleep: Callable[[float], None]
) -> tuple[dict[str, Any], str]:
    """Seed a repository this call just created; returns its pull request and failing head.

    It holds only the auto-initialized default branch, so the demo marker,
    an open pull request, and the failing branch are known absent and not read.
    Each commit builds on the tree just written instead of reading it back.
    """
    parent, parent_tree = _branch_head(client, path, default_branch, sleep=sleep)
    baseline, baseline_tree = _commit_files(
        client, path, parent, baseline_files(), "Seed healthy CI demo", base_tree=parent_tree
    )
    _advance_ref(client, path, default_branch, baseline, force=False)
    head_sha, _tree = _commit_files(
        client,
        path,
        baseline,
        {"calculator.py": FAILING_CALCULATOR},
        "Demo: expose an addition bug with a real test",
        base_tree=baseline_tree,
    )
    client.request(
        "POST",
        f"{path}/git/refs",
        body={"ref": f"refs/heads/{FAILING_BRANCH}", "sha": head_sha},
    )
    return _open_pull(client, path, default_branch), head_sha


def _seed_existing(
    client: GitHubRestClient,
    path: str,
    owner: str,
    default_branch: str,
    *,
    sleep: Callable[[float], None],
    store: RepairStore | None = None,
) -> tuple[dict[str, Any], str, bool, bool]:
    """Initialize or reuse a demo repository that already existed.

    Returns its pull request, the failing head, whether that pull request was
    already open, and whether that open pull request was re-armed.
    """
    if not _demo_initialized(client, path):
        parent = _branch_sha(client, path, default_branch, sleep=sleep)
        baseline, _tree = _commit_files(
            client, path, parent, baseline_files(), "Seed healthy CI demo"
        )
        _advance_ref(client, path, default_branch, baseline, force=False)
    pull = _open_demo_pull(client, path, owner)
    if pull is not None:
        head_sha, rearmed = _failing_head(client, path, pull, store=store)
        return pull, head_sha, True, rearmed
    if _branch_exists(client, path, FAILING_BRANCH):
        raise DemoRefused(
            f"{FAILING_BRANCH} already has commits and no open pull request. "
            "This tool will not rewrite that branch."
        )
    parent = _branch_sha(client, path, default_branch, sleep=sleep)
    head_sha, _tree = _commit_files(
        client,
        path,
        parent,
        {"calculator.py": FAILING_CALCULATOR},
        "Demo: expose an addition bug with a real test",
    )
    _advance_ref(client, path, FAILING_BRANCH, head_sha, force=False)
    return _open_pull(client, path, default_branch), head_sha, False, False


def _failing_head(
    client: GitHubRestClient, path: str, pull: dict[str, Any], *, store: RepairStore | None
) -> tuple[str, bool]:
    """The open demo pull request's failing head, and whether it was just re-armed.

    A head whose ``calculator.py`` is no longer the failing fixture had its repair
    land. One commit restoring the fixture goes on top of it, and the branch moves
    forward without force, so nothing on the branch is rewritten. A repair of the
    pull request that is still running is never re-armed underneath.
    """
    head = _pull_head_sha(pull)
    if _file_at(client, path, "calculator.py", head) == FAILING_CALCULATOR:
        return head, False
    owner, repo = path.removeprefix("repos/").split("/", 1)
    active = (store or RepairStore()).active_for(owner, repo, int(pull["number"]))
    if active is not None:
        raise DemoRefused(
            f"A repair of this demo pull request is still running (task {active.id}). "
            "Read it with get_ci_repair_loop and run the demo again once it has finished."
        )
    rearmed, _tree = _commit_files(
        client,
        path,
        head,
        {"calculator.py": FAILING_CALCULATOR},
        "Demo: reintroduce the addition bug for another repair",
    )
    _advance_ref(client, path, FAILING_BRANCH, rearmed, force=False)
    return rearmed, True


def _file_at(client: GitHubRestClient, path: str, name: str, ref: str) -> str:
    """The text of ``name`` at ``ref``, or ``""`` when that commit has no such file."""
    try:
        payload = client.request("GET", f"{path}/contents/{name}", params={"ref": ref})
    except GitHubApiError as exc:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            return ""
        raise
    return _file_text(object_response(payload))


def _open_pull(client: GitHubRestClient, path: str, default_branch: str) -> dict[str, Any]:
    return object_response(
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


def _load_repository(
    client: GitHubRestClient, owner: str, repo: str, login: str
) -> tuple[dict[str, Any], bool]:
    path = f"repos/{owner}/{repo}"
    try:
        repository = object_response(client.request("GET", path))
        created = False
    except GitHubApiError as exc:
        if exc.status_code != HTTPStatus.NOT_FOUND:
            raise
        repository = _create_repository(client, owner, repo, login)
        created = True
    _require_demo_repository(repository, owner, repo, created=created)
    return repository, created


def _create_repository(
    client: GitHubRestClient, owner: str, repo: str, login: str
) -> dict[str, Any]:
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
    sha, _tree = _branch_head(client, path, branch, sleep=sleep)
    return sha


def _branch_head(
    client: GitHubRestClient, path: str, branch: str, *, sleep: Callable[[float], None]
) -> tuple[str, str]:
    """The branch's head commit and that commit's tree (``""`` when GitHub omits it).

    A just-created repository's branch can lag behind its creation, so a 404
    is retried briefly.
    """
    last: GitHubApiError | None = None
    for _attempt in range(_BRANCH_ATTEMPTS):
        try:
            ref = object_response(client.request("GET", f"{path}/branches/{branch}"))
        except GitHubApiError as exc:
            if exc.status_code != HTTPStatus.NOT_FOUND:
                raise
            last = exc
            sleep(_BRANCH_RETRY_SECONDS)
            continue
        head = ref["commit"]
        details = head.get("commit")
        tree = details.get("tree") if isinstance(details, dict) else None
        tree_sha = str(tree.get("sha") or "") if isinstance(tree, dict) else ""
        return str(head["sha"]), tree_sha
    if last is not None:
        raise last
    raise DemoRefused("The repository's default branch was not available.")


def _commit_files(
    client: GitHubRestClient,
    path: str,
    parent: str,
    files: dict[str, str],
    message: str,
    *,
    base_tree: str = "",
) -> tuple[str, str]:
    """Commit ``files`` on top of ``parent``; returns the new commit and its tree.

    ``base_tree`` is ``parent``'s tree when the caller already knows it; otherwise
    it is read from the parent commit.
    """
    git = f"{path}/git"
    if not base_tree:
        original = object_response(client.request("GET", f"{git}/commits/{parent}"))
        base_tree = str(original["tree"]["sha"])
    tree = object_response(
        client.request(
            "POST",
            f"{git}/trees",
            body={
                "base_tree": base_tree,
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
    return str(commit["sha"]), str(tree["sha"])


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
