from __future__ import annotations

from integrations.pipedream.tools.pipedream_tool import tool


def _source() -> dict[str, object]:
    return {
        "access_mode": "webapp_proxy",
        "apps": [{"service": "notion", "app_slug": "notion", "account_id": "ap_1"}],
    }


def test_call_preserves_non_text_mcp_content(monkeypatch) -> None:
    content = [{"type": "image", "data": "base64", "mimeType": "image/png"}]
    monkeypatch.setattr(
        tool,
        "call_proxy_tool",
        lambda **_kwargs: {"content": content, "isError": False},
    )

    result = tool.call_pipedream_tool(
        tool_name="notion-export",
        pipedream=_source(),
    )

    assert result["available"] is True
    assert result["content"] == content
