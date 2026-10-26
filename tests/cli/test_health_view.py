import json
import re
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from rich.console import Console

from infrastructure.terminal.theme import ERROR, HIGHLIGHT, WARNING
from surfaces.shared.terminal.health import (
    _summary_counts,
    render_health_json,
    render_health_report,
    status_badge,
)


def test_status_badge() -> None:
    # Test passed statuses
    for s in ["passed", "pass", "ok", "healthy", " PASSED "]:
        badge = status_badge(s)
        assert badge.plain == "PASSED"
        assert badge.style == f"bold {HIGHLIGHT}"

    # Test warn statuses
    for s in ["warn", "warning", "degraded", "outdated"]:
        badge = status_badge(s)
        assert badge.plain == "WARN"
        assert badge.style == f"bold {WARNING}"

    # Test missing status
    badge = status_badge("missing")
    assert badge.plain == "MISSING"
    assert badge.style == f"bold {WARNING}"

    # Test failed statuses
    for s in ["failed", "fail", "error", "unhealthy"]:
        badge = status_badge(s)
        assert badge.plain == "FAILED"
        assert badge.style == f"bold {ERROR}"

    # Test unknown status
    badge = status_badge("unknown_status")
    assert badge.plain == "UNKNOWN_STATUS"
    assert badge.style == "bold"

    # Test empty status
    badge = status_badge("")
    assert badge.plain == "UNKNOWN"
    assert badge.style == "bold"


def test_summary_counts() -> None:
    results = [
        {"status": "passed"},
        {"status": "PASSED"},
        {"status": "missing"},
        {"status": "failed"},
        {"status": "unknown"},
        {"status": "error"},
    ]
    counts = _summary_counts(results)
    assert counts["passed"] == 2
    assert counts["missing"] == 1
    assert counts["failed"] == 2
    assert counts["other"] == 1


def test_summary_counts_normalizes_failure_aliases() -> None:
    results = [
        {"status": "failed"},
        {"status": "fail"},
        {"status": "error"},
        {"status": "unhealthy"},
        {"status": "FAILED"},
    ]
    counts = _summary_counts(results)
    assert counts["failed"] == 5
    assert counts["other"] == 0


def test_summary_counts_normalizes_passed_aliases() -> None:
    results = [
        {"status": "passed"},
        {"status": "pass"},
        {"status": "ok"},
        {"status": "healthy"},
    ]
    counts = _summary_counts(results)
    assert counts["passed"] == 4
    assert counts["other"] == 0


def test_summary_counts_unknown_stays_other() -> None:
    results = [
        {"status": "warn"},
        {"status": "degraded"},
        {"status": "weird"},
        {"status": ""},
    ]
    counts = _summary_counts(results)
    assert counts["other"] == 4
    assert counts["passed"] == 0
    assert counts["failed"] == 0
    assert counts["missing"] == 0


def test_render_health_json(capsys) -> None:
    environment = "test-env"
    store_path = Path("/tmp/store")
    results = [
        {"service": "aws", "source": "env", "status": "passed", "detail": "ok"},
        {"service": "github", "source": "config", "status": "failed", "detail": "error"},
    ]

    render_health_json(
        environment=environment,
        integration_store_path=store_path,
        results=results,
    )
    captured = capsys.readouterr()
    output = captured.out

    data = json.loads(output)
    assert data["environment"] == environment
    assert data["integration_store"] == str(store_path)
    assert data["summary"] == {"passed": 1, "missing": 0, "failed": 1, "other": 0}
    assert len(data["results"]) == 2
    assert data["results"][0]["service"] == "aws"
    assert data["results"][1]["status"] == "failed"


@patch("infrastructure.safety.guardrails.rules.get_default_rules_path")
def test_render_health_report_folds_long_detail(mock_rules_path: MagicMock) -> None:
    mock_rules_path.return_value = Path("/nonexistent/rules")
    long_detail = (
        "Datasource discovery failed: could not reach http://172.29.15.185:3001 "
        "(HTTPConnectionPool(host='172.29.15.185', port=3001): Max retries exceeded "
        "with url: /api/datasources (Caused by ConnectTimeoutError(...)))"
    )
    console = Console(file=StringIO(), force_terminal=True, width=80)
    render_health_report(
        console=console,
        environment="test",
        integration_store_path="/tmp",
        results=[
            {
                "service": "grafana",
                "source": "local store",
                "status": "failed",
                "detail": long_detail,
            }
        ],
    )
    output = console.file.getvalue()
    assert "grafana" in output
    assert "FAILED" in output
    # Detail column is folded so no visible row spans the full dump. Measure the
    # visible width (strip ANSI colors, which vary with theme state) against the
    # 80-column terminal, not the raw byte length.
    strip_ansi = re.compile(r"\x1b\[[0-9;]*m")
    lines = [
        strip_ansi.sub("", line)
        for line in output.splitlines()
        if "172.29" in line or "ConnectTimeout" in line
    ]
    assert lines, output
    assert all(len(line) <= 80 for line in lines), lines

    # Case 2: Some missing
    console = Console(file=StringIO(), force_terminal=False, width=100)
    render_health_report(
        console=console,
        environment="test",
        integration_store_path="/tmp",
        results=[{"service": "s1", "status": "missing"}],
    )
    output = console.file.getvalue()
    assert "Action: Configure missing integrations" in output

    # Case 3: Some failed
    console = Console(file=StringIO(), force_terminal=False, width=100)
    render_health_report(
        console=console,
        environment="test",
        integration_store_path="/tmp",
        results=[{"service": "s1", "status": "failed"}],
    )
    output = console.file.getvalue()
    assert "Action: Fix failed integrations" in output

    # Case 4: Both missing and failed (failed should take precedence based on if/elif)
    console = Console(file=StringIO(), force_terminal=False, width=100)
    render_health_report(
        console=console,
        environment="test",
        integration_store_path="/tmp",
        results=[
            {"service": "s1", "status": "failed"},
            {"service": "s2", "status": "missing"},
        ],
    )
    output = console.file.getvalue()
    assert "Action: Fix failed integrations" in output
    assert "Action: Configure missing integrations" not in output
