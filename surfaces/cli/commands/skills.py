"""Publish and inspect live skills releases."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterable
from http import HTTPStatus
from pathlib import Path

import click

from config.account import (
    is_secure_account_origin,
    load_account_record,
    normalize_account_app_url,
    resolve_account_token,
)
from config.skills_auto_update import skills_auto_update_enabled
from core.agent_harness.spi.skill_releases import (
    ReleaseError,
    SkillsRelease,
    active_skill_catalog,
    is_release_path,
    latest_stored_seq,
    read_state,
    skills_dir,
    trusted_release_keys,
    verify_release,
)
from infrastructure.skills_registry import (
    PackageStatus,
    PullStatus,
    PushError,
    SkillsApiError,
    SkillsAuth,
    collect_release_files,
    fetch_release,
    list_releases,
    plan_push,
    publish_release,
    pull_once,
    rollback_release,
    select_packages,
    store_verified_release,
)
from infrastructure.terminal.theme import GLYPH_SUCCESS

_REPO_SKILLS_DIR = Path("core/agent_harness/prompts/skills")


def _default_source_dir() -> Path:
    """The repo's skills tree when run from a checkout, else the bundled tree."""
    local = Path.cwd() / _REPO_SKILLS_DIR
    return local if local.is_dir() else skills_dir()


def _app_url() -> str:
    record = load_account_record()
    try:
        return normalize_account_app_url(record.app_url if record is not None else None)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _auth(app_url: str, runner_token_env: str) -> SkillsAuth:
    # Either credential only ever travels over https (or plain http to this machine).
    if not is_secure_account_origin(app_url):
        raise click.ClickException(f"Refusing to send credentials to {app_url}.")
    if runner_token_env:
        token = os.getenv(runner_token_env, "").strip()
        if not token:
            raise click.ClickException(f"{runner_token_env} is empty.")
        return SkillsAuth(runner_token=token)
    token = resolve_account_token()
    if not token:
        raise click.ClickException("Publishing skills needs `opensre account login` first.")
    return SkillsAuth(account_token=token)


def _source_ref(source: Path) -> str:
    try:
        sha = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return f"git:{sha}" if sha else "local"


def _tool_names() -> set[str]:
    try:
        from tools.registry import get_tool_descriptors

        return {descriptor.name for descriptor in get_tool_descriptors()}
    except Exception:
        return set()


def _store_locally(release: SkillsRelease) -> None:
    try:
        store_verified_release(release)
    except (ReleaseError, OSError) as exc:
        click.echo(f"  Release #{release.seq} is live; not cached on this machine: {exc}")
        return
    if not skills_auto_update_enabled():
        click.echo(
            "  Auto-update is off in this process (source checkout); set "
            "OPENSRE_SKILLS_AUTO_UPDATE=1 to run published releases locally."
        )


@click.group(name="skills")
def skills_command() -> None:
    """Publish skills to every OpenSRE install and inspect the active release."""


@skills_command.command(name="status")
def skills_status() -> None:
    """Show which skills catalog this machine runs and the latest published release."""
    snapshot = active_skill_catalog().current()
    state = read_state()
    click.echo(
        f"Active:      {snapshot.release} ({snapshot.source}, {len(snapshot.skills)} skills)"
    )
    click.echo(f"Auto-update: {'on' if skills_auto_update_enabled() else 'off'}")
    stored = latest_stored_seq()
    click.echo(f"Cached:      {f'#{stored}' if stored else 'none'}")
    checked = state.get("checked_at")
    if isinstance(checked, int | float):
        click.echo(f"Last check:  {int(time.time() - checked)}s ago")
    if state.get("rejected_seq"):
        click.echo(f"Rejected:    #{state['rejected_seq']} (signature or compatibility)")
    for diagnostic in snapshot.diagnostics:
        click.echo(f"  ! {diagnostic}")
    try:
        latest = fetch_release(_app_url())
    except SkillsApiError as exc:
        click.echo(f"Published:   unknown ({exc})")
        return
    seq = latest.release.seq if latest.release is not None else None
    click.echo(f"Published:   {f'#{seq}' if seq else 'none yet'}")


@skills_command.command(name="update")
def skills_update() -> None:
    """Pull the latest release now; it applies at the next turn of every local session."""
    outcome = pull_once(force=True)
    if outcome.status in (PullStatus.FAILED, PullStatus.REJECTED):
        raise click.ClickException(f"Pull {outcome.status}: {outcome.detail}")
    seq = f" #{outcome.seq}" if outcome.seq else ""
    click.echo(f"{GLYPH_SUCCESS} Skills release{seq}: {outcome.status}")


@skills_command.command(name="push")
@click.argument("names", nargs=-1)
@click.option(
    "--path",
    "source",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Skills directory to publish (default: the repo's skills tree).",
)
@click.option("--delete", "delete", multiple=True, help="Remove a skill from the live catalog.")
@click.option("--include-shared", is_flag=True, help="Also publish shared files (common/).")
@click.option("--force", is_flag=True, help="Publish even when the live version is not older.")
@click.option("--dry-run", is_flag=True, help="Show what would be published.")
@click.option(
    "--runner-token-env",
    default="",
    help="Env var holding a GitHub OIDC token (git sync); default: your account login.",
)
def skills_push(
    names: tuple[str, ...],
    source: Path | None,
    delete: tuple[str, ...],
    include_shared: bool,
    force: bool,
    dry_run: bool,
    runner_token_env: str,
) -> None:
    """Publish edited skills; every install picks them up within five minutes.

    Only skills whose metadata.version is newer than the live one are sent, so
    a stale checkout cannot overwrite a newer edit (use --force to override).
    """
    started = time.monotonic()
    root = source or _default_source_dir()
    app_url = _app_url()
    try:
        live = fetch_release(app_url).release
        plan = plan_push(
            collect_release_files(root),
            live,
            names=names,
            delete=delete,
            include_shared=include_shared,
            force=force,
        )
    except (SkillsApiError, PushError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    for change in plan.changes:
        if change.status is PackageStatus.UNCHANGED:
            continue
        versions = f"{change.live_version or '-'} -> {change.local_version or '-'}"
        hint = ""
        if change.status is PackageStatus.STALE:
            hint = "  (not newer than live: bump metadata.version, or --force)"
        click.echo(f"  {change.status:<7} {change.name:<40} {versions}{hint}")
    if plan.is_empty:
        click.echo("Nothing to publish.")
        return
    collisions = set(plan.script_tools) & _tool_names()
    if collisions:
        raise click.ClickException(f"Helper tool names collide with tools: {sorted(collisions)}")
    if dry_run:
        click.echo(f"Dry run: {len(plan.upserts)} files to write, {len(plan.deletes)} to delete.")
        return
    try:
        release, unchanged = publish_release(
            app_url,
            plan.request_body(force=force, source_ref=_source_ref(root)),
            _auth(app_url, runner_token_env),
        )
    except SkillsApiError as exc:
        if exc.status == HTTPStatus.CONFLICT:
            raise click.ClickException(
                f"{exc}. Someone published these skills since you fetched; run "
                "`opensre skills pull <name>` and retry."
            ) from exc
        raise click.ClickException(str(exc)) from exc
    elapsed = time.monotonic() - started
    if unchanged:
        click.echo(f"Live release #{release.seq} already has this content ({elapsed:.1f}s).")
        return
    click.echo(f"{GLYPH_SUCCESS} Published skills release #{release.seq} in {elapsed:.1f}s.")
    _store_locally(release)


@skills_command.command(name="rollback")
@click.option(
    "--to", "to_seq", type=int, default=None, help="Release to restore (default: previous)."
)
def skills_rollback(to_seq: int | None) -> None:
    """Republish an earlier release as the newest one."""
    app_url = _app_url()
    try:
        release = rollback_release(app_url, _auth(app_url, ""), to_seq=to_seq)
    except SkillsApiError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"{GLYPH_SUCCESS} Rolled back: release #{release.seq} is live ({release.source_ref})."
    )
    _store_locally(release)


@skills_command.command(name="history")
@click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
def skills_history(limit: int) -> None:
    """List published releases, newest first."""
    app_url = _app_url()
    try:
        releases = list_releases(app_url, _auth(app_url, ""), limit=limit)
    except SkillsApiError as exc:
        raise click.ClickException(str(exc)) from exc
    for item in releases:
        click.echo(
            f"#{item.get('seq')}  {item.get('created_at', '')}  {item.get('source', '')}"
            f"  {item.get('source_ref', '')}  {item.get('created_by', '')}"
        )


@skills_command.command(name="pull")
@click.argument("names", nargs=-1, required=True)
@click.option(
    "--path",
    "target",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Skills directory to write into (default: the repo's skills tree).",
)
@click.option("--force", is_flag=True, help="Overwrite files with uncommitted local changes.")
def skills_pull(names: tuple[str, ...], target: Path | None, force: bool) -> None:
    """Copy the live version of skills into your checkout (to commit a fast-lane edit)."""
    root = (target or _default_source_dir()).resolve()
    try:
        live = fetch_release(_app_url()).release
    except SkillsApiError as exc:
        raise click.ClickException(str(exc)) from exc
    if live is None:
        raise click.ClickException("No skills release is published yet.")
    try:
        # The same trust as activation: only a signed release reaches the checkout.
        verify_release(live, trusted_release_keys())
        selected = select_packages(live.files, names)
    except (ReleaseError, PushError) as exc:
        raise click.ClickException(str(exc)) from exc
    targets = {relative: _contained(root, relative) for relative in selected}
    dirty = [] if force else _uncommitted(root, targets.values())
    if dirty:
        listed = ", ".join(str(path.relative_to(root)) for path in dirty)
        raise click.ClickException(
            f"Uncommitted changes would be overwritten: {listed}. Commit them, or pass --force."
        )
    for relative, path in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(selected[relative], encoding="utf-8")
    click.echo(f"{GLYPH_SUCCESS} Wrote {len(targets)} files from release #{live.seq} into {root}.")


def _contained(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root``; refuse any path that would land outside it."""
    path = root.joinpath(*relative.split("/")).resolve()
    if not is_release_path(relative) or root not in path.parents:
        raise click.ClickException(f"Release path {relative!r} is outside the skills directory.")
    return path


def _uncommitted(root: Path, paths: Iterable[Path]) -> list[Path]:
    """Return the existing files among ``paths`` that git reports as modified or untracked."""
    existing = [path for path in paths if path.exists()]
    if not existing:
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", *map(str, existing)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return existing
    if result.returncode != 0:
        # Not a git checkout: nothing tells committed from local work, so be safe.
        return existing
    changed = {line[3:].strip() for line in result.stdout.splitlines() if len(line) > 3}
    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout.strip()
    base = Path(top).resolve() if top else root
    return [path for path in existing if _repo_relative(path, base) in changed]


def _repo_relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()
