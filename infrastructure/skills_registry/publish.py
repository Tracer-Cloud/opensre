"""Plan an ``opensre skills push``: which skill packages change, and is the result valid.

A package is a skill directory (its ``SKILL.md``, ``references/``, ``scripts/``
and report template) or a flat top-level card. A package is pushed only when
its local ``metadata.version`` is newer than the live one, so a stale checkout
can never overwrite a teammate's newer edit, and the git sync can run on every
merge. Shared files (``common/``) change only with ``--include-shared``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from config.constants.skills import SKILL_FILENAME, SKILLS_API_VERSION
from core.agent_harness.spi.skill_releases import (
    SkillCardError,
    SkillSource,
    SkillsRelease,
    build_snapshot,
    is_release_path,
    parse_frontmatter,
    read_skill_catalog,
    skills_dir,
)


class PackageStatus(StrEnum):
    NEW = "new"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    STALE = "stale"
    DELETE = "delete"


@dataclass(frozen=True)
class PackageChange:
    """What a push does with one skill package."""

    name: str
    path: str
    status: PackageStatus
    local_version: str = ""
    live_version: str = ""


@dataclass(frozen=True)
class PushPlan:
    """The file changes to publish on top of ``base_seq`` and the resulting catalog."""

    base_seq: int | None
    changes: tuple[PackageChange, ...]
    upserts: dict[str, str] = field(default_factory=dict)
    deletes: tuple[str, ...] = ()
    merged: dict[str, str] = field(default_factory=dict)
    summary: tuple[dict[str, str], ...] = ()
    #: Helper tool names the merged catalog declares (must not shadow real tools).
    script_tools: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.upserts and not self.deletes

    def request_body(self, *, force: bool, source_ref: str) -> dict[str, Any]:
        """The ``POST /api/skills/release`` body for this plan."""
        return {
            "base_seq": self.base_seq,
            "skill_api": SKILLS_API_VERSION,
            "upserts": self.upserts,
            "deletes": list(self.deletes),
            "skills": list(self.summary),
            "force": force,
            "source_ref": source_ref[:200],
        }


class PushError(ValueError):
    """The local catalog cannot be pushed as asked."""


def collect_release_files(root: Path) -> dict[str, str]:
    """Return every shippable catalog file under ``root`` keyed by relative path."""
    collected: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if is_release_path(relative):
            collected[relative] = path.read_text(encoding="utf-8")
    return collected


def _package_roots(paths: Iterable[str]) -> set[str]:
    """Directories holding a card, plus flat top-level cards (keyed by their own path)."""
    roots: set[str] = set()
    for path in paths:
        pure = PurePosixPath(path)
        parent = pure.parent.as_posix()
        if pure.name == SKILL_FILENAME or (parent != "." and pure.stem == pure.parent.name):
            roots.add(parent)
        elif parent == "." and pure.suffix == ".md":
            roots.add(path)
    return roots


def _owner(path: str, roots: set[str]) -> str | None:
    if path in roots:
        return path
    for parent in PurePosixPath(path).parents:
        key = parent.as_posix()
        if key != "." and key in roots:
            return key
    return None


def _packages(files: Mapping[str, str]) -> dict[str, dict[str, str]]:
    roots = _package_roots(files)
    grouped: dict[str, dict[str, str]] = {}
    for path, text in files.items():
        owner = _owner(path, roots)
        grouped.setdefault(owner or "", {})[path] = text
    return grouped


def _card_path(package: str, package_files: Mapping[str, str]) -> str | None:
    if package in package_files:
        return package
    for candidate in (f"{package}/{SKILL_FILENAME}", f"{package}/{PurePosixPath(package).name}.md"):
        if candidate in package_files:
            return candidate
    return None


def _card_identity(package: str, package_files: Mapping[str, str]) -> tuple[str, str]:
    """Return ``(name, version)`` from the package's card, or the path and ``""``."""
    card = _card_path(package, package_files)
    if card is None:
        return package, ""
    try:
        frontmatter, _body = parse_frontmatter(package_files[card])
    except SkillCardError:
        return package, ""
    name = frontmatter.get("name")
    metadata = frontmatter.get("metadata")
    version = metadata.get("version") if isinstance(metadata, dict) else None
    return (name if isinstance(name, str) else package), (str(version) if version else "")


def select_packages(files: Mapping[str, str], names: Iterable[str]) -> dict[str, str]:
    """Return the files of the named skill packages (by skill name or directory)."""
    wanted = {name.strip() for name in names if name.strip()}
    selected: dict[str, str] = {}
    found: set[str] = set()
    for package, package_files in _packages(files).items():
        if not package:
            continue
        name = _card_identity(package, package_files)[0]
        if name in wanted or package in wanted:
            selected.update(package_files)
            found.update({name, package})
    missing = wanted - found
    if missing:
        raise PushError(f"no skill package named {', '.join(sorted(missing))}")
    return selected


def _version_key(version: str) -> tuple[int, ...]:
    parts = []
    for part in version.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def plan_push(
    local_files: Mapping[str, str],
    live: SkillsRelease | None,
    *,
    names: Iterable[str] = (),
    delete: Iterable[str] = (),
    include_shared: bool = False,
    force: bool = False,
) -> PushPlan:
    """Compare a local catalog with the live release and decide what to publish."""
    live_files: Mapping[str, str] = live.files if live is not None else {}
    local_packages = _packages(local_files)
    live_packages = _packages(live_files)
    wanted = {name.strip() for name in names if name.strip()}
    doomed = {name.strip() for name in delete if name.strip()}
    upserts: dict[str, str] = {}
    deletes: set[str] = set()
    changes: list[PackageChange] = []

    for package in sorted(set(local_packages) | set(live_packages)):
        if package == "":
            continue
        local = local_packages.get(package, {})
        remote = live_packages.get(package, {})
        name, local_version = _card_identity(package, local) if local else ("", "")
        live_name, live_version = _card_identity(package, remote) if remote else ("", "")
        name = name or live_name
        if wanted and name not in wanted and package not in wanted:
            continue
        if name in doomed or package in doomed:
            if remote:
                deletes.update(remote)
                changes.append(PackageChange(name, package, PackageStatus.DELETE, "", live_version))
            continue
        if not local or local == remote:
            if local:
                changes.append(
                    PackageChange(
                        name, package, PackageStatus.UNCHANGED, local_version, live_version
                    )
                )
            continue
        newer = not remote or _version_key(local_version) > _version_key(live_version)
        if not newer and not force:
            changes.append(
                PackageChange(name, package, PackageStatus.STALE, local_version, live_version)
            )
            continue
        upserts.update({path: text for path, text in local.items() if remote.get(path) != text})
        deletes.update(path for path in remote if path not in local)
        status = PackageStatus.UPDATE if remote else PackageStatus.NEW
        changes.append(PackageChange(name, package, status, local_version, live_version))

    unknown = (
        (wanted | doomed)
        - {change.name for change in changes}
        - {change.path for change in changes}
    )
    if unknown:
        raise PushError(f"no skill package named {', '.join(sorted(unknown))}")

    shared_local = local_packages.get("", {})
    shared_live = live_packages.get("", {})
    if include_shared or live is None:
        upserts.update({p: t for p, t in shared_local.items() if shared_live.get(p) != t})

    merged = {path: text for path, text in live_files.items() if path not in deletes}
    merged.update(upserts)
    plan = PushPlan(
        base_seq=live.seq if live is not None else None,
        changes=tuple(changes),
        upserts=upserts,
        deletes=tuple(sorted(deletes)),
        merged=merged,
    )
    return plan if plan.is_empty else _validated(plan, live_files)


def _validated(plan: PushPlan, live_files: Mapping[str, str]) -> PushPlan:
    """Build the merged catalog exactly as clients will, and refuse it if any card fails."""
    with tempfile.TemporaryDirectory(prefix="opensre-skills-push-") as temp:
        root = Path(temp)
        for relative, text in plan.merged.items():
            target = root.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        snapshot = build_snapshot(
            root, source=SkillSource.OVERRIDE, release="candidate", strict=True
        )
        if snapshot.diagnostics:
            raise PushError(
                "the pushed catalog has invalid cards:\n" + "\n".join(snapshot.diagnostics)
            )
        names = {skill.name for skill in snapshot.skills}
        required = _required_names(live_files, plan)
        missing = required - names
        if missing:
            raise PushError(
                "the pushed catalog drops skills that released binaries rely on: "
                + ", ".join(sorted(missing))
            )
        summary = tuple(
            {
                "name": skill.name,
                "version": skill.version,
                "digest": snapshot.digests[skill.name][:12],
                "path": _relative(skill.path, root),
            }
            for skill in snapshot.skills
        )
        script_tools = tuple(
            sorted({tool.name for skill in snapshot.skills for tool in skill.script_tools})
        )
    return replace(plan, summary=summary, script_tools=script_tools)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _required_names(live_files: Mapping[str, str], plan: PushPlan) -> set[str]:
    """Skills bundled in this binary (binaries reject a release without them), plus
    live skills not explicitly deleted."""
    bundled = {skill.name for skill in read_skill_catalog(skills_dir()).skills}
    deleted = {change.name for change in plan.changes if change.status is PackageStatus.DELETE}
    live = {
        _card_identity(package, package_files)[0]
        for package, package_files in _packages(live_files).items()
        if package
    }
    return bundled | (live - deleted)


__all__ = [
    "PackageChange",
    "PackageStatus",
    "PushError",
    "PushPlan",
    "collect_release_files",
    "plan_push",
    "select_packages",
]
