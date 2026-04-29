from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from tests.synthetic.rds_postgres.run_suite import run_scenario, score_result
from tests.synthetic.rds_postgres.scenario_loader import (
    SUITE_DIR,
    load_all_scenarios,
    load_scenario,
)
from tests.synthetic.schemas import VALID_EVIDENCE_SOURCES


def test_load_all_scenarios_reads_benchmark_cases() -> None:
    fixtures = load_all_scenarios()

    scenario_ids = [fixture.scenario_id for fixture in fixtures]
    assert "000-healthy" in scenario_ids
    assert "001-replication-lag" in scenario_ids
    assert "002-connection-exhaustion" in scenario_ids


def test_scenario_metadata_is_valid() -> None:
    fixtures = load_all_scenarios()

    for fixture in fixtures:
        meta = fixture.metadata
        assert meta.schema_version, f"{fixture.scenario_id}: schema_version must be set"
        assert meta.engine, f"{fixture.scenario_id}: engine must be set"
        assert meta.failure_mode, f"{fixture.scenario_id}: failure_mode must be set"
        assert meta.region, f"{fixture.scenario_id}: region must be set"
        assert meta.available_evidence, (
            f"{fixture.scenario_id}: available_evidence must not be empty"
        )
        unknown = set(meta.available_evidence) - VALID_EVIDENCE_SOURCES
        assert not unknown, f"{fixture.scenario_id}: unknown evidence sources {unknown}"


def test_scenario_evidence_matches_available_evidence() -> None:
    fixtures = load_all_scenarios()

    for fixture in fixtures:
        evidence_dict = fixture.evidence.as_dict()
        assert set(evidence_dict.keys()) == set(fixture.metadata.available_evidence), (
            f"{fixture.scenario_id}: evidence keys {set(evidence_dict.keys())} "
            f"do not match available_evidence {fixture.metadata.available_evidence}"
        )


def test_score_result_does_not_apply_failover_wording_to_storage_scenario() -> None:
    fixture = load_scenario(SUITE_DIR / "008-storage-full-missing-metric")

    final_state = {
        "root_cause": (
            "The RDS instance ran out of storage space, and storage space exhaustion is "
            "confirmed by the RDS event plus collapsing WriteIOPS."
        ),
        "root_cause_category": "resource_exhaustion",
        "validated_claims": [
            {"claim": 'RDS event states "DB instance ran out of storage space".'},
        ],
        "non_validated_claims": [],
        "causal_chain": ["Storage filled up, blocked writes, and caused the alert."],
        "evidence": {
            "grafana_logs": [
                {"message": "DB instance ran out of storage space."},
            ]
        },
        "executed_hypotheses": [
            {
                "actions": [
                    "query_grafana_logs",
                    "query_grafana_metrics",
                    "query_grafana_alert_rules",
                ]
            }
        ],
    }

    score = score_result(fixture, final_state)

    assert score.passed is True


def test_score_result_keeps_failover_event_reasoning_requirement() -> None:
    fixture = load_scenario(SUITE_DIR / "005-failover")

    final_state = {
        "root_cause": (
            "A Multi-AZ automatic failover occurred after a health check failure on the "
            "primary host, and workload resumed normally after failover completed. The "
            "timeline shows failover initiated, failover in progress, failover completed, "
            "and instance available as the primary evidence source."
        ),
        "root_cause_category": "infrastructure",
        "validated_claims": [
            {"claim": "The timeline confirms the instance became available again."},
        ],
        "non_validated_claims": [],
        "causal_chain": [
            "Health check failure triggered standby promotion and client reconnection."
        ],
        "evidence": {
            "grafana_logs": [
                {"message": "Failover initiated."},
                {"message": "Failover in progress."},
                {"message": "Failover completed."},
                {"message": "Instance available."},
            ]
        },
        "executed_hypotheses": [
            {
                "actions": [
                    "query_grafana_logs",
                    "query_grafana_metrics",
                    "query_grafana_alert_rules",
                ]
            }
        ],
    }

    score = score_result(fixture, final_state)

    assert score.failure_reason == "RDS events gathered but not used as primary reasoning signal"


def test_score_result_accepts_failover_event_reasoning() -> None:
    fixture = load_scenario(SUITE_DIR / "005-failover")

    final_state = {
        "root_cause": (
            "Based on the RDS event timeline (primary evidence source), a Multi-AZ "
            "automatic failover occurred after a health check failure on the primary "
            "host, and workload resumed normally after failover completed."
        ),
        "root_cause_category": "infrastructure",
        "validated_claims": [
            {
                "claim": (
                    "RDS events show the full sequence: failover initiated -> "
                    "failover in progress -> failover completed -> instance available."
                )
            },
        ],
        "non_validated_claims": [],
        "causal_chain": [
            "Health check failure triggered failover, standby promotion, DNS update, "
            "brief outage, and recovery."
        ],
        "evidence": {
            "grafana_logs": [
                {"message": "Failover initiated."},
                {"message": "Failover in progress."},
                {"message": "Failover completed."},
                {"message": "Instance available."},
            ]
        },
        "executed_hypotheses": [
            {
                "actions": [
                    "query_grafana_logs",
                    "query_grafana_metrics",
                    "query_grafana_alert_rules",
                ]
            }
        ],
    }

    score = score_result(fixture, final_state)

    assert score.passed is True


_ALL_SCENARIOS = load_all_scenarios()
_LLM_ATTEMPTS = 2


def _by_difficulty(level: int) -> list:
    return [f for f in _ALL_SCENARIOS if f.metadata.scenario_difficulty == level]


def _should_assert_trajectory(fixture, actual_category: str) -> bool:
    """Keep trajectory assertions for lower-difficulty non-healthy scenarios."""

    return fixture.metadata.scenario_difficulty <= 2 and actual_category != "healthy"


def _run_scenario_test(fixture) -> None:
    """Run scenario with real LLM and mock Grafana backend, then assert scoring."""
    failures: list[str] = []
    for attempt in range(1, _LLM_ATTEMPTS + 1):
        final_state, score = run_scenario(fixture, use_mock_grafana=True)

        try:
            assert final_state["root_cause"]
            assert score.passed is True, (
                f"{fixture.scenario_id} FAILED: {score.failure_reason}\n"
                f"  actual_category={score.actual_category!r}  "
                f"  missing_keywords={score.missing_keywords}"
            )

            if (
                _should_assert_trajectory(fixture, score.actual_category)
                and score.trajectory is not None
            ):
                assert score.trajectory.sequencing_ok, (
                    f"{fixture.scenario_id} TRAJECTORY FAIL: "
                    f"sequencing={score.trajectory.sequencing_ok} "
                    f"calibration={score.trajectory.calibration_ok}\n"
                    f"  expected={score.trajectory.expected_sequence}\n"
                    f"  actual={score.trajectory.actual_sequence}"
                )
            return
        except AssertionError as exc:
            failures.append(f"attempt {attempt}/{_LLM_ATTEMPTS}: {exc}")

    raise AssertionError("\n\n".join(failures))


@pytest.mark.synthetic
@pytest.mark.parametrize("fixture", _by_difficulty(1), ids=lambda f: f.scenario_id)
def test_level1_scenario(fixture) -> None:
    """Level 1 — single dominant signal, all evidence consistent."""
    _run_scenario_test(fixture)


@pytest.mark.synthetic
@pytest.mark.parametrize("fixture", _by_difficulty(2), ids=lambda f: f.scenario_id)
def test_level2_scenario(fixture) -> None:
    """Level 2 — one confounder present, second evidence source needed to rule it out."""
    _run_scenario_test(fixture)


@pytest.mark.synthetic
@pytest.mark.parametrize("fixture", _by_difficulty(3), ids=lambda f: f.scenario_id)
def test_level3_scenario(fixture) -> None:
    """Level 3 — absent or indirect evidence, key metric missing."""
    _run_scenario_test(fixture)


@pytest.mark.synthetic
@pytest.mark.parametrize("fixture", _by_difficulty(4), ids=lambda f: f.scenario_id)
def test_level4_scenario(fixture) -> None:
    """Level 4 — compositional fault, two failure modes causally linked."""
    _run_scenario_test(fixture)


# ---------------------------------------------------------------------------
# Scenario inheritance unit tests
# ---------------------------------------------------------------------------


def _write_minimal_answer_yml(scenario_dir: Path) -> None:
    (scenario_dir / "answer.yml").write_text(
        textwrap.dedent("""\
        root_cause_category: test_category
        required_keywords:
          - test_keyword
        model_response: "Test model response."
    """)
    )


class TestScenarioInheritance:
    """Verify base-inheritance and evidence-file fallback in scenario_loader."""

    def test_metadata_inherited_from_base(self, tmp_path: Path) -> None:
        """Scenario with base: 000-healthy inherits metadata fields it omits."""
        scenario_dir = tmp_path / "999-test-inherit"
        scenario_dir.mkdir()

        (scenario_dir / "scenario.yml").write_text(
            textwrap.dedent("""\
            base: 000-healthy
            scenario_id: 999-test-inherit
            failure_mode: cpu_saturation
            severity: critical
        """)
        )
        _write_minimal_answer_yml(scenario_dir)

        # Symlink the suite directory so _resolve_base_dir can find 000-healthy.
        # We place our scenario inside the real suite dir temporarily.
        real_dir = SUITE_DIR / "999-test-inherit"
        real_dir.mkdir(exist_ok=True)
        try:
            for f in scenario_dir.iterdir():
                (real_dir / f.name).write_bytes(f.read_bytes())

            fixture = load_scenario(real_dir)

            assert fixture.metadata.scenario_id == "999-test-inherit"
            assert fixture.metadata.failure_mode == "cpu_saturation"
            assert fixture.metadata.severity == "critical"
            # These should be inherited from 000-healthy:
            assert fixture.metadata.engine == "postgres"
            assert fixture.metadata.engine_version == "15"
            assert fixture.metadata.instance_class == "db.r6g.2xlarge"
            assert fixture.metadata.region == "us-east-1"
            assert fixture.metadata.db_instance_identifier == "payments-prod"
            assert fixture.metadata.db_cluster == "payments-cluster"
            assert fixture.metadata.schema_version == "1.0"
            assert "aws_cloudwatch_metrics" in fixture.metadata.available_evidence
        finally:
            for f in real_dir.iterdir():
                f.unlink()
            real_dir.rmdir()

    def test_evidence_falls_back_to_base(self) -> None:
        """Scenario without evidence files loads them from the base."""
        real_dir = SUITE_DIR / "999-test-fallback"
        real_dir.mkdir(exist_ok=True)
        try:
            (real_dir / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 000-healthy
                scenario_id: 999-test-fallback
                failure_mode: healthy
                severity: info
            """)
            )
            _write_minimal_answer_yml(real_dir)

            fixture = load_scenario(real_dir)

            # Evidence should come from 000-healthy (non-None since base has all three)
            assert fixture.evidence.aws_cloudwatch_metrics is not None
            assert fixture.evidence.aws_rds_events is not None
            assert fixture.evidence.aws_performance_insights is not None

            # Alert should also fall back to 000-healthy's
            assert fixture.alert["state"] == "normal"
            assert "payments-prod" in fixture.alert["title"]
        finally:
            for f in real_dir.iterdir():
                f.unlink()
            real_dir.rmdir()

    def test_local_evidence_overrides_base(self) -> None:
        """Scenario with its own evidence file uses it instead of the base's."""
        real_dir = SUITE_DIR / "999-test-override"
        real_dir.mkdir(exist_ok=True)
        try:
            (real_dir / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 000-healthy
                scenario_id: 999-test-override
                failure_mode: healthy
                severity: info
            """)
            )
            _write_minimal_answer_yml(real_dir)

            custom_events = {
                "events": [
                    {
                        "date": "2026-04-01T00:00:00Z",
                        "message": "Custom test event",
                        "source_identifier": "payments-prod",
                        "source_type": "db-instance",
                        "event_categories": ["notification"],
                    }
                ]
            }
            (real_dir / "aws_rds_events.json").write_text(json.dumps(custom_events))

            fixture = load_scenario(real_dir)

            assert fixture.evidence.aws_rds_events is not None
            assert len(fixture.evidence.aws_rds_events) == 1
            assert fixture.evidence.aws_rds_events[0]["message"] == "Custom test event"
        finally:
            for f in real_dir.iterdir():
                f.unlink()
            real_dir.rmdir()

    def test_chained_inheritance_rejected(self) -> None:
        """Declaring base on a scenario that itself has a base raises ValueError."""
        real_dir = SUITE_DIR / "999-test-chain-a"
        real_dir_b = SUITE_DIR / "999-test-chain-b"
        real_dir.mkdir(exist_ok=True)
        real_dir_b.mkdir(exist_ok=True)
        try:
            (real_dir.joinpath("scenario.yml")).write_text(
                textwrap.dedent("""\
                base: 000-healthy
                scenario_id: 999-test-chain-a
                failure_mode: healthy
                severity: info
            """)
            )
            (real_dir_b / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 999-test-chain-a
                scenario_id: 999-test-chain-b
                failure_mode: healthy
                severity: info
            """)
            )
            _write_minimal_answer_yml(real_dir_b)

            with pytest.raises(ValueError, match="Chained inheritance is not supported"):
                load_scenario(real_dir_b)
        finally:
            for d in (real_dir, real_dir_b):
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()

    def test_missing_base_raises(self) -> None:
        """Referencing a non-existent base scenario raises ValueError."""
        real_dir = SUITE_DIR / "999-test-missing-base"
        real_dir.mkdir(exist_ok=True)
        try:
            (real_dir / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 999-nonexistent
                scenario_id: 999-test-missing-base
                failure_mode: healthy
                severity: info
            """)
            )
            _write_minimal_answer_yml(real_dir)

            with pytest.raises(ValueError, match="Base scenario '999-nonexistent' not found"):
                load_scenario(real_dir)
        finally:
            for f in real_dir.iterdir():
                f.unlink()
            real_dir.rmdir()

    def test_no_base_works_unchanged(self) -> None:
        """Scenarios without a base field still load normally."""
        fixture = load_scenario(SUITE_DIR / "000-healthy")
        assert fixture.metadata.scenario_id == "000-healthy"
        assert fixture.metadata.failure_mode == "healthy"
        assert fixture.evidence.aws_cloudwatch_metrics is not None
