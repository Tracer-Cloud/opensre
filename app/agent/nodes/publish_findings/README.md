# publish_findings

Turns raw `InvestigationState` into a formatted RCA report and delivers it.

## Three-step pipeline

```
InvestigationState
      │
      ▼  1. Extract
report_context.py   build_report_context()
      │              Reads state, builds the evidence catalog (E1, E2, …),
      │              links claims to catalog entries.
      │              Output: ReportContext (plain TypedDict, no logic).
      │
      ▼  2. Format
formatters/report.py
      │              format_slack_message() → mrkdwn text (used for terminal,
      │                                        Slack text fallback, ingest API)
      │              build_slack_blocks()   → Block Kit dicts (Slack cards)
      │
      │              Both renderers share _render_claim_lines() for claims
      │              and call the same formatter helpers in formatters/*.py.
      │
      ▼  3. Deliver
node.py   generate_report()
             render_report()      → terminal
             send_slack_report()  → Slack (text fallback + Block Kit blocks)
             send_ingest()        → Tracer webapp API (stores report_md)
```

## Files

| File | Purpose |
|---|---|
| `node.py` | LangGraph node entry point; orchestrates the three steps |
| `report_context.py` | `ReportContext` TypedDict + `build_report_context` (extract phase) |
| `formatters/report.py` | Text and Block Kit renderers; shared claim-line helper |
| `formatters/evidence.py` | Formats the "Cited Evidence" section |
| `formatters/infrastructure.py` | Formats failed pods and investigation trace |
| `formatters/lineage.py` | Formats data lineage flow |
| `formatters/base.py` | Shared Slack formatting primitives |
| `renderers/terminal.py` | Rich/plain-text terminal output |
| `urls/aws.py` | Builds CloudWatch, S3, Datadog, Grafana deep-link URLs |

## Why two Slack formats?

Slack requires both a plain-text `text` field (shown in notifications and
screen readers) and `blocks` (the visible interactive cards). Both are
generated from the same `ReportContext`; the text format is also reused
verbatim for terminal output and the ingest API's `report_md` field.
