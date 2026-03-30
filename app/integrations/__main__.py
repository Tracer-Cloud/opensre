"""python -m app.integrations <command> [service] [--send-slack-test]

Commands: setup, list, show, remove, verify
Services: aws, datadog, grafana, github, opensearch, rds, sentry, tracer, slack (verify only)
"""

import sys

from dotenv import load_dotenv

from app.integrations.cli import (
    SUPPORTED,
    cmd_list,
    cmd_remove,
    cmd_setup,
    cmd_show,
    cmd_verify,
)


def main() -> None:
    load_dotenv(override=False)
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print(f"  Supported services: {SUPPORTED}\n")
        print("  Verify services: grafana, datadog, aws, slack, tracer, github, sentry\n")
        return

    cmd = args[0]
    option_args = {arg for arg in args[1:] if arg.startswith("--")}
    positional_args = [arg for arg in args[1:] if not arg.startswith("--")]
    svc = positional_args[0].lower() if positional_args else None

    commands = {
        "setup": cmd_setup,
        "list": lambda _: cmd_list(),
        "show": cmd_show,
        "remove": cmd_remove,
        "verify": lambda service: cmd_verify(
            service,
            send_slack_test="--send-slack-test" in option_args,
        ),
    }
    if cmd not in commands:
        print(f"  Unknown command '{cmd}'. Try: {', '.join(commands)}", file=sys.stderr)
        sys.exit(1)

    commands[cmd](svc)


if __name__ == "__main__":
    main()
