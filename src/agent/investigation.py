"""
Investigation Agent - The heart of agentic incident resolution.

Takes an alert → Tests hypotheses → Produces root cause report.
"""

from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from rich.console import Console
from rich.panel import Panel

from src.models.alert import Alert
from src.models.hypothesis import Hypothesis, HypothesisStatus, Evidence, HYPOTHESIS_TEMPLATES, create_hypothesis_from_template
from src.models.report import IncidentReport, InvestigationTimeline, RecommendedAction
from src.tools.s3_tools import list_s3_files, check_success_marker
from src.tools.nextflow_tools import get_pipeline_run, get_step_status, get_step_logs
from src.tools.warehouse_tools import get_table_freshness, get_loader_status

console = Console()


class InvestigationAgent:
    """Agent that investigates incidents using hypothesis testing."""

    def __init__(self, model: str = "gpt-4o", verbose: bool = True):
        self.llm = ChatOpenAI(model=model, temperature=0)
        self.verbose = verbose
        self.hypotheses: list[Hypothesis] = []
        self.evidence_log: list[Evidence] = []

    def log(self, msg: str, style: str = ""):
        if self.verbose:
            console.print(msg, style=style)

    # ─────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────
    def investigate(self, alert: Alert) -> IncidentReport:
        """Run investigation: alert → hypotheses → evidence → report."""
        start = datetime.utcnow()

        # Show incident panel
        console.print(Panel(
            f"[bold]Incident Investigation Started[/bold]\n\n"
            f"Alert: {alert.alert_name}\nSeverity: {alert.severity}\n"
            f"Affected: {alert.affected_table or 'Unknown'}\nDetected: {alert.detected_at}",
            title="🚨 INCIDENT", border_style="red"
        ))

        # 1. Initialize hypotheses
        self.hypotheses = [create_hypothesis_from_template(t) for t in HYPOTHESIS_TEMPLATES]
        self.log(f"\n📋 Initialized {len(self.hypotheses)} hypotheses to test", "bold blue")
        for h in self.hypotheses:
            self.log(f"   • {h.name}: {h.description}", "dim")

        # 2. Gather context
        self.log("\n🔍 Gathering initial context...", "bold yellow")
        if alert.affected_table:
            r = get_table_freshness.invoke({"table_name": alert.affected_table})
            self.log(f"   📊 {r['message']}")
            r = get_loader_status.invoke({"table_name": alert.affected_table})
            self.log(f"   🔄 {r['message']}")

        # 3. Test each hypothesis
        self.log("\n" + "=" * 50, "bold")
        self.log("HYPOTHESIS TESTING PHASE", "bold magenta")
        self.log("=" * 50, "bold")

        for h in self.hypotheses:
            self._test_and_evaluate(h, alert)

        # 4. Build report
        confirmed = [h for h in self.hypotheses if h.status == HypothesisStatus.CONFIRMED]
        rejected = [h for h in self.hypotheses if h.status == HypothesisStatus.REJECTED]

        if confirmed:
            winner = max(confirmed, key=lambda h: h.confidence)
            root_cause = self._get_root_cause_text(winner)
            confidence = winner.confidence
        else:
            winner, root_cause, confidence = None, "Unable to determine root cause", 0.0

        end = datetime.utcnow()
        return IncidentReport(
            incident_id=alert.incident_id,
            alert_name=alert.alert_name,
            severity=alert.severity,
            title=f"{alert.alert_name}: {alert.affected_table or 'Unknown'} freshness breach",
            summary=alert.summary,
            root_cause=root_cause,
            root_cause_confidence=confidence,
            confirmed_hypothesis=winner,
            rejected_hypotheses=[h.name for h in rejected],
            evidence_summary=self.evidence_log,
            affected_systems=[alert.affected_table or "events_fact", "Nextflow", "Service B Loader"],
            impact_description=f"{alert.affected_table or 'events_fact'} table was stale.",
            recommended_actions=[
                RecommendedAction(action="Rerun Nextflow finalize step", priority="high", estimated_effort="5-10 min", automated=True),
                RecommendedAction(action="Fix IAM permissions for s3:PutObject", priority="critical", estimated_effort="15-30 min", automated=False),
            ],
            detected_at=alert.detected_at,
            investigation_timeline=InvestigationTimeline(
                started_at=start, completed_at=end,
                duration_seconds=(end - start).total_seconds(),
                steps_executed=len(self.hypotheses) + 2,
                hypotheses_tested=len(self.hypotheses),
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # HYPOTHESIS TESTING (data-driven, single method)
    # ─────────────────────────────────────────────────────────────
    def _test_and_evaluate(self, h: Hypothesis, alert: Alert):
        """Test a hypothesis and update its status."""
        self.log(f"\n🧪 Testing: {h.name}", "bold cyan")
        h.status = HypothesisStatus.TESTING
        h.evidence = []

        # Route to the right test based on hypothesis ID
        if h.id == "h1_transform_failed":
            self._check_s3_output(h)
            self._check_step(h, "transform")
        elif h.id == "h2_loader_crashed":
            self._check_loader(h, alert)
        elif h.id == "h3_success_marker_missing":
            self._check_success_marker(h)
            self._check_step(h, "finalize", get_logs=True)
        elif h.id == "h4_upstream_missing":
            self._check_s3_raw(h)

        self.evidence_log.extend(h.evidence)
        self._evaluate(h)

    def _check_s3_output(self, h: Hypothesis):
        """Check if processed output files exist."""
        r = list_s3_files.invoke({"bucket": "tracer-processed-data", "prefix": "events/2026-01-13/"})
        h.evidence.append(Evidence(source="s3", tool_used="list_s3_files", finding=f"Output files: {r['message']}", supports_hypothesis=(r["count"] == 0), raw_data=r))
        self.log(f"   ✓ S3 check: {r['message']}")

    def _check_s3_raw(self, h: Hypothesis):
        """Check if raw input files exist."""
        r = list_s3_files.invoke({"bucket": "tracer-raw-data", "prefix": "events/2026-01-13/"})
        h.evidence.append(Evidence(source="s3", tool_used="list_s3_files", finding=f"Raw input files: {r['message']}", supports_hypothesis=(r["count"] == 0), raw_data=r))
        self.log(f"   ✓ Raw data: {r['message']}")

    def _check_success_marker(self, h: Hypothesis):
        """Check if _SUCCESS marker exists."""
        r = check_success_marker.invoke({"bucket": "tracer-processed-data", "prefix": "events/2026-01-13/"})
        missing = not r["success_marker_exists"]
        h.evidence.append(Evidence(source="s3", tool_used="check_success_marker", finding=f"_SUCCESS marker: {'MISSING' if missing else 'EXISTS'}", supports_hypothesis=missing, raw_data=r))
        self.log(f"   ✓ Success marker: {r['message']}")

    def _check_step(self, h: Hypothesis, step_name: str, get_logs: bool = False):
        """Check Nextflow step status."""
        run = get_pipeline_run.invoke({"pipeline_id": "events-etl"})
        if not run.get("found"):
            return
        run_id = run["run"]["run_id"]
        steps = get_step_status.invoke({"run_id": run_id})
        step = next((s for s in steps.get("steps", []) if s["step_name"] == step_name), None)
        if step:
            failed = step["status"] == "FAILED"
            h.evidence.append(Evidence(source="nextflow", tool_used="get_step_status", finding=f"{step_name.title()} step: {step['status']}", supports_hypothesis=failed, raw_data=steps))
            self.log(f"   ✓ Nextflow {step_name}: {step['status']}")
            if get_logs and failed:
                logs = get_step_logs.invoke({"run_id": run_id, "step_name": step_name})
                h.evidence.append(Evidence(source="nextflow", tool_used="get_step_logs", finding=f"{step_name} logs retrieved", supports_hypothesis=True, raw_data=logs))
                self.log(f"   ✓ Retrieved {step_name} step logs")

    def _check_loader(self, h: Hypothesis, alert: Alert):
        """Check loader status."""
        r = get_loader_status.invoke({"table_name": alert.affected_table or "events_fact"})
        if r.get("found"):
            waiting = any(l["status"] == "WAITING" for l in r.get("loaders", []))
            h.evidence.append(Evidence(source="warehouse", tool_used="get_loader_status", finding=f"Loader: {'WAITING' if waiting else 'NOT WAITING'}", supports_hypothesis=not waiting, raw_data=r))
            self.log(f"   ✓ Loader: {r['message']}")

    def _evaluate(self, h: Hypothesis):
        """Evaluate hypothesis based on evidence."""
        supporting = sum(1 for e in h.evidence if e.supports_hypothesis)
        total = len(h.evidence)

        if supporting == total and total > 0:
            h.status, h.confidence = HypothesisStatus.CONFIRMED, 0.9
        elif supporting == 0:
            h.status, h.confidence = HypothesisStatus.REJECTED, 0.1
        else:
            h.status, h.confidence = HypothesisStatus.INCONCLUSIVE, supporting / total

        emoji = {"confirmed": "✅", "rejected": "❌", "inconclusive": "❓"}
        self.log(f"   {emoji.get(h.status.value, '?')} {h.name}: {h.status.value.upper()} (confidence: {h.confidence:.0%})")

    # ─────────────────────────────────────────────────────────────
    # ROOT CAUSE DESCRIPTION
    # ─────────────────────────────────────────────────────────────
    def _get_root_cause_text(self, h: Hypothesis) -> str:
        """Generate root cause description (LLM with fallback)."""
        evidence = "\n".join(f"- {e.source}: {e.finding}" for e in h.evidence)
        logs = next((e.raw_data.get("logs", "") for e in h.evidence if e.tool_used == "get_step_logs"), "")

        try:
            resp = self.llm.invoke([
                SystemMessage(content="You are an SRE. Be concise and technical."),
                HumanMessage(content=f"Write a 2-3 sentence root cause for:\n\nHypothesis: {h.name}\nEvidence:\n{evidence}\n{f'Logs: {logs}' if logs else ''}")
            ])
            return resp.content.strip()
        except Exception as e:
            self.log(f"   ⚠️ LLM call failed: {e}", "yellow")
            # Fallback
            if h.id == "h3_success_marker_missing":
                return "The Nextflow finalize step failed due to S3 permission denied error, which prevented the _SUCCESS marker from being written. This blocked Service B from loading data into the warehouse."
            return h.description

