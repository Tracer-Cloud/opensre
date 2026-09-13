"""Regressions for local selection, failure aggregation, and Git push enforcement."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / ".github" / "ci"))

from check_catalog import Check, quality_checks  # noqa: E402
from git_changes import changed_files  # noqa: E402
from run_checks import run_checks  # noqa: E402
from test_scope_rules import RULES, select_tests  # noqa: E402


def test_global_contracts_do_not_depend_on_changed_package() -> None:
    commands = [check.args for check in quality_checks()]
    assert any("tests/tools/test_registry_index.py" in cmd for cmd in commands)
    assert any("tests/core/agent/test_tool_registry.py" in cmd for cmd in commands)
    assert any(
        "tests/fleet_monitoring/test_probe.py::"
        "test_psutil_is_not_imported_outside_sanctioned_modules" in cmd
        for cmd in commands
    )
    # Registry discovery must start before unrelated tests can populate imports.
    registry = next(cmd for cmd in commands if "tests/tools/test_registry_index.py" in cmd)
    assert registry[-1] == "tests/tools/test_registry_index.py"


def test_scope_rules_reference_existing_tests() -> None:
    assert all(rule.test_targets for rule in RULES)
    assert not {
        target for rule in RULES for target in rule.test_targets if not (_ROOT / target).exists()
    }


def test_selection_retains_scheduler_constants_and_unmapped_files(tmp_path: Path) -> None:
    for path in ("tests/scheduler", "tests/config", "tests/integrations"):
        (tmp_path / path).mkdir(parents=True)
    scope = select_tests(
        [
            "infrastructure/scheduling/scheduler/executor.py",
            "config/constants/new.py",
            "integrations/new_vendor/client.py",
            "new_runtime/worker.py",
        ],
        root=tmp_path,
    )
    assert set(scope.targets) == {"tests/scheduler/", "tests/config/", "tests/integrations/"}
    assert scope.errors == ("No test rule for new_runtime/worker.py",)


def test_selection_catches_untracked_tests_and_does_not_collect_deleted_files(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "tests" / "new"
    directory.mkdir(parents=True)
    (directory / "test_added.py").write_text("", encoding="utf-8")
    scope = select_tests(["tests/new/test_added.py", "tests/new/test_deleted.py"], root=tmp_path)
    assert scope.targets == ("tests/new/test_added.py",)
    assert not scope.errors


def test_failing_check_does_not_hide_other_failures(tmp_path: Path) -> None:
    witness = tmp_path / "ran"
    checks = (
        Check("first", "static", ("-c", "raise SystemExit(1)")),
        Check(
            "second", "static", ("-c", f"from pathlib import Path; Path({str(witness)!r}).touch()")
        ),
    )
    assert run_checks(checks, root=tmp_path, workers=2) == 1
    assert witness.exists()


def test_first_push_and_untracked_paths_cannot_be_mistaken_for_an_empty_diff(
    push_repo: Path,
) -> None:
    assert "bad.txt" in changed_files(push_repo)
    _git(push_repo, "push", "origin", "main")
    (push_repo / " new test.py").write_text("", encoding="utf-8")
    (push_repo / "staged.py").write_text("", encoding="utf-8")
    _git(push_repo, "add", "staged.py")
    assert changed_files(push_repo) == [" new test.py", "staged.py"]
    with pytest.raises(subprocess.CalledProcessError):
        changed_files(push_repo, "refs/heads/missing-base")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
    )


@pytest.fixture
def push_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Gate test")
    _git(repo, "config", "user.email", "gate@example.invalid")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    (repo / "pyproject.toml").write_text(
        '[project]\nname="gate-fixture"\nversion="0.0.0"\nrequires-python=">=3.13"\n'
        "[project.optional-dependencies]\ndev=[]\n[tool.uv]\npackage=false\n",
        encoding="utf-8",
    )
    subprocess.run(["uv", "lock", "--offline"], cwd=repo, check=True, capture_output=True)
    scripts = repo / ".github" / "ci"
    scripts.mkdir(parents=True)
    for name in ("pre_push.py", "git_changes.py", "install_hooks.py"):
        (scripts / name).write_bytes((_ROOT / ".github" / "ci" / name).read_bytes())
    (scripts / "run_checks.py").write_text(
        "from pathlib import Path\n"
        'raise SystemExit(1 if Path("bad.txt").read_text().strip() == "bad" else 0)\n',
        encoding="utf-8",
    )
    (repo / "bad.txt").write_text("good", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    subprocess.run(
        [sys.executable, str(scripts / "install_hooks.py")],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def test_push_checks_commit_instead_of_dirty_fix_and_records_explicit_override(
    push_repo: Path,
) -> None:
    _git(push_repo, "push", "origin", "main")
    accepted = _git(push_repo, "rev-parse", "HEAD").stdout.strip()
    (push_repo / "bad.txt").write_text("bad", encoding="utf-8")
    _git(push_repo, "add", "bad.txt")
    _git(push_repo, "commit", "-m", "broken")
    (push_repo / "bad.txt").write_text("good", encoding="utf-8")
    blocked = _git(push_repo, "push", "origin", "main", check=False)
    assert blocked.returncode != 0, blocked.stdout + blocked.stderr
    assert _git(push_repo, "ls-remote", "origin", "refs/heads/main").stdout.split()[0] == accepted
    _git(push_repo, "-c", "opensre.prePushOverride=incident recovery", "push", "origin", "main")
    log = push_repo / ".git" / "pre-push-overrides.jsonl"
    assert "incident recovery" in log.read_text(encoding="utf-8")
    assert (push_repo / "bad.txt").read_text(encoding="utf-8") == "good"
    assert len(_git(push_repo, "worktree", "list", "--porcelain").stdout.split("worktree ")) == 2


def test_install_preserves_custom_hooks_and_their_push_input(push_repo: Path) -> None:
    original = push_repo / "custom-hooks"
    original.mkdir()
    witness = push_repo / "previous-input"
    hook = original / "pre-push"
    hook.write_text(f"#!/bin/sh\ncat > '{witness}'\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(push_repo, "config", "--worktree", "core.hooksPath", str(original))
    subprocess.run(
        [sys.executable, str(push_repo / ".github/ci/install_hooks.py")],
        cwd=push_repo,
        check=True,
        capture_output=True,
    )
    blocked = _git(
        push_repo,
        "-c",
        "opensre.prePushOverride=test override",
        "push",
        "origin",
        "main",
        check=False,
    )
    assert blocked.returncode != 0
    assert "refs/heads/main" in witness.read_text(encoding="utf-8")
    assert "exit 1" in hook.read_text(encoding="utf-8")


def test_push_of_a_non_head_ref_validates_that_ref(push_repo: Path) -> None:
    _git(push_repo, "push", "origin", "main")
    _git(push_repo, "checkout", "-b", "broken")
    (push_repo / "bad.txt").write_text("bad", encoding="utf-8")
    _git(push_repo, "add", "bad.txt")
    _git(push_repo, "commit", "-m", "broken branch")
    _git(push_repo, "checkout", "main")
    blocked = _git(push_repo, "push", "origin", "broken", check=False)
    assert blocked.returncode != 0, blocked.stdout + blocked.stderr
    assert not _git(push_repo, "ls-remote", "origin", "refs/heads/broken").stdout


def test_install_is_idempotent_and_does_not_change_a_sibling_worktree(push_repo: Path) -> None:
    original = _git(push_repo, "config", "--get", "core.hooksPath").stdout
    sibling = push_repo.parent / "sibling"
    _git(push_repo, "worktree", "add", "--detach", str(sibling))
    try:
        script = sibling / ".github/ci/install_hooks.py"
        for _ in range(2):
            subprocess.run(
                [sys.executable, str(script)], cwd=sibling, check=True, capture_output=True
            )
        assert _git(push_repo, "config", "--get", "core.hooksPath").stdout == original
        assert _git(sibling, "config", "--get", "core.hooksPath").stdout != original
        # A second install must not chain the managed hook back to itself.
        previous = _git(sibling, "config", "--get", "opensre.previousHooksPath").stdout
        assert "opensre-hooks" not in previous
    finally:
        _git(push_repo, "worktree", "remove", "--force", str(sibling))
