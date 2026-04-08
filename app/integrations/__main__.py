"""python -m app.integrations <command> [service] [options]

Commands: setup, list, show, remove, verify
Services: aws, coralogix, datadog, github, grafana, honeycomb, mongodb, opensearch,
rds, sentry, slack, tracer, vercel

Verify options: --send-slack-test
"""

import sys

from dotenv import load_dotenv

from app.cli.prompt_support import install_questionary_escape_cancel
from app.integrations.cli import (
    SUPPORTED,
    cmd_list,
    cmd_remove,
    cmd_setup,
    cmd_show,
    cmd_verify,
)
from app.integrations.verify import SUPPORTED_VERIFY_SERVICES


def main() -> None:
    load_dotenv(override=False)
    install_questionary_escape_cancel()
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print(f"  Supported services: {SUPPORTED}\n")
        print(f"  Verify services: {', '.join(SUPPORTED_VERIFY_SERVICES)}\n")
        return

    cmd = args[0]
    option_args = {arg for arg in args[1:] if arg.startswith("--")}
    positional_args = [arg for arg in args[1:] if not arg.startswith("--")]
    svc = positional_args[0].lower() if positional_args else None

    commands = {
        "list": lambda _: cmd_list(),
        "show": cmd_show,
        "remove": cmd_remove,
    }
    if cmd not in commands:
        if cmd == "setup":
            resolved_service = cmd_setup(svc)
            if resolved_service in SUPPORTED_VERIFY_SERVICES:
                print(f"  Verifying {resolved_service}...\n")
                sys.exit(cmd_verify(resolved_service))
            return
        if cmd == "verify":
            sys.exit(
                cmd_verify(
                    svc,
                    send_slack_test="--send-slack-test" in option_args,
                )
            )
        print(f"  Unknown command '{cmd}'. Try: setup, list, show, remove, verify", file=sys.stderr)
        sys.exit(1)

    commands[cmd](svc)


if __name__ == "__main__":
    main()
