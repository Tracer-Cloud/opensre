#!/usr/bin/env python3
"""Download OpenRCA-style alert JSON from the Hugging Face OpenSRE dataset (streaming).

Requires: ``pip install 'opensre[opensre-hub]'`` (or dev extra) and Hub auth if the dataset is gated.

The Hub glob yields alerts in dataset order; use ``--index`` to skip to the Nth alert (0-based), or
``--export-dir`` + ``--limit`` to save many alerts as numbered files.

Examples::

  # First alert in prefix (default)
  python scripts/fetch_opensre_hub_alert.py --prefix Bank/query_alerts -o /tmp/a.json

  # Third alert (skip 0,1 then take next)
  python scripts/fetch_opensre_hub_alert.py --prefix Bank/query_alerts --index 2 -o /tmp/third.json

  # First 20 alerts under a directory
  python scripts/fetch_opensre_hub_alert.py --prefix Bank/query_alerts --export-dir ./bank_alerts --limit 20

  # Valid prefixes on tracer-cloud/opensre include:
  #   Bank/query_alerts  Market/cloudbed-1/query_alerts  Market/cloudbed-2/query_alerts
  #   Telecom/query_alerts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch streamed alert(s) from Hub query_alerts/ (single file or batch export)."
    )
    parser.add_argument(
        "--prefix",
        default="Market/cloudbed-1/query_alerts",
        help="Dataset path prefix ending in query_alerts (default: Market/cloudbed-1/query_alerts).",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=0,
        metavar="N",
        help="0-based index into the stream (skip first N alerts, then write the next). Default: 0.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="",
        help="Write one alert JSON here (default: /tmp/opensre-hub-alert.json). Ignored if --export-dir is set.",
    )
    parser.add_argument(
        "--export-dir",
        default="",
        metavar="DIR",
        help="Write multiple alerts as DIR/000.json, DIR/001.json, ... (use with --limit).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="M",
        help="With --export-dir: number of alerts to save (required for batch export).",
    )
    parser.add_argument(
        "--strip-scoring-points",
        action="store_true",
        help="Strip scoring_points from the saved JSON (not recommended for opensre investigate --evaluate).",
    )
    args = parser.parse_args()

    from app.integrations.opensre.hf_remote import stream_opensre_query_alerts

    stream = stream_opensre_query_alerts(
        query_alerts_prefix=args.prefix,
        strip_scoring_points=args.strip_scoring_points,
    )

    if args.export_dir:
        if args.limit <= 0:
            print("--export-dir requires --limit M with M >= 1", file=sys.stderr)
            return 2
        out_dir = Path(args.export_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(args.limit):
            try:
                alert = next(stream)
            except StopIteration:
                print(f"Stream ended after {i} file(s); expected {args.limit}.", file=sys.stderr)
                return 1
            path = out_dir / f"{i:04d}.json"
            path.write_text(json.dumps(alert, indent=2) + "\n", encoding="utf-8")
            print(path)
        return 0

    if args.index < 0:
        print("--index must be >= 0", file=sys.stderr)
        return 2
    for _ in range(args.index):
        try:
            next(stream)
        except StopIteration:
            print(f"Stream has fewer than {args.index + 1} alert(s).", file=sys.stderr)
            return 1
    try:
        alert = next(stream)
    except StopIteration:
        print("No alert at this index.", file=sys.stderr)
        return 1

    out = Path(args.output or "/tmp/opensre-hub-alert.json").expanduser()
    out.write_text(json.dumps(alert, indent=2) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
