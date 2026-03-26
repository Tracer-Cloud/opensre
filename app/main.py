"""CLI entry point for the incident resolution agent."""

from dotenv import load_dotenv

load_dotenv(override=False)

from langsmith import traceable  # noqa: E402

from app.agent.runners import run_investigation  # noqa: E402
from app.alert_templates import build_alert_template  # noqa: E402
from app.cli import parse_args, write_json  # noqa: E402
from app.cli.payload import load_payload  # noqa: E402


@traceable(name="investigation")
def _run(raw_alert: dict) -> dict:
    state = run_investigation("Incident", "unknown", "warning", raw_alert=raw_alert)
    return {
        "slack_message": state["slack_message"],
        "report": state["slack_message"],
        "problem_md": state["problem_md"],
        "root_cause": state["root_cause"],
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.print_template:
        write_json(build_alert_template(args.print_template), args.output)
        return 0

    payload = load_payload(
        input_path=args.input,
        input_json=getattr(args, "input_json", None),
        interactive=getattr(args, "interactive", False),
    )

    result = _run(raw_alert=payload)
    write_json(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
