"""Install a worktree-local push gate without overwriting existing hooks."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from git_changes import git


def install(root: Path) -> Path:
    """Preserve active hooks and enable validation only for this checkout."""
    active = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks")).resolve()
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir"))
    managed = (git_dir / "opensre-hooks").resolve()
    managed.mkdir(exist_ok=True)
    # Worktree settings keep installing in one checkout from breaking another branch.
    git(root, "config", "--local", "extensions.worktreeConfig", "true")
    if active != managed:
        previous = active
        active_push = active / "pre-push"
        if active_push.is_file() and "# OpenSRE managed pre-push" in active_push.read_text(
            encoding="utf-8", errors="replace"
        ):
            # Linked checkouts inherit worktree configuration. Do not chain our
            # inherited wrapper back into the same pre-push program.
            try:
                previous = Path(git(root, "config", "--get", "opensre.previousHooksPath"))
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(
                    "Inherited managed hook has no original hook path; repair Git hook configuration."
                ) from exc
        git(root, "config", "--worktree", "opensre.previousHooksPath", str(previous))
        if active.is_dir():
            for hook in active.iterdir():
                if hook.name == "pre-push" or not hook.is_file() or not os.access(hook, os.X_OK):
                    continue
                destination = managed / hook.name
                destination.write_text(
                    f'#!/bin/sh\nexec {shlex.quote(str(hook))} "$@"\n',
                    encoding="utf-8",
                )
                destination.chmod(0o755)
    launcher = managed / "pre-push"
    launcher.write_text(
        "#!/bin/sh\n# OpenSRE managed pre-push\nset -eu\n"
        "root=$(git rev-parse --show-toplevel)\n"
        'if [ ! -f "$root/.github/ci/pre_push.py" ]; then\n'
        '  echo "Push blocked: this checkout lacks .github/ci/pre_push.py. Restore the validation tooling." >&2\n'
        "  exit 1\nfi\n"
        'exec uv run --no-sync --project "$root" python "$root/.github/ci/pre_push.py" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    git(root, "config", "--worktree", "core.hooksPath", str(managed))
    return managed


if __name__ == "__main__":
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    print(f"Installed push validation in {install(root)}")
