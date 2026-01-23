"""
Generic CLI for the incident resolution agent.

Reads a Grafana alert payload from --input file or stdin and runs
the investigation graph, outputting JSON results to stdout.

For the demo with Rich console output, use: python examples/run_demo.py
"""

# Initialize runtime FIRST, before any other imports
from config import init_runtime
init_runtime()

import argparse
import json
import sys
from langsmith import traceable

from src.models.alert import GrafanaAlertPayload, normalize_grafana_alert
from src.agent.graph import run_investigation


@traceable
def run(alert: GrafanaAlertPayload) -> dict:
    """
    Run the incident resolution agent on a Grafana alert.

    Args:
        alert: The Grafana alert payload to investigate.

    Returns:
        The final investigation state containing:
            - slack_message: str
            - problem_md: str
            - root_cause: str
            - confidence: float
    """
    normalized = normalize_grafana_alert(alert)

    final_state = run_investigation(
        alert_name=normalized.alert_name,
        affected_table=normalized.affected_table or "events_fact",
        severity=normalized.severity,
    )

    return final_state


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Run incident resolution agent on a Grafana alert payload."
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        help="Path to JSON file containing Grafana alert payload. Use - for stdin."
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Path to output JSON file. Defaults to stdout."
    )

    args = parser.parse_args()

    # Read input
    if args.input == "-" or args.input is None:
        # Read from stdin
        data = json.load(sys.stdin)
    else:
        with open(args.input) as f:
            data = json.load(f)

    alert = GrafanaAlertPayload(**data)

    # Run investigation
    result = run(alert)

    # Output result
    output_data = {
        "slack_message": result["slack_message"],
        "problem_md": result["problem_md"],
        "root_cause": result["root_cause"],
        "confidence": result["confidence"],
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
    else:
        json.dump(output_data, f=sys.stdout, indent=2)
        print()  # newline at end


if __name__ == "__main__":
    main()
