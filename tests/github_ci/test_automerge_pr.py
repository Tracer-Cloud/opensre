from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "automerge_pr.py"
_spec = importlib.util.spec_from_file_location("automerge_pr", _MODULE_PATH)
assert _spec and _spec.loader
automerge_pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(automerge_pr)


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


def test_labeled_pr_merges_at_its_checked_head(monkeypatch) -> None:
    pr = {
        "baseRefName": "main",
        "headRefOid": "abc123",
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
    commands = []
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR_NUMBER", "77")
    monkeypatch.setattr(automerge_pr, "_run_gh", lambda _args: pr)
    monkeypatch.setattr(
        automerge_pr.subprocess, "run", lambda args, **_kwargs: commands.append(args)
    )

    automerge_pr.main()
    assert commands[0][commands[0].index("--match-head-commit") + 1] == "abc123"
    assert commands[0][commands[0].index("--subject") + 1] == "A change (#77)"

    commands.clear()
    pr["labels"] = []
    automerge_pr.main()
    assert not commands
