"""Owned demo workspaces, atomic progress records, and the helper JSON protocol."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, Any


def results_directory() -> Path:
    return Path.home() / ".opensre" / "demo-results"


def demo_key(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]+/opensre-ci-repair-demo-[A-Za-z0-9_-]+", repo):
        raise ValueError("Expected owner/opensre-ci-repair-demo-<id>.")
    return repo.replace("/", "--")


def receipt_path(repo: str) -> Path:
    return results_directory() / f"{demo_key(repo)}.json"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_receipt(repo: str) -> dict[str, Any]:
    path = receipt_path(repo)
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("repo") != repo:
        raise ValueError("Demo progress record does not match the repository.")
    return state


def save_receipt(state: dict[str, Any]) -> None:
    atomic_write(receipt_path(str(state["repo"])), json.dumps(state, indent=2) + "\n")


def owned_workspace(state: dict[str, Any]) -> Path:
    workspace = Path(str(state["workspace"]))
    if (
        workspace.is_symlink()
        or workspace.resolve().parent != Path(tempfile.gettempdir()).resolve()
        or not workspace.name.startswith("opensre-ci-repair-demo-")
    ):
        raise ValueError("Workspace is outside the owned demo temp directory.")
    marker = workspace / ".opensre-demo.json"
    if marker.is_symlink() or json.loads(marker.read_text(encoding="utf-8")) != {
        "repo": state["repo"]
    }:
        raise ValueError("Workspace ownership marker does not match the demo.")
    return workspace


if sys.platform == "win32":
    import msvcrt

    def _lock_exclusive(handle: IO[str]) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

else:
    import fcntl

    def _lock_exclusive(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def demo_lock(repo: str) -> Iterator[None]:
    """Hold the per-demo lock for the helper's lifetime.

    The lock is an OS file lock, so a helper that is killed mid-run releases it
    with its file descriptor instead of leaving a stale marker behind.
    """
    path = receipt_path(repo).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        try:
            _lock_exclusive(handle)
        except OSError as exc:
            raise ValueError(
                "An attempt holds the demo lock; inspect its saved progress before retrying."
            ) from exc
        yield


def run_json(operation: Callable[..., dict[str, Any]]) -> None:
    """Read one JSON argument and emit exactly one JSON result, including failures."""
    repo = ""
    try:
        arguments = json.loads(sys.argv[1])
        if not isinstance(arguments, dict):
            raise ValueError("Expected a JSON object.")
        repo = str(arguments.get("repo", ""))
        with demo_lock(repo):
            result = operation(**arguments)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
        with suppress(OSError, ValueError):
            result.update(read_receipt(repo))
    print(json.dumps(result))
    raise SystemExit(0 if result.get("ok") else 1)
