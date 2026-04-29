"""Direct unit tests for the default_db_warning helper."""

from __future__ import annotations

from app.tools.utils.db_warnings import default_db_warning


def test_contains_db_name() -> None:
    warning = default_db_warning("postgres")
    assert "postgres" in warning


def test_contains_warning_prefix() -> None:
    assert default_db_warning("master").startswith("WARNING:")


def test_consistent_template() -> None:
    for db in ("master", "postgres", "mysql"):
        w = default_db_warning(db)
        assert f"defaulted to '{db}'" in w
        assert "Results may not reflect application data." in w


def test_different_db_names_differ() -> None:
    assert default_db_warning("master") != default_db_warning("postgres")
