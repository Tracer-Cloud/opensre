#!/usr/bin/env python3
"""Query OpenSRE / OpenRCA telemetry CSVs locally — no Grafana server.

``opensre investigate`` runs the same reads at the start of the **Gathering evidence**
step (``node_investigate``) when telemetry paths resolve (local clone or Hub).

Examples::

  # Local clone root + relative path (same as alert annotations)
  export OPENSRE_DATASET_ROOT=~/data/w3joe-opensre
  python scripts/query_opensre_telemetry.py \\
    --relative Market/cloudbed-1/telemetry/2022_03_20 list

  # Materialize only that folder from Hugging Face (needs: pip install huggingface_hub)
  export OPENSRE_HF_DATASET_ID=tracer-cloud/opensre
  python scripts/query_opensre_telemetry.py \\
    --relative Market/cloudbed-1/telemetry/2022_03_20 \\
    --from-hub list

  python scripts/query_opensre_telemetry.py \\
    --telemetry-dir /path/to/telemetry/2022_03_20 metrics --contains cpu
  python scripts/query_opensre_telemetry.py --telemetry-dir ... logs --service shipping
  python scripts/query_opensre_telemetry.py --telemetry-dir ... traces

  # First rows of one CSV (stdlib only for this subcommand)
  python scripts/query_opensre_telemetry.py --telemetry-dir ... raw metric/metric_foo.csv --limit 20
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.integrations.opensre.constants import OPENSRE_HF_DATASET_ID  # noqa: E402
from app.integrations.opensre.csv_grafana_backend import OpenSRECsvGrafanaBackend  # noqa: E402


def _resolve_telemetry_dir(args: argparse.Namespace) -> Path:
    if args.telemetry_dir:
        p = Path(args.telemetry_dir).expanduser().resolve()
        if not p.is_dir():
            raise SystemExit(f"Not a directory: {p}")
        return p
    rel = (args.relative or "").strip().strip("/")
    if not rel:
        raise SystemExit("Pass --telemetry-dir or --relative PATH")

    if args.from_hub:
        from app.integrations.opensre.hf_remote import materialize_opensre_telemetry_from_hub

        dataset_id = (args.dataset_id or "").strip() or OPENSRE_HF_DATASET_ID
        return materialize_opensre_telemetry_from_hub(
            dataset_id=dataset_id,
            telemetry_relative=rel,
            revision=args.revision or None,
        )

    import os

    root = (
        (args.dataset_root or "").strip()
        or os.environ.get("OPENSRE_DATASET_ROOT", "").strip()
        or os.environ.get("OPENRCA_DATASET_ROOT", "").strip()
    )
    if not root:
        raise SystemExit(
            "Local mode needs a dataset root: set OPENSRE_DATASET_ROOT or pass --dataset-root"
        )
    p = (Path(root).expanduser() / rel).resolve()
    if not p.is_dir():
        raise SystemExit(f"Telemetry directory missing: {p}")
    return p


def _cmd_list(root: Path) -> None:
    for sub in ("metric", "log", "trace"):
        d = root / sub
        if not d.is_dir():
            continue
        files = sorted(d.glob("*.csv"))
        print(f"=== {sub}/ ({len(files)} csv) ===")
        for f in files:
            print(f"  {f.relative_to(root)}")


def _cmd_raw(root: Path, rel_path: str, limit: int) -> None:
    path = (root / rel_path).resolve()
    if not path.is_file():
        raise SystemExit(f"Not a file under telemetry dir: {path}")
    try:
        path.relative_to(root)
    except ValueError:
        raise SystemExit("Path must be inside telemetry directory") from None
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, str]] = []
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append({k: row.get(k) or "" for k in reader.fieldnames or []})
    print(json.dumps({"file": rel_path, "rows": rows}, indent=2))


def _cmd_metrics(root: Path, contains: str) -> None:
    be = OpenSRECsvGrafanaBackend(telemetry_dir=root, alert_fixture={})
    out = be.query_timeseries(query=contains or "")
    print(json.dumps(out, indent=2, default=str))


def _cmd_logs(root: Path, service: str) -> None:
    be = OpenSRECsvGrafanaBackend(telemetry_dir=root, alert_fixture={})
    out = be.query_logs(service_name=service or "")
    print(json.dumps(out, indent=2, default=str))


def _cmd_traces(root: Path, service: str) -> None:
    be = OpenSRECsvGrafanaBackend(telemetry_dir=root, alert_fixture={})
    out = be.query_traces(service_name=service or "")
    print(json.dumps(out, indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--telemetry-dir",
        help="Absolute path to telemetry day folder (.../telemetry/YYYY_MM_DD)",
    )
    p.add_argument(
        "--relative",
        help="Path under dataset repo, e.g. Market/cloudbed-1/telemetry/2022_03_20",
    )
    p.add_argument(
        "--dataset-root",
        help="Local Hugging Face dataset clone root (overrides OPENSRE_DATASET_ROOT)",
    )
    p.add_argument(
        "--from-hub",
        action="store_true",
        help="Download only --relative/** via huggingface_hub (needs OPENSRE_HF_DATASET_ID or --dataset-id)",
    )
    p.add_argument(
        "--dataset-id",
        default="",
        help=f"Hugging Face dataset id (default {OPENSRE_HF_DATASET_ID})",
    )
    p.add_argument("--revision", default="", help="Hub git revision (default main / env)")

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List metric/log/trace CSV files")

    sp_raw = sub.add_parser("raw", help="Print first rows of one CSV (path relative to telemetry dir)")
    sp_raw.add_argument("csv_path", help="e.g. metric/container_cpu.csv")
    sp_raw.add_argument("--limit", type=int, default=50)

    sp_m = sub.add_parser("metrics", help="Query metric CSVs (substring filter)")
    sp_m.add_argument("--contains", default="", help="Substring to match metric CSV stem")

    sp_l = sub.add_parser("logs", help="Query log CSVs")
    sp_l.add_argument("--service", default="", help="Filter rows containing this substring")

    sp_t = sub.add_parser("traces", help="Query trace CSVs")
    sp_t.add_argument("--service", default="", help="Filter spans containing this substring")

    args = p.parse_args()
    root = _resolve_telemetry_dir(args)

    if args.cmd == "list":
        _cmd_list(root)
    elif args.cmd == "raw":
        _cmd_raw(root, args.csv_path, args.limit)
    elif args.cmd == "metrics":
        _cmd_metrics(root, args.contains)
    elif args.cmd == "logs":
        _cmd_logs(root, args.service)
    elif args.cmd == "traces":
        _cmd_traces(root, args.service)
    else:
        raise SystemExit(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
