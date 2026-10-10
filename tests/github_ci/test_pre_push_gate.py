"""Regressions for local selection, failure aggregation, and Git push enforcement."""

from __future__ import annotations

import io
import json
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
    assert "bad.py" in changed_files(push_repo)
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
def push_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", sys.prefix)
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
        "[project.optional-dependencies]\ndev=[]\n[tool.uv]\npackage=false\n"
        "[tool.ruff]\nline-length=100\n",
        encoding="utf-8",
    )
    subprocess.run(["uv", "lock", "--offline"], cwd=repo, check=True, capture_output=True)
    scripts = repo / ".github" / "ci"
    scripts.mkdir(parents=True)
    for name in ("pre_push.py", "git_changes.py", "install_hooks.py", "check_catalog.py"):
        (scripts / name).write_bytes((_ROOT / ".github" / "ci" / name).read_bytes())
    (scripts / "run_checks.py").write_text(
        "import argparse\n"
        "from pathlib import Path\n\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--scope', action='store_true')\n"
        "parser.add_argument('--head')\n"
        "parser.add_argument('--base')\n"
        "parser.parse_args()\n"
        "raise SystemExit('missing_name' in Path('bad.py').read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    (repo / "bad.py").write_text("value = 1\n", encoding="utf-8")
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
    (push_repo / "bad.py").write_text("value = missing_name\n", encoding="utf-8")
    _git(push_repo, "add", "bad.py")
    _git(push_repo, "commit", "-m", "broken")
    (push_repo / "bad.py").write_text("value = 1\n", encoding="utf-8")
    (push_repo / ".github/ci/run_checks.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    blocked = _git(push_repo, "push", "origin", "main", check=False)
    assert blocked.returncode != 0, blocked.stdout + blocked.stderr
    assert _git(push_repo, "ls-remote", "origin", "refs/heads/main").stdout.split()[0] == accepted
    _git(push_repo, "-c", "opensre.prePushOverride=incident recovery", "push", "origin", "main")
    log = push_repo / ".git" / "pre-push-overrides.jsonl"
    assert "incident recovery" in log.read_text(encoding="utf-8")
    assert (push_repo / "bad.py").read_text(encoding="utf-8") == "value = 1\n"
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
    (push_repo / "bad.py").write_text("value = missing_name\n", encoding="utf-8")
    _git(push_repo, "add", "bad.py")
    _git(push_repo, "commit", "-m", "broken branch")
    _git(push_repo, "checkout", "main")
    blocked = _git(push_repo, "push", "origin", "broken", check=False)
    assert blocked.returncode != 0, blocked.stdout + blocked.stderr
    assert not _git(push_repo, "ls-remote", "origin", "refs/heads/broken").stdout


@pytest.mark.parametrize(
    ("remote_ref", "remote_sha", "expected_readiness"),
    (
        ("refs/heads/feature", "0" * 40, True),
        ("refs/heads/feature", "1" * 40, False),
        ("refs/tags/v1", "0" * 40, False),
    ),
)
def test_only_new_remote_branches_run_pr_readiness(
    push_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_ref: str,
    remote_sha: str,
    expected_readiness: bool,
) -> None:
    import pre_push

    commit = _git(push_repo, "rev-parse", "HEAD").stdout.strip()
    readiness: list[bool] = []

    def _record_validation(
        _root: Path, _commit: str, _base: str | None, *, pr_readiness: bool
    ) -> int:
        readiness.append(pr_readiness)
        return 0

    monkeypatch.setattr(pre_push, "_validate", _record_validation)
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"HEAD {commit} {remote_ref} {remote_sha}\n"))
    monkeypatch.chdir(push_repo)

    assert pre_push.main(["origin"]) == 0
    assert readiness == [expected_readiness]


@pytest.mark.parametrize("base_available", (True, False))
def test_pr_readiness_uses_scoped_checks_only_with_a_remote_base(
    push_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base_available: bool,
) -> None:
    from pre_push import _validate

    base_commit = _git(push_repo, "rev-parse", "HEAD").stdout.strip()
    source = push_repo / "bad.py"
    source.write_text("value = 2\n", encoding="utf-8")
    _git(push_repo, "commit", "-am", "runtime change")
    witness = tmp_path / "readiness-args"
    runner = push_repo / ".github" / "ci" / "run_checks.py"
    runner.write_text(
        "from pathlib import Path\n"
        "import json\n"
        "import os\n"
        "import sys\n\n"
        "Path(os.environ['OPENSRE_READINESS_WITNESS']).write_text(\n"
        "    json.dumps({'args': sys.argv[1:], 'executable': sys.executable}),\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )
    _git(push_repo, "add", ".github/ci/run_checks.py")
    _git(push_repo, "commit", "-m", "record readiness invocation")
    head = _git(push_repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setenv("OPENSRE_READINESS_WITNESS", str(witness))

    base = base_commit if base_available else None
    assert _validate(push_repo, head, base, pr_readiness=True) == 0
    invocation = json.loads(witness.read_text(encoding="utf-8"))
    expected_args = ["--scope", "--head", head, "--base", base_commit] if base else []
    assert invocation["args"] == expected_args
    assert ".venv" in Path(invocation["executable"]).parts


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


def test_quick_checks_only_lint_existing_changed_python_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import run_checks as runner

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Gate test")
    _git(tmp_path, "config", "user.email", "gate@example.invalid")
    (tmp_path / "deleted.py").touch()
    (tmp_path / "unchanged.py").touch()
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    (tmp_path / "deleted.py").unlink()
    (tmp_path / " changed.py").touch()
    (tmp_path / "README.md").touch()
    monkeypatch.chdir(tmp_path)
    assert runner.main(["--quick", "--base", base, "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "ruff check -- ' changed.py'" in output
    assert "ruff format --check -- ' changed.py'" in output
    assert "pytest" not in output
    assert "mypy" not in output
    assert "deleted.py" not in output
    assert "unchanged.py" not in output
    (tmp_path / " changed.py").unlink()
    assert runner.main(["--quick", "--base", base, "--dry-run"]) == 0
    assert "ruff" not in capsys.readouterr().out


def test_snapshot_runs_real_ruff_without_installing_dependencies(tmp_path: Path) -> None:
    from pre_push import _validate

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Gate test")
    _git(tmp_path, "config", "user.email", "gate@example.invalid")
    source = tmp_path / "example.py"
    source.write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    source.write_text("value = missing_name\n", encoding="utf-8")
    _git(tmp_path, "commit", "-am", "lint failure")
    head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    source.write_text("value = 1\n", encoding="utf-8")
    # No pyproject or lockfile: the gate must use installed Ruff, not uv sync.
    assert _validate(tmp_path, head, base) == 1
    _git(tmp_path, "commit", "-am", "fix")
    fixed = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert _validate(tmp_path, fixed, head) == 0
    assert len(_git(tmp_path, "worktree", "list", "--porcelain").stdout.split("worktree ")) == 2


def test_snapshot_diff_failure_blocks_with_recovery_advice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from pre_push import _validate

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Gate test")
    _git(tmp_path, "config", "user.email", "gate@example.invalid")
    _git(tmp_path, "commit", "--allow-empty", "-m", "base")
    head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert _validate(tmp_path, head, "refs/heads/missing") == 1
    assert "Fetch the remote base" in capsys.readouterr().err
    assert len(_git(tmp_path, "worktree", "list", "--porcelain").stdout.split("worktree ")) == 2
