"""Static tool descriptor index — metadata without importing executors (#3686).

The registry's ``_load_registry_snapshot`` imports every vendor tool module (and
its SDK) to read each tool's metadata, which costs ~1.5s at startup. This module
reads the same metadata (name, surfaces, source, display name, and the dotted
module to import for execution) by AST-scanning the tool source files — no
executor imports. Surface-scoped loads and lazy execution build on it.

Tools whose metadata cannot be read statically (re-exported ``RegisteredTool``
objects, runtime-registered packages) are not in the index; the registry imports
those the existing way. ``tests/tools/test_registry_index.py`` pins that gap.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from config.constants.paths import REPO_ROOT
from core.domain.types.tools import ToolSurface
from core.tool import normalize_surfaces
from tools.registry_discovery import INTEGRATION_TOOL_PACKAGES

_SKIP_FILE_SUFFIXES = ("_test.py",)
_TOOL_DECORATOR_NAME = "tool"


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Cheap tool metadata read without importing the executor module."""

    name: str
    surfaces: tuple[ToolSurface, ...]
    source: str | None
    display_name: str | None
    module: str


# Tools the AST scan cannot read: built via ``RegisteredTool(...)`` construction
# (the interactive-shell action tools) or with a non-literal ``surfaces=`` constant
# (slack/telegram). Pinned here so the index is complete; ``test_registry_index``
# asserts every entry matches the imported registry, so drift fails loudly.
def _fallback_descriptors() -> tuple[ToolDescriptor, ...]:
    return (
        ToolDescriptor(
            "ask_user_choice",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.ask_choice",
        ),
        ToolDescriptor(
            "update_plan",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.update_plan",
        ),
        ToolDescriptor(
            "cli_exec",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.cli_command",
        ),
        ToolDescriptor(
            "code_implement",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.implementation",
        ),
        ToolDescriptor(
            "fix_sentry_issue_start",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.sentry_fix",
        ),
        ToolDescriptor(
            "llm_set_provider",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.llm_provider",
        ),
        ToolDescriptor(
            "shell_run",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.shell",
        ),
        ToolDescriptor(
            "propose_scheduled_delivery",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.propose_scheduled_delivery",
        ),
        ToolDescriptor(
            "session_goal_complete",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.session_goal",
        ),
        ToolDescriptor(
            "session_goal_set",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.session_goal",
        ),
        ToolDescriptor(
            "skill_view",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.skill_view",
        ),
        ToolDescriptor(
            "slack_add_reaction",
            (ToolSurface.CHAT, ToolSurface.ACTION),
            "slack",
            None,
            "integrations.slack.tools.slack_add_reaction_tool.tool",
        ),
        ToolDescriptor(
            "slack_join_channel",
            (ToolSurface.CHAT, ToolSurface.ACTION),
            "slack",
            None,
            "integrations.slack.tools.slack_join_channel_tool.tool",
        ),
        ToolDescriptor(
            "slack_list_team_members",
            (ToolSurface.CHAT, ToolSurface.ACTION),
            "slack",
            None,
            "integrations.slack.tools.slack_list_members_tool.tool",
        ),
        ToolDescriptor(
            "slack_read_list",
            (ToolSurface.CHAT, ToolSurface.ACTION),
            "slack",
            None,
            "integrations.slack.tools.slack_read_list_tool.tool",
        ),
        ToolDescriptor(
            "slack_read_messages",
            (ToolSurface.CHAT, ToolSurface.ACTION),
            "slack",
            None,
            "integrations.slack.tools.slack_read_messages_tool.tool",
        ),
        ToolDescriptor(
            "slack_reply_message",
            (ToolSurface.ACTION,),
            "slack",
            None,
            "integrations.slack.tools.slack_reply_message_tool.tool",
        ),
        ToolDescriptor(
            "slack_search_messages",
            (ToolSurface.CHAT, ToolSurface.ACTION),
            "slack",
            None,
            "integrations.slack.tools.slack_search_messages_tool.tool",
        ),
        ToolDescriptor(
            "slack_send_message",
            (ToolSurface.ACTION,),
            "slack",
            None,
            "integrations.slack.tools.slack_send_message_tool.tool",
        ),
        ToolDescriptor(
            "slash_invoke",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.slash",
        ),
        ToolDescriptor(
            "task_cancel",
            (ToolSurface.ACTION,),
            "interactive_shell",
            None,
            "tools.interactive_shell.actions.task_cancel",
        ),
        ToolDescriptor(
            "rocketchat_send_message",
            (ToolSurface.ACTION,),
            "rocketchat",
            None,
            "integrations.rocketchat.tools.rocketchat_send_message_tool.tool",
        ),
        ToolDescriptor(
            "buzz_send_message",
            (ToolSurface.ACTION,),
            "buzz",
            None,
            "integrations.buzz.tools.buzz_send_message_tool.tool",
        ),
        ToolDescriptor(
            "telegram_send_message",
            (ToolSurface.ACTION,),
            "telegram",
            None,
            "integrations.telegram.tools.telegram_send_message_tool.tool",
        ),
    )


def _string_constant(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ToolSurface"
    ):
        return node.attr.lower()
    return None


def _string_tuple(node: ast.expr | None) -> tuple[str, ...] | None:
    if isinstance(node, ast.Tuple | ast.List):
        values = [_string_constant(el) for el in node.elts]
        if all(v is not None for v in values):
            return tuple(v for v in values if v is not None)
    return None


def _keyword(call: ast.Call, key: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == key:
            return kw.value
    return None


def _is_tool_decorator(dec: ast.expr) -> bool:
    call = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(call, ast.Name):
        return call.id == _TOOL_DECORATOR_NAME
    if isinstance(call, ast.Attribute):
        return call.attr == _TOOL_DECORATOR_NAME
    return False


def _is_base_tool_class(cls: ast.ClassDef) -> bool:
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id == "BaseTool":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseTool":
            return True
    return False


def _class_attr(cls: ast.ClassDef, key: str) -> ast.expr | None:
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == key and node.value is not None:
                return node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == key:
                    return node.value
    return None


def _module_dotted(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _descriptors_in_file(path: Path) -> list[ToolDescriptor]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    # Skip the AST parse for files that can't declare a tool. The
    # ``index == registry`` contract test guarantees this never drops a tool.
    if "@tool" not in text and "BaseTool" not in text:
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    module = _module_dotted(path)
    descriptors: list[ToolDescriptor] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and _is_tool_decorator(dec):
                    name = _string_constant(_keyword(dec, "name")) or node.name
                    descriptors.append(
                        ToolDescriptor(
                            name=name,
                            surfaces=normalize_surfaces(_string_tuple(_keyword(dec, "surfaces"))),
                            source=_string_constant(_keyword(dec, "source")),
                            display_name=_string_constant(_keyword(dec, "display_name")),
                            module=module,
                        )
                    )
        elif isinstance(node, ast.ClassDef) and _is_base_tool_class(node):
            class_name = _string_constant(_class_attr(node, "name"))
            if class_name is None:
                continue
            descriptors.append(
                ToolDescriptor(
                    name=class_name,
                    surfaces=normalize_surfaces(_string_tuple(_class_attr(node, "surfaces"))),
                    source=_string_constant(_class_attr(node, "source")),
                    display_name=_string_constant(_class_attr(node, "display_name")),
                    module=module,
                )
            )
    return descriptors


def _scan_roots() -> list[Path]:
    roots = [REPO_ROOT / "tools"]
    for dotted in INTEGRATION_TOOL_PACKAGES:
        directory = REPO_ROOT / Path(*dotted.split("."))
        if directory.is_dir():
            roots.append(directory)
    return roots


#: Where a frozen build ships the index, relative to the bundle root
#: (``sys._MEIPASS``). ``opensre.spec`` writes it at build time.
BAKED_INDEX_RELATIVE_PATH = Path("tools") / "descriptor_index.json"


def _baked_index_path() -> Path | None:
    """Path of the build-time index inside a frozen bundle, else ``None``."""
    if not getattr(sys, "frozen", False):
        return None
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is None:
        return None
    return Path(bundle_root) / BAKED_INDEX_RELATIVE_PATH


def baked_index_available() -> bool:
    """True when this frozen build shipped a descriptor index."""
    path = _baked_index_path()
    return path is not None and path.is_file()


def _descriptor_to_json(descriptor: ToolDescriptor) -> dict[str, Any]:
    return {
        "name": descriptor.name,
        "surfaces": [surface.value for surface in descriptor.surfaces],
        "source": descriptor.source,
        "display_name": descriptor.display_name,
        "module": descriptor.module,
    }


def _descriptor_from_json(raw: dict[str, Any]) -> ToolDescriptor:
    return ToolDescriptor(
        name=str(raw["name"]),
        surfaces=tuple(ToolSurface(surface) for surface in raw["surfaces"]),
        source=raw.get("source"),
        display_name=raw.get("display_name"),
        module=str(raw["module"]),
    )


def dump_descriptor_index(path: Path) -> None:
    """Write the source-scanned index as JSON for a frozen build to load.

    A PyInstaller bundle carries bytecode only, so the AST scan finds nothing
    there; the build runs this against the checkout and ships the result.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [_descriptor_to_json(d) for d in build_descriptor_index().values()]
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _load_baked_index(path: Path) -> dict[str, ToolDescriptor]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {d.name: d for d in (_descriptor_from_json(entry) for entry in entries)}


@lru_cache(maxsize=1)
def build_descriptor_index() -> dict[str, ToolDescriptor]:
    """Map tool name -> descriptor, first definition wins (registry order).

    A frozen build loads the index baked at build time instead of scanning
    source (which it does not carry).
    """
    baked = _baked_index_path()
    if baked is not None and baked.is_file():
        return _load_baked_index(baked)
    index: dict[str, ToolDescriptor] = {}
    for root in _scan_roots():
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or path.name.endswith(_SKIP_FILE_SUFFIXES):
                continue
            for descriptor in _descriptors_in_file(path):
                index.setdefault(descriptor.name, descriptor)
    # Pinned descriptors override (slack/telegram surfaces) or add (action tools).
    for descriptor in _fallback_descriptors():
        index[descriptor.name] = descriptor
    return index


def clear_descriptor_index_cache() -> None:
    build_descriptor_index.cache_clear()


__all__ = [
    "BAKED_INDEX_RELATIVE_PATH",
    "ToolDescriptor",
    "baked_index_available",
    "build_descriptor_index",
    "clear_descriptor_index_cache",
    "dump_descriptor_index",
]
