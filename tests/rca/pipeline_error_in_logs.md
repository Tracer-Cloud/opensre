# Alert: [tracer] Pipeline Error in Logs

<!--
  RCA test file — parsed by tests/rca/run_rca_test.py
  Required fields (in the ## Alert Metadata JSON block):
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name (or grafana_folder as fallback)
  The full JSON block is passed as raw_alert.
-->

## Source
Datadog log monitor

## Message
Pipeline error detected in tracer-test namespace. Review application logs for the PIPELINE_ERROR pattern. @slack-devs-alerts

More than 0 log events matched in the last 1m against the monitored query: `PIPELINE_ERROR kube_namespace:tracer-test`

| Time | Host | Message |
|------|------|---------|
| 19:38:34 UTC | i-0ff575091ceced162 | PIPELINE_ERROR: Schema validation failed: Missing fields ['customer_id'] in record 0 |

## Alert Metadata

```json
{
  "title": "[tracer] Pipeline Error in Logs",
  "state": "alerting",
  "commonLabels": {
    "alertname": "PipelineErrorInLogs",
    "severity": "critical",
    "pipeline_name": "tracer-test"
  },
  "commonAnnotations": {
    "kube_namespace": "tracer-test",
    "kube_job": "etl-transform-error",
    "summary": "PIPELINE_ERROR: Schema validation failed: Missing fields ['customer_id'] in record 0"
  }
}
```
