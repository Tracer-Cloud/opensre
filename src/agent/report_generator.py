"""
Report generator for incident resolution.

Code #2: Evidence-backed report
Functions that assemble root cause, evidence, and recommended fix 
into actionable output (JSON, Slack message, problem.md).
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.report import IncidentReport


def generate_report(report: IncidentReport) -> dict:
    """
    Generate a structured JSON report from the incident report.
    
    This proves the output is actionable, not just chat.
    """
    return {
        "incident_id": report.incident_id,
        "title": report.title,
        "severity": report.severity,
        "detected_at": report.detected_at.isoformat(),
        "root_cause": {
            "description": report.root_cause,
            "confidence": report.root_cause_confidence,
            "hypothesis": report.confirmed_hypothesis.name if report.confirmed_hypothesis else None,
        },
        "hypotheses_evaluated": {
            "confirmed": report.confirmed_hypothesis.name if report.confirmed_hypothesis else None,
            "rejected": report.rejected_hypotheses,
        },
        "evidence": [
            {
                "source": e.source,
                "tool": e.tool_used,
                "finding": e.finding,
                "supports_root_cause": e.supports_hypothesis,
            }
            for e in report.evidence_summary[:5]  # Top 5 evidence items
        ],
        "impact": {
            "affected_systems": report.affected_systems,
            "description": report.impact_description,
        },
        "recommended_actions": [
            {
                "action": a.action,
                "priority": a.priority,
                "automated": a.automated,
            }
            for a in report.recommended_actions
        ],
        "investigation": {
            "duration_seconds": report.investigation_timeline.duration_seconds if report.investigation_timeline else None,
            "steps_executed": report.investigation_timeline.steps_executed if report.investigation_timeline else None,
            "hypotheses_tested": report.investigation_timeline.hypotheses_tested if report.investigation_timeline else None,
        },
        "generated_at": report.generated_at.isoformat(),
    }


def generate_slack_message(report: IncidentReport) -> dict:
    """
    Generate a Slack Block Kit message from the incident report.
    
    Returns a dict that can be sent to Slack's webhook API.
    """
    # Emoji based on severity
    severity_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(report.severity, "⚪")
    
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{severity_emoji} Incident Investigation Complete", "emoji": True}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{report.title}*\n{report.summary}"}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🔍 Root Cause* (confidence: {report.root_cause_confidence:.0%})\n{report.root_cause}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Affected Systems*\n{', '.join(report.affected_systems)}"},
                {"type": "mrkdwn", "text": f"*Impact*\n{report.impact_description}"},
            ]
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📋 Recommended Actions*\n" + "\n".join([
                f"• [{a.priority.upper()}] {a.action}" for a in report.recommended_actions
            ])}
        },
    ]
    
    # Add investigation timeline if available
    if report.investigation_timeline:
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f"🤖 Investigation completed in {report.investigation_timeline.duration_seconds:.1f}s | "
                    f"{report.investigation_timeline.hypotheses_tested} hypotheses tested | "
                    f"{report.investigation_timeline.steps_executed} steps executed"
                )
            }]
        })
    
    return {"blocks": blocks}


def generate_problem_md(report: IncidentReport, output_path: Optional[Path] = None) -> str:
    """
    Generate a problem.md file from the incident report.
    
    This is a permanent record of the incident and its resolution.
    """
    rejected_list = "\n".join([f"- {h}" for h in report.rejected_hypotheses]) or "None"
    evidence_list = "\n".join([
        f"- **{e.source}** ({e.tool_used}): {e.finding}"
        for e in report.evidence_summary[:5]
    ]) or "None collected"
    actions_list = "\n".join([
        f"- [{a.priority.upper()}] {a.action}" + (" *(can be automated)*" if a.automated else "")
        for a in report.recommended_actions
    ]) or "None"
    
    md_content = f"""# Incident Report: {report.title}

**Incident ID:** {report.incident_id}  
**Severity:** {report.severity.upper()}  
**Detected:** {report.detected_at.isoformat()}  
**Report Generated:** {report.generated_at.isoformat()}

---

## Summary

{report.summary}

---

## Root Cause Analysis

**Confidence:** {report.root_cause_confidence:.0%}

{report.root_cause}

### Hypotheses Evaluated

**Confirmed:** {report.confirmed_hypothesis.name if report.confirmed_hypothesis else 'None'}

**Rejected:**
{rejected_list}

### Key Evidence

{evidence_list}

---

## Impact

**Affected Systems:** {', '.join(report.affected_systems)}

{report.impact_description}

---

## Recommended Actions

{actions_list}

---

## Investigation Timeline

- **Started:** {report.investigation_timeline.started_at.isoformat() if report.investigation_timeline else 'N/A'}
- **Completed:** {report.investigation_timeline.completed_at.isoformat() if report.investigation_timeline else 'N/A'}
- **Duration:** {report.investigation_timeline.duration_seconds:.1f}s
- **Steps Executed:** {report.investigation_timeline.steps_executed if report.investigation_timeline else 'N/A'}
- **Hypotheses Tested:** {report.investigation_timeline.hypotheses_tested if report.investigation_timeline else 'N/A'}

---

*Generated by Tracer AI Agent v{report.agent_version}*
"""
    
    if output_path:
        output_path.write_text(md_content)
    
    return md_content

