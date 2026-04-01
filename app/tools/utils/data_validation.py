"""Data validators - sanitize API responses before LLM sees them."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ValidationIssue:
    """Represents a data quality issue found during validation."""

    field: str
    raw_value: Any
    issue_type: str  # "impossible_value", "unit_mismatch", "missing_data", etc.
    severity: str  # "error", "warning", "info"
    explanation: str
    suggested_fix: str | None = None


class MetricsValidator:
    """Validates and normalizes metrics data from APIs."""

    issues: list[ValidationIssue]

    # Define reasonable bounds for metrics
    METRIC_BOUNDS = {
        "memory_percent": (0, 100),
        "cpu_percent": (0, 100),
        "disk_percent": (0, 100),
    }

    # Common unit conversion patterns
    LIKELY_BYTE_TO_PERCENT_THRESHOLD = 1000  # If "percent" > 1000, probably bytes

    def __init__(self) -> None:
        self.issues = []

    def validate_metrics(self, metrics: dict) -> dict:
        """
        Validate and normalize metrics, flagging impossible values.

        Handles different API response structures:
        - Flat structure: {"cpu": 95, "ram": 8471740416, "disk": 50}
        - Nested structure: {"memory": {"percent": 8471740416}, "cpu": {"percent": 95}}
        - List structure: {"data": [{"cpu": 95, "ram": 8471740416}]}

        Returns:
            Normalized metrics dict with added 'data_quality_issues' key
        """
        normalized = metrics.copy() if isinstance(metrics, dict) else {}
        self.issues = []

        # Handle list structure (common in API responses)
        if "data" in normalized and isinstance(normalized["data"], list):
            # Validate each data point in the list
            validated_data = []
            for data_point in normalized["data"]:
                if isinstance(data_point, dict):
                    validated_point = self._validate_flat_metrics(data_point.copy())
                    validated_data.append(validated_point)
                else:
                    validated_data.append(data_point)
            normalized["data"] = validated_data
            # Also check aggregated values if present
            if "max_cpu" in normalized or "max_ram" in normalized:
                normalized = self._validate_flat_metrics(normalized)

        # Check nested memory metrics
        if "memory" in normalized:
            normalized["memory"] = self._validate_memory_metric(normalized["memory"])

        # Check CPU metrics
        if "cpu" in normalized:
            normalized["cpu"] = self._validate_cpu_metric(normalized["cpu"])

        # Check disk metrics
        if "disk" in normalized:
            normalized["disk"] = self._validate_disk_metric(normalized["disk"])

        # Check for flat structure metrics (cpu, ram, disk at top level)
        normalized = self._validate_flat_metrics(normalized)

        # Check for percentage fields at top level
        for key in ["percent", "percentage", "usage_percent"]:
            if key in normalized:
                value = normalized[key]
                if isinstance(value, (int, float)) and value > 100:
                    self._flag_impossible_percentage(key, value, normalized)

        # Attach validation issues to the response
        if self.issues:
            normalized["data_quality_issues"] = [
                {
                    "field": issue.field,
                    "raw_value": issue.raw_value,
                    "issue": issue.issue_type,
                    "severity": issue.severity,
                    "explanation": issue.explanation,
                    "suggested_fix": issue.suggested_fix,
                }
                for issue in self.issues
            ]

        return normalized

    def _validate_memory_metric(self, memory_data: dict | Any) -> dict:
        """Validate and fix memory metrics with intelligent unit inference."""
        if not isinstance(memory_data, dict):
            return {"raw": memory_data, "validated": False}

        normalized = memory_data.copy()

        # Check for impossible percentage values
        if "percent" in memory_data:
            raw_percent = memory_data["percent"]

            if isinstance(raw_percent, (int, float)) and raw_percent > 100:
                # Infer the most likely unit and interpretation
                interpretation = self._infer_memory_unit(raw_percent)

                self.issues.append(
                    ValidationIssue(
                        field="memory.percent",
                        raw_value=raw_percent,
                        issue_type="impossible_percentage",
                        severity="error",
                        explanation=interpretation["explanation"],
                        suggested_fix=interpretation["suggested_fix"],
                    )
                )

                # Add interpretation hints to the normalized data
                normalized["percent_interpretation"] = interpretation
                normalized["percent_invalid"] = True
                normalized["percent_raw"] = raw_percent
                normalized["percent"] = None  # Mark as invalid, LLM should use interpretation

        # Also check for "ram" field (common in API responses)
        if "ram" in memory_data:
            raw_ram = memory_data["ram"]
            if isinstance(raw_ram, (int, float)) and raw_ram > 100:
                interpretation = self._infer_memory_unit(raw_ram)
                self.issues.append(
                    ValidationIssue(
                        field="memory.ram",
                        raw_value=raw_ram,
                        issue_type="impossible_percentage",
                        severity="error",
                        explanation=interpretation["explanation"],
                        suggested_fix=interpretation["suggested_fix"],
                    )
                )
                normalized["ram_interpretation"] = interpretation
                normalized["ram_invalid"] = True
                normalized["ram_raw"] = raw_ram
                normalized["ram"] = None

        return normalized

    def _infer_memory_unit(self, value: float) -> dict:
        """
        Infer the most likely unit for a memory value that's labeled as percentage.

        Returns interpretation with likely unit, explanation, and suggested fix.
        """
        # Common memory sizes in bytes
        if value >= 1024**3:  # >= 1GB
            gb_value = value / (1024**3)
            interpretation = {
                "likely_unit": "bytes",
                "likely_value_gb": round(gb_value, 2),
                "likely_value_mb": round(value / (1024**2), 2),
                "explanation": (
                    f"Value {value:,.0f} labeled as 'percent' is impossible (>100%). "
                    f"This is most likely a unit error where bytes are reported as percentage. "
                    f"Interpretation: {gb_value:.2f} GB ({value / (1024**2):,.0f} MB) of memory used. "
                    f"To determine actual percentage, divide by total memory: "
                    f"percent = (used_bytes / total_memory_bytes) * 100"
                ),
                "suggested_fix": (
                    f"Treat this as {gb_value:.2f} GB of memory used, not a percentage. "
                    f"Compare against instance type memory limits to determine if memory was exhausted."
                ),
            }
        elif value >= 1024**2:  # >= 1MB
            mb_value = value / (1024**2)
            interpretation = {
                "likely_unit": "bytes",
                "likely_value_mb": round(mb_value, 2),
                "likely_value_gb": round(value / (1024**3), 2),
                "explanation": (
                    f"Value {value:,.0f} labeled as 'percent' is impossible (>100%). "
                    f"This is likely a unit error where bytes are reported as percentage. "
                    f"Interpretation: {mb_value:.2f} MB ({value / (1024**3):.2f} GB) of memory used."
                ),
                "suggested_fix": (
                    f"Treat this as {mb_value:.2f} MB of memory used, not a percentage."
                ),
            }
        else:
            # Value is > 100 but < 1MB - might be a different error
            interpretation = {
                "likely_unit": "unknown",
                "explanation": (
                    f"Value {value:,.0f} labeled as 'percent' exceeds 100% but is unusually small. "
                    f"This suggests a data collection or unit conversion error."
                ),
                "suggested_fix": "Verify the metric source and unit conversion logic",
            }

        return interpretation

    def _validate_cpu_metric(self, cpu_data: dict | Any) -> dict:
        """Validate CPU metrics."""
        if not isinstance(cpu_data, dict):
            return {"raw": cpu_data, "validated": False}

        normalized = cpu_data.copy()

        if "percent" in cpu_data:
            raw_percent = cpu_data["percent"]

            # CPU can legitimately exceed 100% (multi-core)
            # But beyond 1000% is suspicious for most workloads
            if isinstance(raw_percent, (int, float)) and raw_percent > 1000:
                self.issues.append(
                    ValidationIssue(
                        field="cpu.percent",
                        raw_value=raw_percent,
                        issue_type="suspicious_value",
                        severity="warning",
                        explanation=(
                            f"CPU usage reported as {raw_percent}% which is unusually high. "
                            f"While multi-core systems can exceed 100%, values over 1000% "
                            f"suggest a data collection error or misconfigured metric."
                        ),
                        suggested_fix="Verify CPU metric calculation and core count normalization",
                    )
                )
                # Cap at reasonable value or mark as suspicious
                normalized["percent_suspicious"] = True
                normalized["percent_raw"] = raw_percent

        return normalized

    def _validate_disk_metric(self, disk_data: dict | Any) -> dict:
        """Validate disk metrics."""
        if not isinstance(disk_data, dict):
            return {"raw": disk_data, "validated": False}

        normalized = disk_data.copy()

        if "percent" in disk_data:
            raw_percent = disk_data["percent"]

            if isinstance(raw_percent, (int, float)) and raw_percent > 100:
                self.issues.append(
                    ValidationIssue(
                        field="disk.percent",
                        raw_value=raw_percent,
                        issue_type="impossible_percentage",
                        severity="error",
                        explanation=(
                            f"Disk usage reported as {raw_percent}% which is impossible. "
                            f"This indicates a data collection or unit conversion error."
                        ),
                        suggested_fix="Verify disk metric calculation and units",
                    )
                )
                normalized["percent"] = None
                normalized["percent_invalid"] = True
                normalized["percent_raw"] = raw_percent

        return normalized

    def _validate_flat_metrics(self, metrics: dict) -> dict:
        """
        Validate metrics in flat structure (cpu, ram, disk at top level).

        Common API format: {"cpu": 95.28, "ram": 8471740416, "disk": 50}
        """
        normalized = metrics.copy()

        # Check "ram" field (often used instead of "memory")
        if "ram" in normalized:
            raw_ram = normalized["ram"]
            if isinstance(raw_ram, (int, float)) and raw_ram > 100:
                interpretation = self._infer_memory_unit(raw_ram)
                self.issues.append(
                    ValidationIssue(
                        field="ram",
                        raw_value=raw_ram,
                        issue_type="impossible_percentage",
                        severity="error",
                        explanation=interpretation["explanation"],
                        suggested_fix=interpretation["suggested_fix"],
                    )
                )
                normalized["ram_interpretation"] = interpretation
                normalized["ram_invalid"] = True
                normalized["ram_raw"] = raw_ram
                # Don't set to None - keep raw value but mark as invalid
                # LLM can use interpretation to understand it

        # Check "max_ram" if present
        if "max_ram" in normalized:
            raw_max_ram = normalized["max_ram"]
            if isinstance(raw_max_ram, (int, float)) and raw_max_ram > 100:
                interpretation = self._infer_memory_unit(raw_max_ram)
                self.issues.append(
                    ValidationIssue(
                        field="max_ram",
                        raw_value=raw_max_ram,
                        issue_type="impossible_percentage",
                        severity="error",
                        explanation=interpretation["explanation"],
                        suggested_fix=interpretation["suggested_fix"],
                    )
                )
                normalized["max_ram_interpretation"] = interpretation
                normalized["max_ram_invalid"] = True
                normalized["max_ram_raw"] = raw_max_ram

        return normalized

    def _flag_impossible_percentage(self, field: str, value: Any, data: dict) -> None:
        """Flag an impossible percentage value."""
        if isinstance(value, (int, float)) and value > 100:
            # For memory-related fields, use intelligent inference
            if "memory" in field.lower() or "ram" in field.lower():
                interpretation = self._infer_memory_unit(value)
                self.issues.append(
                    ValidationIssue(
                        field=field,
                        raw_value=value,
                        issue_type="impossible_percentage",
                        severity="error",
                        explanation=interpretation["explanation"],
                        suggested_fix=interpretation["suggested_fix"],
                    )
                )
                data[f"{field}_interpretation"] = interpretation
            else:
                self.issues.append(
                    ValidationIssue(
                        field=field,
                        raw_value=value,
                        issue_type="impossible_percentage",
                        severity="error",
                        explanation=(
                            f"Field '{field}' has value {value}% which exceeds 100% and is impossible. "
                            f"This is likely a data collection error or unit mismatch."
                        ),
                        suggested_fix="Verify the metric source and unit conversion",
                    )
                )
            data[f"{field}_invalid"] = True
            data[f"{field}_raw"] = value


def validate_host_metrics(metrics: dict | Any) -> dict:
    """
    Validate host metrics data.

    Handles different API response structures:
    - List structure: {"success": True, "data": [{"cpu": 95, "ram": 8471740416, "disk": 50}]}
    - Flat structure: {"cpu": 95, "ram": 8471740416}
    - Nested structure: {"memory": {"percent": 8471740416}}

    Args:
        metrics: Raw metrics data from API

    Returns:
        Validated and normalized metrics dict with interpretation hints
    """
    validator = MetricsValidator()
    if not isinstance(metrics, dict):
        return {
            "raw": metrics,
            "validated": False,
            "data_quality_issues": [
                {
                    "field": "root",
                    "raw_value": metrics,
                    "issue": "invalid_format",
                    "severity": "error",
                    "explanation": "Metrics data is not in expected dictionary format",
                }
            ],
        }

    # Handle list-based structure (common in API responses)
    if "data" in metrics and isinstance(metrics["data"], list):
        validated_data = []
        for data_point in metrics["data"]:
            if isinstance(data_point, dict):
                # Validate each data point
                validated_point = validator._validate_flat_metrics(data_point.copy())
                validated_data.append(validated_point)
            else:
                validated_data.append(data_point)

        result = metrics.copy()
        result["data"] = validated_data

        # Collect all issues from all data points
        all_issues = []
        for point in validated_data:
            if isinstance(point, dict) and "data_quality_issues" in point:
                all_issues.extend(point["data_quality_issues"])

        if all_issues:
            result["data_quality_issues"] = all_issues

        return result

    # Handle flat or nested structure
    return validator.validate_metrics(metrics)
