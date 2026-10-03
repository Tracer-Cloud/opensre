"""``opensre skills push`` never overwrites a newer live edit or ships a broken catalog."""

from __future__ import annotations

import pytest

from infrastructure.skills_registry import PackageStatus, PushError, plan_push, select_packages
from tests.utils.skill_releases import ReleaseSigner, bundled_files, release_signer

__all__ = ["release_signer"]

_SKILL = "repair-github-ci"
_CARD = f"{_SKILL}/SKILL.md"


def _bump(text: str, version: str, note: str) -> str:
    """Return the card with a new ``metadata.version`` and an extra body line."""
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("version:"):
            indent = line[: len(line) - len(line.lstrip())]
            line = f'{indent}version: "{version}"'
        lines.append(line)
    return "\n".join(lines) + f"\n{note}\n"


def test_only_skills_with_a_newer_version_are_published(release_signer: ReleaseSigner) -> None:
    live_files = bundled_files()
    live = release_signer.sign(live_files, seq=4)
    local = dict(live_files)
    local[_CARD] = _bump(local[_CARD], "99.1", "Local edit.")
    stale = "delivering-morning-briefings/SKILL.md"
    local[stale] += "\nEdited without a version bump.\n"

    plan = plan_push(local, live)

    statuses = {change.name: change.status for change in plan.changes}
    assert statuses[_SKILL] is PackageStatus.UPDATE
    assert statuses["delivering-morning-briefings"] is PackageStatus.STALE
    assert plan.base_seq == 4
    assert set(plan.upserts) == {_CARD}
    assert plan.merged[_CARD].endswith("Local edit.\n")
    assert plan.merged[stale] == live_files[stale]
    assert {entry["name"] for entry in plan.summary} >= {_SKILL, "delivering-morning-briefings"}


def test_a_stale_checkout_does_not_publish_anything(release_signer: ReleaseSigner) -> None:
    live_files = bundled_files()
    live_files[_CARD] = _bump(live_files[_CARD], "99.2", "Teammate's newer edit.")
    live = release_signer.sign(live_files, seq=9)

    plan = plan_push(bundled_files(), live)

    assert plan.is_empty
    assert {c.name for c in plan.changes if c.status is PackageStatus.STALE} == {_SKILL}


def test_a_catalog_with_an_invalid_card_is_refused(release_signer: ReleaseSigner) -> None:
    live = release_signer.sign(bundled_files(), seq=2)
    local = bundled_files()
    local[_CARD] = _bump(local[_CARD], "99.1", "Edit.").replace("name: repair-github-ci", "name: X")

    with pytest.raises(PushError, match="invalid cards"):
        plan_push(local, live)


def test_deleting_a_skill_released_binaries_rely_on_is_refused(
    release_signer: ReleaseSigner,
) -> None:
    live = release_signer.sign(bundled_files(), seq=2)

    with pytest.raises(PushError, match="drops skills"):
        plan_push(bundled_files(), live, delete=[_SKILL])


def test_pull_selects_one_package_without_its_nested_children() -> None:
    files = bundled_files()

    selected = select_packages(files, ["onboarding-github-ci"])

    assert "onboarding-github-ci/SKILL.md" in selected
    assert not any("/a-analyzing-github-ci-performance/" in path for path in selected)


def test_only_the_named_shared_files_are_published(release_signer: ReleaseSigner) -> None:
    """A merge touching one shared file must not revert a newer live edit to another."""
    live_files = {
        **bundled_files(),
        "common/edited-in-git.md": "old\n",
        "common/edited-live.md": "newer fast-lane edit\n",
        "common/removed-in-git.md": "gone\n",
    }
    live = release_signer.sign(live_files, seq=6)
    local = {
        **bundled_files(),
        "common/edited-in-git.md": "merged change\n",
        "common/edited-live.md": "stale checkout copy\n",
    }

    plan = plan_push(
        local, live, shared_paths=["common/edited-in-git.md", "common/removed-in-git.md"]
    )

    assert plan.upserts == {"common/edited-in-git.md": "merged change\n"}
    assert plan.deletes == ("common/removed-in-git.md",)
    assert plan.merged["common/edited-live.md"] == "newer fast-lane edit\n"
