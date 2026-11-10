"""Offline status probes for the compact launch banner.

Counts only — no skill bodies, no vendor SDKs, no prompt_toolkit. The chips are
decorative startup chrome; loading the action-skill harness or full catalog
health graph here made first paint pay hundreds of milliseconds for two integers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from config.account import load_account_record
from config.constants.skills import SKILLS_DIR_ENV
from integrations.git import GitCommandError, origin_url
from integrations.github import count_ci_fixes
from surfaces.shared.llm_setup.model_choices import active_model


@dataclass(frozen=True)
class LaunchStatus:
    """Counts displayed beside the OpenSRE mark, and who is signed in where."""

    skill_count: int
    ci_fix_count: int
    #: The signed-in member (email, else user id); ``None`` before sign-in.
    account: str | None = None
    #: The model the next turn runs on: the hosted one, or a local provider's
    #: when the member selected one (that selection keeps the account identity).
    model: str | None = None
    #: ``owner/name`` of the repository the shell was opened in, from its origin remote.
    repository: str | None = None


# ``surfaces/shared/terminal/banner/banner_state.py`` → package root (opensre/).
_PACKAGE_ROOT = Path(__file__).resolve().parents[4]
_BUNDLED_SKILLS_DIR = _PACKAGE_ROOT / "core" / "agent_harness" / "prompts" / "skills"


_NOT_SKILLS = frozenset({"AGENTS.md", "README.md"})
_REPORT_SUFFIX = "_report.md"


def _has_card(directory: Path) -> bool:
    return (directory / "SKILL.md").is_file() or (directory / f"{directory.name}.md").is_file()


def _count_bundled_skill_files(directory: Path) -> int:
    """Count discoverable skill recipes without importing the skill loader.

    Mirrors ``catalog.discovery.iter_skill_paths``: package dirs, one level of
    nested child packages, then top-level ``*.md`` cards — without reading file
    bodies or importing harness package ``__init__`` graphs.
    """
    if not directory.is_dir():
        return 0
    count = 0
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        count += _has_card(child)
        count += sum(
            1
            for nested in child.iterdir()
            if nested.is_dir() and not nested.name.startswith(".") and _has_card(nested)
        )
    count += sum(
        1
        for path in directory.glob("*.md")
        if path.name not in _NOT_SKILLS and not path.name.endswith(_REPORT_SUFFIX)
    )
    return count


def _skills_dir() -> Path:
    """The catalog this process serves: ``OPENSRE_SKILLS_DIR`` when set, else the bundle."""
    override = os.getenv(SKILLS_DIR_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            return candidate
    return _BUNDLED_SKILLS_DIR


def _count_loaded_skills() -> int:
    """Return the number of action-agent skills (filesystem only)."""
    try:
        return _count_bundled_skill_files(_skills_dir())
    except Exception:
        return 0


def _signed_in_account() -> str | None:
    """The saved login's identity (local file only, never the webapp)."""
    try:
        record = load_account_record()
    except OSError:
        return None
    if record is None:
        return None
    return record.email or record.user_id


def _active_model_name() -> str | None:
    """The model that answers the next turn; not the account's saved model by itself.

    A local provider selected after sign-in keeps the account identity but
    routes elsewhere, so naming the saved hosted model would be wrong then.
    """
    try:
        _provider, model = active_model()
    except Exception:
        return None
    return model or None


def repository_slug(remote_url: str) -> str | None:
    """``owner/name`` from an origin URL in any git spelling; ``None`` when it has none."""
    path = remote_url.strip()
    # A local path remote has a directory, not an owner.
    if not path or path.startswith("file:"):
        return None
    if "://" in path:
        path = path.split("://", 1)[1].split("/", 1)[1] if "/" in path.split("://", 1)[1] else ""
    elif ":" in path and not path.startswith("/"):
        path = path.split(":", 1)[1]
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    name = parts[-1].removesuffix(".git")
    return f"{parts[-2]}/{name}" if name else None


def _current_repository() -> str | None:
    """The repository of the working directory, or ``None`` outside a git checkout."""
    try:
        return repository_slug(origin_url(os.getcwd()))
    except (GitCommandError, OSError):
        return None


def load_launch_status() -> LaunchStatus:
    """Load the startup-safe status summary without network calls."""
    account = _signed_in_account()
    return LaunchStatus(
        skill_count=_count_loaded_skills(),
        ci_fix_count=count_ci_fixes(),
        account=account,
        model=_active_model_name() if account else None,
        repository=_current_repository(),
    )


__all__ = ["LaunchStatus", "load_launch_status", "repository_slug"]
