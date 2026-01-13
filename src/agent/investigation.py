"""
Investigation loop for the incident resolution agent.

Code #1: Investigation loop
The LangChain agent loop that:
- Takes the alert
- Proposes hypotheses
- Calls tools
- Updates hypothesis state

This is the heart of "agentic" behavior.
"""

import json
from datetime import datetime
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from rich.console import Console
from rich.panel import Panel

from src.models.alert import Alert
from src.models.hypothesis import (
    Hypothesis,
    HypothesisStatus,
    Evidence,
    HYPOTHESIS_TEMPLATES,
    create_hypothesis_from_template,
)
from src.models.report import IncidentReport, InvestigationTimeline
from src.tools.s3_tools import list_s3_files, check_success_marker
from src.tools.nextflow_tools import get_pipeline_run, get_step_status, get_step_logs
from src.tools.warehouse_tools import get_table_freshness, get_loader_status


console = Console()


class InvestigationAgent:
    """
    Agent that investigates production incidents using structured hypothesis testing.
    
    The agent:
    1. Receives a normalized alert
    2. Creates hypotheses about potential root causes
    3. Uses tools to gather evidence
    4. Updates hypothesis status based on evidence
    5. Produces a final report
    """
    
    def __init__(self, model: str = "gpt-4o", verbose: bool = True):
        self.llm = ChatOpenAI(model=model, temperature=0)
        self.verbose = verbose
        self.tools: list[BaseTool] = [
            list_s3_files,
            check_success_marker,
            get_pipeline_run,
            get_step_status,
            get_step_logs,
            get_table_freshness,
            get_loader_status,
        ]
        self.hypotheses: list[Hypothesis] = []
        self.evidence_log: list[Evidence] = []
        self.investigation_start: Optional[datetime] = None
        
    def _log(self, message: str, style: str = ""):
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            console.print(message, style=style)
    
    def _initialize_hypotheses(self) -> None:
        """Initialize hypotheses from templates."""
        self.hypotheses = [
            create_hypothesis_from_template(t) for t in HYPOTHESIS_TEMPLATES
        ]
        self._log(f"\n📋 Initialized {len(self.hypotheses)} hypotheses to test", "bold blue")
        for h in self.hypotheses:
            self._log(f"   • {h.name}: {h.description}", "dim")
    
    def _gather_initial_context(self, alert: Alert) -> dict:
        """Gather initial context about the incident."""
        self._log("\n🔍 Gathering initial context...", "bold yellow")
        
        context = {}
        
        # Get table freshness
        if alert.affected_table:
            result = get_table_freshness.invoke({"table_name": alert.affected_table})
            context["table_freshness"] = result
            self._log(f"   📊 {result['message']}")
        
        # Get loader status
        if alert.affected_table:
            result = get_loader_status.invoke({"table_name": alert.affected_table})
            context["loader_status"] = result
            self._log(f"   🔄 {result['message']}")
        
        return context

    def _test_hypothesis(self, hypothesis: Hypothesis, alert: Alert) -> Hypothesis:
        """Test a single hypothesis by gathering evidence."""
        self._log(f"\n🧪 Testing: {hypothesis.name}", "bold cyan")
        hypothesis.status = HypothesisStatus.TESTING
        
        evidence_list = []
        
        # Execute tool calls based on hypothesis
        if hypothesis.id == "h1_transform_failed":
            evidence_list = self._test_transform_failed(alert)
        elif hypothesis.id == "h2_loader_crashed":
            evidence_list = self._test_loader_crashed(alert)
        elif hypothesis.id == "h3_success_marker_missing":
            evidence_list = self._test_success_marker_missing(alert)
        elif hypothesis.id == "h4_upstream_missing":
            evidence_list = self._test_upstream_missing(alert)
        
        # Update hypothesis with evidence
        hypothesis.evidence = evidence_list
        self.evidence_log.extend(evidence_list)

        return hypothesis

    def _test_transform_failed(self, alert: Alert) -> list[Evidence]:
        """Test H1: Nextflow transform step failed."""
        evidence = []

        # Check S3 for output file
        result = list_s3_files.invoke({
            "bucket": "tracer-processed-data",
            "prefix": "events/2026-01-13/"
        })
        output_exists = result["count"] > 0
        evidence.append(Evidence(
            source="s3",
            tool_used="list_s3_files",
            finding=f"Output files: {result['message']}",
            supports_hypothesis=not output_exists,  # If output exists, transform didn't fail
            raw_data=result,
        ))
        self._log(f"   ✓ S3 check: {result['message']}")

        # Check Nextflow step status
        run_result = get_pipeline_run.invoke({"pipeline_id": "events-etl"})
        if run_result.get("found"):
            run_id = run_result["run"]["run_id"]
            step_result = get_step_status.invoke({"run_id": run_id})

            transform_ok = any(
                s["step_name"] == "transform" and s["status"] == "COMPLETED"
                for s in step_result.get("steps", [])
            )
            evidence.append(Evidence(
                source="nextflow",
                tool_used="get_step_status",
                finding=f"Transform step: {'COMPLETED' if transform_ok else 'FAILED'}",
                supports_hypothesis=not transform_ok,
                raw_data=step_result,
            ))
            self._log(f"   ✓ Nextflow transform: {'COMPLETED' if transform_ok else 'FAILED'}")

        return evidence

    def _test_loader_crashed(self, alert: Alert) -> list[Evidence]:
        """Test H2: Service B loader crashed."""
        evidence = []

        result = get_loader_status.invoke({"table_name": alert.affected_table or "events_fact"})
        if result.get("found"):
            loaders = result.get("loaders", [])
            is_waiting = any(l["status"] == "WAITING" for l in loaders)
            evidence.append(Evidence(
                source="warehouse",
                tool_used="get_loader_status",
                finding=f"Loader status: {'WAITING' if is_waiting else 'NOT WAITING'}",
                supports_hypothesis=not is_waiting,  # If waiting, it didn't crash
                raw_data=result,
            ))
            self._log(f"   ✓ Loader: {result['message']}")

        return evidence

    def _test_success_marker_missing(self, alert: Alert) -> list[Evidence]:
        """Test H3: _SUCCESS marker missing."""
        evidence = []

        # Check for _SUCCESS marker
        result = check_success_marker.invoke({
            "bucket": "tracer-processed-data",
            "prefix": "events/2026-01-13/"
        })
        marker_missing = not result["success_marker_exists"]
        evidence.append(Evidence(
            source="s3",
            tool_used="check_success_marker",
            finding=f"_SUCCESS marker: {'MISSING' if marker_missing else 'EXISTS'}",
            supports_hypothesis=marker_missing,
            raw_data=result,
        ))
        self._log(f"   ✓ Success marker: {result['message']}")

        # Check finalize step
        run_result = get_pipeline_run.invoke({"pipeline_id": "events-etl"})
        if run_result.get("found"):
            run_id = run_result["run"]["run_id"]
            step_result = get_step_status.invoke({"run_id": run_id})

            finalize_failed = any(
                s["step_name"] == "finalize" and s["status"] == "FAILED"
                for s in step_result.get("steps", [])
            )
            evidence.append(Evidence(
                source="nextflow",
                tool_used="get_step_status",
                finding=f"Finalize step: {'FAILED' if finalize_failed else 'OK'}",
                supports_hypothesis=finalize_failed,
                raw_data=step_result,
            ))
            self._log(f"   ✓ Finalize step: {'FAILED' if finalize_failed else 'OK'}")

            # Get finalize logs
            if finalize_failed:
                logs_result = get_step_logs.invoke({"run_id": run_id, "step_name": "finalize"})
                evidence.append(Evidence(
                    source="nextflow",
                    tool_used="get_step_logs",
                    finding=f"Finalize logs available ({len(logs_result.get('logs', ''))} chars)",
                    supports_hypothesis=True,
                    raw_data=logs_result,
                ))
                self._log(f"   ✓ Retrieved finalize step logs")

        return evidence

    def _test_upstream_missing(self, alert: Alert) -> list[Evidence]:
        """Test H4: Upstream raw data missing."""
        evidence = []

        result = list_s3_files.invoke({
            "bucket": "tracer-raw-data",
            "prefix": "events/2026-01-13/"
        })
        data_exists = result["count"] > 0
        evidence.append(Evidence(
            source="s3",
            tool_used="list_s3_files",
            finding=f"Raw input files: {result['message']}",
            supports_hypothesis=not data_exists,
            raw_data=result,
        ))
        self._log(f"   ✓ Raw data: {result['message']}")

        return evidence

    def _evaluate_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        """Evaluate a hypothesis based on collected evidence."""
        supporting = sum(1 for e in hypothesis.evidence if e.supports_hypothesis)
        contradicting = len(hypothesis.evidence) - supporting

        if supporting > 0 and contradicting == 0:
            hypothesis.status = HypothesisStatus.CONFIRMED
            hypothesis.confidence = 0.9
            hypothesis.conclusion = f"All {supporting} evidence items support this hypothesis"
        elif contradicting > 0 and supporting == 0:
            hypothesis.status = HypothesisStatus.REJECTED
            hypothesis.confidence = 0.1
            hypothesis.conclusion = f"All {contradicting} evidence items contradict this hypothesis"
        else:
            hypothesis.status = HypothesisStatus.INCONCLUSIVE
            hypothesis.confidence = supporting / (supporting + contradicting) if (supporting + contradicting) > 0 else 0.5
            hypothesis.conclusion = f"Mixed evidence: {supporting} supporting, {contradicting} contradicting"

        status_emoji = {"confirmed": "✅", "rejected": "❌", "inconclusive": "❓"}
        self._log(
            f"   {status_emoji.get(hypothesis.status.value, '?')} "
            f"{hypothesis.name}: {hypothesis.status.value.upper()} "
            f"(confidence: {hypothesis.confidence:.0%})"
        )

        return hypothesis

    def investigate(self, alert: Alert) -> IncidentReport:
        """
        Run the full investigation loop.

        This is the main entry point - takes an alert and produces a report.
        """
        self.investigation_start = datetime.utcnow()
        steps_executed = 0

        console.print(Panel(
            f"[bold]Incident Investigation Started[/bold]\n\n"
            f"Alert: {alert.alert_name}\n"
            f"Severity: {alert.severity}\n"
            f"Affected: {alert.affected_table or 'Unknown'}\n"
            f"Detected: {alert.detected_at}",
            title="🚨 INCIDENT",
            border_style="red"
        ))

        # Initialize hypotheses
        self._initialize_hypotheses()

        # Gather initial context
        context = self._gather_initial_context(alert)
        steps_executed += 2

        # Test each hypothesis
        self._log("\n" + "="*50, "bold")
        self._log("HYPOTHESIS TESTING PHASE", "bold magenta")
        self._log("="*50, "bold")

        for i, hypothesis in enumerate(self.hypotheses):
            hypothesis = self._test_hypothesis(hypothesis, alert)
            hypothesis = self._evaluate_hypothesis(hypothesis)
            self.hypotheses[i] = hypothesis
            steps_executed += 1

        # Find confirmed hypothesis
        confirmed = [h for h in self.hypotheses if h.status == HypothesisStatus.CONFIRMED]
        rejected = [h for h in self.hypotheses if h.status == HypothesisStatus.REJECTED]

        investigation_end = datetime.utcnow()
        duration = (investigation_end - self.investigation_start).total_seconds()

        # Determine root cause
        if confirmed:
            root_cause_hypothesis = max(confirmed, key=lambda h: h.confidence)
            root_cause = self._generate_root_cause_description(root_cause_hypothesis)
            confidence = root_cause_hypothesis.confidence
        else:
            root_cause_hypothesis = None
            root_cause = "Unable to determine root cause with high confidence"
            confidence = 0.0

        # Build report
        from src.models.report import RecommendedAction

        report = IncidentReport(
            incident_id=alert.incident_id,
            alert_name=alert.alert_name,
            severity=alert.severity,
            title=f"{alert.alert_name}: {alert.affected_table or 'Unknown'} freshness breach",
            summary=alert.summary,
            root_cause=root_cause,
            root_cause_confidence=confidence,
            confirmed_hypothesis=root_cause_hypothesis,
            rejected_hypotheses=[h.name for h in rejected],
            evidence_summary=self.evidence_log,
            affected_systems=[alert.affected_table or "events_fact", "Nextflow", "Service B Loader"],
            impact_description=(
                f"{alert.affected_table or 'events_fact'} table was stale. "
                "Downstream analytics delayed until ingestion completes."
            ),
            recommended_actions=[
                RecommendedAction(
                    action="Rerun or fix the Nextflow finalize step to write the _SUCCESS marker",
                    priority="high",
                    estimated_effort="5-10 minutes",
                    automated=True,
                ),
                RecommendedAction(
                    action="Fix IAM permissions for s3:PutObject on the processed bucket",
                    priority="critical",
                    estimated_effort="15-30 minutes",
                    automated=False,
                ),
            ],
            detected_at=alert.detected_at,
            investigation_timeline=InvestigationTimeline(
                started_at=self.investigation_start,
                completed_at=investigation_end,
                duration_seconds=duration,
                steps_executed=steps_executed,
                hypotheses_tested=len(self.hypotheses),
            ),
        )

        return report

    def _generate_root_cause_description(self, hypothesis: Hypothesis) -> str:
        """Generate a human-readable root cause description using LLM."""
        # Collect evidence summaries
        evidence_text = "\n".join([
            f"- {e.source}: {e.finding}" for e in hypothesis.evidence
        ])

        # Get logs if available
        logs_text = ""
        for e in hypothesis.evidence:
            if e.tool_used == "get_step_logs" and e.raw_data:
                logs_text = e.raw_data.get("logs", "")

        prompt = f"""Based on the following evidence, write a clear, technical root cause description.

Hypothesis: {hypothesis.name}
Description: {hypothesis.description}

Evidence collected:
{evidence_text}

{f"Relevant logs:{chr(10)}{logs_text}" if logs_text else ""}

Write a 2-3 sentence root cause description that:
1. States exactly what failed
2. Explains why it failed (if known from logs)
3. Describes the downstream impact

Be specific and technical. Do not use generic language."""

        try:
            response = self.llm.invoke([
                SystemMessage(content="You are an expert SRE analyzing production incidents. Be concise and technical."),
                HumanMessage(content=prompt)
            ])
            return response.content.strip()
        except Exception as e:
            self._log(f"   ⚠️ LLM call failed: {e}", "yellow")
            # Fallback to template-based description
            if hypothesis.id == "h3_success_marker_missing":
                return (
                    "The Nextflow finalize step failed due to S3 permission denied error, "
                    "which prevented the _SUCCESS marker from being written. "
                    "This blocked Service B from loading data into the warehouse."
                )
            return hypothesis.description

    def rank_hypotheses_with_llm(self, alert: Alert) -> list[dict]:
        """Use LLM to rank hypotheses and suggest investigation order."""
        hypotheses_text = "\n".join([
            f"{i+1}. {h.name}: {h.description}"
            for i, h in enumerate(self.hypotheses)
        ])

        prompt = f"""Given this production incident alert, rank the hypotheses by likelihood.

Alert: {alert.alert_name}
Summary: {alert.summary}
Affected: {alert.affected_table or 'Unknown table'}

Hypotheses to rank:
{hypotheses_text}

Return a JSON array ranking hypotheses from most to least likely.
Format: [{{"hypothesis": "name", "likelihood": "high/medium/low", "reason": "brief reason"}}]
Only return the JSON array, no other text."""

        try:
            response = self.llm.invoke([
                SystemMessage(content="You are an expert data engineer triaging incidents. Return only valid JSON."),
                HumanMessage(content=prompt)
            ])
            return json.loads(response.content.strip())
        except Exception as e:
            self._log(f"   ⚠️ LLM ranking failed: {e}", "yellow")
            return []

