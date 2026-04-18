from __future__ import annotations

from app.cli.tests.discover import load_test_catalog


def test_load_test_catalog_includes_make_targets_and_rca_fixtures() -> None:
    catalog = load_test_catalog()

    assert catalog.find("make:test-cov") is not None
    assert catalog.find("make:demo") is not None
    assert catalog.find("rca:pipeline_error_in_logs") is not None


def test_load_test_catalog_excludes_synthetic_suite_for_now() -> None:
    catalog = load_test_catalog()

    assert catalog.find("suite:rds_postgres") is None
