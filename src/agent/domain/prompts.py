"""
Prompt builders.

Pure functions that return prompt strings. Easy to test and version.
"""

from src.agent.infrastructure.clients import S3CheckResult, NextflowCheckResult


def s3_interpretation_prompt(result: S3CheckResult) -> str:
    """Build prompt for interpreting S3 check results."""
    return f"""You are investigating a data freshness incident for table events_fact.

You just queried S3 bucket "tracer-processed-data" with prefix "events/2026-01-13/" and got:
- _SUCCESS marker exists: {result.marker_exists}
- Files found: {result.file_count}
- File list: {result.files}

Interpret these findings in 1-2 bullet points. What does this tell us about the pipeline state?
Be concise (under 80 chars per bullet). Start each line with •"""


def nextflow_interpretation_prompt(result: NextflowCheckResult) -> str:
    """Build prompt for interpreting Nextflow check results."""
    return f"""You are investigating a data freshness incident for table events_fact.

You just queried the Nextflow API for pipeline "events-etl" and got:
- Pipeline found: {result.found}
- Finalize step status: {result.status}
- Error message: {result.error or 'none'}
- Logs:
```
{result.logs or 'No logs available'}
```

Interpret these findings in 1-2 bullet points. What does this tell us about why the pipeline failed?
Be concise (under 80 chars per bullet). Start each line with •"""


def root_cause_synthesis_prompt(
    alert_name: str,
    affected_table: str,
    s3_marker_exists: bool,
    s3_file_count: int,
    nextflow_status: str | None,
    nextflow_logs: str | None,
) -> str:
    """Build prompt for synthesizing root cause from all evidence."""
    return f"""You are an expert data infrastructure engineer. You have investigated a production incident and collected the following evidence.

## Incident
- Alert: {alert_name}
- Affected Table: {affected_table}

## Evidence Collected

### S3 Check Results
- _SUCCESS marker exists: {s3_marker_exists}
- Files in output prefix: {s3_file_count}

### Nextflow Pipeline Check Results
- Finalize step status: {nextflow_status}
- Logs:
```
{nextflow_logs or 'No logs available'}
```

## Task
Synthesize these findings into a root cause conclusion.

Respond in exactly this format:
ROOT_CAUSE:
• <first key finding as a bullet point>
• <second key finding as a bullet point>
• <third key finding - the actual root cause>
• <impact on downstream systems>
CONFIDENCE: <number between 0 and 100>

Keep each bullet point concise (under 80 characters). Use exactly 3-4 bullet points."""

