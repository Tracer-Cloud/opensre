"""python -m app.integrations <command> [service]

Commands: setup, list, show, remove
Services: aws, datadog, grafana, opensearch, rds, tracer
"""

import sys

from app.integrations.cli import SUPPORTED, cmd_list, cmd_remove, cmd_setup, cmd_show


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print(f"  Supported services: {SUPPORTED}\n")
        return

    cmd = args[0]
    svc = args[1].lower() if len(args) > 1 else None

    commands = {"setup": cmd_setup, "list": lambda _: cmd_list(), "show": cmd_show, "remove": cmd_remove}
    if cmd not in commands:
        print(f"  Unknown command '{cmd}'. Try: {', '.join(commands)}", file=sys.stderr)
        sys.exit(1)

    commands[cmd](svc)


if __name__ == "__main__":
    main()
