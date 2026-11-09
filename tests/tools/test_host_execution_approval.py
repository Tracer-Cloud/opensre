"""Tools that run code on the host wait for a person before they run."""

from __future__ import annotations

import pytest

from tools.registry import get_registered_tool


@pytest.mark.parametrize("tool_name", ["shell_run", "execute_python_code"])
def test_host_execution_tools_require_approval(tool_name: str) -> None:
    # Chat gateways and headless prompts only ask for tools declaring this flag.
    tool = get_registered_tool(tool_name)
    assert tool is not None
    assert tool.requires_approval is True
    assert tool.approval_reason
