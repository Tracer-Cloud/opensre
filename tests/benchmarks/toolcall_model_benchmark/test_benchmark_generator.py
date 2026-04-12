from __future__ import annotations

from tests.benchmarks.toolcall_model_benchmark.benchmark_generator import (
    CaseMetrics,
    SummaryMetrics,
    parse_args,
)
from tests.benchmarks.toolcall_model_benchmark.readme_updater import (
    render_readme_summary,
)


def _make_cases() -> list[CaseMetrics]:
    return [
        CaseMetrics(
            scenario_id="001-replication-lag",
            run_status="ok",
            duration_seconds=12.5,
            input_tokens=5000,
            output_tokens=1500,
            total_tokens=6500,
            estimated_cost_usd=0.021,
        ),
        CaseMetrics(
            scenario_id="002-connection-exhaustion",
            run_status="error",
            duration_seconds=0.0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_usd=0.0,
            error="timeout",
        ),
    ]


def _make_summary() -> SummaryMetrics:
    return SummaryMetrics(
        case_count=2,
        success_count=1,
        error_count=1,
        total_duration_seconds=12.5,
        avg_duration_seconds=6.25,
        total_input_tokens=5000,
        total_output_tokens=1500,
        total_tokens=6500,
        total_estimated_cost_usd=0.021,
        avg_estimated_cost_usd=0.0105,
    )


class TestRenderReadmeSummary:
    def test_contains_table_header(self) -> None:
        result = render_readme_summary(_make_cases(), _make_summary())
        assert "| Scenario | Status | Duration (s) | Tokens | Est. Cost (USD) |" in result

    def test_contains_all_scenarios(self) -> None:
        result = render_readme_summary(_make_cases(), _make_summary())
        assert "001-replication-lag" in result
        assert "002-connection-exhaustion" in result

    def test_contains_pass_rate(self) -> None:
        result = render_readme_summary(_make_cases(), _make_summary())
        assert "1/2 passed" in result

    def test_contains_total_cost(self) -> None:
        result = render_readme_summary(_make_cases(), _make_summary())
        assert "$0.0210" in result

    def test_contains_link_to_full_report(self) -> None:
        result = render_readme_summary(_make_cases(), _make_summary())
        assert "[docs/benchmarks/results.md]" in result

    def test_table_rows_are_pipe_delimited(self) -> None:
        result = render_readme_summary(_make_cases(), _make_summary())
        data_lines = [ln for ln in result.splitlines() if ln.startswith("| 0")]
        assert len(data_lines) == 2
        for line in data_lines:
            assert line.startswith("|")
            assert line.endswith("|")

    def test_single_scenario(self) -> None:
        cases = [_make_cases()[0]]
        summary = SummaryMetrics(
            case_count=1,
            success_count=1,
            error_count=0,
            total_duration_seconds=12.5,
            avg_duration_seconds=12.5,
            total_input_tokens=5000,
            total_output_tokens=1500,
            total_tokens=6500,
            total_estimated_cost_usd=0.021,
            avg_estimated_cost_usd=0.021,
        )
        result = render_readme_summary(cases, summary)
        assert "1/1 passed" in result

    def test_empty_cases(self) -> None:
        summary = SummaryMetrics(
            case_count=0,
            success_count=0,
            error_count=0,
            total_duration_seconds=0.0,
            avg_duration_seconds=0.0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_tokens=0,
            total_estimated_cost_usd=0.0,
            avg_estimated_cost_usd=0.0,
        )
        result = render_readme_summary([], summary)
        assert "0/0 passed" in result
        assert "| Scenario |" in result


class TestParseArgs:
    def test_defaults(self) -> None:
        args = parse_args([])
        assert args.md_out == "docs/benchmarks/results.md"
        assert args.no_update_readme is False
        assert args.readme_path is None
        assert args.scenario == []

    def test_no_update_readme_flag(self) -> None:
        args = parse_args(["--no-update-readme"])
        assert args.no_update_readme is True

    def test_readme_path_flag(self) -> None:
        args = parse_args(["--readme-path", "/tmp/README.md"])
        assert args.readme_path == "/tmp/README.md"

    def test_scenario_override(self) -> None:
        args = parse_args(["--scenario", "001-replication-lag", "--scenario", "003-storage-full"])
        assert args.scenario == ["001-replication-lag", "003-storage-full"]

    def test_md_out_override(self) -> None:
        args = parse_args(["--md-out", "/tmp/results.md"])
        assert args.md_out == "/tmp/results.md"

    def test_all_flags_together(self) -> None:
        args = parse_args(
            [
                "--scenario",
                "001-replication-lag",
                "--md-out",
                "/tmp/out.md",
                "--no-update-readme",
                "--readme-path",
                "/tmp/README.md",
            ]
        )
        assert args.scenario == ["001-replication-lag"]
        assert args.md_out == "/tmp/out.md"
        assert args.no_update_readme is True
        assert args.readme_path == "/tmp/README.md"
