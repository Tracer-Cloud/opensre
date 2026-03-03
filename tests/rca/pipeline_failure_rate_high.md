# Alert: [FIRING:1] DatasourceNoData — Pipeline Failure Rate High

<!--
  RCA test file — parsed by tests/rca/run_rca_test.py
  Required fields (in the ## Alert Metadata JSON block):
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name (or grafana_folder as fallback)
  The full JSON block is passed as raw_alert.
-->

## Source
Grafana Cloud (grafanacloud-prom)

## Message
**Firing**

Value: [no value]

Labels:
- alertname = DatasourceNoData
- datasource_uid = grafanacloud-prom
- grafana_folder = tracer-ai-agent
- ref_id = A
- rulename = Pipeline Failure Rate High

Annotations:
- Source: https://tracerbio.grafana.net/alerting/grafana/dfcxktck7ej9cb/view?orgId=1

## Alert Metadata

```json
{
  "title": "[FIRING:1] DatasourceNoData tracer-ai-agent (grafanacloud-prom A Pipeline Failure Rate High)",
  "state": "alerting",
  "commonLabels": {
    "alertname": "DatasourceNoData",
    "datasource_uid": "grafanacloud-prom",
    "grafana_folder": "tracer-ai-agent",
    "ref_id": "A",
    "rulename": "Pipeline Failure Rate High",
    "severity": "critical",
    "pipeline_name": "tracer-ai-agent"
  },
  "commonAnnotations": {
    "summary": "Pipeline Failure Rate High - no data received from prometheus datasource",
    "source_url": "https://tracerbio.grafana.net/alerting/grafana/dfcxktck7ej9cb/view?orgId=1"
  }
}
```
