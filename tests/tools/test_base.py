from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.tools.base import BaseTool


def test_base_tool_rejects_blank_name() -> None:
    with pytest.raises(ValidationError, match="name"):
        type(
            "BlankNameTool",
            (BaseTool,),
            {
                "name": "   ",
                "description": "Valid description",
                "input_schema": {"type": "object", "properties": {}},
                "source": "grafana",
                "run": lambda _self, **_kwargs: {},
            },
        )


def test_base_tool_rejects_blank_description() -> None:
    with pytest.raises(ValidationError, match="description"):
        type(
            "BlankDescriptionTool",
            (BaseTool,),
            {
                "name": "valid_tool",
                "description": "   ",
                "input_schema": {"type": "object", "properties": {}},
                "source": "grafana",
                "run": lambda _self, **_kwargs: {},
            },
        )
