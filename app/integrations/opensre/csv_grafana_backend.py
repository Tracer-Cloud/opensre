"""Grafana-protocol backend that serves OpenRCA-style CSV telemetry (metrics, logs, traces)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.integrations.opensre.grafana_wire_format import (
    format_loki_query_range,
    format_mimir_query_range,
    format_ruler_rules,
)


def _parse_ts(value: str) -> float | None:
    s = value.strip()
    if not s:
        return None
    try:
        if s.isdigit():
            ts = float(s)
            if ts > 1e12:
                ts /= 1e9
            elif ts > 1e10:
                ts /= 1000.0
            return ts
        normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
        dt = datetime.fromisoformat(normalized)
        return dt.replace(tzinfo=UTC).timestamp() if dt.tzinfo is None else dt.timestamp()
    except (ValueError, TypeError, OSError):
        return None


def _iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_TIME_KEYS = frozenset(
    k.lower()
    for k in (
        "timestamp",
        "datetime",
        "time",
        "ts",
        "starttime",
        "start_time",
        "starttimeunixnano",
        "endtime",
        "end_time",
    )
)


def _pick_time_key(fieldnames: list[str]) -> str | None:
    lower_map = {h.lower(): h for h in fieldnames}
    for cand in _TIME_KEYS:
        if cand in lower_map:
            return lower_map[cand]
    return None


def _pick_numeric_key(row: dict[str, str], skip: set[str]) -> str | None:
    for k, v in row.items():
        if k in skip or v is None:
            continue
        try:
            float(str(v).strip())
            return k
        except ValueError:
            continue
    return None


def _read_limited_rows(path: Path, max_rows: int) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows: list[dict[str, str]] = []
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            rows.append({k: (row.get(k) or "") for k in fieldnames})
        return fieldnames, rows


class OpenSRECsvGrafanaBackend:
    """Serve local CSV trees under ``telemetry/<date>/{metric,log,trace}/`` as Grafana API shapes."""

    def __init__(
        self,
        *,
        telemetry_dir: Path,
        alert_fixture: dict[str, Any] | None = None,
        max_rows_per_file: int = 4000,
        max_output_logs: int = 800,
    ) -> None:
        self._root = telemetry_dir.resolve()
        self._alert = alert_fixture or {}
        self._max_rows = max_rows_per_file
        self._max_logs = max_output_logs

    def query_timeseries(self, query: str = "", **_: Any) -> dict[str, Any]:
        metric_dir = self._root / "metric"
        if not metric_dir.is_dir():
            return {"status": "success", "data": {"resultType": "matrix", "result": []}}

        metric_data_results: list[dict[str, Any]] = []
        q = (query or "").lower()

        for csv_path in sorted(metric_dir.glob("*.csv")):
            stem = csv_path.stem.lower()
            if q and q not in stem and stem not in q:
                continue
            fieldnames, rows = _read_limited_rows(csv_path, self._max_rows)
            if not rows:
                continue
            ts_key = _pick_time_key(fieldnames)
            if not ts_key:
                continue
            val_key = _pick_numeric_key(rows[0], {ts_key})
            if not val_key:
                continue
            timestamps: list[str] = []
            values: list[float] = []
            label_cols = [c for c in fieldnames if c not in (ts_key, val_key)]
            dim_values: dict[str, str] = {}
            for col in label_cols:
                vals = {str(r.get(col, "")).strip() for r in rows if r.get(col)}
                vals.discard("")
                if len(vals) == 1:
                    dim_values[col] = next(iter(vals))

            for row in rows:
                ts_raw = row.get(ts_key, "")
                parsed = _parse_ts(ts_raw)
                if parsed is None:
                    continue
                try:
                    v = float(str(row.get(val_key, "")).strip())
                except ValueError:
                    continue
                timestamps.append(_iso_from_ts(parsed))
                values.append(v)

            if not timestamps:
                continue

            dims = [{"Name": k, "Value": v} for k, v in dim_values.items()]
            metric_data_results.append(
                {
                    "metric_name": csv_path.stem,
                    "stat": "Average",
                    "dimensions": dims,
                    "timestamps": timestamps,
                    "values": values,
                }
            )

        return format_mimir_query_range({"metric_data_results": metric_data_results})

    def query_logs(self, service_name: str = "", **_: Any) -> dict[str, Any]:
        log_dir = self._root / "log"
        if not log_dir.is_dir():
            return format_loki_query_range({"events": []})

        events: list[dict[str, Any]] = []
        svc = (service_name or "").strip().lower()

        for csv_path in sorted(log_dir.glob("*.csv")):
            fieldnames, rows = _read_limited_rows(csv_path, self._max_rows)
            if not rows:
                continue
            ts_key = _pick_time_key(fieldnames)
            ident = csv_path.stem
            for row in rows:
                if svc:
                    row_blob = " ".join(str(v).lower() for v in row.values())
                    if svc not in row_blob:
                        continue
                ts_raw = row.get(ts_key, "") if ts_key else ""
                parsed = _parse_ts(ts_raw) if ts_raw else None
                iso = (
                    _iso_from_ts(parsed)
                    if parsed is not None
                    else _iso_from_ts(datetime.now(tz=UTC).timestamp())
                )
                parts = [f"{k}={v}" for k, v in row.items() if v and k != ts_key]
                message = " | ".join(parts) if parts else str(row)
                events.append(
                    {
                        "date": iso,
                        "message": message[:2000],
                        "source_type": "opensre_log",
                        "source_identifier": ident,
                    }
                )
                if len(events) >= self._max_logs:
                    break
            if len(events) >= self._max_logs:
                break

        return format_loki_query_range({"events": events})

    def query_alert_rules(self, **_: Any) -> dict[str, Any]:
        alert = (
            self._alert
            if isinstance(self._alert, dict) and self._alert.get("commonLabels")
            else self._default_alert()
        )
        return format_ruler_rules(alert)

    def query_traces(self, service_name: str = "", **_: Any) -> dict[str, Any]:
        trace_dir = self._root / "trace"
        if not trace_dir.is_dir():
            return {"traces": [], "metrics": {}}

        svc = (service_name or "").strip().lower()
        span_by_trace: dict[str, list[dict[str, Any]]] = {}

        for csv_path in sorted(trace_dir.glob("*.csv")):
            fieldnames, rows = _read_limited_rows(csv_path, self._max_rows)
            if not rows:
                continue
            lower = {h.lower(): h for h in fieldnames}
            tid_col = next(
                (lower[k] for k in ("trace_id", "traceid") if k in lower),
                None,
            )
            name_col = next(
                (
                    lower[k]
                    for k in ("operation_name", "operationname", "span_name", "name")
                    if k in lower
                ),
                None,
            )
            svc_col = next(
                (lower[k] for k in ("service_name", "servicename", "service") if k in lower),
                None,
            )

            for row in rows:
                tid = (
                    row.get(tid_col, "singleton") if tid_col else "singleton"
                ).strip() or "singleton"
                op = (row.get(name_col, "span") if name_col else "span").strip() or "span"
                sname = (row.get(svc_col, "service") if svc_col else "service").strip() or "service"
                if svc and svc not in sname.lower() and svc not in op.lower():
                    continue
                attrs: dict[str, Any] = {k: v for k, v in row.items() if v}
                span_by_trace.setdefault(tid, []).append(
                    {
                        "name": op,
                        "attributes": attrs,
                    }
                )

        traces = [{"traceID": tid, "spans": spans} for tid, spans in span_by_trace.items()]
        return {"traces": traces, "metrics": {}}

    def _default_alert(self) -> dict[str, Any]:
        return {
            "title": "OpenSRE local telemetry",
            "state": "alerting",
            "commonLabels": {"alertname": "OpenSREScenario", "pipeline_name": "opensre"},
            "commonAnnotations": {"summary": "Synthetic alert wrapper for CSV telemetry"},
        }
