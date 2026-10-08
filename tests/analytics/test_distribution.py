from __future__ import annotations

import json
from importlib.metadata import PathDistribution
from pathlib import Path

import pytest

from infrastructure.analytics import distribution


def _loaded_file(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    file = root / "infrastructure" / "analytics" / "distribution.py"
    file.parent.mkdir(parents=True)
    file.touch()
    monkeypatch.setattr(distribution, "__file__", str(file))
    monkeypatch.delattr(distribution.sys, "frozen", raising=False)
    monkeypatch.delattr(distribution.sys, "_MEIPASS", raising=False)
    return file


def _metadata(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    info = root / "opensre-0.1.dist-info"
    info.mkdir(parents=True)
    info.joinpath("METADATA").write_text("Name: opensre\nVersion: 0.1\n")
    installed = PathDistribution(info)
    monkeypatch.setattr(distribution.importlib.metadata, "distribution", lambda _name: installed)
    return info


def test_installed_wheel_inside_checkout_is_not_local_development(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tmp_path.joinpath(".git").mkdir()
    tmp_path.joinpath("pyproject.toml").write_text('[project]\nname = "opensre"\n')
    root = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages"
    file = _loaded_file(monkeypatch, root)
    info = _metadata(monkeypatch, root)
    info.joinpath("RECORD").write_text(f"{file.relative_to(root).as_posix()},,\n")

    assert distribution.detect_distribution() == "installed_package"


def test_editable_install_must_match_loaded_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source %20"
    _loaded_file(monkeypatch, source)
    source.joinpath("pyproject.toml").write_text('[project]\nname = "opensre"\n')
    info = _metadata(monkeypatch, tmp_path / "site-packages")
    info.joinpath("direct_url.json").write_text(
        json.dumps({"url": source.as_uri(), "dir_info": {"editable": True}})
    )
    assert distribution.detect_distribution() == "editable_package"

    info.joinpath("direct_url.json").write_text(
        json.dumps(
            {"url": (tmp_path / "another-checkout").as_uri(), "dir_info": {"editable": True}}
        )
    )
    assert distribution.detect_distribution() == "source_checkout"


def test_unknown_distribution_does_not_claim_packaged_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _loaded_file(monkeypatch, tmp_path)
    info = _metadata(monkeypatch, tmp_path)
    info.joinpath("direct_url.json").write_text("malformed metadata")

    assert distribution.detect_distribution() == "unknown"


def test_frozen_binary_is_independent_of_checkout_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(distribution.sys, "frozen", True, raising=False)

    assert distribution.detect_distribution() == "frozen_binary"
