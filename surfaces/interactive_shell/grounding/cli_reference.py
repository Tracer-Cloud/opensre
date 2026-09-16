"""Reference text for OpenSRE interactive-shell CLI answers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import click

from core.agent_harness.spi.defaults import DefaultPromptContextProvider
from core.agent_harness.spi.grounding import (
    CacheStats,
    GroundingSource,
    log_grounding_cache_diagnostics,
)

_logger = logging.getLogger(__name__)

_MAX_REFERENCE_CHARS = 28_000

# Heuristic: truncated or failed reference output must not be cached or the
# assistant would keep an empty reference for the whole process.
_MIN_CACHEABLE_CLI_REFERENCE_CHARS = 80
_CLI_REFERENCE_SENTINEL = "=== opensre --help ==="
SlashCommandProvider = Callable[[], Mapping[str, Any]]
CommandGroupProvider = Callable[[], click.Command | None]


def _is_cacheable_cli_reference(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < _MIN_CACHEABLE_CLI_REFERENCE_CHARS:
        return False
    return _CLI_REFERENCE_SENTINEL in text


def _slash_command_names(provider: SlashCommandProvider | None) -> list[str]:
    if provider is None:
        return []
    return sorted(provider().keys())


def _current_cli_signature(
    cli_group: click.Command | None,
    slash_provider: SlashCommandProvider | None = None,
) -> str:
    """Stable signature of the CLI command surface and interactive slash commands.

    Bumps cache when subcommands change, slash-command metadata changes, or the
    installed package version changes.
    """
    from config.version import get_opensre_version

    if isinstance(cli_group, click.Group):
        cmd_names = ",".join(sorted(cli_group.commands.keys()))
    else:
        cmd_names = ""
    slash_names = ",".join(_slash_command_names(slash_provider))
    return f"opensre={get_opensre_version()}|commands={cmd_names}|slash={slash_names}"


def _format_param(param: click.Parameter) -> str:
    """Return a compact, side-effect-free description of a Click parameter."""
    if isinstance(param, click.Option):
        names = ", ".join((*param.opts, *param.secondary_opts))
        if not names:
            names = param.name or "(option)"
        value_hint = ""
        if not param.is_flag and not param.count:
            value_hint = " " + (param.metavar or param.name or "VALUE").upper()
        default = ""
        if param.show_default and param.default not in (None, "", ()):
            default = f" [default: {param.default}]"
        help_text = (param.help or "").strip()
        return f"  {names}{value_hint} - {help_text}{default}".rstrip()

    if isinstance(param, click.Argument):
        name = (param.name or "ARG").upper()
        required = "" if param.required else " (optional)"
        return f"  {name}{required}"

    return f"  {param.name or type(param).__name__}"


def _format_command_reference(
    command: click.Command,
    *,
    path: str,
    include_subcommands: bool = True,
) -> str:
    """Render Click command metadata without invoking help callbacks.

    ``click.Command.get_help()`` eventually calls ``format_help()``. The root
    OpenSRE group overrides that path to render via Rich's live console, which
    can leak into the interactive terminal when the assistant builds grounding
    context. This renderer inspects command objects directly instead.
    """
    lines = [f"Usage: {path} [OPTIONS]"]
    if isinstance(command, click.Group):
        lines[0] += " COMMAND [ARGS]..."
    elif command.params:
        arg_names = [
            (param.name or "ARG").upper()
            for param in command.params
            if isinstance(param, click.Argument)
        ]
        if arg_names:
            lines[0] += " " + " ".join(arg_names)

    help_text = (command.help or command.short_help or "").strip()
    if help_text:
        lines.extend(["", help_text])

    params = [param for param in command.params if not getattr(param, "hidden", False)]
    if params:
        lines.extend(["", "Options/Arguments:"])
        lines.extend(_format_param(param) for param in params)

    if include_subcommands and isinstance(command, click.Group):
        command_rows: list[tuple[str, str]] = []
        with click.Context(command, info_name=path.rsplit(" ", 1)[-1]) as ctx:
            for name in command.list_commands(ctx):
                subcommand = command.get_command(ctx, name)
                if subcommand is None or subcommand.hidden:
                    continue
                command_rows.append((name, subcommand.get_short_help_str(limit=160)))
        if command_rows:
            lines.extend(["", "Commands:"])
            lines.extend(f"  {name} - {summary}".rstrip() for name, summary in command_rows)

    return "\n".join(lines).rstrip() + "\n"


def _build_cli_reference_text_uncached(
    cli_group: click.Command | None,
    slash_provider: SlashCommandProvider | None = None,
) -> str:
    """Build a side-effect-free CLI reference without invoking Click commands."""
    parts: list[str] = []

    if cli_group is None:
        parts.append("=== opensre --help ===\n")
        parts.append(
            "The CLI command surface is not available in this runtime.\n"
            "Launch the OpenSRE CLI (`opensre`) for command-level help.\n"
        )
    else:
        parts.append("=== opensre --help ===\n")
        parts.append(_format_command_reference(cli_group, path="opensre"))

        if isinstance(cli_group, click.Group):
            with click.Context(cli_group, info_name="opensre") as ctx:
                for name in sorted(cli_group.commands.keys()):
                    command = cli_group.get_command(ctx, name)
                    if command is None or command.hidden:
                        continue
                    parts.append(f"\n=== opensre {name} --help ===\n")
                    parts.append(_format_command_reference(command, path=f"opensre {name}"))

    parts.append("\n=== Interactive-shell slash commands ===\n")
    parts.append(_interactive_shell_slash_hints(slash_provider))

    text = "".join(parts)
    if len(text) > _MAX_REFERENCE_CHARS:
        return text[:_MAX_REFERENCE_CHARS] + "\n\n[... reference truncated ...]\n"
    return text


def _interactive_shell_slash_hints(provider: SlashCommandProvider | None = None) -> str:
    lines = [
        "In the interactive shell, chat with the terminal assistant to run tools "
        + "or get CLI help.",
        "Every nonempty shell command runs through the platform command shell.",
        "This includes plain commands, pipes, redirects, substitutions, and compact operators.",
        "A leading ! is optional.",
        "Slash commands:",
        "",
    ]
    if provider is not None:
        for cmd in provider().values():
            name = getattr(cmd, "name", "")
            description = getattr(cmd, "description", "")
            if name:
                lines.append(f"  {name} - {description}".rstrip())
    else:
        lines.append("  (slash command registry is supplied by the interactive shell runtime)")
    lines.extend(
        [
            "",
            "Launch the interactive shell: `opensre` (requires a TTY).",
        ]
    )
    return "\n".join(lines)


class CliReference:
    """Session-scoped cache for assembled CLI help reference text.

    Holds its cache as instance state so each REPL session (via
    :func:`session_cli_reference`) owns an isolated cache with no module-level
    mutable globals.
    """

    name = "cli"

    def __init__(self) -> None:
        self._signature: str | None = None
        self._text: str | None = None
        self._created_at_monotonic: float = 0.0
        self._hits: int = 0
        self._misses: int = 0
        self._slash_commands_provider: SlashCommandProvider | None = None
        self._command_group_provider: CommandGroupProvider | None = None

    def set_slash_commands_provider(self, provider: SlashCommandProvider | None) -> None:
        """Set the shell-owned slash command source used for grounding text."""
        self._slash_commands_provider = provider
        self.invalidate()

    def set_command_group_provider(self, provider: CommandGroupProvider | None) -> None:
        """Bind a surface-owned callable that returns the root Click command group."""
        self._command_group_provider = provider
        self.invalidate()

    def _resolve_command_group(self) -> click.Command | None:
        provider = self._command_group_provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception:
            _logger.debug("Command group provider raised; skipping CLI reference", exc_info=True)
            return None

    def build_text(self) -> str:
        """Assemble ``opensre`` and subcommand ``--help`` output for LLM grounding.

        Cached on this instance while the command registry signature matches.
        """
        slash_provider = self._slash_commands_provider
        cli_group = self._resolve_command_group()
        sig = _current_cli_signature(cli_group, slash_provider)
        if self._text is not None and self._signature == sig:
            self._hits += 1
            return self._text

        self._misses += 1
        text = _build_cli_reference_text_uncached(cli_group, slash_provider)
        if cli_group is not None and _is_cacheable_cli_reference(text):
            self._signature = sig
            self._text = text
            self._created_at_monotonic = time.monotonic()
        else:
            self._signature = None
            self._text = None
            self._created_at_monotonic = 0.0
            _logger.warning(
                "CLI reference build produced non-cacheable output (%d chars); skipping cache",
                len(text),
            )
        return text

    def invalidate(self) -> None:
        """Drop cached CLI reference text (for tests or forced refresh)."""
        self._signature = None
        self._text = None
        self._created_at_monotonic = 0.0
        self._hits = 0
        self._misses = 0

    def stats(self) -> CacheStats:
        """Debug counters for grounding cache hit/miss and last signature."""
        return CacheStats(
            name=self.name,
            hits=self._hits,
            misses=self._misses,
            cached=self._text is not None,
            signature=self._signature,
            created_at_monotonic=self._created_at_monotonic,
        )

    def as_grounding_source(self) -> GroundingSource:
        return GroundingSource(name=self.name, stats_fn=self.stats)


def _slash_commands() -> Mapping[str, object]:
    from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

    return SLASH_COMMANDS


_SESSION_CLI_REFERENCE_ATTR = "_shell_cli_reference"


def _session_cli_command_group(session: Any) -> click.Command | None:
    """The Click group the entrypoint handed the shell, if any."""
    terminal = getattr(session, "terminal", None)
    group = getattr(terminal, "cli_command_group", None)
    return group if isinstance(group, click.Command) else None


def session_cli_reference(session: Any) -> CliReference:
    """Return the session-scoped CLI catalog cache, creating it on first use."""
    cached = getattr(session, _SESSION_CLI_REFERENCE_ATTR, None)
    if isinstance(cached, CliReference):
        return cached
    cli = CliReference()
    cli.set_command_group_provider(lambda: _session_cli_command_group(session))
    cli.set_slash_commands_provider(_slash_commands)
    setattr(session, _SESSION_CLI_REFERENCE_ATTR, cli)
    return cli


def shell_prompt_context_provider(session: Any) -> ShellPromptContextProvider:
    """Build a prompt provider that reuses the session's CLI catalog cache."""
    return ShellPromptContextProvider(session)


class ShellPromptContextProvider(DefaultPromptContextProvider):
    """The shell's prompt provider: the default grounding plus the CLI catalog.

    Overrides only what differs from
    :class:`~core.agent_harness.prompts.grounding.DefaultPromptContextProvider`:
    the surface, the CLI reference (the entrypoint's Click group + slash commands, cached per
    session), and cache diagnostics that include that catalog.
    """

    def __init__(self, session: Any) -> None:
        super().__init__(session, surface="interactive_shell")
        self._cli = session_cli_reference(session)

    def bind_session(self, session: Any) -> None:
        super().bind_session(session)
        self._cli = session_cli_reference(session)

    def cli_reference(self) -> str:
        return self._cli.build_text()

    def log_diagnostics(self, reason: str) -> None:
        super().log_diagnostics(reason)
        log_grounding_cache_diagnostics([self._cli.as_grounding_source()], reason)


__all__ = [
    "CliReference",
    "CommandGroupProvider",
    "ShellPromptContextProvider",
    "SlashCommandProvider",
    "session_cli_reference",
    "shell_prompt_context_provider",
]
