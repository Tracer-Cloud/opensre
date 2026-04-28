"""Tests for GitLabFileTool (function-based, @tool decorated)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

from app.tools.GitLabFileTool import get_gitlab_file_contents
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitLabFileToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_gitlab_file_contents.__opensre_registered_tool__


def test_is_available_requires_connection_project_id_and_file_path() -> None:
    rt = get_gitlab_file_contents.__opensre_registered_tool__
    assert (
        rt.is_available(
            {
                "gitlab": {
                    "connection_verified": True,
                    "project_id": "42",
                    "file_path": "src/main.py",
                }
            }
        )
        is True
    )
    assert rt.is_available({"gitlab": {"connection_verified": True, "project_id": "42"}}) is False
    assert (
        rt.is_available({"gitlab": {"connection_verified": True, "file_path": "src/main.py"}})
        is False
    )
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_gitlab_file_contents.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "file_path": "src/main.py",
                "ref_name": "develop",
                "gitlab_url": "https://gitlab.example.com",
                "gitlab_token": "glpat-test",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["project_id"] == "42"
    assert params["file_path"] == "src/main.py"
    assert params["ref"] == "develop"
    assert params["gitlab_url"] == "https://gitlab.example.com"
    assert params["gitlab_token"] == "glpat-test"


def test_extract_params_defaults_ref_to_main() -> None:
    rt = get_gitlab_file_contents.__opensre_registered_tool__
    sources = mock_agent_state(
        {"gitlab": {"connection_verified": True, "project_id": "42", "file_path": "src/main.py"}}
    )
    params = rt.extract_params(sources)
    assert params["ref"] == "main"


def test_run_returns_unavailable_when_config_missing() -> None:
    with patch("app.tools.GitLabFileTool._resolve_config", return_value=None):
        result = get_gitlab_file_contents(project_id="42", file_path="src/main.py")
    assert result["available"] is False
    assert "not configured" in result["error"]
    assert result["file"] == {}


def test_run_happy_path_decodes_base64_content() -> None:
    raw_content = "print('hello world')\n"
    encoded = base64.b64encode(raw_content.encode("utf-8")).decode("ascii")
    fake_response = {
        "file_name": "main.py",
        "file_path": "src/main.py",
        "ref": "main",
        "size": len(raw_content),
        "content": encoded,
    }
    with (
        patch("app.tools.GitLabFileTool._resolve_config", return_value=MagicMock()),
        patch("app.tools.GitLabFileTool.get_gitlab_file", return_value=fake_response),
    ):
        result = get_gitlab_file_contents(project_id="42", file_path="src/main.py")
    assert result["available"] is True
    assert result["file"]["content"] == raw_content
    assert result["file"]["file_path"] == "src/main.py"


def test_run_returns_unavailable_for_oversized_file() -> None:
    fake_response = {"size": 75_000, "content": "ignored"}
    with (
        patch("app.tools.GitLabFileTool._resolve_config", return_value=MagicMock()),
        patch("app.tools.GitLabFileTool.get_gitlab_file", return_value=fake_response),
    ):
        result = get_gitlab_file_contents(project_id="42", file_path="big.bin")
    assert result["available"] is False
    assert "too large" in result["error"].lower()
    assert result["file"] == {}


def test_run_returns_unavailable_for_binary_file() -> None:
    binary_bytes = b"\xff\xfe\x00\x01\x02\x03"
    encoded = base64.b64encode(binary_bytes).decode("ascii")
    fake_response = {
        "file_name": "blob.bin",
        "file_path": "blob.bin",
        "ref": "main",
        "size": len(binary_bytes),
        "content": encoded,
    }
    with (
        patch("app.tools.GitLabFileTool._resolve_config", return_value=MagicMock()),
        patch("app.tools.GitLabFileTool.get_gitlab_file", return_value=fake_response),
    ):
        result = get_gitlab_file_contents(project_id="42", file_path="blob.bin")
    assert result["available"] is False
    assert "not UTF-8" in result["error"]


def test_run_handles_empty_content() -> None:
    fake_response = {
        "file_name": "empty.txt",
        "file_path": "empty.txt",
        "ref": "main",
        "size": 0,
        "content": "",
    }
    with (
        patch("app.tools.GitLabFileTool._resolve_config", return_value=MagicMock()),
        patch("app.tools.GitLabFileTool.get_gitlab_file", return_value=fake_response),
    ):
        result = get_gitlab_file_contents(project_id="42", file_path="empty.txt")
    assert result["available"] is True
    assert result["file"]["content"] == ""
