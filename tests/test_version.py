from __future__ import annotations

import importlib.metadata

from app import version as version_module


def test_get_version_falls_back_to_default_when_package_metadata_is_missing(
    monkeypatch,
) -> None:
    def _raise_package_not_found(_: str) -> str:
        raise importlib.metadata.PackageNotFoundError(version_module.PACKAGE_NAME)

    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)

    assert version_module.get_version() == version_module.DEFAULT_VERSION
