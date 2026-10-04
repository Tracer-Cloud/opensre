"""Tests for the registered-tool catalog used by the ``/tools list`` slash command."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from rich.console import Console

from core.domain.types.tools import ToolSurface
from core.tool.contracts import RegisteredTool
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.command_registry.tools_cmds import _TOOLS_FIRST_ARGS, _cmd_tools
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.tables import tool_catalog
from surfaces.shared.terminal.tables.tool_catalog import (
    ToolCatalogEntry,
    _summarize_input_schema,
    build_tool_catalog,
    format_tool_catalog_text,
)


def _make_tool(
    name: str,
    *,
    description: str = "Tool description.",
    surfaces: tuple[ToolSurface, ...] = (ToolSurface.CHAT,),
    input_schema: dict[str, Any] | None = None,
    origin_module: str = "tools.registry",
) -> RegisteredTool:
    """Construct a minimal RegisteredTool stub for catalog rendering tests."""
    return RegisteredTool(
        name=name,
        description=description,
        input_schema=input_schema or {"type": "object", "properties": {}, "required": []},
        source="knowledge",
        run=lambda **_: None,
        surfaces=surfaces,
        origin_module=origin_module,
        origin_name=name,
    )


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[RegisteredTool]]:
    """Replace the live registry with a curated set so tests are deterministic."""
    tools: list[RegisteredTool] = []

    def _fake_get_registered_tools(
        surface: ToolSurface | str | None = None,
    ) -> list[RegisteredTool]:
        if surface is None:
            return list(tools)
        return [t for t in tools if surface in t.surfaces]

    monkeypatch.setattr(tool_catalog, "get_registered_tools", _fake_get_registered_tools)
    yield tools


class TestSummarizeInputSchema:
    def test_empty_schema_renders_no_params(self) -> None:
        assert _summarize_input_schema({}) == "(no params)"
        assert (
            _summarize_input_schema({"type": "object", "properties": {}, "required": []})
            == "(no params)"
        )

    def test_required_params_render_without_question_mark(self) -> None:
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query", "limit"],
        }
        assert _summarize_input_schema(schema) == "query: string, limit: integer"

    def test_optional_params_get_question_mark_suffix(self) -> None:
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        }
        assert _summarize_input_schema(schema) == "query: string, limit?: integer"

    def test_untyped_property_renders_as_any(self) -> None:
        schema = {
            "type": "object",
            "properties": {"cloudops_backend": {}},
            "required": ["cloudops_backend"],
        }
        assert _summarize_input_schema(schema) == "cloudops_backend: any"

    def test_overlong_summary_is_truncated_with_ellipsis(self) -> None:
        # Build a schema large enough to exceed the 200-char cap.
        properties = {f"param_{i}": {"type": "string"} for i in range(40)}
        schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties.keys()),
        }
        rendered = _summarize_input_schema(schema)
        assert len(rendered) <= 200
        assert rendered.endswith("…")


class TestBuildToolCatalog:
    def test_returns_empty_list_when_no_tools(self, fake_registry: list[RegisteredTool]) -> None:
        del fake_registry  # unused — registry is empty by default
        assert build_tool_catalog() == []

    def test_projects_each_tool_into_a_catalog_entry(
        self, fake_registry: list[RegisteredTool]
    ) -> None:
        fake_registry.append(
            _make_tool(
                "search_github",
                description="Search GitHub code.",
                surfaces=(ToolSurface.CHAT,),
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        )
        entries = build_tool_catalog()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.name == "search_github"
        assert entry.surfaces == ("chat",)
        assert entry.description == "Search GitHub code."
        assert entry.input_schema_summary == "query: string"
        # Source file for the registry module resolves to its repo-relative path.
        assert entry.source_file == "tools/registry.py"

    def test_filters_by_surface_when_provided(self, fake_registry: list[RegisteredTool]) -> None:
        fake_registry.extend(
            [
                _make_tool("action_only", surfaces=(ToolSurface.ACTION,)),
                _make_tool("chat_only", surfaces=(ToolSurface.CHAT,)),
                _make_tool("both", surfaces=(ToolSurface.CHAT, ToolSurface.ACTION)),
            ]
        )
        chat_entries = build_tool_catalog(surface=ToolSurface.CHAT)
        names = {e.name for e in chat_entries}
        assert names == {"chat_only", "both"}

    def test_unresolvable_origin_module_yields_empty_source_file(
        self, fake_registry: list[RegisteredTool]
    ) -> None:
        # A tool that registered but whose origin module no longer imports
        # cleanly (e.g. partial uninstall) must still surface in the catalog.
        fake_registry.append(_make_tool("orphan", origin_module="not.a.real.module"))
        entries = build_tool_catalog()
        assert len(entries) == 1
        assert entries[0].source_file == ""

    def test_origin_module_outside_repo_returns_absolute_source_path(
        self, fake_registry: list[RegisteredTool], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = Path("/virtual/site-packages/some_pkg/plugin.py")
        fake_mod = SimpleNamespace(__file__=str(outside))

        monkeypatch.setattr(tool_catalog.importlib, "import_module", lambda _: fake_mod)
        fake_registry.append(_make_tool("ext_tool", origin_module="some_pkg.plugin"))
        entries = build_tool_catalog()
        assert len(entries) == 1
        assert entries[0].source_file == outside.as_posix()


class TestFormatToolCatalogText:
    def test_returns_empty_string_for_empty_catalog(self) -> None:
        assert format_tool_catalog_text([]) == ""

    def test_groups_by_surface_with_chat_first(self) -> None:
        entries = [
            ToolCatalogEntry(
                name="alpha",
                surfaces=("action",),
                description="alpha desc",
                source_file="tools/alpha.py",
                input_schema_summary="(no params)",
            ),
            ToolCatalogEntry(
                name="beta",
                surfaces=("chat",),
                description="beta desc",
                source_file="tools/beta.py",
                input_schema_summary="x: string",
            ),
        ]
        text = format_tool_catalog_text(entries)
        # Chat header must precede action header (canonical ordering).
        chat_pos = text.find("## chat")
        action_pos = text.find("## action")
        assert chat_pos != -1 and action_pos != -1
        assert chat_pos < action_pos
        assert "alpha" in text and "beta" in text

    def test_dual_surface_tool_appears_under_each_surface(self) -> None:
        entries = [
            ToolCatalogEntry(
                name="dual",
                surfaces=("chat", "action"),
                description="dual desc",
                source_file="tools/dual.py",
                input_schema_summary="(no params)",
            )
        ]
        text = format_tool_catalog_text(entries)
        # Dual-surface tools surface in BOTH groups so the user can tell which
        # tools each surface can reach.
        assert text.count("**dual**") == 2
        assert "## chat (1 tool)" in text
        assert "## action (1 tool)" in text

    def test_omits_source_line_when_source_file_unknown(self) -> None:
        entries = [
            ToolCatalogEntry(
                name="orphan",
                surfaces=("chat",),
                description="orphan desc",
                source_file="",
                input_schema_summary="(no params)",
            )
        ]
        text = format_tool_catalog_text(entries)
        assert "**orphan**" in text
        assert "source:" not in text


class TestListToolsSlashCommand:
    """``/tools list`` reaches the catalog and prints non-empty output."""

    def _capture(self) -> tuple[Console, io.StringIO]:
        buf = io.StringIO()
        return Console(file=buf, force_terminal=False, highlight=False), buf

    def test_list_tools_prints_grouped_catalog(self) -> None:
        console, buf = self._capture()
        session = Session()
        # Stub the catalog so the test stays decoupled from registry contents.
        fake = [
            ToolCatalogEntry(
                name="search_github",
                surfaces=("chat", "action"),
                description="Search GitHub code.",
                source_file="tools/search_github.py",
                input_schema_summary="query: string",
            )
        ]
        with patch(
            "surfaces.interactive_shell.command_registry.tools_cmds.build_tool_catalog",
            return_value=fake,
        ):
            assert _cmd_tools(session, console, ["list"]) is True
        out = buf.getvalue()
        assert "search_github" in out
        assert "action" in out
        assert "chat" in out
        assert "Search GitHub code." in out

    def test_bare_tools_command_prints_full_registered_catalog(self) -> None:
        console, buf = self._capture()
        session = Session()
        fake = [
            ToolCatalogEntry(
                name="telegram_send_message",
                surfaces=("chat", "action"),
                description="Send a Telegram message.",
                source_file="tools/telegram_send_message_tool/tool.py",
                input_schema_summary="message: string",
            )
        ]
        with patch(
            "surfaces.interactive_shell.command_registry.tools_cmds.build_tool_catalog",
            return_value=fake,
        ) as catalog:
            assert dispatch_slash("/tools", session, console) is True
        catalog.assert_called_once_with()
        out = buf.getvalue()
        assert "telegram_send_message" in out
        assert "action" in out
        assert "chat" in out

    def test_interactive_list_opens_browser(self) -> None:
        console, buf = self._capture()
        entries = [
            ToolCatalogEntry("first", ("chat",), "First description", "", "query: string"),
            ToolCatalogEntry(
                "second", ("chat", "action"), "Second [bold]description[/bold]", "", "id: integer"
            ),
        ]
        with (
            patch(
                "surfaces.interactive_shell.command_registry.tools_cmds.build_tool_catalog",
                return_value=entries,
            ),
            patch(
                "surfaces.interactive_shell.command_registry.tools_cmds.repl_tty_interactive",
                return_value=True,
            ),
            patch("surfaces.interactive_shell.command_registry.tools_cmds.browse_tools") as browser,
        ):
            assert _cmd_tools(Session(), console, ["list"]) is True
        browser.assert_called_once_with(entries)
        assert buf.getvalue() == ""

    def test_live_catalog_includes_telegram_send_message(self) -> None:
        names = {entry.name for entry in build_tool_catalog()}
        assert "telegram_send_message" in names

    def test_list_tools_disables_markup_for_plain_catalog_text(self) -> None:
        console, buf = self._capture()
        session = Session()
        fake = [
            ToolCatalogEntry(
                name="risky_tool",
                surfaces=("chat",),
                description="Payload [bold]injection[/bold] attempt",
                source_file="tools/risky.py",
                input_schema_summary="x: string",
            )
        ]
        with patch(
            "surfaces.interactive_shell.command_registry.tools_cmds.build_tool_catalog",
            return_value=fake,
        ):
            assert _cmd_tools(session, console, ["list"]) is True
        out = buf.getvalue()
        assert "[bold]injection[/bold]" in out

    def test_list_tools_handles_empty_registry(self) -> None:
        console, buf = self._capture()
        session = Session()
        with patch(
            "surfaces.interactive_shell.command_registry.tools_cmds.build_tool_catalog",
            return_value=[],
        ):
            assert _cmd_tools(session, console, ["list"]) is True
        assert "no tools registered" in buf.getvalue()

    def test_tools_first_args_advertise_list_for_tab_completion(self) -> None:
        names = {arg for arg, _hint in _TOOLS_FIRST_ARGS}
        assert "list" in names
