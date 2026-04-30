from __future__ import annotations

import unittest.mock
from pathlib import Path

import pytest

from app.cli.tests.discover import (
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


def test_discover_make_targets_finds_target_at_line_one() -> None:
    """Regression guard: re.MULTILINE regex must match a target with no preceding newline."""
    fake_makefile = "test-cov:\n\tpytest\n\ntest-full:\n\tpytest --full\n"
    with unittest.mock.patch(
        "app.cli.tests.discover.MAKEFILE_PATH",
        new=unittest.mock.MagicMock(
            read_text=unittest.mock.Mock(return_value=fake_makefile),
            __str__=unittest.mock.Mock(return_value="Makefile"),
        ),
    ):
        items = discover_make_targets()

    ids = [item.id for item in items]
    assert "make:test-cov" in ids


# ---------------------------------------------------------------------------
# Bundled-binary degradation (regression for #1078)
#
# ``packaging/opensre.spec`` collects only ``app/`` data files, so at runtime
# in a PyInstaller-bundled ``opensre`` binary the ``tests/`` tree, ``Makefile``,
# and ``tests/e2e/rca`` directory are absent. Each ``discover_*`` helper must
# return cleanly so ``opensre tests`` and ``opensre tests list`` keep working
# against whatever data files *are* bundled, instead of crashing with
# ``FileNotFoundError`` from a raw ``iterdir()`` / ``read_text()`` call.
# ---------------------------------------------------------------------------


def _patch_discover_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo_root: Path | None = None,
    makefile: Path | None = None,
    rca_dir: Path | None = None,
    synthetic_dir: Path | None = None,
) -> None:
    """Helper: monkeypatch any subset of the discover module's path constants.

    Reduces the ``monkeypatch.setattr("app.cli.tests.discover.X", ...)`` ×4
    repetition that tests in ``TestDiscoverGracefulOnMissingSource`` would
    otherwise carry. Per @muddlebee's PR #952 review nit on duplicated test
    setup."""
    if repo_root is not None:
        monkeypatch.setattr("app.cli.tests.discover.REPO_ROOT", repo_root)
    if makefile is not None:
        monkeypatch.setattr("app.cli.tests.discover.MAKEFILE_PATH", makefile)
    if rca_dir is not None:
        monkeypatch.setattr("app.cli.tests.discover.RCA_DIR", rca_dir)
    if synthetic_dir is not None:
        monkeypatch.setattr("app.cli.tests.discover.SYNTHETIC_SCENARIOS_DIR", synthetic_dir)


class TestDiscoverGracefulOnMissingSource:
    def test_rds_synthetic_returns_empty_when_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reported #1078 crash: ``iterdir()`` on a missing path."""
        _patch_discover_paths(monkeypatch, synthetic_dir=tmp_path / "missing-rds-postgres")
        assert _discover_rds_synthetic_scenarios() == []

    def test_make_targets_returns_empty_when_makefile_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``discover_make_targets`` was the next class-of-bug landmine —
        ``MAKEFILE_PATH.read_text()`` would also raise ``FileNotFoundError``
        in the same bundled-binary scenario."""
        _patch_discover_paths(monkeypatch, makefile=tmp_path / "Makefile")
        assert discover_make_targets() == []

    def test_rca_files_returns_empty_when_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Path.glob`` on a missing parent already returned an empty
        iterator on CPython, but the explicit guard documents the contract
        and protects against future stdlib churn."""
        _patch_discover_paths(monkeypatch, rca_dir=tmp_path / "rca-not-here")
        assert discover_rca_files() == []

    def test_load_test_catalog_does_not_crash_with_no_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full-degradation contract: bundled binary with *no* data files
        must still produce a (possibly empty) catalog and not raise."""
        empty = tmp_path / "empty"
        empty.mkdir()
        _patch_discover_paths(
            monkeypatch,
            repo_root=empty,
            makefile=empty / "Makefile",
            rca_dir=empty / "rca",
            synthetic_dir=empty / "rds_postgres",
        )

        catalog = load_test_catalog()
        # No exception, returns an empty catalog (no make/rca/synthetic items).
        assert catalog.find("make:test-cov") is None
        assert catalog.find("rca:pipeline_error_in_logs") is None
        assert all(not item.id.startswith("synthetic:") for item in catalog.all_items())

    def test_rds_synthetic_still_discovers_when_dir_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity: the existence guard must not break the source-checkout
        path. With one scenario directory on disk, the helper must still
        emit one catalog item."""
        scenarios_dir = tmp_path / "rds_postgres"
        (scenarios_dir / "001-replication-lag").mkdir(parents=True)
        _patch_discover_paths(monkeypatch, synthetic_dir=scenarios_dir)

        items = _discover_rds_synthetic_scenarios()
        assert len(items) == 1
        assert items[0].id == "synthetic:001-replication-lag"

    def test_rds_synthetic_returns_empty_when_dir_present_but_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edge case from gap analysis: directory exists but is empty —
        ``iterdir()`` is fine, and the function must return ``[]``."""
        scenarios_dir = tmp_path / "rds_postgres"
        scenarios_dir.mkdir(parents=True)
        _patch_discover_paths(monkeypatch, synthetic_dir=scenarios_dir)
        assert _discover_rds_synthetic_scenarios() == []

    def test_rds_synthetic_skips_underscore_and_pycache_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The existing ``startswith('_')`` filter must skip ``__pycache__``
        and any other underscore-prefixed sibling. It must also skip stray
        files at the top level (only directories become catalog items)."""
        scenarios_dir = tmp_path / "rds_postgres"
        (scenarios_dir / "001-real-scenario").mkdir(parents=True)
        (scenarios_dir / "__pycache__").mkdir()
        (scenarios_dir / "_template").mkdir()
        (scenarios_dir / "README.md").write_text("not a scenario")
        _patch_discover_paths(monkeypatch, synthetic_dir=scenarios_dir)

        items = _discover_rds_synthetic_scenarios()
        ids = [item.id for item in items]
        assert ids == ["synthetic:001-real-scenario"]

    def test_rds_synthetic_enriches_display_name_from_scenario_yml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Happy YAML path: ``scenario.yml`` with a ``failure_mode`` field
        produces the ``"<id>  [<mode>]"`` display name."""
        scenarios_dir = tmp_path / "rds_postgres"
        scenario = scenarios_dir / "001-replication-lag"
        scenario.mkdir(parents=True)
        (scenario / "scenario.yml").write_text("failure_mode: replication-lag\n")
        _patch_discover_paths(monkeypatch, synthetic_dir=scenarios_dir)

        items = _discover_rds_synthetic_scenarios()
        assert len(items) == 1
        assert items[0].display_name == "001-replication-lag  [replication-lag]"

    def test_rds_synthetic_tolerates_malformed_scenario_yml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defensive YAML parse: malformed ``scenario.yml`` must not crash
        discovery — fall back to the bare directory name as display name."""
        scenarios_dir = tmp_path / "rds_postgres"
        scenario = scenarios_dir / "002-broken-yaml"
        scenario.mkdir(parents=True)
        (scenario / "scenario.yml").write_text(":::not yaml:::\n  - [unbalanced\n")
        _patch_discover_paths(monkeypatch, synthetic_dir=scenarios_dir)

        items = _discover_rds_synthetic_scenarios()
        assert len(items) == 1
        assert items[0].display_name == "002-broken-yaml"
