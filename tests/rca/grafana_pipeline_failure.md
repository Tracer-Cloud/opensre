# Alert: [FIRING:1] Pipeline Load Failure — events_fact

<!--
  RCA test file — parsed by tests/rca/run_rca_test.py
  Run via: make test-rca-grafana
  Infra: make grafana-local-up seeds Loki with events_fact pipeline logs.
  Required fields (in the ## Alert Metadata JSON block):
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name
  The full JSON block is passed as raw_alert.
-->

## Source
Grafana (local stack — http://localhost:3000)

## Message
**Firing**

The events_fact ETL pipeline failed at the load stage. The pipeline extracted 128 rows
but could not write to the database — connection refused with an authentication error.

Labels:
- alertname = PipelineLoadFailure
- severity = critical
- pipeline_name = events_fact
- environment = local

Annotations:
- summary = events_fact pipeline failed at load stage: database authentication error
- execution_run_id = local-events-fact-run-001

## Alert Metadata

```json
{
  "title": "[FIRING:1] PipelineLoadFailure critical - events_fact",
  "state": "alerting",
  "commonLabels": {
    "alertname": "PipelineLoadFailure",
    "severity": "critical",
    "pipeline_name": "events_fact",
    "environment": "local"
  },
  "commonAnnotations": {
    "summary": "events_fact pipeline failed at load stage: database authentication error",
    "description": "Pipeline events_fact aborted during load. 128 rows extracted but not written. Database connection failed with authentication error.",
    "execution_run_id": "local-events-fact-run-001",
    "correlation_id": "local-events-fact-corr-001"
  },
  "externalURL": "http://localhost:3000",
  "version": "4",
  "groupKey": "{}:{alertname=\"PipelineLoadFailure\"}",
  "truncatedAlerts": 0
}
```
