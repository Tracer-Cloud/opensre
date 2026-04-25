from __future__ import annotations

from pathlib import Path

import pytest

from app.cli.tests.discover import (
    _comment_map_for_makefile,
    _discover_rds_synthetic_scenarios,
    discover_make_targets,
    discover_rca_files,
    load_test_catalog,
)


def test_load_test_catalog_includes_make_targets_and_rca_fixtures() -> None:
    catalog = load_test_catalog()

    assert catalog.find("make:test-cov") is not None
    assert catalog.find("make:demo") is not None
    assert catalog.find("rca:pipeline_error_in_logs") is not None


def test_load_test_catalog_excludes_synthetic_suite_for_now() -> None:
    catalog = load_test_catalog()

    assert catalog.find("suite:rds_postgres") is None


def test_comment_map_for_makefile(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "# Run tests\n"
        "test:\n"
        "\techo test\n"
        "\n"
        "# Build the app\n"
        "# (with fast option)\n"
        "build:\n"
        "\techo build\n",
        encoding="utf-8",
    )

    comment_map = _comment_map_for_makefile(makefile)
    assert comment_map.get("test") == "Run tests"
    assert comment_map.get("build") == "Build the app (with fast option)"


def test_discover_make_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "# Run coverage tests\n"
        "test-cov:\n"
        "\tpytest --cov\n"
        "\n"
        "# Another target\n"
        "not-indexed:\n"
        "\techo no\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.cli.tests.discover.MAKEFILE_PATH", makefile)

    items = discover_make_targets()
    # test-cov is in _TARGETS_TO_INDEX, so it should be discovered
    assert any(item.id == "make:test-cov" for item in items)
    # not-indexed is NOT in _TARGETS_TO_INDEX, so it shouldn't be discovered
    assert not any(item.id == "make:not-indexed" for item in items)


def test_discover_rca_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rca_dir = tmp_path / "rca"
    rca_dir.mkdir()
    (rca_dir / "my_test_alert.md").write_text(
        "# My Awesome Alert\n\nsome details", encoding="utf-8"
    )
    (rca_dir / "another_alert.md").write_text("No heading here", encoding="utf-8")

    monkeypatch.setattr("app.cli.tests.discover.RCA_DIR", rca_dir)

    items = discover_rca_files()
    assert len(items) == 2

    item1 = next((i for i in items if i.id == "rca:my_test_alert"), None)
    assert item1 is not None
    assert item1.display_name == "My Awesome Alert"

    item2 = next((i for i in items if i.id == "rca:another_alert"), None)
    assert item2 is not None
    assert item2.display_name == "Another Alert"


def test_discover_rds_synthetic_scenarios(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    synthetic_dir = repo_root / "tests" / "synthetic" / "rds_postgres"
    scenario1_dir = synthetic_dir / "scenario1"
    scenario1_dir.mkdir(parents=True)
    (scenario1_dir / "scenario.yml").write_text("failure_mode: High CPU", encoding="utf-8")

    scenario2_dir = synthetic_dir / "scenario2"
    scenario2_dir.mkdir()
    # scenario2 has no yaml

    monkeypatch.setattr("app.cli.tests.discover.REPO_ROOT", repo_root)

    items = _discover_rds_synthetic_scenarios()
    assert len(items) == 2

    item1 = next((i for i in items if i.id == "synthetic:scenario1"), None)
    assert item1 is not None
    assert item1.display_name == "scenario1  [High CPU]"

    item2 = next((i for i in items if i.id == "synthetic:scenario2"), None)
    assert item2 is not None
    assert item2.display_name == "scenario2"
