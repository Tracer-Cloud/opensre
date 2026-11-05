"""Tests for the Gitee MCP function tools."""

from __future__ import annotations

import base64

import pytest

from integrations.gitee.tools import gitee_code_tool, gitee_mcp_tool
from integrations.gitee.tools._shared import decode, file_view
from integrations.gitee.tools.gitee_code_tool import (
    compare_gitee_refs,
    get_gitee_file,
    list_gitee_repos,
    list_gitee_tree,
    search_gitee_files,
)
from integrations.gitee.tools.gitee_mcp_tool import call_gitee_tool, list_gitee_tools
from tests.tools.conftest import BaseToolContract, mock_agent_state

_URL = "https://api.gitee.com/mcp"
_TOKEN = "gitee_secret"


class TestGiteeListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_gitee_tools.__opensre_registered_tool__


class TestGiteeCallToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return call_gitee_tool.__opensre_registered_tool__


class TestGiteeFileToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_gitee_file.__opensre_registered_tool__


class TestGiteeTreeToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_gitee_tree.__opensre_registered_tool__


class TestGiteeSearchToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_gitee_files.__opensre_registered_tool__


class TestGiteeCompareToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return compare_gitee_refs.__opensre_registered_tool__


class TestGiteeReposToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_gitee_repos.__opensre_registered_tool__


def _payload(structured: object) -> dict[str, object]:
    return {
        "source": "gitee_mcp",
        "available": True,
        "tool": "get_file_content",
        "arguments": {},
        "text": "",
        "structured_content": structured,
    }


def _dispatch_returning(*results: tuple[dict[str, object] | None, dict[str, object] | None]):
    queue = list(results)

    def fake_dispatch(
        _config: object, _tool_name: str, _arguments: dict[str, object]
    ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        return queue.pop(0)

    return fake_dispatch


def test_tools_available_when_connection_verified() -> None:
    sources = mock_agent_state(
        {"gitee_mcp": {"connection_verified": True, "url": _URL, "auth_token": _TOKEN}}
    )
    for tool in (
        get_gitee_file,
        list_gitee_tree,
        search_gitee_files,
        compare_gitee_refs,
        list_gitee_repos,
        list_gitee_tools,
        call_gitee_tool,
    ):
        assert tool.__opensre_registered_tool__.is_available(sources) is True


def test_tools_unavailable_without_verification() -> None:
    sources = mock_agent_state({"gitee_mcp": {"connection_verified": False}})
    assert get_gitee_file.__opensre_registered_tool__.is_available(sources) is False


def test_extract_params_maps_source_fields() -> None:
    runtime = get_gitee_file.__opensre_registered_tool__
    params = runtime.extract_params(
        {
            "gitee_mcp": {
                "connection_verified": True,
                "url": _URL,
                "mode": "streamable-http",
                "auth_token": _TOKEN,
            }
        }
    )
    assert params["gitee_url"] == _URL
    assert params["gitee_mode"] == "streamable-http"
    assert params["gitee_token"] == _TOKEN


def test_get_gitee_file_decodes_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    encoded = base64.b64encode(b"print('hi')").decode()
    monkeypatch.setattr(
        gitee_code_tool,
        "dispatch",
        _dispatch_returning(
            (
                _payload({"type": "file", "encoding": "base64", "content": encoded}),
                None,
            )
        ),
    )
    result = get_gitee_file(
        owner="octo",
        repo="demo",
        path="app.py",
        gitee_url=_URL,
        gitee_mode="streamable-http",
        gitee_token=_TOKEN,
    )
    assert result["kind"] == "file"
    assert result["content"] == "print('hi')"
    assert result["truncated"] is False


def test_get_gitee_file_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gitee_code_tool,
        "dispatch",
        _dispatch_returning(
            (_payload({"type": "file", "content": "x" * 5000}), None)
        ),
    )
    result = get_gitee_file(
        owner="octo",
        repo="demo",
        path="app.py",
        max_chars=1000,
        gitee_url=_URL,
        gitee_mode="streamable-http",
        gitee_token=_TOKEN,
    )
    assert len(str(result["content"])) == 1000
    assert result["truncated"] is True


def test_list_gitee_tree_returns_directory_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gitee_code_tool,
        "dispatch",
        _dispatch_returning(
            (
                _payload(
                    [
                        {"name": "src", "type": "dir"},
                        {"name": "README.md", "type": "file"},
                    ]
                ),
                None,
            )
        ),
    )
    result = list_gitee_tree(
        owner="octo",
        repo="demo",
        gitee_url=_URL,
        gitee_mode="streamable-http",
        gitee_token=_TOKEN,
    )
    assert result["kind"] == "directory"
    assert result["count"] == 2
    assert len(result["entries"]) == 2


def test_search_gitee_files_returns_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gitee_code_tool,
        "dispatch",
        _dispatch_returning(
            (_payload(["src/a.py", "src/b.py"]), None)
        ),
    )
    result = search_gitee_files(
        owner="octo",
        repo="demo",
        query="TimeoutError",
        gitee_url=_URL,
        gitee_mode="streamable-http",
        gitee_token=_TOKEN,
    )
    assert result["count"] == 2
    assert result["matches"] == ["src/a.py", "src/b.py"]


def test_compare_gitee_refs_returns_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gitee_code_tool,
        "dispatch",
        _dispatch_returning((_payload({"commits": [{"sha": "abc"}]}), None)),
    )
    result = compare_gitee_refs(
        owner="octo",
        repo="demo",
        base="v1.0.0",
        head="v1.1.0",
        gitee_url=_URL,
        gitee_mode="streamable-http",
        gitee_token=_TOKEN,
    )
    assert result["base"] == "v1.0.0"
    assert result["head"] == "v1.1.0"
    assert result["compare"] == {"commits": [{"sha": "abc"}]}


def test_list_gitee_tools_returns_compact_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_tools = [
        {
            "name": "get_file_content",
            "description": "Get a file",
            "input_schema": {"type": "object"},
        }
    ]
    monkeypatch.setattr(gitee_mcp_tool, "list_gitee_mcp_tools", lambda _config: fake_tools)
    result = list_gitee_tools(
        gitee_url=_URL,
        gitee_mode="streamable-http",
        gitee_token=_TOKEN,
    )
    assert result["available"] is True
    assert result["tools"][0]["name"] == "get_file_content"
    assert "input_schema" not in result["tools"][0]


def test_call_gitee_tool_passes_through_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gitee_mcp_tool,
        "dispatch",
        _dispatch_returning(
            (
                {
                    "source": "gitee_mcp",
                    "available": True,
                    "tool": "get_file_content",
                    "arguments": {"owner": "octo"},
                    "text": "",
                    "structured_content": {"content": "hi"},
                },
                None,
            )
        ),
    )
    result = call_gitee_tool(
        tool_name="get_file_content",
        arguments={"owner": "octo"},
        gitee_url=_URL,
        gitee_mode="streamable-http",
        gitee_token=_TOKEN,
    )
    assert result["available"] is True
    assert result["tool"] == "get_file_content"
    assert result["structured_content"] == {"content": "hi"}


def test_call_gitee_tool_surfaces_mcp_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gitee_mcp_tool,
        "dispatch",
        _dispatch_returning(
            (
                None,
                {
                    "source": "gitee_mcp",
                    "available": False,
                    "error": "permission denied",
                },
            )
        ),
    )
    result = call_gitee_tool(
        tool_name="create_issue",
        gitee_url=_URL,
        gitee_mode="streamable-http",
        gitee_token=_TOKEN,
    )
    assert result["available"] is False
    assert "permission denied" in str(result["error"])


def test_call_gitee_tool_requires_name() -> None:
    result = call_gitee_tool(tool_name="", gitee_url=_URL, gitee_token=_TOKEN)
    assert result["available"] is False
    assert "tool_name is required" in str(result["error"])


def test_shared_file_view_decodes_and_truncates() -> None:
    encoded = base64.b64encode(b"abcdef").decode()
    view = file_view(
        {"type": "file", "encoding": "base64", "content": encoded},
        max_chars=3,
    )
    assert view["content"] == "abc"
    assert view["truncated"] is True


def test_shared_decode_prefers_structured_content() -> None:
    assert decode({"structured_content": {"a": 1}, "text": '{"b": 2}'}) == {"a": 1}
    assert decode({"structured_content": None, "text": '{"b": 2}'}) == {"b": 2}
    assert decode({"structured_content": None, "text": "plain text"}) == "plain text"
