# Test Specification Principles

## 1. Separation of Concerns: Pure Business Logic
**Principle:** Pipeline business logic (`use_case.py`) must be completely isolated from test orchestration and observability code.

**Why:** Simulates real customer code that has no awareness of Tracer, RCA, or investigation infrastructure. Tests the agent's ability to investigate production-like failures.

**Pattern:**
```python
# use_case.py - Pure business logic, no test infrastructure
def extract_and_validate(input_path: str) -> str:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"input file not present: {input_path}")
    return data

# test_orchestrator.py - All test orchestration separate
def main():
    try:
        result = use_case.main()  # Run pure business logic
    except Exception as e:
        # Orchestration: logging, alerting, investigation
```

**Anti-pattern:** Mixing test infrastructure with business logic.

---

## 2. Real End-to-End Testing: No Mocking
**Principle:** Tests must trigger real failures using actual AWS services, real APIs, and actual infrastructure. No mocked services or simulated failures.

**Why:** Validates the agent can investigate production-like scenarios with real AWS APIs, real error messages, and real data lineage.

**Pattern:**
```python
# Trigger actual failure via HTTP to real Lambda
response = requests.post(
    UPSTREAM_DOWNSTREAM_CONFIG["ingester_api_url"],
    json={"correlation_id": correlation_id, "inject_schema_change": True},
)

# Query real CloudWatch logs
logs_client = boto3.client("logs")
response = logs_client.filter_log_events(
    logGroupName=log_group,
    filterPattern=correlation_id,
)
```

**Requirements:**
- Real AWS infrastructure (CloudWatch, S3, Lambda)
- Real HTTP endpoints that can fail
- Real data written to real buckets
- Real error messages from real services

---

## 3. Traceable Investigation Metadata
**Principle:** Every investigation must be decorated with `@traceable` and include comprehensive metadata for observability and debugging.

**Why:** Enables tracking investigation quality, debugging agent behavior, and measuring performance over time in LangSmith.

**Pattern:**
```python
@traceable(
    name=f"Pipeline Investigation - {raw_alert['alert_id'][:8]}",
    metadata={
        "alert_id": raw_alert["alert_id"],
        "pipeline_name": pipeline_name,
        "correlation_id": failure_data["correlation_id"],
        "s3_key": failure_data["s3_key"],
    },
)
def run_investigation():
    return _run(
        alert_name=f"Pipeline failure: {pipeline_name}",
        pipeline_name=pipeline_name,
        severity="critical",
        raw_alert=raw_alert,
    )
```

**Required Metadata:**
- `alert_id`: Unique identifier for this investigation
- `pipeline_name`: Which pipeline is being investigated
- `correlation_id` or `run_id`: Trace the failure through logs
- Context-specific keys (s3_key, log_group, function_name, etc.)

---

## 4. Alert Factory Pattern: Standardized Alert Structure
**Principle:** All tests must use the `create_alert` factory to generate alerts with proper structure and annotations.

**Why:** Ensures consistent alert format, proper context source declarations, and complete metadata for investigation.

**Pattern:**
```python
raw_alert = create_alert(
    pipeline_name=pipeline_name,
    run_name=run_id,
    status="failed",
    timestamp=datetime.now(UTC).isoformat(),
    annotations={
        "s3_bucket": failure_data["bucket"],
        "s3_key": failure_data["s3_key"],
        "correlation_id": failure_data["correlation_id"],
        "error": failure_data["error_message"],
        "lambda_log_group": failure_data["log_group"],
        "function_name": config["mock_dag_function_name"],
        "context_sources": "s3,lambda,cloudwatch",  # Declares available evidence
    },
)
```

**Required Alert Fields:**
- `pipeline_name`: Which pipeline failed
- `run_name`: Unique run identifier
- `status`: "failed" (or "success" for negative tests)
- `timestamp`: When the failure occurred
- `annotations`: All context needed for investigation
- `annotations.context_sources`: Comma-separated list of available evidence sources

---

## 5. Failure-First Test Design
**Principle:** Tests are designed to fail first, capture complete failure context, then invoke investigation. The failure is the test case.

**Why:** Validates the agent can investigate real failures with realistic incomplete information, not artificial success scenarios.

**Test Flow Pattern:**
```python
def main():
    # Step 1: Trigger real failure
    failure_data = trigger_pipeline_failure()
    
    # Step 2: Capture failure context (logs, metrics, data)
    error_message = extract_error_from_cloudwatch(failure_data)
    
    # Step 3: Create alert with captured context
    raw_alert = create_alert(annotations={...failure_data...})
    
    # Step 4: Invoke investigation agent
    result = run_investigation(raw_alert)
    
    # Step 5: Validate investigation quality
    assert result.get('validity_score') > 0.7
```

**Anti-pattern:** Testing happy paths or artificially injecting failures after the fact.

---

## 6. Context Source Annotations: Investigation Strategy Hints
**Principle:** Alerts must explicitly declare which evidence sources are available via `context_sources` annotation.

**Why:** Guides the agent's investigation strategy by declaring upfront what data sources exist (CloudWatch logs, S3 objects, Lambda configs, etc.).

**Pattern:**
```python
annotations={
    # Evidence source declarations
    "context_sources": "s3,lambda,cloudwatch",
    
    # S3 context
    "s3_bucket": "landing-bucket",
    "s3_key": "raw/data/2024/file.json",
    
    # Lambda context
    "function_name": "processor-function",
    "lambda_log_group": "/aws/lambda/processor",
    
    # CloudWatch context
    "cloudwatch_log_group": "/ecs/pipeline",
    "correlation_id": "run-123",
}
```

**Valid Context Sources:**
- `cloudwatch`: CloudWatch logs are available
- `s3`: S3 objects/metadata are available
- `lambda`: Lambda function configs/logs are available
- `batch`: AWS Batch job information
- `tracer_web`: Tracer platform pipeline metadata
- `storage`: General storage layer (S3, EFS, etc.)

**Agent Behavior:** 
The investigation node uses `context_sources` to determine which `investigation_actions` to execute, avoiding wasted API calls to unavailable services.

---
