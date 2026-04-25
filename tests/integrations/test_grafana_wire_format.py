"""Unit tests for app/integrations/opensre/grafana_wire_format.py.

All functions are pure data transforms — no I/O, no mocking required.

grafana_wire_format.py uses only stdlib (re, datetime).  It lives inside a
package whose __init__.py eagerly imports the full LangGraph node tree, so we
load the module directly from its file to keep this test self-contained and
fast without requiring the entire optional-dependency stack to be installed.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the module directly, bypassing app/integrations/opensre/__init__.py
# ---------------------------------------------------------------------------

_MODULE_PATH = (
    Path(__file__).parent.parent.parent / "app/integrations/opensre/grafana_wire_format.py"
)
_spec = importlib.util.spec_from_file_location(
    "app.integrations.opensre.grafana_wire_format", _MODULE_PATH
)
assert _spec is not None and _spec.loader is not None
_gwf = importlib.util.module_from_spec(_spec)
sys.modules["app.integrations.opensre.grafana_wire_format"] = _gwf
_spec.loader.exec_module(_gwf)  # type: ignore[union-attr]

_dimension_labels = _gwf._dimension_labels
_iso_to_unix = _gwf._iso_to_unix
_iso_to_unix_ns = _gwf._iso_to_unix_ns
_metric_name = _gwf._metric_name
format_loki_query_range = _gwf.format_loki_query_range
format_mimir_query_range = _gwf.format_mimir_query_range
format_ruler_rules = _gwf.format_ruler_rules

# ---------------------------------------------------------------------------
# _iso_to_unix
# ---------------------------------------------------------------------------


class TestIsoToUnix:
    def test_z_suffix_parses_as_utc(self) -> None:
        expected = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
        assert _iso_to_unix("2026-01-01T00:00:00Z") == pytest.approx(expected)

    def test_explicit_utc_offset_equal_to_z(self) -> None:
        assert _iso_to_unix("2026-01-01T00:00:00+00:00") == pytest.approx(
            _iso_to_unix("2026-01-01T00:00:00Z")
        )

    def test_non_utc_offset_normalised_to_utc(self) -> None:
        # +01:00 means one hour ahead of UTC — the underlying unix value must
        # equal the UTC equivalent of the same wall-clock moment.
        assert _iso_to_unix("2026-01-01T01:00:00+01:00") == pytest.approx(
            _iso_to_unix("2026-01-01T00:00:00Z")
        )

    def test_naive_datetime_treated_as_utc(self) -> None:
        # A timestamp without any timezone info is assumed UTC per the code path
        # that calls dt.replace(tzinfo=UTC) when dt.tzinfo is None.
        assert _iso_to_unix("2026-01-01T00:00:00") == pytest.approx(
            _iso_to_unix("2026-01-01T00:00:00Z")
        )

    def test_returns_float(self) -> None:
        assert isinstance(_iso_to_unix("2026-01-01T00:00:00Z"), float)

    def test_sub_second_precision_preserved(self) -> None:
        base = _iso_to_unix("2026-01-01T00:00:00Z")
        with_ms = _iso_to_unix("2026-01-01T00:00:00.500Z")
        assert with_ms == pytest.approx(base + 0.5, abs=1e-3)


# ---------------------------------------------------------------------------
# _iso_to_unix_ns
# ---------------------------------------------------------------------------


class TestIsoToUnixNs:
    def test_returns_string(self) -> None:
        assert isinstance(_iso_to_unix_ns("2026-01-01T00:00:00Z"), str)

    def test_value_matches_unix_times_1e9(self) -> None:
        unix = _iso_to_unix("2026-01-01T00:00:00Z")
        expected_ns = str(int(unix * 1_000_000_000))
        assert _iso_to_unix_ns("2026-01-01T00:00:00Z") == expected_ns

    def test_nanosecond_string_is_numeric(self) -> None:
        ns = _iso_to_unix_ns("2026-06-15T12:30:00Z")
        assert ns.isdigit()

    def test_two_adjacent_seconds_differ_by_1e9(self) -> None:
        t0 = int(_iso_to_unix_ns("2026-01-01T00:00:00Z"))
        t1 = int(_iso_to_unix_ns("2026-01-01T00:00:01Z"))
        assert t1 - t0 == 1_000_000_000


# ---------------------------------------------------------------------------
# _metric_name
# ---------------------------------------------------------------------------


class TestMetricName:
    @pytest.mark.parametrize(
        ("metric_name", "stat", "expected"),
        [
            # camelCase with acronym prefix
            ("CPUUtilization", "Average", "aws_rds_cpu_utilization_average"),
            # acronym followed by camelCase word
            ("DBConnections", "Sum", "aws_rds_db_connections_sum"),
            # multiple camelCase words
            ("FreeStorageSpace", "Maximum", "aws_rds_free_storage_space_maximum"),
            # all-uppercase acronym in the middle
            ("ReadIOPS", "Average", "aws_rds_read_iops_average"),
            # already fully lowercase
            ("read_iops", "average", "aws_rds_read_iops_average"),
            # stat is uppercased — must be lowercased in output
            ("CPUUtilization", "AVERAGE", "aws_rds_cpu_utilization_average"),
            # single word, no camelCase
            ("connections", "p99", "aws_rds_connections_p99"),
        ],
    )
    def test_metric_name_formats(self, metric_name: str, stat: str, expected: str) -> None:
        assert _metric_name(metric_name, stat) == expected

    def test_output_always_starts_with_aws_rds(self) -> None:
        assert _metric_name("SomeMetric", "avg").startswith("aws_rds_")

    def test_output_is_all_lowercase(self) -> None:
        result = _metric_name("CPUUtilization", "Average")
        assert result == result.lower()

    def test_stat_appended_after_underscore(self) -> None:
        result = _metric_name("Latency", "p95")
        assert result.endswith("_p95")


# ---------------------------------------------------------------------------
# _dimension_labels
# ---------------------------------------------------------------------------


class TestDimensionLabels:
    def test_single_dimension_lowercases_name(self) -> None:
        dims = [{"Name": "DBInstanceIdentifier", "Value": "prod-db-1"}]
        assert _dimension_labels(dims) == {"dbinstanceidentifier": "prod-db-1"}

    def test_multiple_dimensions(self) -> None:
        dims = [
            {"Name": "DBInstanceIdentifier", "Value": "prod-db-1"},
            {"Name": "Region", "Value": "us-east-1"},
        ]
        result = _dimension_labels(dims)
        assert result == {"dbinstanceidentifier": "prod-db-1", "region": "us-east-1"}

    def test_entry_missing_name_is_skipped(self) -> None:
        dims = [{"Value": "orphan-value"}, {"Name": "Region", "Value": "us-west-2"}]
        assert _dimension_labels(dims) == {"region": "us-west-2"}

    def test_entry_missing_value_is_skipped(self) -> None:
        dims = [{"Name": "Region"}, {"Name": "Az", "Value": "us-east-1a"}]
        assert _dimension_labels(dims) == {"az": "us-east-1a"}

    def test_empty_list_returns_empty_dict(self) -> None:
        assert _dimension_labels([]) == {}

    def test_value_case_is_preserved(self) -> None:
        dims = [{"Name": "DBInstanceIdentifier", "Value": "MyProd-DB"}]
        assert _dimension_labels(dims)["dbinstanceidentifier"] == "MyProd-DB"


# ---------------------------------------------------------------------------
# format_mimir_query_range
# ---------------------------------------------------------------------------


class TestFormatMimirQueryRange:
    def _entry(
        self,
        metric_name: str = "CPUUtilization",
        stat: str = "Average",
        dimensions: list[dict[str, str]] | None = None,
        timestamps: list[str] | None = None,
        values: list[float] | None = None,
    ) -> dict:
        return {
            "metric_name": metric_name,
            "stat": stat,
            "dimensions": [{"Name": "DBInstanceIdentifier", "Value": "db-1"}]
            if dimensions is None
            else dimensions,
            "timestamps": ["2026-01-01T00:00:00Z"] if timestamps is None else timestamps,
            "values": [42.0] if values is None else values,
        }

    def test_top_level_envelope_shape(self) -> None:
        result = format_mimir_query_range({"metric_data_results": [self._entry()]})
        assert result["status"] == "success"
        assert result["data"]["resultType"] == "matrix"
        assert isinstance(result["data"]["result"], list)

    def test_empty_metric_data_results_returns_empty_result(self) -> None:
        result = format_mimir_query_range({"metric_data_results": []})
        assert result["data"]["result"] == []

    def test_missing_metric_data_results_key_returns_empty_result(self) -> None:
        result = format_mimir_query_range({})
        assert result["data"]["result"] == []

    def test_metric_labels_include_dunder_name(self) -> None:
        result = format_mimir_query_range({"metric_data_results": [self._entry()]})
        metric = result["data"]["result"][0]["metric"]
        assert "__name__" in metric
        assert metric["__name__"] == "aws_rds_cpu_utilization_average"

    def test_metric_labels_include_dimensions(self) -> None:
        result = format_mimir_query_range({"metric_data_results": [self._entry()]})
        metric = result["data"]["result"][0]["metric"]
        assert "dbinstanceidentifier" in metric
        assert metric["dbinstanceidentifier"] == "db-1"

    def test_values_are_float_string_pairs(self) -> None:
        result = format_mimir_query_range({"metric_data_results": [self._entry()]})
        series = result["data"]["result"][0]
        assert len(series["values"]) == 1
        ts, val = series["values"][0]
        assert isinstance(ts, float)
        assert isinstance(val, str)

    def test_values_zipped_correctly(self) -> None:
        entry = self._entry(
            timestamps=["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"],
            values=[10.5, 20.0],
        )
        result = format_mimir_query_range({"metric_data_results": [entry]})
        pairs = result["data"]["result"][0]["values"]
        assert len(pairs) == 2
        assert pairs[0][1] == "10.5"
        assert pairs[1][1] == "20.0"

    def test_value_converted_to_string(self) -> None:
        entry = self._entry(values=[87.5])
        result = format_mimir_query_range({"metric_data_results": [entry]})
        assert result["data"]["result"][0]["values"][0][1] == "87.5"

    def test_multiple_entries_produce_multiple_series(self) -> None:
        entries = [
            self._entry(metric_name="CPUUtilization"),
            self._entry(metric_name="FreeStorageSpace", stat="Minimum"),
        ]
        result = format_mimir_query_range({"metric_data_results": entries})
        names = [s["metric"]["__name__"] for s in result["data"]["result"]]
        assert "aws_rds_cpu_utilization_average" in names
        assert "aws_rds_free_storage_space_minimum" in names

    def test_no_dimensions_produces_only_dunder_name_label(self) -> None:
        entry = self._entry(dimensions=[])
        result = format_mimir_query_range({"metric_data_results": [entry]})
        metric = result["data"]["result"][0]["metric"]
        assert list(metric.keys()) == ["__name__"]

    def test_empty_timestamps_and_values_produce_empty_values_list(self) -> None:
        entry = self._entry(timestamps=[], values=[])
        result = format_mimir_query_range({"metric_data_results": [entry]})
        assert result["data"]["result"][0]["values"] == []


# ---------------------------------------------------------------------------
# format_loki_query_range
# ---------------------------------------------------------------------------


class TestFormatLokiQueryRange:
    def _event(
        self,
        source_type: str = "db-instance",
        source_identifier: str = "prod-db-1",
        date: str = "2026-01-01T00:00:00Z",
        message: str = "DB restarted",
    ) -> dict:
        return {
            "source_type": source_type,
            "source_identifier": source_identifier,
            "date": date,
            "message": message,
        }

    def test_top_level_envelope_shape(self) -> None:
        fixture = {"events": [self._event()]}
        result = format_loki_query_range(fixture)
        assert result["status"] == "success"
        assert result["data"]["resultType"] == "streams"
        assert isinstance(result["data"]["result"], list)

    def test_empty_events_returns_empty_result(self) -> None:
        result = format_loki_query_range({"events": []})
        assert result["data"]["result"] == []

    def test_missing_events_key_returns_empty_result(self) -> None:
        result = format_loki_query_range({})
        assert result["data"]["result"] == []

    def test_single_event_produces_one_stream(self) -> None:
        result = format_loki_query_range({"events": [self._event()]})
        assert len(result["data"]["result"]) == 1

    def test_stream_has_correct_keys(self) -> None:
        result = format_loki_query_range({"events": [self._event()]})
        stream_entry = result["data"]["result"][0]
        assert "stream" in stream_entry
        assert "values" in stream_entry

    def test_stream_labels_contain_source_type_and_identifier(self) -> None:
        result = format_loki_query_range({"events": [self._event()]})
        stream = result["data"]["result"][0]["stream"]
        assert stream["source_type"] == "db-instance"
        assert stream["source_identifier"] == "prod-db-1"

    def test_log_value_is_nanosecond_string_and_message(self) -> None:
        result = format_loki_query_range({"events": [self._event(message="hello")]})
        ns_ts, line = result["data"]["result"][0]["values"][0]
        assert isinstance(ns_ts, str)
        assert ns_ts.isdigit()
        assert line == "hello"

    def test_nanosecond_timestamp_matches_iso_conversion(self) -> None:
        result = format_loki_query_range({"events": [self._event(date="2026-01-01T00:00:00Z")]})
        ns_ts = result["data"]["result"][0]["values"][0][0]
        expected_unix = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
        expected_ns = str(int(expected_unix * 1_000_000_000))
        assert ns_ts == expected_ns

    def test_events_with_same_source_grouped_into_one_stream(self) -> None:
        events = [
            self._event(date="2026-01-01T00:00:00Z", message="first"),
            self._event(date="2026-01-01T00:01:00Z", message="second"),
        ]
        result = format_loki_query_range({"events": events})
        assert len(result["data"]["result"]) == 1
        assert len(result["data"]["result"][0]["values"]) == 2

    def test_events_with_different_sources_split_into_separate_streams(self) -> None:
        events = [
            self._event(source_identifier="db-1", message="a"),
            self._event(source_identifier="db-2", message="b"),
        ]
        result = format_loki_query_range({"events": events})
        assert len(result["data"]["result"]) == 2

    def test_log_lines_sorted_by_timestamp_ascending(self) -> None:
        events = [
            self._event(date="2026-01-01T00:02:00Z", message="third"),
            self._event(date="2026-01-01T00:00:00Z", message="first"),
            self._event(date="2026-01-01T00:01:00Z", message="second"),
        ]
        result = format_loki_query_range({"events": events})
        lines = [v[1] for v in result["data"]["result"][0]["values"]]
        assert lines == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# format_ruler_rules
# ---------------------------------------------------------------------------


class TestFormatRulerRules:
    def _fixture(
        self,
        state: str = "alerting",
        alert_name: str = "HighCPU",
        pipeline_name: str = "rds-pipeline",
        title: str = "High CPU Alert",
        annotations: dict | None = None,
    ) -> dict:
        return {
            "state": state,
            "title": title,
            "commonLabels": {
                "alertname": alert_name,
                "pipeline_name": pipeline_name,
            },
            "commonAnnotations": {"summary": "CPU exceeded threshold"}
            if annotations is None
            else annotations,
        }

    def test_top_level_has_groups_key(self) -> None:
        result = format_ruler_rules(self._fixture())
        assert "groups" in result
        assert isinstance(result["groups"], list)

    def test_single_group_with_one_rule(self) -> None:
        result = format_ruler_rules(self._fixture())
        assert len(result["groups"]) == 1
        assert len(result["groups"][0]["rules"]) == 1

    def test_group_has_name_and_rules_keys(self) -> None:
        result = format_ruler_rules(self._fixture())
        group = result["groups"][0]
        assert "name" in group
        assert "rules" in group

    def test_rule_has_required_keys(self) -> None:
        result = format_ruler_rules(self._fixture())
        rule = result["groups"][0]["rules"][0]
        assert "state" in rule
        assert "name" in rule
        assert "labels" in rule
        assert "annotations" in rule

    def test_alerting_state_maps_to_firing(self) -> None:
        result = format_ruler_rules(self._fixture(state="alerting"))
        assert result["groups"][0]["rules"][0]["state"] == "firing"

    def test_non_alerting_state_maps_to_inactive(self) -> None:
        for state in ("ok", "pending", "resolved", "normal"):
            result = format_ruler_rules(self._fixture(state=state))
            assert result["groups"][0]["rules"][0]["state"] == "inactive", state

    def test_alert_name_from_common_labels(self) -> None:
        result = format_ruler_rules(self._fixture(alert_name="HighCPU"))
        assert result["groups"][0]["rules"][0]["name"] == "HighCPU"

    def test_alert_name_falls_back_to_title(self) -> None:
        fixture = {
            "state": "alerting",
            "title": "FallbackTitle",
            "commonLabels": {"pipeline_name": "rds-pipeline"},
            "commonAnnotations": {},
        }
        result = format_ruler_rules(fixture)
        assert result["groups"][0]["rules"][0]["name"] == "FallbackTitle"

    def test_alert_name_defaults_to_unknown_when_neither_present(self) -> None:
        result = format_ruler_rules(
            {"state": "alerting", "commonLabels": {}, "commonAnnotations": {}}
        )
        assert result["groups"][0]["rules"][0]["name"] == "UnknownAlert"

    def test_group_name_from_pipeline_name_label(self) -> None:
        result = format_ruler_rules(self._fixture(pipeline_name="rds-pipeline"))
        assert result["groups"][0]["name"] == "rds-pipeline"

    def test_group_name_defaults_to_synthetic(self) -> None:
        fixture = {
            "state": "alerting",
            "title": "Alert",
            "commonLabels": {"alertname": "SomeAlert"},
            "commonAnnotations": {},
        }
        result = format_ruler_rules(fixture)
        assert result["groups"][0]["name"] == "synthetic"

    def test_labels_passed_through_from_common_labels(self) -> None:
        result = format_ruler_rules(self._fixture())
        labels = result["groups"][0]["rules"][0]["labels"]
        assert "alertname" in labels
        assert "pipeline_name" in labels

    def test_annotations_passed_through_from_common_annotations(self) -> None:
        result = format_ruler_rules(
            self._fixture(annotations={"summary": "CPU high", "runbook": "https://wiki"})
        )
        annotations = result["groups"][0]["rules"][0]["annotations"]
        assert annotations["summary"] == "CPU high"
        assert annotations["runbook"] == "https://wiki"

    def test_empty_common_labels_and_annotations(self) -> None:
        result = format_ruler_rules(
            {"state": "alerting", "commonLabels": {}, "commonAnnotations": {}}
        )
        rule = result["groups"][0]["rules"][0]
        assert rule["labels"] == {}
        assert rule["annotations"] == {}
