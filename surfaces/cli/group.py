"""Root Click group: per-command import and Rich help rendering.

Kept separate from ``surfaces.cli.app`` so the entrypoint stays a thin
wiring module. Command *modules* load on first use of that command.
Root ``opensre --help`` uses the command spec table and must not import
auth, kubernetes, or the agent harness.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, TypeVar, cast, overload

import click

from surfaces.cli.commands.command_specs import (
    COMMAND_SPECS,
    COMMAND_SPECS_BY_NAME,
    load_command,
)

_GetDefault = TypeVar("_GetDefault")


class LazyRichGroup(click.Group):
    """Root CLI group: spec-table help, one command module per invocation."""

    _loaded_commands: dict[str, click.Command]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._loaded_commands = {}
        self.commands = LazyCommandsDict(self, self.commands)

    def list_commands(self, ctx: click.Context) -> list[str]:
        del ctx
        return [spec.name for spec in COMMAND_SPECS]

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        del ctx
        loaded = self._loaded_commands.get(cmd_name)
        if loaded is not None:
            return loaded
        spec = COMMAND_SPECS_BY_NAME.get(cmd_name)
        if spec is None:
            return None
        command = load_command(spec)
        self._loaded_commands[cmd_name] = command
        # Set through ``dict`` so the lazy view's iteration/len overrides are
        # bypassed; ``self.commands`` is a LazyCommandsDict (a dict subclass).
        dict.__setitem__(cast("dict[str, click.Command]", self.commands), cmd_name, command)
        return command

    def format_help(self, ctx: click.Context, _formatter: click.HelpFormatter) -> None:
        assert isinstance(ctx.command, click.Group)
        from surfaces.cli.layout import render_help

        render_help(ctx.command)

    def help_command_rows(self) -> tuple[tuple[str, str], ...]:
        """Visible commands for root help, without importing implementations."""
        from surfaces.cli.commands.command_specs import visible_help_rows

        return visible_help_rows()


class LazyCommandsDict(dict[str, click.Command]):
    """Click command mapping that loads one command module per key."""

    def __init__(self, owner: LazyRichGroup, initial: Mapping[str, click.Command]) -> None:
        super().__init__(initial)
        self._owner = owner

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if super().__contains__(key):
            return True
        return key in COMMAND_SPECS_BY_NAME

    def __iter__(self) -> Iterator[str]:
        seen: set[str] = set()
        for name in super().__iter__():
            seen.add(name)
            yield name
        for spec in COMMAND_SPECS:
            if spec.name not in seen:
                yield spec.name

    def __len__(self) -> int:
        return len(COMMAND_SPECS)

    def __getitem__(self, key: str) -> click.Command:
        command = self._owner.get_command(click.Context(self._owner), key)
        if command is None:
            raise KeyError(key)
        return command

    @overload
    def get(self, key: str, default: None = None, /) -> click.Command | None:
        pass

    @overload
    def get(self, key: str, default: click.Command, /) -> click.Command:
        pass

    @overload
    def get(self, key: str, default: _GetDefault, /) -> click.Command | _GetDefault:
        pass

    def get(self, key: str, default: object = None, /) -> object:
        command = self._owner.get_command(click.Context(self._owner), key)
        if command is None:
            return default
        return command

    def keys(self) -> Any:
        return iter(self)

    def values(self) -> Any:
        return (self[name] for name in self)

    def items(self) -> Any:
        return ((name, self[name]) for name in self)


class ThemeParamType(click.ParamType):
    """Validate theme names without importing terminal UI dependencies at startup."""

    name = "theme"

    def _choices(self) -> tuple[str, ...]:
        from infrastructure.terminal.theme import list_theme_names

        return list_theme_names()

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        normalized = str(value).strip().lower()
        choices = self._choices()
        if normalized in choices:
            return normalized
        return self.fail(
            f"{value!r} is not one of: {', '.join(choices)}.",
            param,
            ctx,
        )
