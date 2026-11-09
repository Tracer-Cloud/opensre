from __future__ import annotations

import datetime as _dt
import importlib.metadata

from config import version as version_module
from config.runtime_metadata import build_info


def _raise_package_not_found(_: str) -> str:
    raise importlib.metadata.PackageNotFoundError("opensre")


def _no_git(monkeypatch) -> None:
    """Force the git-metadata lookup to find nothing (stripped checkout)."""
    monkeypatch.setattr(build_info, "find_git_layout", lambda: None)


class _FixedClock:
    """A datetime stand-in whose ``now`` is fixed, so the build date is stable."""

    @staticmethod
    def now(tz: object = None) -> _dt.datetime:
        return _dt.datetime(2026, 8, 27, tzinfo=tz)  # type: ignore[arg-type]


def _git_head_at(monkeypatch, sha: str) -> None:
    monkeypatch.setattr(build_info, "find_git_layout", object)
    monkeypatch.setattr(build_info, "read_git_head_sha", lambda _layout: sha)
    monkeypatch.setattr(version_module, "datetime", _FixedClock)


def test_release_version_string_is_returned_verbatim(monkeypatch) -> None:
    # A full release string (with a ``+`` local segment) is used as-is.
    monkeypatch.setattr(
        version_module.importlib.metadata,
        "version",
        lambda _name: "0.1.2026.8.27+main.85fd865",
    )
    assert version_module.get_opensre_version() == "0.1.2026.8.27+main.85fd865"


def test_pyproject_base_is_returned_when_git_is_unavailable(monkeypatch, tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    version_file = config_dir / "version.py"
    version_file.touch()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")

    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "__file__", str(version_file))
    _no_git(monkeypatch)

    assert version_module.get_opensre_version() == "9.9.9"


def test_dev_default_when_metadata_pyproject_and_git_all_missing(monkeypatch, tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    version_file = config_dir / "version.py"
    version_file.touch()

    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "__file__", str(version_file))
    _no_git(monkeypatch)

    assert version_module.get_opensre_version() == "0.1"


def test_dev_checkout_expands_to_the_dated_main_build_shape(monkeypatch) -> None:
    # A dev checkout expands the base to the release shape ``0.1.Y.M.D+main.<sha>``.
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    _git_head_at(monkeypatch, "85fd865")

    assert version_module.get_opensre_version() == "0.1.2026.8.27+main.85fd865"


def test_dev_build_version_is_deterministic_for_a_fixed_commit_and_clock(monkeypatch) -> None:
    # A fixed commit + clock yields one stable string across calls — no flake.
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    _git_head_at(monkeypatch, "85fd865")

    first = version_module.get_opensre_version()
    second = version_module.get_opensre_version()

    assert first == second == "0.1.2026.8.27+main.85fd865"


class _NextDayClock:
    """The day after :class:`_FixedClock`, for a build that did not change."""

    @staticmethod
    def now(tz: object = None) -> _dt.datetime:
        return _dt.datetime(2026, 8, 28, tzinfo=tz)  # type: ignore[arg-type]


def test_build_stamp_of_a_dev_checkout_changes_with_the_commit_not_the_day(monkeypatch) -> None:
    # The scheduler service is restarted when this stamp changes; a dated stamp
    # would restart it every day for the same code.
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    _git_head_at(monkeypatch, "85fd865")
    today = version_module.get_build_stamp()
    monkeypatch.setattr(version_module, "datetime", _NextDayClock)

    assert version_module.get_build_stamp() == today == "0.1+main.85fd865"
    _git_head_at(monkeypatch, "98e2e6e")
    assert version_module.get_build_stamp() == "0.1+main.98e2e6e"
