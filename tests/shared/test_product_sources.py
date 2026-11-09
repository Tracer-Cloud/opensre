"""A stray virtualenv inside a product package must not reach the scanners."""

from __future__ import annotations

from pathlib import Path

from tests.shared.product_sources import is_vendored, product_python_files


def test_a_virtualenv_inside_a_package_is_not_product_source() -> None:
    # Arrange / Act / Assert: the case that broke three guards at once.
    assert is_vendored(Path(".venv/lib/python3.14/site-packages/kubernetes/client.py"))


def test_ordinary_product_paths_are_kept() -> None:
    assert not is_vendored(Path("agent_harness/turns/action_driver.py"))


def test_scanning_a_package_skips_its_vendored_trees(tmp_path: Path) -> None:
    # Arrange: one real module beside a vendored tree and a cache.
    (tmp_path / "real.py").write_text("x = 1\n")
    vendored = tmp_path / ".venv" / "lib" / "site-packages"
    vendored.mkdir(parents=True)
    (vendored / "third_party.py").write_text("def broken(:\n")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "stale.py").write_text("y = 2\n")

    # Act
    found = product_python_files(tmp_path)

    # Assert
    assert [path.name for path in found] == ["real.py"]


def test_a_missing_root_yields_nothing(tmp_path: Path) -> None:
    assert product_python_files(tmp_path / "absent") == []
