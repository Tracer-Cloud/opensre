"""No provider advertises a tool's injected parameters to the model.

``RegisteredTool`` prunes injected parameters — credentials, endpoints, mode
flags — into ``public_input_schema``. ``input_schema`` keeps them, because the
harness fills them at call time.

The CLI-backed client read ``input_schema``, so on that backend a GitHub tool
offered the model its own ``github_token`` as a fillable argument, while the
Anthropic, OpenAI and Bedrock clients pruned it. Nothing caught the divergence:
every client typed the parameter ``list[Any]``, so no contract said which schema
was meant.
"""

from __future__ import annotations

from typing import Any

from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from core.llm.transports.sdk.bedrock_converse import build_converse_tool_specs

_INJECTED = ("github_token", "github_url")


class _ToolWithACredential:
    """A tool whose schema carries an injected credential the model must not see."""

    name = "github_issue_create"
    description = "Create a GitHub issue."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "github_token": {"type": "string"},
                "github_url": {"type": "string"},
            },
            "required": ["title", "github_token"],
        }

    @property
    def public_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }


def _mentions_a_credential(payload: object) -> bool:
    return any(name in repr(payload) for name in _INJECTED)


def test_the_tool_fixture_would_leak_if_the_raw_schema_were_used() -> None:
    """Guard the guard: the raw schema really does carry the credential."""
    assert _mentions_a_credential(_ToolWithACredential().input_schema)
    assert not _mentions_a_credential(_ToolWithACredential().public_input_schema)


def test_openai_specs_hide_injected_params() -> None:
    # Arrange / Act
    specs = build_openai_tool_specs([_ToolWithACredential()])

    # Assert
    assert not _mentions_a_credential(specs)


def test_converse_specs_hide_injected_params() -> None:
    # Arrange / Act
    specs = build_converse_tool_specs([_ToolWithACredential()])

    # Assert
    assert not _mentions_a_credential(specs)
