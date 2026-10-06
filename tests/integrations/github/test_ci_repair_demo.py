"""The demo seed creates one failing PR, and cleanup keeps the approved repo name."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import time
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from integrations.git import changed_paths
from integrations.github.client import GitHubApiError
from integrations.github.tools.ci_repair_demo import cleanup
from integrations.github.tools.ci_repair_demo.seed import (
    FAILING_BRANCH,
    FAILING_CALCULATOR,
    PASSING_CALCULATOR,
    TEST_CALCULATOR,
    DemoRefused,
    baseline_files,
    seed_demo,
)
from integrations.github.tools.ci_repair_demo.tool import finish_ci_repair_demo, seed_ci_repair_demo
from integrations.github.tools.ci_repair_loop import seeded
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.storage import RepairStore

_OWNER = "octocat"
_REPO = "opensre-ci-repair-demo"


@pytest.fixture(autouse=True)
def _no_pull_seeded_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts before this process has seeded any pull request."""
    monkeypatch.setattr(seeded, "_SEEDED", {})


class _RepoState:
    """Files, refs, and pulls for one repository name."""

    def __init__(self, name: str, *, missing: bool) -> None:
        self.name = name
        self.missing = missing
        self.refs: dict[str, str] = {}
        self.commits: dict[str, dict[str, str]] = {}
        self.trees: dict[str, dict[str, str]] = {}
        self.parents: dict[str, list[str]] = {}
        self.prs: list[dict[str, Any]] = []
        self.private = True
        self.fork = False
        self.push = True

    def ensure_readme(self) -> None:
        self.missing = False
        if "main" not in self.refs:
            self.commits["readme"] = {"README.md": "# demo\n"}
            self.refs["main"] = "readme"

    def repository(self) -> dict[str, Any]:
        return {
            "full_name": f"{_OWNER}/{self.name}",
            "private": self.private,
            "fork": self.fork,
            "default_branch": "main",
            "permissions": {"push": self.push},
        }

    def files(self, branch: str) -> dict[str, str]:
        return self.commits[self.refs[branch]]


class _Api:
    """Just enough of the GitHub REST surface for one demo seed."""

    def __init__(self, *, missing: bool = True, login: str = _OWNER) -> None:
        self.login = login
        self.calls: list[tuple[str, str]] = []
        self.runs: list[dict[str, Any]] = []
        self.status_code: int | None = None
        self.missing_ref_error: GitHubApiError | None = None
        self._primary = _RepoState(_REPO, missing=missing)
        self._repos = {_REPO: self._primary}

    @property
    def missing(self) -> bool:
        return self._primary.missing

    @missing.setter
    def missing(self, value: bool) -> None:
        self._primary.missing = value

    @property
    def refs(self) -> dict[str, str]:
        return self._primary.refs

    @property
    def commits(self) -> dict[str, dict[str, str]]:
        return self._primary.commits

    @property
    def prs(self) -> list[dict[str, Any]]:
        return self._primary.prs

    @property
    def parents(self) -> dict[str, list[str]]:
        return self._primary.parents

    @property
    def private(self) -> bool:
        return self._primary.private

    @private.setter
    def private(self, value: bool) -> None:
        self._primary.private = value

    @property
    def fork(self) -> bool:
        return self._primary.fork

    @fork.setter
    def fork(self, value: bool) -> None:
        self._primary.fork = value

    @property
    def push(self) -> bool:
        return self._primary.push

    @push.setter
    def push(self, value: bool) -> None:
        self._primary.push = value

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> Any:
        self.calls.append((method, path))
        if self.status_code is not None and path == f"repos/{_OWNER}/{_REPO}":
            raise GitHubApiError(
                '{"message":"Repository creation failed.","documentation_url":"https://docs.github.com/rest","errors":[{"private-api-detail":true}]}',
                status_code=self.status_code,
                path=path,
            )
        if path == "user":
            return {"login": self.login, "id": 1}
        if path in {"user/repos", f"orgs/{_OWNER}/repos"} and method == "POST":
            assert body is not None
            state = self._state(str(body["name"]))
            state.ensure_readme()
            return state.repository()
        parsed = self._parse(path)
        if parsed is None:
            raise AssertionError((method, path))
        state, tail = parsed
        if tail == "":
            if state.missing:
                raise GitHubApiError("missing", status_code=HTTPStatus.NOT_FOUND, path=path)
            return state.repository()
        if tail == "contents":
            return [{"name": name.split("/", 1)[0], "type": "file"} for name in state.files("main")]
        if tail == "contents/.opensre-demo.json":
            return {"content": state.files("main")[".opensre-demo.json"]}
        if tail == "contents/calculator.py":
            assert params is not None
            text = state.commits.get(str(params["ref"]), {}).get("calculator.py")
            if text is None:
                raise GitHubApiError("missing", status_code=HTTPStatus.NOT_FOUND, path=path)
            # GitHub wraps the base64 at 60 columns, as encodebytes does.
            return {"encoding": "base64", "content": base64.encodebytes(text.encode()).decode()}
        if tail.startswith("branches/"):
            branch = tail.split("/", 1)[1]
            sha = state.refs.get(branch)
            if sha is None:
                raise GitHubApiError("missing", status_code=HTTPStatus.NOT_FOUND, path=path)
            # A commit's tree is named like the commit here, as in GET git/commits below.
            return {"commit": {"sha": sha, "commit": {"tree": {"sha": sha}}}}
        if method == "GET" and tail.startswith("git/commits/"):
            sha = tail.rsplit("/", 1)[-1]
            return {"sha": sha, "tree": {"sha": sha}}
        if method == "POST" and tail == "git/trees":
            assert body is not None
            base = str(body["base_tree"])
            merged = dict(state.trees.get(base) or state.commits.get(base) or {})
            for item in body["tree"]:
                merged[str(item["path"])] = str(item["content"])
            tree_sha = f"t{len(state.trees)}"
            state.trees[tree_sha] = merged
            return {"sha": tree_sha}
        if method == "POST" and tail == "git/commits":
            assert body is not None
            sha = f"c{len(state.commits)}"
            state.commits[sha] = dict(state.trees[str(body["tree"])])
            state.parents[sha] = [str(parent) for parent in body["parents"]]
            return {"sha": sha}
        if method == "PATCH" and tail.startswith("git/refs/heads/"):
            assert body is not None
            branch = tail.removeprefix("git/refs/heads/")
            if branch not in state.refs:
                if self.missing_ref_error is not None:
                    raise self.missing_ref_error
                raise GitHubApiError("missing", status_code=HTTPStatus.NOT_FOUND, path=path)
            assert body["force"] is False
            state.refs[branch] = str(body["sha"])
            return {}
        if method == "POST" and tail == "git/refs":
            assert body is not None
            branch = str(body["ref"]).removeprefix("refs/heads/")
            state.refs[branch] = str(body["sha"])
            return {}
        if tail == "pulls" and method == "GET":
            assert params is not None
            return [
                pull
                for pull in state.prs
                if pull["head_label"] == params["head"] and pull["state"] == "open"
            ]
        if tail == "pulls" and method == "POST":
            assert body is not None
            pull = {
                "number": len(state.prs) + 1,
                "state": "open",
                "head_label": f"{_OWNER}:{body['head']}",
                "head": {"sha": state.refs[str(body["head"])]},
                "title": body["title"],
                "body": body["body"],
            }
            state.prs.append(pull)
            return pull
        if tail == "actions/runs":
            assert params is not None
            sha = str(params["head_sha"])
            return {
                "workflow_runs": [
                    {**run, "head_sha": run.get("head_sha") or sha}
                    for run in self.runs
                    if run.get("head_sha") in {None, sha}
                ]
            }
        raise AssertionError((method, path))

    def _state(self, name: str) -> _RepoState:
        found = self._repos.get(name)
        if found is None:
            found = _RepoState(name, missing=True)
            self._repos[name] = found
        return found

    def _parse(self, path: str) -> tuple[_RepoState, str] | None:
        prefix = f"repos/{_OWNER}/"
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix) :]
        name, _sep, tail = rest.partition("/")
        if not name:
            return None
        return self._state(name), tail

    def _ensure_readme(self) -> None:
        self._primary.ensure_readme()

    def _repository(self) -> dict[str, Any]:
        return self._primary.repository()

    def _files(self, branch: str) -> dict[str, str]:
        return self._primary.files(branch)


def test_seed_creates_a_private_repo_and_returns_the_failed_run() -> None:
    api = _Api()
    api.runs.append({"id": 4242, "conclusion": "failure", "event": "pull_request"})

    result = seed_demo(api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0)

    assert result["pr_number"] == 1
    assert result["failed_run_id"] == 4242
    assert result["pr_url"] == f"https://github.com/{_OWNER}/{_REPO}/pull/1"
    assert result["created_repository"] is True
    assert result["reused"] is False
    assert api._files("main")["calculator.py"] == PASSING_CALCULATOR
    workflow = api._files("main")[".github/workflows/test.yml"]
    assert workflow == baseline_files()[".github/workflows/test.yml"]
    assert "Demo calculator CI" in workflow
    assert "pull_request:" in workflow
    assert "push:" not in workflow
    assert api._files(FAILING_BRANCH)["calculator.py"] == FAILING_CALCULATOR
    assert api._files(FAILING_BRANCH)["test_calculator.py"] == TEST_CALCULATOR
    assert api.prs[0]["body"] == "This pull request is a demo. Do not merge.\n"
    assert ("POST", "user/repos") in api.calls
    assert all(not path.startswith("orgs/") for _method, path in api.calls)
    assert all("search" not in path for _method, path in api.calls)


def _initialized_demo() -> _Api:
    """An existing demo repository: seeded main, no failing branch, no pull request."""
    api = _Api(missing=False)
    api._ensure_readme()
    api.commits["readme"].update(baseline_files())
    return api


def test_a_repository_the_seed_created_is_not_read_for_what_it_cannot_hold() -> None:
    """A new repository has no marker, pull request, or failing branch to look up."""
    api = _Api()
    api.runs.append({"id": 4242, "conclusion": "failure", "event": "pull_request"})

    seed_demo(api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0)

    path = f"repos/{_OWNER}/{_REPO}"
    assert api.calls == [
        ("GET", "user"),
        ("GET", path),
        ("POST", "user/repos"),
        ("GET", f"{path}/branches/main"),
        ("POST", f"{path}/git/trees"),
        ("POST", f"{path}/git/commits"),
        ("PATCH", f"{path}/git/refs/heads/main"),
        ("POST", f"{path}/git/trees"),
        ("POST", f"{path}/git/commits"),
        ("POST", f"{path}/git/refs"),
        ("POST", f"{path}/pulls"),
        ("GET", f"{path}/actions/runs"),
    ]


def test_seed_does_not_rewrite_an_existing_demo_branch() -> None:
    api = _Api(missing=False)
    api._ensure_readme()
    api.commits["readme"].update(baseline_files())
    api.commits["kept"] = {"calculator.py": "def add(a, b):\n    return a + b + 1\n"}
    api.refs[FAILING_BRANCH] = "kept"

    with pytest.raises(DemoRefused, match="will not rewrite"):
        seed_demo(api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0)

    assert api.refs[FAILING_BRANCH] == "kept"
    assert not any(method == "POST" and path.endswith("/pulls") for method, path in api.calls)


def test_seed_waits_for_the_pull_request_run_when_push_failed_first() -> None:
    api = _Api()
    api.runs.append({"id": 1, "conclusion": "failure", "event": "push"})

    def sleep(_seconds: float) -> None:
        api.runs.append({"id": 2, "conclusion": "failure", "event": "pull_request"})

    result = seed_demo(api, _OWNER, _REPO, sleep=sleep, now=lambda: 0.0)

    assert result["failed_run_id"] == 2


def test_seed_reuses_an_open_demo_pull_request() -> None:
    api = _Api(missing=False)
    api._ensure_readme()
    api.commits["readme"].update(baseline_files())
    api.commits["existing-head"] = {**baseline_files(), "calculator.py": FAILING_CALCULATOR}
    api.refs[FAILING_BRANCH] = "existing-head"
    api.prs.append(
        {
            "number": 7,
            "state": "open",
            "head_label": f"{_OWNER}:{FAILING_BRANCH}",
            "head": {"sha": "existing-head"},
        }
    )
    api.runs.append(
        {
            "id": 55,
            "conclusion": "failure",
            "event": "pull_request",
            "head_sha": "existing-head",
        }
    )

    result = seed_demo(api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0)

    assert result["reused"] is True
    assert result["rearmed"] is False
    assert result["pr_number"] == 7
    assert result["head_sha"] == "existing-head"
    assert result["failed_run_id"] == 55
    assert result["created_repository"] is False
    assert not any(method in {"POST", "PATCH"} for method, _path in api.calls)


def _repaired_demo() -> _Api:
    """A retained demo whose open PR #1 already carries the repair."""
    api = _initialized_demo()
    api.commits["fix-head"] = dict(baseline_files())
    api.refs[FAILING_BRANCH] = "fix-head"
    api.prs.append(
        {
            "number": 1,
            "state": "open",
            "head_label": f"{_OWNER}:{FAILING_BRANCH}",
            "head": {"sha": "fix-head"},
        }
    )
    return api


def test_a_repaired_demo_pull_request_is_rearmed_on_top_of_its_fix(tmp_path: Path) -> None:
    """A reused demo whose repair landed gets one failing commit; the fix is not rewritten."""
    api = _repaired_demo()
    rearm = f"c{len(api.commits)}"
    # The repaired head's run passed; only the new commit's run fails.
    api.runs.append(
        {"id": 11, "conclusion": "success", "event": "pull_request", "head_sha": "fix-head"}
    )
    api.runs.append({"id": 12, "conclusion": "failure", "event": "pull_request", "head_sha": rearm})

    result = seed_demo(
        api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0, store=RepairStore(tmp_path)
    )

    assert result["reused"] is True
    assert result["rearmed"] is True
    assert result["pr_number"] == 1
    assert result["head_sha"] == rearm
    assert result["failed_run_id"] == 12
    assert api.parents[rearm] == ["fix-head"]
    assert api.refs[FAILING_BRANCH] == rearm
    assert api._files(FAILING_BRANCH)["calculator.py"] == FAILING_CALCULATOR
    assert api._files(FAILING_BRANCH)["test_calculator.py"] == TEST_CALCULATOR
    path = f"repos/{_OWNER}/{_REPO}"
    assert ("PATCH", f"{path}/git/refs/heads/{FAILING_BRANCH}") in api.calls
    assert ("POST", f"{path}/git/refs") not in api.calls
    assert ("POST", f"{path}/pulls") not in api.calls
    assert seeded.was_seeded_here(1, _OWNER, _REPO, 1, rearm)
    assert not seeded.was_seeded_here(1, _OWNER, _REPO, 1, "fix-head")


def test_a_repair_still_running_is_not_rearmed_underneath(tmp_path: Path) -> None:
    """A second demo request must not push a failing commit under a repair in flight."""
    api = _repaired_demo()
    store = RepairStore(tmp_path)
    now = time.time()
    store.save(
        RepairRun(
            id="running-1",
            owner=_OWNER,
            repo=_REPO,
            actor=_OWNER,
            actor_id=1,
            started_at=now,
            deadline=now + 600,
            pr_number=1,
            fast_checks=True,
            initial_sha="seed-head",
            status=RepairStatus.RUNNING,
        )
    )

    with pytest.raises(DemoRefused, match="still running \\(task running-1\\)"):
        seed_demo(api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0, store=store)

    assert api.refs[FAILING_BRANCH] == "fix-head"
    assert not any(method in {"POST", "PATCH"} for method, _path in api.calls)


def test_seed_leaves_an_unrelated_repository_and_seeds_a_fresh_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _Api(missing=False)
    api._ensure_readme()
    api.commits["readme"]["src/app.py"] = "print('real')\n"
    taken = _RepoState("opensre-ci-repair-demo-aaaa", missing=False)
    taken.ensure_readme()
    taken.commits["readme"]["src/app.py"] = "print('also real')\n"
    api._repos[taken.name] = taken
    api.runs.append({"id": 77, "conclusion": "failure", "event": "pull_request"})
    names = iter(("opensre-ci-repair-demo-aaaa", "opensre-ci-repair-demo-bbbb"))
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.seed._fresh_demo_repo_name",
        lambda: next(names),
    )

    result = seed_demo(api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0)

    fresh = "opensre-ci-repair-demo-bbbb"
    assert result["owner"] == _OWNER
    assert result["repo"] == fresh
    assert result["requested_repo"] == _REPO
    assert result["pr_url"] == f"https://github.com/{_OWNER}/{fresh}/pull/1"
    assert result["failed_run_id"] == 77
    assert result["created_repository"] is True
    assert "Do not ask the user." in result["response_text"]
    primary = f"repos/{_OWNER}/{_REPO}"
    assert all(
        method == "GET"
        for method, path in api.calls
        if path == primary or path.startswith(f"{primary}/")
    )
    assert ("POST", "user/repos") in api.calls
    assert "src/app.py" not in api._repos[fresh].files("main")
    # Only the demo it seeded is remembered, for the seeding account (the fake's id 1)
    # at the head it returned; the refused real repositories never are.
    head = result["head_sha"]
    assert seeded.was_seeded_here(1, _OWNER, fresh, 1, head)
    assert not seeded.was_seeded_here(1, _OWNER, _REPO, 1, head)
    assert not seeded.was_seeded_here(1, _OWNER, "opensre-ci-repair-demo-aaaa", 1, head)


def test_a_non_404_repository_error_does_not_create(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api()
    api.status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.GitHubRestClient",
        lambda _token: api,
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.configured_token",
        lambda _token: "token",
    )

    result = seed_ci_repair_demo(_OWNER, _REPO)

    assert result["ok"] is False
    assert "private-api-detail" not in result["error"]
    assert "documentation_url" not in result["error"]
    assert f"HTTP {HTTPStatus.INTERNAL_SERVER_ERROR.value}" in result["error"]
    assert "Repository creation failed." in result["error"]
    assert ("POST", "user/repos") not in api.calls


def test_a_missing_branch_update_returns_422_and_the_branch_is_created() -> None:
    api = _initialized_demo()
    api.missing_ref_error = GitHubApiError(
        '{"message":"Reference does not exist","status":"422"}',
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        path=f"repos/{_OWNER}/{_REPO}/git/refs/heads/{FAILING_BRANCH}",
        method="PATCH",
    )
    api.runs.append({"id": 9, "conclusion": "failure", "event": "pull_request"})

    result = seed_demo(api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0)

    assert result["pr_number"] == 1
    assert api._files(FAILING_BRANCH)["calculator.py"] == FAILING_CALCULATOR
    assert ("PATCH", f"repos/{_OWNER}/{_REPO}/git/refs/heads/{FAILING_BRANCH}") in api.calls
    assert ("POST", f"repos/{_OWNER}/{_REPO}/git/refs") in api.calls


def test_a_rejected_branch_update_names_the_github_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _initialized_demo()
    ref = f"repos/{_OWNER}/{_REPO}/git/refs/heads/{FAILING_BRANCH}"
    api.missing_ref_error = GitHubApiError(
        '{"message":"Update is not a fast forward"}',
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        path=ref,
        method="PATCH",
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.GitHubRestClient",
        lambda _token: api,
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.configured_token",
        lambda _token: "token",
    )

    result = seed_ci_repair_demo(_OWNER, _REPO)

    assert result["ok"] is False
    assert "PATCH" in result["error"]
    assert ref in result["error"]
    assert f"HTTP {HTTPStatus.UNPROCESSABLE_ENTITY.value}" in result["error"]
    assert "Update is not a fast forward" in result["error"]
    assert ("POST", f"repos/{_OWNER}/{_REPO}/git/refs") not in api.calls


def test_a_token_like_github_message_is_omitted() -> None:
    from integrations.github.tools.ci_repair_demo.tool import _seed_error_text

    text = _seed_error_text(
        GitHubApiError(
            '{"message":"bad ghp_secretvalue"}',
            status_code=HTTPStatus.FORBIDDEN,
        )
    )

    assert "ghp_" not in text
    assert f"HTTP {HTTPStatus.FORBIDDEN.value}" in text


def test_a_passing_pull_request_run_is_refused() -> None:
    api = _Api()
    api.runs.append({"id": 1, "conclusion": "success", "event": "pull_request"})

    with pytest.raises(DemoRefused, match="did not fail CI"):
        seed_demo(api, _OWNER, _REPO, sleep=_forbidden_sleep, now=lambda: 0.0)


def test_seed_waits_until_the_pull_request_run_fails() -> None:
    api = _Api()
    clock = {"now": 0.0}

    def sleep(_seconds: float) -> None:
        api.runs.append({"id": 9, "conclusion": "failure", "event": "pull_request"})

    result = seed_demo(api, _OWNER, _REPO, sleep=sleep, now=lambda: clock["now"])

    assert result["failed_run_id"] == 9


def test_seed_stops_when_the_failure_wait_ends() -> None:
    api = _Api()
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds + 180

    with pytest.raises(DemoRefused, match="before the wait ended"):
        seed_demo(api, _OWNER, _REPO, sleep=sleep, now=lambda: clock["now"])


def test_the_seeded_calculator_fails_its_own_test(tmp_path: Path) -> None:
    for name, content in baseline_files().items():
        if name.startswith("."):
            continue
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (tmp_path / "calculator.py").write_text(PASSING_CALCULATOR)
    (tmp_path / "test_calculator.py").write_text(TEST_CALCULATOR)
    good = subprocess.run(
        [sys.executable, "-m", "unittest", "-v"], cwd=tmp_path, capture_output=True, check=False
    )
    assert good.returncode == 0
    (tmp_path / "calculator.py").write_text(FAILING_CALCULATOR)
    shutil.rmtree(tmp_path / "__pycache__", ignore_errors=True)
    bad = subprocess.run(
        [sys.executable, "-m", "unittest", "-v"], cwd=tmp_path, capture_output=True, check=False
    )
    assert bad.returncode != 0
    assert b"AssertionError" in bad.stderr


def test_running_the_demo_test_adds_nothing_the_repair_scope_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The calculator.py-only scope reads git status; the coding agent's Python writes bytecode."""
    # Arrange: the seeded files in a fresh checkout, without this machine's git config
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name, content in baseline_files().items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    before = set(changed_paths(str(tmp_path)))
    # The coding agent's environment carries neither bytecode setting.
    unset = {"PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"}
    env = {key: value for key, value in os.environ.items() if key not in unset}

    # Act
    subprocess.run(
        [sys.executable, "-m", "unittest", "-q"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=True,
    )

    # Assert: the bytecode exists, and git status does not report it
    assert (tmp_path / "__pycache__").is_dir()
    assert set(changed_paths(str(tmp_path))) == before


def _owned_task(repo: str = "Tracer-Cloud/opensre-ci-repair-demo") -> SimpleNamespace:
    return SimpleNamespace(id="9f4ed7a7a92f", name=f"CI repair: {repo}")


def test_finish_keeps_an_unsuffixed_repository_and_removes_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cleanup, "results_directory", lambda: tmp_path)
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.get_task",
        lambda _task_id: _owned_task(),
    )
    removed: list[str] = []
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.remove_task",
        lambda task_id: removed.append(task_id) or True,
    )
    monkeypatch.setattr("integrations.github.tools.ci_repair_demo.tool.list_tasks", lambda: [])

    analysis = "**Root cause analysis**\n- What failed: Check `test` failed on commit abc0000."

    result = finish_ci_repair_demo(
        repo="Tracer-Cloud/opensre-ci-repair-demo",
        pr_number=4,
        loop_id="9f4ed7a7a92f",
        outcome="success",
        failed_run_id=11,
        fix_commit="abc123",
        passing_run_id=22,
        analysis=analysis,
    )

    assert result["ok"] is True
    assert result["loop_removed"] is True
    assert result["repository_retained"] is True
    assert "remains" in result["response_text"]
    text = Path(result["evidence"]).read_text(encoding="utf-8")
    assert "Tracer-Cloud/opensre-ci-repair-demo" in text
    assert "Repository retained." in text
    assert text.endswith(f"{analysis}\n")
    assert removed == ["9f4ed7a7a92f"]


def test_finish_reports_a_loop_that_is_still_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cleanup, "results_directory", lambda: tmp_path)
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.get_task",
        lambda _task_id: _owned_task(),
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.remove_task", lambda _task_id: False
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.list_tasks",
        lambda: [SimpleNamespace(id="9f4ed7a7a92f")],
    )

    result = finish_ci_repair_demo(
        repo="Tracer-Cloud/opensre-ci-repair-demo",
        pr_number=4,
        loop_id="9f4ed7a7a92f",
        outcome="failed",
    )

    assert result["ok"] is False
    assert result["loop_removed"] is False
    assert result["repository_retained"] is True
    assert "still listed" in result["error"]


def test_finish_refuses_a_schedule_that_is_not_this_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.get_task",
        lambda _task_id: SimpleNamespace(id="other", name="Morning digest"),
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.remove_task",
        lambda task_id: removed.append(task_id),
    )

    result = finish_ci_repair_demo(
        repo="Tracer-Cloud/opensre-ci-repair-demo",
        pr_number=4,
        loop_id="other",
        outcome="failed",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "refused"
    assert "not the CI repair" in result["error"]
    assert removed == []


def test_finish_removes_the_loop_when_evidence_cannot_be_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.get_task",
        lambda _task_id: _owned_task(),
    )

    def _unwritable(**_kwargs: object) -> str:
        raise OSError("read-only")

    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.write_evidence",
        _unwritable,
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.remove_task",
        lambda task_id: removed.append(task_id) or True,
    )
    monkeypatch.setattr("integrations.github.tools.ci_repair_demo.tool.list_tasks", lambda: [])

    result = finish_ci_repair_demo(
        repo="Tracer-Cloud/opensre-ci-repair-demo",
        pr_number=4,
        loop_id="9f4ed7a7a92f",
        outcome="failed",
    )

    assert result["ok"] is False
    assert result["loop_removed"] is True
    assert result["repository_retained"] is True
    assert "OSError" in result["error"]
    assert "read-only" not in result["error"]
    assert removed == ["9f4ed7a7a92f"]


def test_finish_refuses_success_without_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[str] = []
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.get_task",
        lambda _task_id: _owned_task(),
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_repair_demo.tool.remove_task",
        lambda task_id: removed.append(task_id) or True,
    )
    monkeypatch.setattr("integrations.github.tools.ci_repair_demo.tool.list_tasks", lambda: [])

    result = finish_ci_repair_demo(
        repo="Tracer-Cloud/opensre-ci-repair-demo",
        pr_number=4,
        loop_id="9f4ed7a7a92f",
        outcome="success",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "refused"
    assert "failed run" in result["error"]
    assert result["loop_removed"] is True
    assert removed == ["9f4ed7a7a92f"]


def _forbidden_sleep(_seconds: float) -> None:
    raise AssertionError("seed waited even though the Actions run had already failed")
