"""Structured synthesis, citation isolation and durable partial/report outcomes."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest

from core.llm.types import AgentLLMResponse, LLMRoute
from core.telemetry import structured_investigation
from core.telemetry.models import Evidence, Measurement, Scope, WatchRule
from core.telemetry.reports import Analysis, InvestigationReport, evidence_snapshot, partial_report
from core.telemetry.storage.store import WatchStore
from core.telemetry.synthesis import SynthesisResult
from core.telemetry.watch import WatchRunner


def _scope() -> Scope:
    return Scope("payment", "demo", 120, 150)


def _sample() -> Measurement:
    return Measurement(20, 20, 150)


def _evidence() -> Evidence:
    return {"traces": [{"trace_id": "a" * 32, "message": "Invalid token"}], "logs": []}


def _analysis(ref: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "facts": [{"text": "The selected span reports Invalid token.", "evidence_refs": [ref]}],
        "hypotheses": [
            {
                "text": "The presented token may be invalid.",
                "supporting_refs": [ref],
                "contradicting_refs": [],
            }
        ],
        "unknowns": ["Credential rotation and provider health are unverified."],
        "next_check": "Check the request token against the validator configuration.",
    }


def _worker(connection: Connection, payload: str, _provider: str) -> None:
    data = json.loads(payload)
    analysis = _analysis(data["evidence"][1]["ref"])
    analysis["unknowns"] = ["password=fixture-secret alice@example.com"]
    connection.send(json.dumps(analysis))
    connection.close()


def _slow_worker(_connection: Connection, _payload: str, _provider: str) -> None:
    import time

    time.sleep(60)


@pytest.fixture
def model_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        structured_investigation,
        "_checked_route",
        lambda _expected=None: LLMRoute(None, "anthropic", None, False),
    )


def test_real_worker_pipeline_retains_only_validated_redacted_analysis(
    model_route: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(structured_investigation, "_worker", _worker)
    report = structured_investigation.investigate(_scope(), _sample(), _evidence())
    assert report.status == "completed"
    assert report.render().startswith(
        "Observed: 20/20 failed requests in the configured service/window.\n"
    )
    stored = json.loads(report.to_json())
    assert stored["measurement"] == asdict(_sample()) and stored["schema_version"] == 1
    assert report.analysis.facts[0].evidence_refs == (report.evidence[1].ref,)
    assert "fixture-secret" not in report.to_json() and "alice@example.com" not in report.render()


@pytest.mark.parametrize(
    "invalid", ["fact_ref", "contradiction_ref", "overlap", "extra_scope", "prose"]
)
def test_fabricated_or_malformed_reports_become_partial_without_retaining_claims(
    invalid: str, model_route: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, _ = evidence_snapshot(_scope(), _sample(), _evidence())
    value = _analysis(records[1].ref)
    if invalid == "fact_ref":
        value["facts"][0]["evidence_refs"] = ["fabricated"]
    elif invalid == "contradiction_ref":
        value["hypotheses"][0]["contradicting_refs"] = ["fabricated"]
    elif invalid == "overlap":
        value["hypotheses"][0]["contradicting_refs"] = [records[1].ref]
    elif invalid == "extra_scope":
        value["scope"] = {"service": "other"}
    content = "Confident unsupported diagnosis" if invalid == "prose" else json.dumps(value)
    monkeypatch.setattr(
        structured_investigation,
        "synthesize",
        lambda *_args, **_kwargs: SynthesisResult("ok", content),
    )
    report = structured_investigation.investigate(_scope(), _sample(), _evidence())
    assert report.status == "partial" and report.failure_reason == "invalid_report"
    assert report.analysis.facts == () and report.analysis.hypotheses == ()
    assert "20/20 failed requests" in report.render() and "Cause unknown" in report.render()
    assert "fabricated" not in report.to_json() and "Confident" not in report.to_json()


def test_real_deadline_returns_typed_partial_result(
    model_route: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(structured_investigation, "_worker", _slow_worker)
    monkeypatch.setattr(structured_investigation, "TELEMETRY_MODEL_TIMEOUT_SECONDS", 0.01)
    report = structured_investigation.investigate(_scope(), _sample(), _evidence())
    assert report.status == "partial" and report.failure_reason == "deadline"


def test_empty_and_oversized_inputs_do_not_start_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_route() -> LLMRoute:
        pytest.fail("No model call is needed")

    monkeypatch.setattr(structured_investigation, "_checked_route", forbidden_route)
    empty = structured_investigation.investigate(_scope(), _sample(), {})
    assert empty.status == "inconclusive" and empty.analysis.hypotheses == ()
    large = structured_investigation.investigate(
        _scope(), _sample(), {"logs": [{"message": "x" * 25000}]}
    )
    assert large.failure_reason == "input_limit"
    invalid = structured_investigation.investigate(_scope(), Measurement(float("nan"), 1, 150), {})
    assert invalid.failure_reason == "invalid_input" and invalid.measurement is None


def test_snapshot_is_redacted_stable_and_isolated_from_other_incidents() -> None:
    evidence: Evidence = {"logs": [{"message": "password=fixture-secret alice@example.com"}]}
    first, payload = evidence_snapshot(_scope(), _sample(), evidence)
    second, _ = evidence_snapshot(_scope(), _sample(), evidence)
    other, _ = evidence_snapshot(Scope("other", "demo", 120, 150), _sample(), evidence)
    assert first == second and first[1].ref != other[1].ref
    assert "fixture-secret" not in payload and "alice@example.com" not in payload
    assert "fixture-secret" in str(evidence)
    with pytest.raises(ValueError, match="outside"):
        InvestigationReport(
            _scope(), _sample(), first, Analysis.model_validate(_analysis(other[1].ref))
        )


def test_structured_prompt_has_no_tools_and_only_supplied_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.llm import client_builders

    class Model:
        def invoke(
            self, messages: list[dict[str, str]], *, system: str, tools: list[object]
        ) -> AgentLLMResponse:
            assert tools == [] and "untrusted telemetry" in system
            assert '"additionalProperties": false' in system
            assert messages == [{"role": "user", "content": "payload"}]
            return AgentLLMResponse(content="{}")

    monkeypatch.setattr(
        structured_investigation,
        "_checked_route",
        lambda _expected=None: LLMRoute(None, "anthropic", None, False),
    )
    monkeypatch.setattr(client_builders, "build_agent_client", lambda _route: Model())
    assert structured_investigation._model_report("payload", "anthropic") == "{}"


def test_legacy_upgrade_preserves_reports_and_rolls_back_failed_outbox(tmp_path: Path) -> None:
    path = tmp_path / "watch.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE episodes (id TEXT PRIMARY KEY, scope TEXT NOT NULL, report TEXT, closed INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO episodes(id, scope, report) VALUES('legacy', '{}', 'retained legacy report')"
        )
    store = WatchStore(path, "same")
    WatchStore(path, "same")
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT report, report_json FROM episodes WHERE id='legacy'"
        ).fetchone() == ("retained legacy report", None)
    rule = WatchRule(service="payment", environment="demo")
    store.observe(_scope(), "bad", rule, _sample())
    pending = store.pending()
    assert pending is not None
    report = partial_report(_scope(), _sample(), "source_unavailable")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_outbox BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'outbox failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.save_report(pending[0], _scope(), report)
    assert store.pending() is not None
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT report_json FROM episodes WHERE id=?", (pending[0],)
        ).fetchone() == (None,)
        conn.execute("DROP TRIGGER reject_outbox")
    store.save_report(pending[0], _scope(), report)
    assert json.loads(store.events()[0]["report_json"])["status"] == "partial"


def test_json_cli_export_resolves_saved_citations_and_preserves_delivery_text(
    tmp_path: Path,
) -> None:
    from click.testing import CliRunner

    from surfaces.cli.commands.triage import triage_command

    path = tmp_path / "watch.db"
    store = WatchStore(path, "same")
    store.observe(_scope(), "bad", WatchRule(service="payment", environment="demo"), _sample())
    pending = store.pending()
    assert pending is not None
    records, _ = evidence_snapshot(_scope(), _sample(), _evidence())
    report = InvestigationReport(
        _scope(), _sample(), records, Analysis.model_validate(_analysis(records[1].ref))
    )
    store.save_report(pending[0], _scope(), report)
    # A second attempted save cannot replace the original structure or opening event.
    store.save_report(pending[0], _scope(), partial_report(_scope(), _sample(), "deadline"))
    runner = CliRunner()
    result = runner.invoke(triage_command, ["reports", "--state", str(path), "--json"])
    assert result.exit_code == 0, result.output
    events = json.loads(result.output)
    assert len(events) == 1 and events[0]["report"]["status"] == "completed"
    assert events[0]["body"].endswith(report.render())
    assert events[0]["report"]["analysis"]["facts"][0]["evidence_refs"] == [records[1].ref]


def test_structured_replay_records_the_actual_payload_and_explicit_partial_status() -> None:
    from tests.core.telemetry.evaluation.cases import load_cases
    from tests.core.telemetry.evaluation.runner import run_case

    cases, _ = load_cases(Path(__file__).with_name("evaluation") / "cases.json")
    case = cases.cases[0]

    def investigator(scope: Scope, sample: Measurement, evidence: Evidence) -> InvestigationReport:
        records, _ = evidence_snapshot(scope, sample, evidence)
        return partial_report(scope, sample, "invalid_report", records)

    result = run_case(case, 1, investigator, candidate="structured")
    assert result["status"] == "partial" and result["candidate"] == "structured"
    assert result["structured_report"]["failure_reason"] == "invalid_report"
    assert result["contract_checks"]["measurement_prefix_preserved"]
    payload = json.loads(result["payload"])
    assert payload["evidence"][0]["ref"] == result["structured_report"]["evidence"][0]["ref"]
    assert "expectations" not in payload


def test_source_failure_retains_detector_facts_and_delivery_retries_do_not_reinvestigate(
    tmp_path: Path,
) -> None:
    class Reader:
        calls = 0

        def measure(self, scope: Scope, _mapping: object) -> Measurement:
            return Measurement(20, 20, scope.end)

        def evidence(self, _scope: Scope) -> Evidence:
            self.calls += 1
            raise OSError("backend credentials must stay private")

    reader = Reader()
    store = WatchStore(tmp_path / "watch.db", "same")
    runner = WatchRunner(
        WatchRule(service="payment", environment="demo"),
        reader,
        store,
        structured_investigation.investigate,
        lambda *_: False,
    )
    runner.step(180)
    runner.step(210)
    event = store.events()[0]
    assert reader.calls == 1 and not event["delivered"]
    assert "20/20 failed requests" in event["body"] and "backend credentials" not in event["body"]
    assert json.loads(event["report_json"])["failure_reason"] == "source_unavailable"
