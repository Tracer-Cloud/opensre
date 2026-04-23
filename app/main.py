"""CLI entry point for the incident resolution agent."""

from dotenv import load_dotenv

load_dotenv(override=False)

from app.cli import parse_args, write_json  # noqa: E402
from app.cli.alert_templates import build_alert_template  # noqa: E402
from app.cli.errors import OpenSREError  # noqa: E402
from app.cli.investigate import run_investigation_cli  # noqa: E402
from app.cli.payload import load_payload  # noqa: E402
from app.integrations.required_integrations import MissingIntegrationError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)
    if args.print_template:
        write_json(build_alert_template(args.print_template), args.output)
        return 0

    payload = load_payload(
        input_path=args.input,
        input_json=getattr(args, "input_json", None),
        interactive=getattr(args, "interactive", False),
    )
    try:
        result = run_investigation_cli(
            raw_alert=payload,
            alert_name=getattr(args, "alert_name", None),
            pipeline_name=getattr(args, "pipeline_name", None),
            severity=getattr(args, "severity", None),
        )
    except MissingIntegrationError as exc:
        raise OpenSREError(str(exc)) from exc
    write_json(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
