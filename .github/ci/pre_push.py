"""Validate each pushed commit in isolation, preserving any pre-existing push hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from git_changes import default_base, git


def _setting(root: Path, name: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "config", "--get", name],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _previous_hook(root: Path, args: list[str], updates: str) -> int:
    previous = _setting(root, "opensre.previousHooksPath")
    if previous:
        hook = Path(previous) / "pre-push"
        if hook.is_file() and os.access(hook, os.X_OK):
            return subprocess.run(
                [str(hook), *args],
                input=updates,
                text=True,
                cwd=root,
                check=False,
            ).returncode
    return 0


def _record_override(root: Path, reason: str, updates: str) -> None:
    record = (
        json.dumps(
            {
                "at": datetime.now(UTC).isoformat(),
                "reason": reason,
                "updates": updates.splitlines(),
            }
        )
        + "\n"
    )
    log = Path(git(root, "rev-parse", "--absolute-git-dir")) / "pre-push-overrides.jsonl"
    with log.open("a", encoding="utf-8") as stream:
        stream.write(record)
    print(
        f"OVERRIDE: local validation bypassed; reason and revisions recorded in {log}.", flush=True
    )


def _validate(root: Path, commit: str, base: str | None) -> int:
    # Use a real Git worktree: tests may inspect Git state or use editable entrypoints.
    with tempfile.TemporaryDirectory(prefix="opensre-pre-push-") as directory:
        snapshot = Path(directory) / "checkout"
        git(root, "worktree", "add", "--detach", "--quiet", str(snapshot), commit)
        try:
            local_variables = set(git(root, "rev-parse", "--local-env-vars").splitlines())
            local_variables.update({"UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "PYTHONPATH"})
            environment = {
                key: value for key, value in os.environ.items() if key not in local_variables
            }
            print(
                f"Preparing locked dependencies for {commit[:12]} (outside check timing).",
                flush=True,
            )
            setup = subprocess.run(
                ["uv", "sync", "--frozen", "--extra", "dev", "--quiet"],
                cwd=snapshot,
                env=environment,
                check=False,
            )
            if setup.returncode:
                return setup.returncode
            runner = snapshot / ".github" / "ci" / "run_checks.py"
            if not runner.is_file():
                # Also validate branches created before this gate was introduced.
                runner = Path(__file__).with_name("run_checks.py")
            command = ["uv", "run", "--no-sync", "python", str(runner), "--scope", "--head", commit]
            if base:
                command.extend(["--base", base])
            return subprocess.run(command, cwd=snapshot, env=environment, check=False).returncode
        finally:
            git(root, "worktree", "remove", "--force", str(snapshot))


def main(args: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if args is None else args)
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    updates = sys.stdin.read()
    previous_result = _previous_hook(root, arguments, updates)
    if previous_result:
        return previous_result
    override = _setting(root, "opensre.prePushOverride")
    if override:
        _record_override(root, override, updates)
        return 0
    failures = False
    validated: set[tuple[str, str | None]] = set()
    remote = arguments[0] if arguments else "origin"
    for line in updates.splitlines():
        fields = line.split()
        if len(fields) != 4:
            print("Push blocked: malformed Git pre-push input.", file=sys.stderr)
            return 1
        local_ref, local_sha, remote_ref, remote_sha = fields
        if set(local_sha) == {"0"}:
            continue  # A deletion introduces no code to validate.
        try:
            commit = git(root, "rev-parse", "--verify", f"{local_sha}^{{commit}}")
        except subprocess.CalledProcessError:
            print(f"Push blocked: {local_ref} does not identify a commit.", file=sys.stderr)
            return 1
        base = default_base(root, remote)
        if remote_ref == "refs/heads/main" and set(remote_sha) != {"0"}:
            base = remote_sha
        validation = (commit, base)
        if validation in validated:
            continue
        validated.add(validation)
        print(f"Validating {local_ref} at {commit[:12]}.", flush=True)
        failures = bool(_validate(root, commit, base)) or failures
    if failures:
        print(
            "Push blocked. Fix the reported checks and commit the changes before retrying.",
            file=sys.stderr,
        )
    return int(failures)


if __name__ == "__main__":
    raise SystemExit(main())
