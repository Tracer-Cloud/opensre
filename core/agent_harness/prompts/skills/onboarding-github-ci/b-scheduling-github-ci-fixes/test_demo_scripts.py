"""Real local Git demo retries and evidence ownership, with no network access."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPTS = Path(__file__).with_name("scripts")
_REPO = "tester/opensre-ci-repair-demo-fixture"


@pytest.fixture
def demo_modules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[ModuleType, ModuleType, ModuleType]]:
    monkeypatch.syspath_prepend(str(_SCRIPTS))
    state = importlib.import_module("_demo_state")
    seed = importlib.import_module("seed_demo_repository")
    evidence = importlib.import_module("write_demo_evidence")
    monkeypatch.setattr(state, "results_directory", lambda: tmp_path / "results")
    monkeypatch.setattr(evidence, "results_directory", lambda: tmp_path / "results")
    yield state, seed, evidence
    receipt = state.read_receipt(_REPO)
    if receipt:
        workspace = Path(receipt["workspace"])
        if workspace.exists():
            shutil.rmtree(workspace)


def _git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_seed_retries_partial_push_without_rewriting_remote_history(
    demo_modules: tuple[ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state, seed, _ = demo_modules
    bare = tmp_path / "remote.git"
    initial = tmp_path / "initial"
    initial.mkdir()
    _git(initial, "init", "-b", "main")
    (initial / "README.md").write_text("Demo\n")
    _git(initial, "add", "README.md")
    _git(
        initial,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "Initial",
    )
    _git(tmp_path, "clone", "--bare", str(initial), str(bare))
    real_git = seed._git
    fail_push = True

    def local_git(checkout: Path, *args: str) -> str:
        nonlocal fail_push
        if args[0] == "clone":
            result = real_git(checkout, "clone", str(bare), args[-1])
            real_git(
                Path(args[-1]), "remote", "set-url", "origin", f"https://github.com/{_REPO}.git"
            )
            return str(result)
        if args[0] in {"push", "ls-remote"}:
            if fail_push and args[0] == "push" and args[-1].endswith("refs/heads/demo/failing-ci"):
                fail_push = False
                raise RuntimeError("simulated connection failure")
            args = tuple(str(bare) if arg == "origin" else arg for arg in args)
        return str(real_git(checkout, *args))

    monkeypatch.setattr(seed, "_git", local_git)
    with pytest.raises(RuntimeError, match="connection failure"):
        seed.seed_demo_repository(_REPO)
    receipt = state.read_receipt(_REPO)
    assert receipt["stage"] == "push_failing_branch"
    assert _git(bare, "rev-parse", "main") == receipt["seed_sha"]
    result = seed.seed_demo_repository(_REPO)
    assert result["head_sha"] == receipt["head_sha"]
    assert _git(bare, "rev-parse", "demo/failing-ci") == receipt["head_sha"]
    assert seed.seed_demo_repository(_REPO)["head_sha"] == receipt["head_sha"]
    assert _git(bare, "diff", "--name-only", "main", "demo/failing-ci") == "calculator.py"
    checkout = Path(result["workspace"]) / "checkout"
    for branch, expected in (("main", 0), ("demo/failing-ci", 1)):
        _git(checkout, "checkout", branch)
        tested = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "-v"], cwd=checkout, capture_output=True
        )
        assert tested.returncode == expected


def _owned_state(state: ModuleType) -> dict[str, Any]:
    workspace = Path(tempfile.mkdtemp(prefix="opensre-ci-repair-demo-"))
    (workspace / ".opensre-demo.json").write_text(json.dumps({"repo": _REPO}))
    receipt = {"repo": _REPO, "workspace": str(workspace), "stage": "ready"}
    state.save_receipt(receipt)
    return receipt


def test_evidence_failure_preserves_checkout_and_failed_demo_ids_can_be_absent(
    demo_modules: tuple[ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _, evidence = demo_modules
    receipt = _owned_state(state)
    write = evidence.atomic_write

    def disk_full(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(evidence, "atomic_write", disk_full)
    with pytest.raises(OSError, match="disk full"):
        evidence.write_demo_evidence(_REPO, 1, "loop", "failed", blocker="No repair commit")
    assert Path(receipt["workspace"]).exists()
    monkeypatch.setattr(evidence, "atomic_write", write)
    result = evidence.write_demo_evidence(_REPO, 1, "loop", "failed", blocker="No repair commit")
    assert not Path(receipt["workspace"]).exists()
    text = Path(result["evidence"]).read_text()
    assert "Outcome: failed" in text and "Fix commit: none" in text
    assert "Checkout cleanup: complete" in text


def test_evidence_refuses_unowned_checkout(
    demo_modules: tuple[ModuleType, ModuleType, ModuleType], tmp_path: Path
) -> None:
    state, _, evidence = demo_modules
    valuable = tmp_path / "valuable"
    valuable.mkdir()
    sentinel = valuable / "keep.txt"
    sentinel.write_text("keep")
    state.save_receipt({"repo": _REPO, "workspace": str(valuable)})
    with pytest.raises(ValueError, match="outside the owned"):
        evidence.write_demo_evidence(_REPO, 1, "loop", "failed")
    assert sentinel.read_text() == "keep"
    state.receipt_path(_REPO).unlink()


def test_final_evidence_update_failure_reports_actual_cleanup(
    demo_modules: tuple[ModuleType, ModuleType, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _, evidence = demo_modules
    receipt = _owned_state(state)
    write = evidence.atomic_write

    def fail_final_update(path: Path, text: str) -> None:
        if "cleanup: complete" in text:
            raise OSError("disk full")
        write(path, text)

    monkeypatch.setattr(evidence, "atomic_write", fail_final_update)
    result = evidence.write_demo_evidence(_REPO, 1, "loop", "failed")
    assert result["ok"] is False and result["checkout_removed"] is True
    assert not Path(receipt["workspace"]).exists()
    assert Path(result["evidence"]).is_file()


def test_a_killed_helper_releases_the_demo_lock(
    demo_modules: tuple[ModuleType, ModuleType, ModuleType], tmp_path: Path
) -> None:
    state, _, _ = demo_modules
    holder_script = (
        "import sys, importlib; sys.path.insert(0, sys.argv[1]); "
        "state = importlib.import_module('_demo_state'); "
        "from pathlib import Path; state.results_directory = lambda: Path(sys.argv[3]); "
        "import time\n"
        "with state.demo_lock(sys.argv[2]):\n"
        "    print('held', flush=True)\n"
        "    time.sleep(60)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, str(_SCRIPTS), _REPO, str(tmp_path / "results")],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        with pytest.raises(ValueError, match="holds the demo lock"), state.demo_lock(_REPO):
            pass
    finally:
        holder.kill()
        holder.wait(timeout=10)

    # Death, not a graceful exit, freed the lock: no stale marker survives the holder.
    with state.demo_lock(_REPO):
        pass
