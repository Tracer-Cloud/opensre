# Alert: [synthetic-rds] Connection Exhaustion On payments-prod

<!--
  RCA test file — parsed by tests/rca/run_rca_test.py
  Required fields (in the ## Alert Metadata JSON block):
    commonLabels.severity      → passed as severity to the agent
    commonLabels.pipeline_name → passed as pipeline_name (or service as fallback)
  The full JSON block is passed as raw_alert.

  This test validates issue #656 acceptance criteria by demonstrating:
  - Synthetic RDS alert reliably routes to tracer_data (RCA-capable path)
  - Evidence-backed investigation proceeds with connection metrics
  - Router improvement enables proper incident diagnosis
-->

## Source
AWS CloudWatch (RDS)

## Message
**Firing**

Database connection exhaustion alert on payments-prod. The instance has reached 98% of max_connections with application traffic failing due to connection pool exhaustion.

## Alert Metadata

```json
{
  "title": "[synthetic-rds] Connection Exhaustion On payments-prod",
  "state": "alerting",
  "alert_source": "cloudwatch",
  "commonLabels": {
    "alertname": "RDSDatabaseConnectionsHigh",
    "severity": "critical",
    "pipeline_name": "rds-postgres-synthetic",
    "service": "rds",
    "engine": "postgres"
  },
  "commonAnnotations": {
    "summary": "DatabaseConnections reached 98% of max_connections and application traffic started receiving too many clients errors.",
    "error": "remaining connection slots are reserved for non-replication superuser connections",
    "suspected_symptom": "API requests intermittently fail because the pool cannot obtain new sessions.",
    "db_instance_identifier": "payments-prod",
    "db_instance": "payments-prod",
    "db_cluster": "payments-cluster",
    "cloudwatch_region": "us-east-1",
    "rds_failure_mode": "connection_exhaustion",
    "context_sources": "cloudwatch"
  }
}
```

## Expectations

### Router Behavior
- Message containing alert fields (alertname, severity, state=alerting, db_instance_identifier) should route to `tracer_data` branch
- Router should recognize synthetic incident payload pattern and direct to RCA-capable path

### Investigation Path
- Should extract RDS connection exhaustion symptom from alert summary
- Should retrieve DatabaseConnections metric from mock CloudWatch backend
- Should analyze connection pool saturation as root cause
- Should identify excessive connection usage or pool misconfiguration as potential remediation path

### Investigation Outcome
- Must reach the evidence-backed investigation phase (not general chat path)
- Should produce diagnosis pointing to connection pool exhaustion root cause
- Should distinguish between client-side pool exhaustion vs database-side connection limit issues
