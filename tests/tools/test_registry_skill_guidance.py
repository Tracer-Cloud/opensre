"""Truncation must preserve success criteria and well-formed guidance wrappers."""

from dataclasses import replace
from pathlib import Path

import pytest

from config.constants.paths import REPO_ROOT
from config.constants.skill_success import success_section
from tools import registry, registry_skill_guidance


def test_long_checkout_path_preserves_github_success_criteria(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = REPO_ROOT / "integrations/github/tools/github_cli/SKILL.md"
    target = tmp_path / ("checkout-" * 20) / ("nested-" * 20) / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    original = registry.get_registered_tool_map()["github_cli"]
    tools = {original.name: replace(original, skill_guidance="")}
    monkeypatch.setattr(registry_skill_guidance, "_skill_guidance_files", lambda: (target,))

    registry_skill_guidance.apply_skill_guidance(tools)

    guidance = tools[original.name].skill_guidance
    assert str(target) in guidance
    assert success_section("operating-github-cli") in guidance
    assert guidance.endswith("</tool_guidance>")
    assert len(guidance) <= 2400


def test_combined_content_budget_preserves_each_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = []
    for index, marker in enumerate(("A", "B", "C")):
        path = tmp_path / f"skill-{index}.md"
        path.write_text(
            f"---\nname: budget-{index}\ndescription: Test guidance\ntools: [github_cli]\n---\n"
            + marker * 1000,
            encoding="utf-8",
        )
        paths.append(path)
    original = registry.get_registered_tool_map()["github_cli"]
    tools = {original.name: replace(original, skill_guidance="")}
    monkeypatch.setattr(registry_skill_guidance, "_skill_guidance_files", lambda: tuple(paths))

    registry_skill_guidance.apply_skill_guidance(tools)

    guidance = tools[original.name].skill_guidance
    assert guidance.count("</tool_guidance>") == 2
    assert "A" * 1000 in guidance
    assert "B" * 10 in guidance
    assert guidance.endswith("...\n</tool_guidance>")
    assert len(guidance) <= 2400
    assert 'name="budget-2"' not in guidance
