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


class TestDiscoverGracefulOnMissingSource:
    def test_rds_synthetic_returns_empty_when_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reported #1078 crash: ``iterdir()`` on a missing path."""
        # tmp_path has no ``tests/synthetic/rds_postgres`` subtree.
        monkeypatch.setattr("app.cli.tests.discover.REPO_ROOT", tmp_path)
        assert _discover_rds_synthetic_scenarios() == []

    def test_make_targets_returns_empty_when_makefile_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``discover_make_targets`` was the next class-of-bug landmine —
        ``MAKEFILE_PATH.read_text()`` would also raise ``FileNotFoundError``
        in the same bundled-binary scenario."""
        monkeypatch.setattr("app.cli.tests.discover.MAKEFILE_PATH", tmp_path / "Makefile")
        assert discover_make_targets() == []

    def test_rca_files_returns_empty_when_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Path.glob`` on a missing parent already returned an empty
        iterator on CPython, but the explicit guard documents the contract
        and protects against future stdlib churn."""
        monkeypatch.setattr("app.cli.tests.discover.RCA_DIR", tmp_path / "rca-not-here")
        assert discover_rca_files() == []

    def test_load_test_catalog_does_not_crash_with_no_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full-degradation contract: bundled binary with *no* data files
        must still produce a (possibly empty) catalog and not raise."""
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr("app.cli.tests.discover.REPO_ROOT", empty)
        monkeypatch.setattr("app.cli.tests.discover.MAKEFILE_PATH", empty / "Makefile")
        monkeypatch.setattr("app.cli.tests.discover.RCA_DIR", empty / "rca")

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
        scenarios_dir = tmp_path / "tests" / "synthetic" / "rds_postgres"
        (scenarios_dir / "001-replication-lag").mkdir(parents=True)
        monkeypatch.setattr("app.cli.tests.discover.REPO_ROOT", tmp_path)

        items = _discover_rds_synthetic_scenarios()
        assert len(items) == 1
        assert items[0].id == "synthetic:001-replication-lag"
