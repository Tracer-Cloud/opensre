"""python -m integrations <command> [service] [options]

Commands: setup, list, show, remove, verify
Services: see the supported-service lists in the help output below

Verify options: --send-slack-test
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from dotenv import load_dotenv

from infrastructure.analytics.capture import capture_cli_invoked
from infrastructure.analytics.event_properties import build_cli_invoked_properties
from infrastructure.analytics.provider import capture_first_run_if_needed, shutdown_analytics
from infrastructure.observability.errors.sentry import init_sentry
from infrastructure.terminal.prompt_support import install_questionary_escape_cancel
from integrations.cli import (
    cmd_list,
    cmd_remove,
    cmd_setup,
    cmd_show,
    cmd_verify,
    setup_services,
)
from integrations.verify import SUPPORTED_VERIFY_SERVICES

_ENTRYPOINT = "python -m integrations"

CommandHandler = Callable[[str | None, set[str]], None]


def _run_list(_svc: str | None, _options: set[str]) -> None:
    cmd_list()


def _run_show(svc: str | None, _options: set[str]) -> None:
    cmd_show(svc)


def _run_remove(svc: str | None, _options: set[str]) -> None:
    cmd_remove(svc)


def _run_setup(svc: str | None, _options: set[str]) -> None:
    resolved_service = cmd_setup(svc)
    if resolved_service in SUPPORTED_VERIFY_SERVICES:
        print(f"  Verifying {resolved_service}...\n")
        raise SystemExit(cmd_verify(resolved_service))


def _run_verify(svc: str | None, options: set[str]) -> None:
    raise SystemExit(
        cmd_verify(
            svc,
            send_slack_test="--send-slack-test" in options,
        )
    )


# Single source of truth for accepted commands — unknown-command help and
# dispatch stay in lockstep without a second if-chain.
_COMMANDS: dict[str, CommandHandler] = {
    "setup": _run_setup,
    "list": _run_list,
    "show": _run_show,
    "remove": _run_remove,
    "verify": _run_verify,
}


def _print_help() -> None:
    print(__doc__)
    print(f"  Supported services: {', '.join(setup_services())}\n")
    print(f"  Verify services: {', '.join(SUPPORTED_VERIFY_SERVICES)}\n")


def _capture_invocation(command: str) -> None:
    capture_first_run_if_needed()
    capture_cli_invoked(
        build_cli_invoked_properties(
            entrypoint=_ENTRYPOINT,
            command_parts=[command],
        ),
        ["integrations", command],
    )


def main() -> None:
    load_dotenv(override=False)
    init_sentry(entrypoint="integrations")
    install_questionary_escape_cancel()
    args = sys.argv[1:]

    try:
        if not args or args[0] in ("-h", "--help"):
            _print_help()
            return

        cmd = args[0]
        handler = _COMMANDS.get(cmd)
        if handler is None:
            known = ", ".join(_COMMANDS)
            print(
                f"  Unknown command '{cmd}'. Try: {known}",
                file=sys.stderr,
            )
            raise SystemExit(1)

        option_args = {arg for arg in args[1:] if arg.startswith("--")}
        positional_args = [arg for arg in args[1:] if not arg.startswith("--")]
        svc = positional_args[0].lower() if positional_args else None

        _capture_invocation(cmd)
        handler(svc, option_args)
    finally:
        shutdown_analytics(flush=False)


if __name__ == "__main__":
    main()
