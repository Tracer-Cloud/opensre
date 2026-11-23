from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "automerge_pr.py"
_spec = importlib.util.spec_from_file_location("automerge_pr", _MODULE_PATH)
assert _spec and _spec.loader
automerge_pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(automerge_pr)

_APPROVED_HEAD = "a" * 40
_UNSEEN_HEAD = "b" * 40


def _approval(state: str = "success", creator: str = "github-actions[bot]") -> dict[str, Any]:
    return {"context": "automerge/approved", "state": state, "creator": {"login": creator}}


class _GitHub:
    """The slice of the GitHub API the script talks to, with its writes recorded."""

    def __init__(self, pr: dict[str, Any], statuses: dict[str, list[dict[str, Any]]]) -> None:
        self.pr = pr
        self.statuses = statuses
        self.permissions: dict[str, str] = {}
        self.calls: list[list[str]] = []

    def run_gh(self, args: list[str]) -> Any:
        self.calls.append(args)
        if args[:2] == ["pr", "view"]:
            return self.pr
        path = next(arg for arg in args if arg.startswith("repos/"))
        if path.endswith("/statuses"):
            return [self.statuses.get(path.split("/")[-2], [])]
        if path.endswith("/permission"):
            login = path.split("/")[-2]
            if login not in self.permissions:
                raise automerge_pr.subprocess.CalledProcessError(1, "gh")
            return {"permission": self.permissions[login]}
        return {}

    def run(self, args: list[str], **_kwargs: Any) -> None:
        self.calls.append(args[1:])


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> _GitHub:
    pr = {
        "baseRefName": "main",
        "headRefOid": _APPROVED_HEAD,
        "state": "OPEN",
        "isDraft": False,
        "labels": [{"name": "automerge"}],
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            {"name": "CI Gate", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "Greptile Review", "status": "IN_PROGRESS"},
        ],
        "title": "A change",
    }
    fake = _GitHub(pr, {_APPROVED_HEAD: [_approval()]})
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR_NUMBER", "77")
    monkeypatch.setattr(automerge_pr, "_run_gh", fake.run_gh)
    monkeypatch.setattr(automerge_pr.subprocess, "run", fake.run)
    return fake


def _merges(github: _GitHub) -> list[list[str]]:
    return [call for call in github.calls if call[:2] == ["pr", "merge"]]


def _writes(github: _GitHub) -> list[tuple[str, str]]:
    return [
        (call[call.index("--method") + 1], call[call.index("--method") + 2])
        for call in github.calls
        if "--method" in call
    ]


def test_ci_gate_and_all_other_checks_must_pass_except_greptile() -> None:
    gate = {"name": "CI Gate", "status": "COMPLETED", "conclusion": "SUCCESS"}
    greptile = {"name": "Greptile Review", "status": "IN_PROGRESS"}
    assert automerge_pr._checks_are_green([gate, greptile])[0]
    for check in (
        {"name": "other", "status": "IN_PROGRESS"},
        {"name": "vale-spellcheck", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"context": "other", "state": "FAILURE"},
    ):
        assert not automerge_pr._checks_are_green([gate, check])[0]
    assert not automerge_pr._checks_are_green([greptile])[0]
    assert not automerge_pr._checks_are_green([{**gate, "conclusion": "SKIPPED"}])[0]


def test_labeled_pr_merges_at_its_checked_head(github: _GitHub) -> None:
    automerge_pr.main()
    (merge,) = _merges(github)
    assert merge[merge.index("--match-head-commit") + 1] == _APPROVED_HEAD
    assert merge[merge.index("--subject") + 1] == "A change (#77)"

    github.calls.clear()
    github.pr["labels"] = []
    automerge_pr.main()
    assert not _merges(github)


def test_a_head_pushed_after_the_label_is_not_merged(github: _GitHub) -> None:
    """The label outlives a push, so a green, labeled head nobody approved must wait."""
    github.pr["headRefOid"] = _UNSEEN_HEAD

    automerge_pr.main()

    assert not _merges(github)


@pytest.mark.parametrize(
    "statuses",
    [
        [_approval(creator="mallory")],
        [_approval(state="failure"), _approval()],
    ],
    ids=["status from another account", "approval withdrawn later"],
)
def test_only_the_current_trusted_approval_counts(
    github: _GitHub, statuses: list[dict[str, Any]]
) -> None:
    github.statuses[_APPROVED_HEAD] = statuses

    automerge_pr.main()

    assert not _merges(github)


def _record(monkeypatch: pytest.MonkeyPatch, *, action: str, actor: str, labeled: bool) -> None:
    monkeypatch.setenv("EVENT_ACTION", action)
    monkeypatch.setenv("EVENT_ACTOR", actor)
    monkeypatch.setenv("PR_HEAD_SHA", _UNSEEN_HEAD)
    monkeypatch.setenv("HAS_AUTOMERGE_LABEL", "true" if labeled else "false")
    automerge_pr.record_approval()


@pytest.mark.parametrize("action", ["labeled", "synchronize"])
def test_a_writer_labeling_or_pushing_approves_that_head(
    github: _GitHub, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    github.permissions["maintainer"] = "write"

    _record(monkeypatch, action=action, actor="maintainer", labeled=True)

    assert _writes(github) == [("POST", f"repos/o/r/statuses/{_UNSEEN_HEAD}")]


@pytest.mark.parametrize(
    ("action", "actor", "permission"),
    [
        ("synchronize", "contributor", "read"),
        ("synchronize", "stranger", None),
        ("labeled", "triager", "read"),
    ],
)
def test_without_write_access_nothing_is_approved_and_the_label_comes_off(
    github: _GitHub,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    actor: str,
    permission: str | None,
) -> None:
    if permission is not None:
        github.permissions[actor] = permission

    _record(monkeypatch, action=action, actor=actor, labeled=True)

    assert _writes(github) == [("DELETE", "repos/o/r/issues/77/labels/automerge")]


def test_a_push_to_an_unlabeled_pr_changes_nothing(
    github: _GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(monkeypatch, action="synchronize", actor="contributor", labeled=False)

    assert not _writes(github)
