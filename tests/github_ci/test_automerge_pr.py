from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "automerge_pr.py"
_spec = importlib.util.spec_from_file_location("automerge_pr", _MODULE_PATH)
assert _spec and _spec.loader
automerge_pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(automerge_pr)


def test_squash_commit_subject_appends_pr_number() -> None:
    assert (
        automerge_pr._squash_commit_subject("fix(cli): show full root cause", "3025")
        == "fix(cli): show full root cause (#3025)"
    )


def test_squash_commit_subject_avoids_duplicate_pr_number() -> None:
    assert (
        automerge_pr._squash_commit_subject("fix(cli): example (#3025)", "3025")
        == "fix(cli): example (#3025)"
    )


def test_checks_are_green_for_completed_check_runs() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "CI Gate",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "windows test",
                "status": "COMPLETED",
                "conclusion": "SKIPPED",
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_missing_ci_gate_blocks_merge() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "SUCCESS",
            }
        ]
    )
    assert green is False
    assert reason == "CI Gate has not reported yet"


def test_checks_are_green_mixed_check_run_and_status_context() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "CI Gate",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "SUCCESS",
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_status_context_failure_blocks_merge() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {"name": "CI Gate", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "FAILURE",
            },
        ]
    )
    assert green is False
    assert reason == "status not green: ci/legacy (FAILURE)"


def test_status_context_pending_blocks_merge() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {"name": "CI Gate", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "PENDING",
            },
        ]
    )
    assert green is False
    assert reason == "status still pending: ci/legacy"


def test_ignores_automerge_workflow_check_while_running() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "CI Gate",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "Merge when CI is green",
                "workflowName": "Auto-merge",
                "status": "IN_PROGRESS",
                "conclusion": None,
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_mintlify_vale_spellcheck_failure_blocks_merge() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "CI Gate",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "Mintlify Validation (tracer) - vale-spellcheck",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            },
        ]
    )
    assert green is False
    assert "vale-spellcheck" in reason


def test_ignores_greptile_review_while_running() -> None:
    """Greptile is external; waiting on it strands PRs after the last Actions retry."""
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "CI Gate",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "Greptile Review",
                "status": "IN_PROGRESS",
                "conclusion": None,
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_runtime_and_ci_paths_are_never_auto_merged() -> None:
    """The blast-radius paths need a human on the merge button.

    ``core``, ``platform`` and ``gateway`` carry the agent runtime, the
    multi-tenant platform and the chat gateway; a bad change reaches every user.
    The workflow and script paths are the merge machinery itself, which must not
    be able to widen its own permissions unattended.
    """
    # Arrange
    protected = [
        "core/agent/react_loop.py",
        "platform/guardrails/engine.py",
        "gateway/transports/slack/handler.py",
        ".github/workflows/ci.yml",
        ".github/scripts/automerge_pr.py",
    ]

    # Act / Assert
    for path in protected:
        assert automerge_pr._protected_paths([path]) == [path], path


def test_ordinary_paths_stay_eligible() -> None:
    """Docs, tests and integrations keep merging without a human."""
    # Arrange
    files = [
        "tests/core/test_thing.py",
        "integrations/datadog/client.py",
        "surfaces/cli/app.py",
        "README.md",
    ]

    # Act / Assert
    assert automerge_pr._protected_paths(files) == []


def test_one_protected_file_taints_the_whole_pr() -> None:
    """A mostly-docs PR that also edits the runtime still needs a human."""
    # Arrange
    files = ["docs/a.mdx", "core/agent/agent.py", "docs/b.mdx"]

    # Act / Assert
    assert automerge_pr._protected_paths(files) == ["core/agent/agent.py"]


def test_lookalike_prefixes_are_not_protected() -> None:
    """Only real package roots count, not names that merely start the same."""
    # Arrange
    files = ["coreutils/x.py", "platformer/y.py", "gateways.md"]

    # Act / Assert
    assert automerge_pr._protected_paths(files) == []


def _pr_payload(**overrides: object) -> dict:
    """A labeled, open, green PR targeting main."""
    payload = {
        "baseRefName": "main",
        "headRefOid": "0123456789abcdef",
        "state": "OPEN",
        "isDraft": False,
        "labels": [{"name": "automerge"}],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "changedFiles": 2,
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "CI Gate",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            }
        ],
        "title": "a change",
    }
    payload.update(overrides)
    return payload


def _run_pr(monkeypatch, payload: dict, files: list[dict]) -> list:
    """Drive ``main`` for one PR, returning the gh commands it executed."""
    commands: list = []

    def _fake_gh(args: list[str]) -> object:
        # ``api`` fetches the changed files; ``pr view`` fetches the PR itself.
        return files if args[0] == "api" else payload

    monkeypatch.setattr(automerge_pr, "_run_gh", _fake_gh)
    monkeypatch.setattr(
        automerge_pr.subprocess, "run", lambda *args, **_kwargs: commands.append(args[0])
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR_NUMBER", "77")
    automerge_pr.main()
    return commands


def test_a_green_protected_pr_is_not_merged(monkeypatch) -> None:
    """The policy has to hold in ``main``, not only in the helper.

    Asserting on ``_protected_paths`` alone stays green even if the caller stops
    consulting it, so this pins the refusal on the path that actually merges.
    """
    # Arrange / Act
    commands = _run_pr(
        monkeypatch,
        _pr_payload(),
        [{"filename": "docs/a.mdx"}, {"filename": "core/agent/agent.py"}],
    )

    # Assert
    assert commands == [], "a PR touching core/ was auto-merged"


def test_a_green_ordinary_pr_is_still_merged(monkeypatch) -> None:
    """The guard must not block everything it was not aimed at."""
    # Arrange / Act
    commands = _run_pr(
        monkeypatch, _pr_payload(), [{"filename": "docs/a.mdx"}, {"filename": "docs/b.mdx"}]
    )

    # Assert
    assert len(commands) == 1
    assert "merge" in commands[0]
    assert commands[0][commands[0].index("--match-head-commit") + 1] == "0123456789abcdef"


def test_moving_a_file_out_of_a_protected_directory_is_caught(monkeypatch) -> None:
    """A rename hides the origin unless the pre-rename path is checked.

    ``gh pr view --json files`` reports only the destination, so moving
    ``core/agent/agent.py`` to ``docs/`` would read as an ordinary new doc and
    auto-merge — carrying runtime code out of the protected tree. The REST
    listing carries ``previous_filename``, which is what makes this visible.
    """
    # Arrange / Act
    # ``changedFiles`` must match the listing, or the truncation guard would
    # refuse first and this would pass without ever testing the rename.
    commands = _run_pr(
        monkeypatch,
        _pr_payload(changedFiles=1),
        [{"filename": "docs/agent.py", "previous_filename": "core/agent/agent.py"}],
    )

    # Assert
    assert commands == [], "a file moved out of core/ was auto-merged"


def test_a_truncated_listing_refuses_the_merge(monkeypatch) -> None:
    """Fewer REST entries than ``changedFiles`` means the check cannot see everything."""
    # Arrange / Act
    commands = _run_pr(monkeypatch, _pr_payload(changedFiles=105), [{"filename": "docs/a.mdx"}])

    # Assert
    assert commands == []


def test_rename_inflation_cannot_mask_a_truncated_listing(monkeypatch) -> None:
    """Destination + previous_filename must not satisfy the completeness check.

    A truncated rename-heavy page can expand to more paths than ``changedFiles``
    even while file entries are still missing. Counting paths would then let an
    unseen ``core/`` change past the cut reach ``gh pr merge``.
    """
    # Arrange — 8 entries (3 renames) against 10 changed files → 11 paths, incomplete
    files = [
        {"filename": "docs/a.mdx"},
        {"filename": "docs/b.mdx"},
        {"filename": "docs/c.mdx"},
        {"filename": "docs/d.mdx"},
        {"filename": "docs/e.mdx"},
        {"filename": "docs/old_a.py", "previous_filename": "docs/new_a.py"},
        {"filename": "docs/old_b.py", "previous_filename": "docs/new_b.py"},
        {"filename": "docs/old_c.py", "previous_filename": "docs/new_c.py"},
    ]
    assert len(files) == 8
    assert len(automerge_pr._paths_from_pr_files(files)) == 11

    # Act
    commands = _run_pr(monkeypatch, _pr_payload(changedFiles=10), files)

    # Assert
    assert commands == [], "rename-inflated truncated listing was treated as complete"
    assert automerge_pr._file_listing_is_complete(10, files) is False
    assert automerge_pr._file_listing_is_complete(8, files) is True


def test_ci_gate_must_succeed() -> None:
    for conclusion in ("SKIPPED", "FAILURE", None):
        assert not automerge_pr._checks_are_green(
            [{"name": "CI Gate", "status": "COMPLETED", "conclusion": conclusion}]
        )[0]


def test_missing_changed_file_count_blocks_merge(monkeypatch) -> None:
    assert not _run_pr(monkeypatch, _pr_payload(changedFiles=None), [{"filename": "docs/a.mdx"}])


def test_missing_head_sha_blocks_merge(monkeypatch) -> None:
    assert not _run_pr(
        monkeypatch,
        _pr_payload(headRefOid=None),
        [{"filename": "docs/a.mdx"}, {"filename": "docs/b.mdx"}],
    )


def test_paginated_file_listing_checks_every_page(monkeypatch) -> None:
    calls = []

    def fetch(args):
        calls.append(args)
        return [[{"filename": "docs/a.mdx"}], [{"filename": "core/a.py"}]]

    monkeypatch.setattr(automerge_pr, "_run_gh", fetch)
    entries = automerge_pr._list_pr_files("o/r", "77")
    assert "--slurp" in calls[0]
    assert automerge_pr._protected_paths(automerge_pr._paths_from_pr_files(entries)) == [
        "core/a.py"
    ]
