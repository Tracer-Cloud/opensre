"""Tests for the integrations credential store."""

from __future__ import annotations

import json
import os
import stat
from unittest.mock import patch

import pytest

from app.integrations.store import _save


def _assert_private_permissions(store_file) -> None:
    mode = stat.S_IMODE(store_file.stat().st_mode)
    if os.name == "nt":
        # Windows file access is governed by ACLs; chmod-style mode bits are not portable here.
        assert mode & stat.S_IWRITE
        return
    assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"


class TestSavePermissions:
    def test_saved_file_has_0o600_permissions(self, tmp_path: pytest.TempPathFactory) -> None:
        store_file = tmp_path / "integrations.json"  # type: ignore[operator]
        data = {"mariadb": {"host": "db.example.com", "database": "prod"}}

        with patch("app.integrations.store.STORE_PATH", store_file):
            _save(data)

        _assert_private_permissions(store_file)

    def test_saved_file_content_is_valid_json(self, tmp_path: pytest.TempPathFactory) -> None:
        store_file = tmp_path / "integrations.json"  # type: ignore[operator]
        data = {"mariadb": {"host": "db.example.com"}}

        with patch("app.integrations.store.STORE_PATH", store_file):
            _save(data)

        content = json.loads(store_file.read_text())
        assert content == data

    def test_save_creates_parent_directories(self, tmp_path: pytest.TempPathFactory) -> None:
        nested = tmp_path / "a" / "b" / "integrations.json"  # type: ignore[operator]

        with patch("app.integrations.store.STORE_PATH", nested):
            _save({"key": "value"})

        assert nested.exists()

    def test_save_overwrites_existing_file_with_correct_permissions(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        store_file = tmp_path / "integrations.json"  # type: ignore[operator]
        store_file.write_text("{}")
        store_file.chmod(0o644)

        with patch("app.integrations.store.STORE_PATH", store_file):
            _save({"updated": True})

        _assert_private_permissions(store_file)
        assert json.loads(store_file.read_text())["updated"] is True
