"""What the process entrypoint hands the CLI: how to open the shell and how to run the gateway attached.

The CLI never imports the interactive shell or the gateway composition; the
entrypoint that composes the surfaces passes these callables in through the
click context, and the CLI prints the landing page or an error when a host did
not provide them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

import click

if TYPE_CHECKING:
    from config.repl_config import ReplConfig
else:
    ReplConfig = Any

#: Work the shell runs once its banner is on screen (``None``: nothing deferred).
AfterBanner = Callable[[], None] | None


class ShellLauncher(Protocol):
    """``(config, resume_session_id, after_banner, capture_shell_rendered=True)``."""

    def __call__(
        self,
        config: ReplConfig,
        resume_session_id: str | None,
        after_banner: AfterBanner,
        capture_shell_rendered: bool = True,
    ) -> int: ...


#: Runs the gateway attached to this terminal until it stops.
GatewayForegroundRunner = Callable[[], None]

CLI_HOST_CONTEXT_KEY: Final[str] = "cli_host"


@dataclass(frozen=True)
class CliHost:
    """Capabilities the surrounding process gives the CLI; ``None`` means unavailable."""

    launch_shell: ShellLauncher | None = None
    start_gateway_foreground: GatewayForegroundRunner | None = None


def cli_host(ctx: click.Context) -> CliHost:
    """The host recorded on the root context, or an empty one."""
    root_obj = ctx.find_root().obj
    host = root_obj.get(CLI_HOST_CONTEXT_KEY) if isinstance(root_obj, dict) else None
    return host if isinstance(host, CliHost) else CliHost()


__all__ = [
    "CLI_HOST_CONTEXT_KEY",
    "AfterBanner",
    "CliHost",
    "GatewayForegroundRunner",
    "ShellLauncher",
    "cli_host",
]
