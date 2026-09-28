"""The ``opensre`` process entrypoint: composes the CLI, the interactive shell and the gateway entry.

The surfaces are peers and do not import each other; this module is the one
place that knows all of them. A bare ``opensre`` on a terminal opens the shell,
``opensre <command>`` runs the CLI, and ``opensre gateway start --foreground``
runs the gateway attached — each through a callable handed to the CLI as its
:class:`~surfaces.cli.host.CliHost`. The console script and the frozen binary
both start here, so neither loses the shell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.repl_config import ReplConfig
    from surfaces.cli.host import AfterBanner, CliHost


def _launch_shell(
    config: ReplConfig,
    resume_session_id: str | None,
    after_banner: AfterBanner,
    capture_shell_rendered: bool = True,
) -> int:
    from surfaces.cli.app import cli
    from surfaces.interactive_shell import run_repl

    return run_repl(
        config=config,
        resume_session_id=resume_session_id,
        cli_command_group=cli,
        after_banner=after_banner,
        capture_shell_rendered=capture_shell_rendered and not resume_session_id,
    )


def _start_gateway_foreground() -> None:
    from surfaces.gateway_entry import start_gateway

    start_gateway()


def _host() -> CliHost:
    from surfaces.cli.host import CliHost

    return CliHost(
        launch_shell=_launch_shell,
        start_gateway_foreground=_start_gateway_foreground,
    )


def main(argv: list[str] | None = None) -> int:
    """Run ``opensre`` with the shell and the gateway entry wired in; return the exit status."""
    from surfaces.cli.app import main as cli_main

    return cli_main(argv, host=_host())


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
