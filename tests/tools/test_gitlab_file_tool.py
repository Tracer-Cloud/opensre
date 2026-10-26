"""Tests for GitLabFileTool (function-based, @tool decorated)."""

from __future__ import annotations

import base64
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from integrations.gitlab.tools.gitlab_file_tool import (
    _map_get_gitlab_file_contents,
    get_gitlab_file_contents,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


def _registered_tool() -> Any:
    return cast(Any, get_gitlab_file_contents).__opensre_registered_tool__


class TestGitLabFileToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool()


def test_is_available_requires_connection_project_id_and_file_path() -> None:
    rt = _registered_tool()
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
    rt = _registered_tool()
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


def test_extract_params_maps_local_store_credentials() -> None:
    """Store-configured integrations carry base_url/auth_token; extract_params
    must still surface them as the injected credential kwargs."""
    rt = _registered_tool()
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "file_path": "runbooks.md",
                "base_url": "https://gitlab.example.com/api/v4",
                "auth_token": "glpat-store",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["project_id"] == "42"
    assert params["file_path"] == "runbooks.md"
    assert params["gitlab_url"] == "https://gitlab.example.com/api/v4"
    assert params["gitlab_token"] == "glpat-store"


def test_extract_params_defaults_ref_to_main() -> None:
    rt = _registered_tool()
    sources = mock_agent_state(
        {"gitlab": {"connection_verified": True, "project_id": "42", "file_path": "src/main.py"}}
    )
    params = rt.extract_params(sources)
    assert params["ref"] == "main"


def test_schema_does_not_expose_gitlab_credentials_as_model_inputs() -> None:
    rt = _registered_tool()

    assert "gitlab_url" not in rt.input_schema["properties"]
    assert "gitlab_token" not in rt.input_schema["properties"]


def test_run_uses_resolved_sources_creds_when_env_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a store/keyring-configured GitLab integration with NO env vars
    still resolves credentials from the resolved sources dict and reads the file."""
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_BASE_URL", raising=False)

    rt = _registered_tool()
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "file_path": "runbooks/api.md",
                "ref_name": "main",
                "base_url": "https://gitlab.example.com/api/v4",
                "auth_token": "glpat-store",
            }
        }
    )
    params = rt.extract_params(sources)

    raw_content = "# runbook\n"
    encoded = base64.b64encode(raw_content.encode("utf-8")).decode("ascii")
    fake_response = {
        "file_name": "api.md",
        "file_path": "runbooks/api.md",
        "ref": "main",
        "size": len(raw_content),
        "content": encoded,
    }
    with patch(
        "integrations.gitlab.tools.gitlab_file_tool.get_gitlab_file", return_value=fake_response
    ) as mock_fn:
        result = get_gitlab_file_contents(**params)

    assert result["available"] is True
    assert result["file"]["content"] == raw_content
    config = mock_fn.call_args.kwargs["config"]
    assert config.auth_token == "glpat-store"
    assert config.api_base_url == "https://gitlab.example.com/api/v4"


def test_run_returns_unavailable_when_config_missing() -> None:
    with patch("integrations.gitlab.tools.gitlab_file_tool._resolve_config", return_value=None):
        result = get_gitlab_file_contents(project_id="42", file_path="src/main.py")
    assert result == {
        "source": "gitlab",
        "available": False,
        "error": "gitlab integration is not configured.",
        "failure_state": "missing_config",
        "file": {},
    }


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
        patch(
            "integrations.gitlab.tools.gitlab_file_tool._resolve_config", return_value=MagicMock()
        ),
        patch(
            "integrations.gitlab.tools.gitlab_file_tool.get_gitlab_file", return_value=fake_response
        ),
    ):
        result = get_gitlab_file_contents(project_id="42", file_path="src/main.py")
    assert result["available"] is True
    assert result["file"]["content"] == raw_content
    assert result["file"]["file_path"] == "src/main.py"


def test_run_returns_unavailable_for_oversized_file() -> None:
    fake_response = {"size": 75_000, "content": "ignored"}
    with (
        patch(
            "integrations.gitlab.tools.gitlab_file_tool._resolve_config", return_value=MagicMock()
        ),
        patch(
            "integrations.gitlab.tools.gitlab_file_tool.get_gitlab_file", return_value=fake_response
        ),
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
        patch(
            "integrations.gitlab.tools.gitlab_file_tool._resolve_config", return_value=MagicMock()
        ),
        patch(
            "integrations.gitlab.tools.gitlab_file_tool.get_gitlab_file", return_value=fake_response
        ),
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
        patch(
            "integrations.gitlab.tools.gitlab_file_tool._resolve_config", return_value=MagicMock()
        ),
        patch(
            "integrations.gitlab.tools.gitlab_file_tool.get_gitlab_file", return_value=fake_response
        ),
    ):
        result = get_gitlab_file_contents(project_id="42", file_path="empty.txt")
    assert result["available"] is True
    assert result["file"]["content"] == ""


class TestMapGetGitlabFileContents:
    def test_records_entry_with_line_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_gitlab_file_contents(
            evidence,
            {
                "available": True,
                "file": {
                    "file_path": "config/settings.yaml",
                    "ref": "main",
                    "content": "line1\nline2\nline3",
                },
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_gitlab_file"
        assert entries[0]["summary"] == "'config/settings.yaml' @ main, 3 line(s)"

    def test_records_correct_line_count_with_trailing_newline(self) -> None:
        """Regression: a trailing newline must not be counted as an extra line."""
        evidence: dict[str, Any] = {}

        _map_get_gitlab_file_contents(
            evidence,
            {
                "available": True,
                "file": {
                    "file_path": "config/settings.yaml",
                    "ref": "main",
                    "content": "line1\nline2\nline3\n",
                },
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"] == "'config/settings.yaml' @ main, 3 line(s)"
        )

    def test_records_entry_with_zero_lines_for_empty_file(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_gitlab_file_contents(
            evidence,
            {"available": True, "file": {"file_path": "empty.txt", "ref": "main", "content": ""}},
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "'empty.txt' @ main, 0 line(s)"

    def test_records_nothing_when_file_empty_dict(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_gitlab_file_contents(evidence, {"available": True, "file": {}}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_gitlab_file_contents(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence
