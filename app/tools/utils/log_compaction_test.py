"""Tests for log compaction utilities (deduplication + taxonomy)."""

from __future__ import annotations

from app.tools.utils.log_compaction import (
    _classify_error_type,
    _extract_components,
    _normalize_message,
    build_error_taxonomy,
    compact_logs,
    deduplicate_logs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_log(message: str, level: str = "ERROR", ts: str = "") -> dict:
    return {"message": message, "log_level": level, "timestamp": ts}


# ===========================================================================
# Phase 1: deduplicate_logs
# ===========================================================================


class TestDeduplicateLogs:
    """Phase 1 — deduplication + count grouping."""

    def test_empty_input(self):
        assert deduplicate_logs([]) == []

    def test_single_log_unchanged(self):
        logs = [_make_log("Something happened", "INFO", "2024-01-15T10:00:00Z")]
        result = deduplicate_logs(logs)
        assert len(result) == 1
        assert result[0]["message"] == "Something happened"
        assert result[0]["count"] == 1
        assert result[0]["first_seen"] == "2024-01-15T10:00:00Z"
        assert result[0]["last_seen"] == "2024-01-15T10:00:00Z"

    def test_exact_duplicates_grouped(self):
        logs = [
            _make_log("Connection timeout to upstream service", "ERROR", "2024-01-15T10:00:01Z"),
            _make_log("Connection timeout to upstream service", "ERROR", "2024-01-15T10:02:00Z"),
            _make_log("Connection timeout to upstream service", "ERROR", "2024-01-15T10:04:58Z"),
        ]
        result = deduplicate_logs(logs)
        assert len(result) == 1
        assert result[0]["count"] == 3
        assert result[0]["first_seen"] == "2024-01-15T10:00:01Z"
        assert result[0]["last_seen"] == "2024-01-15T10:04:58Z"

    def test_near_duplicates_with_variable_tokens(self):
        """Messages differing only by UUID / timestamp / IP should be grouped."""
        logs = [
            _make_log("Request abc12345-1234-1234-1234-123456789abc failed after 30s", "ERROR", "t1"),
            _make_log("Request def12345-1234-1234-1234-123456789def failed after 45s", "ERROR", "t2"),
        ]
        result = deduplicate_logs(logs)
        assert len(result) == 1
        assert result[0]["count"] == 2

    def test_different_levels_not_grouped(self):
        """Same message at different log levels should stay separate."""
        logs = [
            _make_log("Disk usage high", "WARN", "t1"),
            _make_log("Disk usage high", "ERROR", "t2"),
        ]
        result = deduplicate_logs(logs)
        assert len(result) == 2

    def test_different_messages_stay_separate(self):
        logs = [
            _make_log("Connection timeout", "ERROR", "t1"),
            _make_log("Schema validation failed", "ERROR", "t2"),
            _make_log("Out of memory", "ERROR", "t3"),
        ]
        result = deduplicate_logs(logs)
        assert len(result) == 3

    def test_max_output_cap(self):
        logs = [_make_log(f"Unique error {i}", "ERROR", f"t{i}") for i in range(100)]
        result = deduplicate_logs(logs, max_output=10)
        assert len(result) == 10

    def test_burst_scenario_from_issue(self):
        """The exact scenario from issue #226: 48 identical timeouts + 2 distinct errors."""
        logs = (
            [_make_log("Connection timeout to upstream service", "ERROR", f"2024-01-15T10:00:{i:02d}Z") for i in range(48)]
            + [_make_log("Schema validation failed: missing field 'user_id'", "ERROR", "2024-01-15T10:01:00Z")]
            + [_make_log("NullPointerException in DataProcessor.transform", "ERROR", "2024-01-15T10:01:30Z")]
        )
        assert len(logs) == 50

        result = deduplicate_logs(logs, max_output=50)
        # 48 timeouts → 1 group, plus 2 distinct errors = 3 entries
        assert len(result) == 3
        # The timeout group should have count=48
        timeout_group = next(r for r in result if "timeout" in r["message"].lower())
        assert timeout_group["count"] == 48

    def test_preserves_log_level_case(self):
        logs = [_make_log("Oops", "error")]
        result = deduplicate_logs(logs)
        assert result[0]["log_level"] == "ERROR"

    def test_missing_timestamp_handled(self):
        logs = [_make_log("No timestamp"), _make_log("No timestamp")]
        result = deduplicate_logs(logs)
        assert len(result) == 1
        assert result[0]["count"] == 2
        assert result[0]["first_seen"] == ""

    def test_ip_address_normalization(self):
        logs = [
            _make_log("Connection refused to 10.0.0.1:5432", "ERROR", "t1"),
            _make_log("Connection refused to 192.168.1.100:5432", "ERROR", "t2"),
        ]
        result = deduplicate_logs(logs)
        assert len(result) == 1
        assert result[0]["count"] == 2


# ===========================================================================
# Phase 2: build_error_taxonomy
# ===========================================================================


class TestBuildErrorTaxonomy:
    """Phase 2 — structured error taxonomy."""

    def test_empty_input(self):
        result = build_error_taxonomy([])
        assert result["total_logs_fetched"] == 0
        assert result["distinct_error_types"] == 0
        assert result["error_taxonomy"] == []

    def test_single_error_type(self):
        logs = [
            _make_log("Connection timeout to upstream after 30s", ts="t1"),
            _make_log("Connection timeout to upstream after 45s", ts="t2"),
            _make_log("Connection timeout to upstream after 60s", ts="t3"),
        ]
        result = build_error_taxonomy(logs)
        assert result["total_logs_fetched"] == 3
        assert result["distinct_error_types"] == 1
        assert result["error_taxonomy"][0]["error_type"] == "ConnectionTimeout"
        assert result["error_taxonomy"][0]["count"] == 3

    def test_multiple_error_types(self):
        logs = [
            _make_log("Connection timeout to upstream"),
            _make_log("Connection timeout to upstream"),
            _make_log("Out of memory error on worker-3"),
            _make_log("Schema validation failed: missing field 'id'"),
        ]
        result = build_error_taxonomy(logs)
        assert result["distinct_error_types"] == 3
        # Sorted by count desc — timeout should be first
        assert result["error_taxonomy"][0]["error_type"] == "ConnectionTimeout"
        assert result["error_taxonomy"][0]["count"] == 2

    def test_affected_components_extracted(self):
        logs = [
            _make_log("Timeout connecting to service=upstream-api host=db-primary"),
        ]
        result = build_error_taxonomy(logs)
        components = result["error_taxonomy"][0]["affected_components"]
        assert "upstream-api" in components
        assert "db-primary" in components

    def test_sample_messages_deduped(self):
        """Near-identical samples should not duplicate."""
        logs = [
            _make_log("Timeout after 30s connecting to 10.0.0.1"),
            _make_log("Timeout after 45s connecting to 10.0.0.2"),
            _make_log("Timeout after 60s connecting to 10.0.0.3"),
        ]
        result = build_error_taxonomy(logs, max_samples=5)
        samples = result["error_taxonomy"][0]["sample_messages"]
        # Near-identical after normalization → only 1 unique sample
        assert len(samples) == 1

    def test_sample_messages_cap(self):
        logs = [_make_log(f"Timeout error in component-{i}") for i in range(20)]
        result = build_error_taxonomy(logs, max_samples=3)
        samples = result["error_taxonomy"][0]["sample_messages"]
        assert len(samples) <= 3

    def test_raw_samples_across_types(self):
        logs = [
            _make_log("Connection timeout"),
            _make_log("Out of memory"),
            _make_log("Schema validation failed"),
        ]
        result = build_error_taxonomy(logs)
        assert len(result["raw_samples"]) >= 3

    def test_timestamp_range_tracked(self):
        logs = [
            _make_log("Timeout", ts="2024-01-15T10:00:01Z"),
            _make_log("Timeout", ts="2024-01-15T10:04:58Z"),
        ]
        result = build_error_taxonomy(logs)
        entry = result["error_taxonomy"][0]
        assert entry["first_seen"] == "2024-01-15T10:00:01Z"
        assert entry["last_seen"] == "2024-01-15T10:04:58Z"


# ===========================================================================
# classify / extract helpers
# ===========================================================================


class TestClassifyErrorType:
    def test_timeout(self):
        assert _classify_error_type("Connection timed out after 30s") == "ConnectionTimeout"

    def test_oom(self):
        assert _classify_error_type("Container killed by OOM killer") == "OutOfMemory"

    def test_auth(self):
        assert _classify_error_type("Authentication failed for user admin") == "AuthenticationError"

    def test_schema(self):
        assert _classify_error_type("Schema validation error: missing field") == "SchemaValidation"

    def test_unknown(self):
        assert _classify_error_type("Something completely unexpected") == "Unknown"

    def test_rate_limit(self):
        assert _classify_error_type("429 Too Many Requests") == "RateLimited"

    def test_not_found(self):
        assert _classify_error_type("404 Not Found: /api/v2/resource") == "ResourceNotFound"

    def test_import_error(self):
        assert _classify_error_type("ImportError: No module named 'pandas'") == "ImportError"


class TestExtractComponents:
    def test_key_value_pattern(self):
        assert "my-svc" in _extract_components("error in service=my-svc")

    def test_quoted_identifiers(self):
        assert "upstream-api" in _extract_components("failed to reach 'upstream-api'")

    def test_no_components(self):
        assert _extract_components("simple error") == []


# ===========================================================================
# normalize helper
# ===========================================================================


class TestNormalizeMessage:
    def test_uuid_replaced(self):
        result = _normalize_message("Request abc12345-1234-1234-1234-123456789abc failed")
        assert "abc12345" not in result
        assert "<*>" in result

    def test_ip_replaced(self):
        result = _normalize_message("Connecting to 10.0.0.1:5432")
        assert "10.0.0.1" not in result

    def test_timestamp_replaced(self):
        result = _normalize_message("Error at 2024-01-15T10:00:01Z in worker")
        assert "2024-01-15" not in result


# ===========================================================================
# compact_logs (convenience wrapper)
# ===========================================================================


class TestCompactLogs:
    def test_combines_both_phases(self):
        logs = [
            _make_log("Connection timeout", "ERROR", "t1"),
            _make_log("Connection timeout", "ERROR", "t2"),
            _make_log("Connection timeout", "ERROR", "t3"),
            _make_log("Schema validation failed", "ERROR", "t4"),
            _make_log("All good", "INFO", "t5"),
        ]
        result = compact_logs(logs, max_output=50)

        # Phase 1: deduplicated list
        assert len(result["compacted_logs"]) == 3  # 3 timeout→1, 1 schema, 1 info
        assert result["total_raw"] == 5

        # Phase 2: taxonomy (errors only)
        taxonomy = result["error_taxonomy"]
        assert taxonomy["total_logs_fetched"] == 4  # 4 error logs
        assert taxonomy["distinct_error_types"] == 2

    def test_no_errors_produces_empty_taxonomy(self):
        logs = [_make_log("All good", "INFO")]
        result = compact_logs(logs)
        assert result["error_taxonomy"]["distinct_error_types"] == 0
        assert len(result["compacted_logs"]) == 1
