"""The frozen binary starts where the ``opensre`` console script starts.

A PyInstaller build entered through ``surfaces/cli`` alone would run a CLI with
no :class:`~surfaces.cli.host.CliHost`: bare ``opensre`` would print the landing
page instead of opening the shell, ``onboard`` would not hand off to it, and
``gateway start --foreground`` would fail. The spec and ``pyproject`` must name
the same entry.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_ENTRYPOINT_MODULE = "surfaces.entrypoint"
_ENTRYPOINT_SOURCE = "surfaces/entrypoint.py"


def test_console_script_points_at_the_composition_entrypoint() -> None:
    # Arrange
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    # Act
    console_script = pyproject["project"]["scripts"]["opensre"]

    # Assert
    assert console_script == f"{_ENTRYPOINT_MODULE}:main"


def test_pyinstaller_spec_analyses_the_same_entrypoint() -> None:
    # Arrange
    spec = (REPO_ROOT / "opensre.spec").read_text(encoding="utf-8")

    # Act: the single script passed to Analysis(...).
    match = re.search(r"a = Analysis\(\s*\[str\(ROOT / \"([^\"]+)\"\)\]", spec)

    # Assert
    assert match is not None, "could not find the Analysis entry script in opensre.spec"
    assert match.group(1) == _ENTRYPOINT_SOURCE


def test_package_module_runner_uses_the_entrypoint() -> None:
    """``python -m surfaces`` is the whole product, like the console script."""
    # Arrange / Act
    source = (REPO_ROOT / "surfaces/__main__.py").read_text(encoding="utf-8")

    # Assert
    assert f"from {_ENTRYPOINT_MODULE} import main" in source


def test_frozen_bundle_ships_runtime_prompts() -> None:
    """Runtime prompt loaders can read their adjacent Markdown in the bundle."""
    spec = (REPO_ROOT / "opensre.spec").read_text(encoding="utf-8")

    assert '"core.agent_harness.prompts"' in spec
    assert 'includes=["opensre_system_prompt.md", "proactive_messages/*.md"]' in spec
    assert (REPO_ROOT / "core/agent_harness/prompts/opensre_system_prompt.md").is_file()
    assert (
        REPO_ROOT / "core/agent_harness/prompts/proactive_messages/MASTER_JUDGEMENT.md"
    ).is_file()


def test_release_artifacts_do_not_ship_removed_planning_instructions() -> None:
    """Planning instructions were retired; do not reintroduce the data file."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert "core.agent_harness.task_plan" not in package_data
    assert not (REPO_ROOT / "core/agent_harness/task_plan/planning_instructions.md").is_file()
