"""Crash recovery resumes only a repair whose verification never settled."""

from dataclasses import replace
from pathlib import Path

import pytest

from integrations.github.tools.ci_fix import resume
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.storage import database
from integrations.github.tools.ci_fix.storage.attempts import (
    PreparedPush,
    repair_key,
    save_prepared_push,
)

_CTX = CiFixContext(
    owner="Tracer-Cloud",
    repo="opensre",
    number=4597,
    title="feat: add fixer",
    url="https://github.com/Tracer-Cloud/opensre/pull/4597",
    base_branch="main",
    head_branch="feat/fix-ci",
    head_sha="fix-sha",
    skipped_check_names=(),
    failing_checks=(),
    task="Fix CI.",
)
_KEY = repair_key("Tracer-Cloud", "opensre", "4597")


def test_a_settled_repair_is_not_resumed_but_an_unsettled_one_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passed record left in the ledger must not make every later tick "succeed" again."""
    monkeypatch.setattr(database, "database_path", lambda: tmp_path / "repairs.db")
    monkeypatch.setattr(resume, "remote_branch_sha", lambda *_a, **_k: "fix-sha")
    prepared = PreparedPush("source-sha", "fix-sha", "feat/fix-ci", ["app.py"])

    save_prepared_push(_KEY, replace(prepared, checks_state="passed"))
    assert resume.resumed_push(_CTX, "/workspace", github_token="tok") is None

    save_prepared_push(_KEY, replace(prepared, checks_state="timed_out"))
    recovered = resume.resumed_push(_CTX, "/workspace", github_token="tok")
    assert recovered is not None
    restored, push = recovered
    assert restored.head_sha == "source-sha"
    assert push.head_sha == "fix-sha"
