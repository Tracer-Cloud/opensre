"""CI validates raw cards even when runtime discovery excludes them."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

import core.agent_harness.prompts.skills as skills
from config.constants.skills import ONBOARDING_SKILL_NAME, SKIP_DEMO_OPTION
from tests.utils.skill_cards import skill_card


@pytest.fixture
def catalog_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setattr(
        "core.agent_harness.prompts.skills.content.files.skills_dir", lambda: tmp_path
    )
    skills.clear_skills_caches()
    yield tmp_path
    skills.clear_skills_caches()


def test_all_bundled_cards_pass_the_production_validator() -> None:
    catalog = skills.read_skill_catalog()
    assert catalog.diagnostics == ()
    assert len(catalog.skills) == len(list(skills.skills_dir().rglob("SKILL.md")))


def test_broken_card_does_not_prevent_valid_discovery(
    catalog_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (catalog_root / "valid.md").write_text(skill_card("valid"))
    (catalog_root / "broken.md").write_text("---\nname: [broken\n---\nBody.")
    (catalog_root / "AGENTS.md").write_text("# Contributor instructions")
    assert [skill.name for skill in skills.list_action_skills()] == ["valid"]
    assert "broken.md" in caplog.text
    assert "AGENTS.md" not in caplog.text
    assert skills.load_skill_body("agents") == ""
    assert skills.read_skill_catalog().diagnostics


@pytest.mark.parametrize(
    "invalid",
    [
        skill_card("broken").replace("2026-01-01", "2026-99-99").encode(),
        b"\xff",
    ],
)
def test_invalid_date_or_encoding_does_not_abort_discovery(
    catalog_root: Path, invalid: bytes
) -> None:
    (catalog_root / "valid.md").write_text(skill_card("valid"))
    (catalog_root / "broken.md").write_bytes(invalid)
    catalog = skills.read_skill_catalog()
    assert [skill.name for skill in catalog.skills] == ["valid"]
    assert len(catalog.diagnostics) == 1
    assert "broken.md" in catalog.diagnostics[0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requires", None),
        ("requires", []),
        ("requires", [" "]),
        ("usecases", [{"First-experience demo": "setup"}]),
        ("last_changed_at", "2026-01-01"),
        ("version", 1),
        ("type", "repair"),
    ],
)
def test_metadata_is_required_and_strict(catalog_root: Path, field: str, value: Any) -> None:
    metadata = yaml.safe_load(skill_card("invalid").split("---")[1])["metadata"]
    if value is None:
        del metadata[field]
    else:
        metadata[field] = value
    (catalog_root / "invalid.md").write_text(skill_card("invalid", metadata=metadata))
    catalog = skills.read_skill_catalog()
    assert catalog.skills == ()
    assert any(field in diagnostic for diagnostic in catalog.diagnostics)


@pytest.mark.parametrize(
    "fields",
    [
        {"recurring": "true"},
        {"after_tool": []},
        {"demo_order": 1},
        {"getting_started": "Demo", "demo_order": True},
        {"getting_started": SKIP_DEMO_OPTION, "demo_order": 1},
        {"pre_execute": [{"tool": "shell_run", "args": {"command": "echo hello"}}]},
        {"pre_execute": [{"tool": "ask_user_choice", "args": {"title": "Pick", "options": "a,b"}}]},
    ],
)
def test_runtime_fields_reject_unsupported_or_mistyped_values(
    catalog_root: Path, fields: dict[str, Any]
) -> None:
    (catalog_root / "invalid.md").write_text(skill_card("invalid", **fields))
    catalog = skills.read_skill_catalog()
    assert catalog.skills == ()
    assert catalog.diagnostics


def test_duplicate_skill_names_are_all_excluded(catalog_root: Path) -> None:
    for filename in ("first.md", "second.md"):
        (catalog_root / filename).write_text(skill_card("duplicate"))
    (catalog_root / "valid.md").write_text(skill_card("valid"))
    catalog = skills.read_skill_catalog()
    assert [skill.name for skill in catalog.skills] == ["valid"]
    assert len(catalog.diagnostics) == 2
    assert all("duplicate name" in diagnostic for diagnostic in catalog.diagnostics)


@pytest.mark.parametrize("child_count", [0, 8])
def test_unrenderable_generated_menu_excludes_only_the_master(
    catalog_root: Path, child_count: int
) -> None:
    (catalog_root / "master.md").write_text(
        skill_card(
            ONBOARDING_SKILL_NAME,
            pre_execute=[{"tool": "ask_user_choice", "args": {"title": "Pick a workflow"}}],
        )
    )
    for index in range(child_count):
        (catalog_root / f"child-{index}.md").write_text(
            skill_card(f"child-{index}", getting_started=f"Demo {index}", demo_order=index + 1)
        )
    catalog = skills.read_skill_catalog()
    assert len(catalog.skills) == child_count
    assert all(skill.name != ONBOARDING_SKILL_NAME for skill in catalog.skills)
    assert len(catalog.diagnostics) == 1
    assert "generated demo menu" in catalog.diagnostics[0]


@pytest.mark.parametrize(
    "args",
    [
        {"title": "Pick", "options": ["same", " same "]},
        {
            "questions": [
                {"label": "First", "title": "Pick", "options": ["one", "two"]},
                {"label": "Second", "title": "pick", "options": ["one", "two"]},
            ]
        },
    ],
)
def test_ambiguous_entry_menus_fail_validation(catalog_root: Path, args: dict[str, Any]) -> None:
    (catalog_root / "invalid.md").write_text(
        skill_card("invalid", pre_execute=[{"tool": "ask_user_choice", "args": args}])
    )
    catalog = skills.read_skill_catalog()
    assert catalog.skills == ()
    assert "distinct" in catalog.diagnostics[0]


def test_multiple_entry_menus_cannot_overwrite_pending_questions(catalog_root: Path) -> None:
    (catalog_root / "invalid.md").write_text(
        skill_card(
            "invalid",
            pre_execute=[
                {"tool": "ask_user_choice", "args": {"title": title, "options": ["one", "two"]}}
                for title in ("First?", "Second?")
            ],
        )
    )
    catalog = skills.read_skill_catalog()
    assert catalog.skills == ()
    assert "pre_execute" in catalog.diagnostics[0]


def test_duplicate_yaml_keys_are_not_silently_overwritten(catalog_root: Path) -> None:
    raw = skill_card("invalid").replace("name: invalid", "name: invalid\nname: replacement")
    (catalog_root / "invalid.md").write_text(raw)
    catalog = skills.read_skill_catalog()
    assert catalog.skills == ()
    assert "duplicate YAML key" in catalog.diagnostics[0]


def test_include_symlink_cannot_escape_the_skills_tree(catalog_root: Path) -> None:
    outside = catalog_root.parent / "outside.md"
    outside.write_text("Do not disclose this file.")
    (catalog_root / "escape.md").symlink_to(outside)
    card = catalog_root / "package/SKILL.md"
    card.parent.mkdir()
    card.write_text(skill_card("invalid", includes=["escape.md"]))
    catalog = skills.read_skill_catalog()
    assert not any(skill.name == "invalid" for skill in catalog.skills)
    assert any("includes:" in diagnostic for diagnostic in catalog.diagnostics)
    assert "Do not disclose" not in skills.load_skills_index()


def test_missing_include_excludes_the_card(catalog_root: Path) -> None:
    (catalog_root / "invalid.md").write_text(skill_card("invalid", includes=["missing.md"]))
    catalog = skills.read_skill_catalog()
    assert catalog.skills == ()
    assert "missing.md" in catalog.diagnostics[0]


def test_body_revalidates_cached_cards_and_cache_reset_refreshes_the_index(
    catalog_root: Path,
) -> None:
    card = catalog_root / "workflow" / "SKILL.md"
    card.parent.mkdir()
    card.write_text(skill_card("workflow", "Original body.", description="Original summary."))
    assert "Original summary." in skills.load_skills_index()
    assert skills.load_skill_body("workflow") == "Original body."

    card.write_text(skill_card("workflow", "Updated body.", description="Updated summary."))
    assert skills.load_skill_body("workflow") == "Updated body."
    assert "Original summary." in skills.load_skills_index()
    skills.clear_skills_caches()
    assert "Updated summary." in skills.load_skills_index()

    card.write_text(skill_card("workflow", "Invalid body.", includes=["missing.md"]))
    assert skills.load_skill_body("workflow") == ""
    skills.clear_skills_caches()
    assert skills.list_action_skills() == ()
    assert skills.load_skills_index() == ""
