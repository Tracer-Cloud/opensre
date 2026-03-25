"""python -m app.cli <command>

Commands:
  onboard  Run the local LLM onboarding flow
"""

from __future__ import annotations

import sys

from app.cli.wizard import run_wizard


def main(argv: list[str] | None = None) -> int:
    """Dispatch CLI subcommands."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    command = args[0]
    if command == "onboard":
        return run_wizard(args[1:])

    print(f"Unknown command '{command}'. Try: onboard", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
