# LangSmith Run Naming Convention

All test cases now use consistent naming in LangSmith with the format:

```
{test_case_name} - {alert_id[:8]}
```

## Test Case Names

| Test Case | LangSmith Name Pattern | Metadata Included |
|-----------|----------------------|-------------------|
| **Prefect ECS** | `test_prefect_ecs - {alert_id}` | alert_id, pipeline_name, flow_run_id, flow_run_name, ecs_cluster, log_group, s3_key |
| **Lambda Upstream** | `test_lambda_upstream - {alert_id}` | alert_id, pipeline_name, correlation_id, s3_key, lambda_function |
| **Flink ML** | `test_flink_ml - {alert_id}` | alert_id, pipeline_name, correlation_id, s3_key, ecs_cluster, log_group, task_arn |
| **Superfluid** | `test_superfluid - {alert_id}` | alert_id, pipeline_name, trace_id, run_url, run_name |
| **CloudWatch Demo** | `test_cloudwatch_demo - {alert_id}` | alert_id, pipeline_name, run_id, cloudwatch_log_group, log_stream |
| **S3 Failed Python** | `test_s3_failed_python - {alert_id}` | alert_id, pipeline_name, run_id, log_file, s3_bucket |

## Implementation Pattern

Each test wraps the investigation with `@traceable`:

```python
@traceable(
    run_type="chain",
    name=f"test_<name> - {alert['alert_id'][:8]}",
    metadata={
        "alert_id": alert["alert_id"],
        "pipeline_name": pipeline_name,
        # ... test-specific context
    },
)
def run_investigation():
    return _run(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=alert,
    )

result = run_investigation()
```

## Benefits

1. **Easy Filtering**: All runs for a test case can be filtered by prefix (e.g., "test_prefect_ecs")
2. **Unique Identification**: Alert ID ensures each investigation is uniquely identifiable
3. **Rich Metadata**: Context-specific metadata enables deep investigation analysis
4. **Consistent Naming**: All test cases follow the same pattern for easy navigation

## Example LangSmith Run

```
Name: test_prefect_ecs - 148e3d6e
Metadata:
  - alert_id: 148e3d6e-92a5-4f72-87df-963a76affa14
  - pipeline_name: upstream_downstream_pipeline_prefect
  - flow_run_id: flow-123-456
  - flow_run_name: upstream-downstream-2026-01-31
  - ecs_cluster: tracer-prefect-cluster
  - log_group: /ecs/tracer-prefect
  - s3_key: ingested/20260131/data.json
```
