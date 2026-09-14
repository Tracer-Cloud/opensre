"""Seed a disposable CI demo, preserving confirmed state before each push."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from _demo_state import demo_key, owned_workspace, read_receipt, run_json, save_receipt

_CALCULATOR = "def add(left: int, right: int) -> int:\n    return left + right\n"
_BROKEN_CALCULATOR = "def add(left: int, right: int) -> int:\n    return left - right\n"
_TEST = """import unittest

from calculator import add


class CalculatorTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
"""
_WORKFLOW = """name: Demo calculator CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python -m unittest -v
"""


def _git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[-2000:]}")
    return result.stdout.strip()


def _checkpoint(state: dict[str, Any], stage: str) -> None:
    state["stage"] = stage
    save_receipt(state)


def _commit(checkout: Path, message: str) -> str:
    _git(
        checkout,
        "-c",
        "user.name=OpenSRE Demo",
        "-c",
        "user.email=demo@opensre.local",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        message,
    )
    return _git(checkout, "rev-parse", "HEAD")


def _push(checkout: Path, commit: str, branch: str, expected: str | None) -> None:
    remote = _git(checkout, "ls-remote", "origin", f"refs/heads/{branch}")
    current = remote.split()[0] if remote else None
    if current == commit:
        return
    if current != expected:
        raise ValueError(f"Remote {branch} changed; inspect the demo before retrying.")
    _git(checkout, "push", "origin", f"{commit}:refs/heads/{branch}")


def seed_demo_repository(repo: str) -> dict[str, Any]:
    """Seed only demo-named repositories and resume from verified local/remote commits."""
    demo_key(repo)
    state = read_receipt(repo)
    if not state:
        workspace = Path(tempfile.mkdtemp(prefix="opensre-ci-repair-demo-"))
        (workspace / ".opensre-demo.json").write_text(json.dumps({"repo": repo}), encoding="utf-8")
        state = {"repo": repo, "workspace": str(workspace), "stage": "clone"}
        save_receipt(state)
    workspace = owned_workspace(state)
    checkout = workspace / "checkout"
    remote = f"https://github.com/{repo}.git"
    if not checkout.exists():
        _git(workspace, "clone", remote, str(checkout))
    if _git(checkout, "remote", "get-url", "origin") != remote:
        raise ValueError("Checkout remote does not match this demo.")
    if "base_sha" not in state:
        branches = _git(checkout, "ls-remote", "--heads", "origin").splitlines()
        if len(branches) != 1 or not branches[0].endswith("refs/heads/main"):
            raise ValueError("Expected a newly created repository with only main.")
        state["base_sha"] = branches[0].split()[0]
        _checkpoint(state, "seed")
    if "seed_sha" not in state:
        if _git(checkout, "rev-parse", "HEAD") != state["base_sha"] or _git(
            checkout, "status", "--porcelain"
        ):
            raise ValueError(
                "Unrecorded local changes; inspect the saved workspace before retrying."
            )
        _git(checkout, "checkout", "main")
        for name, content in {
            ".github/workflows/test.yml": _WORKFLOW,
            "calculator.py": _CALCULATOR,
            "test_calculator.py": _TEST,
            "AGENTS.md": "Run `python -m unittest -v`.\n",
        }.items():
            path = checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _git(
            checkout,
            "add",
            ".github/workflows/test.yml",
            "calculator.py",
            "test_calculator.py",
            "AGENTS.md",
        )
        state["seed_sha"] = _commit(checkout, "Seed demo calculator CI")
        _checkpoint(state, "push_main")
    _push(checkout, str(state["seed_sha"]), "main", str(state["base_sha"]))
    if "head_sha" not in state:
        _checkpoint(state, "create_failing_branch")
        if _git(checkout, "rev-parse", "HEAD") != state["seed_sha"] or _git(
            checkout, "status", "--porcelain"
        ):
            raise ValueError(
                "Unrecorded local changes; inspect the saved workspace before retrying."
            )
        _git(checkout, "checkout", "-B", "demo/failing-ci", str(state["seed_sha"]))
        (checkout / "calculator.py").write_text(_BROKEN_CALCULATOR, encoding="utf-8")
        _git(checkout, "add", "calculator.py")
        state["head_sha"] = _commit(checkout, "Introduce demo calculator regression")
        _checkpoint(state, "push_failing_branch")
    _push(checkout, str(state["head_sha"]), "demo/failing-ci", None)
    _checkpoint(state, "ready")
    return {"ok": True, **state, "summary": f"Prepared failing calculator CI in {repo}."}


if __name__ == "__main__":
    run_json(seed_demo_repository)
