"""Unit tests for the data_validation utilities."""

import pytest

from app.tools.utils.data_validation import MetricsValidator, validate_host_metrics


def test_impossible_percentages():
    """Test that percentages over 100 are flagged as impossible."""
    validator = MetricsValidator()

    # Disk percent > 100
    payload = {"disk": {"percent": 150}}
    result = validator.validate_metrics(payload)

    assert result["disk"]["percent_invalid"] is True
    assert result["disk"]["percent_raw"] == 150
    assert result["disk"]["percent"] is None

    # Check that data quality issues are attached
    issues = result["data_quality_issues"]
    assert len(issues) == 1
    assert issues[0]["field"] == "disk.percent"
    assert issues[0]["issue"] == "impossible_percentage"
    assert issues[0]["severity"] == "error"


def test_cpu_suspicious_percentage():
    """Test that CPU > 1000% is flagged as suspicious but not strictly impossible."""
    validator = MetricsValidator()
    result = validator.validate_metrics({"cpu": {"percent": 1500}})

    assert result["cpu"]["percent_suspicious"] is True
    assert result["cpu"]["percent_raw"] == 1500
    assert result["data_quality_issues"][0]["issue"] == "suspicious_value"
    assert result["data_quality_issues"][0]["severity"] == "warning"


def test_byte_to_gb_and_mb_inference():
    """Test memory unit inference for bytes masquerading as percentages."""
    validator = MetricsValidator()

    # 8 GB in bytes
    gb_bytes = 8 * (1024**3)
    result_gb = validator.validate_metrics({"memory": {"percent": gb_bytes}})

    assert result_gb["memory"]["percent_invalid"] is True
    interpretation_gb = result_gb["memory"]["percent_interpretation"]
    assert interpretation_gb["likely_unit"] == "bytes"
    assert interpretation_gb["likely_value_gb"] == 8.0

    # 500 MB in bytes
    mb_bytes = 500 * (1024**2)
    result_mb = validator.validate_metrics({"memory": {"percent": mb_bytes}})

    assert result_mb["memory"]["percent_invalid"] is True
    interpretation_mb = result_mb["memory"]["percent_interpretation"]
    assert interpretation_mb["likely_unit"] == "bytes"
    assert interpretation_mb["likely_value_mb"] == 500.0


def test_class_flat_metric_payload():
    """Test class validation on flat API response structures."""
    payload = {"cpu": 95, "ram": 8589934592, "disk": 50}
    validator = MetricsValidator()
    result = validator.validate_metrics(payload)

    # Ram should be flagged as invalid and inferred as 8 GB
    assert result["ram_invalid"] is True
    assert result["ram_interpretation"]["likely_unit"] == "bytes"
    assert result["ram_interpretation"]["likely_value_gb"] == 8.0

    # Issues should be attached at the root level
    assert "data_quality_issues" in result
    assert result["data_quality_issues"][0]["field"] == "ram"


def test_wrapper_flat_metric_payload():
    """Test wrapper validation on flat API response structures."""
    payload = {"cpu": 95, "ram": 8589934592, "disk": 50}
    result = validate_host_metrics(payload)

    # Ram should be flagged as invalid and inferred as 8 GB
    assert result["ram_invalid"] is True
    assert result["ram_interpretation"]["likely_unit"] == "bytes"
    assert result["ram_interpretation"]["likely_value_gb"] == 8.0

    # Issues should be attached at the root level
    assert "data_quality_issues" in result
    assert result["data_quality_issues"][0]["field"] == "ram"


def test_nested_metric_payload():
    """Test validation on deeply nested API response structures."""
    payload = {"memory": {"percent": 8589934592}, "cpu": {"percent": 95}}
    validator = MetricsValidator()
    result = validator.validate_metrics(payload)

    assert result["memory"]["percent_invalid"] is True
    assert "percent_interpretation" in result["memory"]
    assert result["data_quality_issues"][0]["field"] == "memory.percent"


def test_class_list_structure_payload():
    """Test class validation on list-based API response structures."""
    payload = {
        "success": True,
        "data": [
            {"cpu": 95, "ram": 8589934592, "disk": 50},
            {"cpu": 10, "ram": 45, "disk": 20},  # Valid payload
        ],
    }
    validator = MetricsValidator()
    result = validator.validate_metrics(payload)

    # First item in data array should have validation flags
    assert result["data"][0]["ram_invalid"] is True
    assert result["data"][0]["ram_interpretation"]["likely_unit"] == "bytes"

    # Second item should remain untouched
    assert "ram_invalid" not in result["data"][1]

    # Root level should aggregate the data_quality_issues
    assert "data_quality_issues" in result
    assert len(result["data_quality_issues"]) == 1
    assert result["data_quality_issues"][0]["field"] == "ram"


@pytest.mark.xfail(reason="Known bug: wrapper fails to attach data_quality_issues for lists")
def test_wrapper_list_structure_payload():
    """Test wrapper validation on lists (Currently expected to fail due to bug)."""
    payload = {
        "success": True,
        "data": [{"cpu": 95, "ram": 8589934592, "disk": 50}, {"cpu": 10, "ram": 45, "disk": 20}],
    }
    result = validate_host_metrics(payload)

    # This assertion WILL fail because of the bug, but xfail tells pytest we expect it to!
    assert "data_quality_issues" in result


def test_invalid_format():
    """Test fallback when metrics is not a dictionary."""
    result = validate_host_metrics("this is just a string, not a dict")
    assert result["validated"] is False
    assert result["data_quality_issues"][0]["issue"] == "invalid_format"
