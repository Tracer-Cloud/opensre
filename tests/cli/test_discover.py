from __future__ import annotations

import unittest.mock

from app.cli.tests.discover import discover_make_targets, load_test_catalog


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
