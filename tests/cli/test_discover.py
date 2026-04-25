from pathlib import Path

import pytest
import yaml

import app.cli.tests.discover as discover
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


class TestCommentMapForMakefile:
    def test_parses_simple_comments(self, tmp_path: Path) -> None:
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# Run the tests\ntest:\n\tpytest\n",
            encoding="utf-8",
        )
        comments = _comment_map_for_makefile(makefile)
        assert comments == {"test": "Run the tests"}

    def test_parses_multiline_comments(self, tmp_path: Path) -> None:
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# This is a multiline\n# comment for the demo\ndemo:\n\tpython demo.py\n",
            encoding="utf-8",
        )
        comments = _comment_map_for_makefile(makefile)
        assert comments == {"demo": "This is a multiline comment for the demo"}

    def test_clears_buffer_on_empty_line(self, tmp_path: Path) -> None:
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# This comment is orphaned\n\ntest:\n\tpytest\n",
            encoding="utf-8",
        )
        comments = _comment_map_for_makefile(makefile)
        assert comments == {"test": ""}

    def test_multiple_targets(self, tmp_path: Path) -> None:
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# Test target\ntest:\n\tpytest\n\n# Demo target\ndemo:\n\tpython demo.py\n",
            encoding="utf-8",
        )
        comments = _comment_map_for_makefile(makefile)
        assert comments == {"test": "Test target", "demo": "Demo target"}

    def test_special_characters_in_comments(self, tmp_path: Path) -> None:
        makefile = tmp_path / "Makefile"
        makefile.write_text(
            "# Special ch@racters $ & ! #\ntest:\n\tpytest\n",
            encoding="utf-8",
        )
        comments = _comment_map_for_makefile(makefile)
        assert comments == {"test": "Special ch@racters $ & ! #"}

    def test_missing_makefile_raises_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "DoesNotExist"
        with pytest.raises(FileNotFoundError):
            _comment_map_for_makefile(missing)


class TestDiscoverMakeTargets:
    @pytest.fixture
    def mock_makefile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        makefile = tmp_path / "Makefile"
        monkeypatch.setattr(discover, "MAKEFILE_PATH", makefile)
        return makefile

    def test_skips_missing_targets(self, mock_makefile: Path) -> None:
        mock_makefile.write_text("\ntest:\n\tpytest\n", encoding="utf-8")

        targets = discover_make_targets()
        target_ids = {t.id for t in targets}

        assert "make:test" in target_ids
        assert "make:demo" not in target_ids

    def test_applies_metadata(self, mock_makefile: Path) -> None:
        mock_makefile.write_text("\ntest:\n\tpytest\n", encoding="utf-8")

        targets = discover_make_targets()
        test_target = next(t for t in targets if t.id == "make:test")

        # Verify metadata from _TARGET_METADATA is applied
        assert test_target.display_name == "Fast Unit + Prefect E2E"
        assert "ci-safe" in test_target.tags
        assert "test" in test_target.tags

    def test_default_metadata_for_unknown_target(
        self, mock_makefile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Temporarily add a target to _TARGETS_TO_INDEX that isn't in _TARGET_METADATA
        original_indexed = discover._TARGETS_TO_INDEX
        monkeypatch.setattr(discover, "_TARGETS_TO_INDEX", (*original_indexed, "unknown-target"))

        mock_makefile.write_text("\nunknown-target:\n\techo hello\n", encoding="utf-8")

        targets = discover_make_targets()
        unknown = next(t for t in targets if t.id == "make:unknown-target")

        assert unknown.display_name == "unknown-target"
        assert unknown.tags == ("make",)
        assert unknown.description == "Run `unknown-target` from the Makefile."

    def test_empty_makefile_returns_no_targets(self, mock_makefile: Path) -> None:
        mock_makefile.write_text("", encoding="utf-8")
        assert discover_make_targets() == []

    def test_missing_makefile_raises_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        missing = tmp_path / "DoesNotExist"
        monkeypatch.setattr(discover, "MAKEFILE_PATH", missing)
        with pytest.raises(FileNotFoundError):
            discover_make_targets()


class TestDiscoverRcaFiles:
    @pytest.fixture
    def mock_rca_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        rca_dir = tmp_path / "rca"
        rca_dir.mkdir()
        monkeypatch.setattr(discover, "RCA_DIR", rca_dir)
        return rca_dir

    def test_discovers_markdown_files(self, mock_rca_dir: Path) -> None:
        (mock_rca_dir / "test_issue.md").write_text("# Test Title\nContent", encoding="utf-8")
        (mock_rca_dir / "ignored.txt").write_text("Not MD", encoding="utf-8")

        items = discover_rca_files()
        assert len(items) == 1
        assert items[0].id == "rca:test_issue"
        assert items[0].display_name == "Test Title"

    def test_falls_back_to_filename_if_no_header(self, mock_rca_dir: Path) -> None:
        (mock_rca_dir / "no_header_file.md").write_text("Just content", encoding="utf-8")

        items = discover_rca_files()
        assert items[0].display_name == "No Header File"

    def test_empty_rca_dir_returns_nothing(self, mock_rca_dir: Path) -> None:
        assert discover_rca_files() == []


class TestDiscoverSyntheticScenarios:
    @pytest.fixture
    def mock_repo_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr(discover, "REPO_ROOT", tmp_path)
        return tmp_path

    def test_discovers_scenarios_and_parses_yaml(self, mock_repo_root: Path) -> None:
        scenarios_dir = mock_repo_root / "tests" / "synthetic" / "rds_postgres"
        scenarios_dir.mkdir(parents=True)

        # Scenario with YAML
        s1 = scenarios_dir / "cpu_spike"
        s1.mkdir()
        (s1 / "scenario.yml").write_text("failure_mode: High Load", encoding="utf-8")

        # Scenario without YAML
        s2 = scenarios_dir / "disk_full"
        s2.mkdir()

        # Hidden folder (should be skipped)
        (scenarios_dir / "_internal").mkdir()

        items = _discover_rds_synthetic_scenarios()
        assert len(items) == 2
        
        cpu_item = next(i for i in items if i.id == "synthetic:cpu_spike")
        assert cpu_item.display_name == "cpu_spike  [High Load]"
        
        disk_item = next(i for i in items if i.id == "synthetic:disk_full")
        assert disk_item.display_name == "disk_full"

    def test_handles_corrupt_yaml(self, mock_repo_root: Path) -> None:
        scenarios_dir = mock_repo_root / "tests" / "synthetic" / "rds_postgres"
        scenarios_dir.mkdir(parents=True)
        s1 = scenarios_dir / "bad_yaml"
        s1.mkdir()
        (s1 / "scenario.yml").write_text("!!invalid yaml", encoding="utf-8")

        items = _discover_rds_synthetic_scenarios()
        assert items[0].display_name == "bad_yaml"


def test_load_test_catalog_sorting_and_aggregation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mock everything to return controlled sets
    monkeypatch.setattr(discover, "REPO_ROOT", tmp_path)
    
    # Create required directory for discover_cli_commands
    (tmp_path / "tests" / "synthetic" / "rds_postgres").mkdir(parents=True)
    
    makefile = tmp_path / "Makefile"
    makefile.write_text("test:\n\techo test", encoding="utf-8")
    monkeypatch.setattr(discover, "MAKEFILE_PATH", makefile)
    
    rca_dir = tmp_path / "rca"
    rca_dir.mkdir()
    (rca_dir / "aaa.md").write_text("# ZZZ First Title", encoding="utf-8")
    (rca_dir / "zzz.md").write_text("# AAA Last Title", encoding="utf-8")
    monkeypatch.setattr(discover, "RCA_DIR", rca_dir)

    # We won't mock discover_cli_commands as it's small, but REPO_ROOT is already mocked
    
    catalog = load_test_catalog()
    display_names = [item.display_name for item in catalog.items]
    
    # Verify sorting (case-insensitive)
    assert display_names == sorted(display_names, key=str.lower)
    assert "AAA Last Title" in display_names
    assert "ZZZ First Title" in display_names


def test_load_test_catalog_exclude_logic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that only certain types of items are included/excluded as expected."""
    monkeypatch.setattr(discover, "REPO_ROOT", tmp_path)
    (tmp_path / "tests" / "synthetic" / "rds_postgres").mkdir(parents=True)
    
    makefile = tmp_path / "Makefile"
    makefile.write_text("\ntest-cov:\ndemo:\n", encoding="utf-8")
    monkeypatch.setattr(discover, "MAKEFILE_PATH", makefile)
    
    rca_dir = tmp_path / "rca"
    rca_dir.mkdir()
    (rca_dir / "pipeline_error_in_logs.md").write_text("# Error", encoding="utf-8")
    monkeypatch.setattr(discover, "RCA_DIR", rca_dir)

    catalog = load_test_catalog()
    
    # Verify our specific "smoke test" items are found (using their IDs)
    assert catalog.find("make:test-cov") is not None
    assert catalog.find("make:demo") is not None
    assert catalog.find("rca:pipeline_error_in_logs") is not None
    
    # Verify synthetic suites (which have 'suite:' prefix) are excluded
    assert catalog.find("suite:rds_postgres") is None
