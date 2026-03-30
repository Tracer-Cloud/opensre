# Synthetic RDS PostgreSQL Suite

This suite benchmarks RDS PostgreSQL root-cause analysis against bundled telemetry fixtures instead of live AWS infrastructure.

## Scenarios

- `001-replication-lag`
- `002-connection-exhaustion`

Each scenario folder contains:

- `alert.json`: synthetic alert payload
- `cloudwatch_metrics.json`: summarized CloudWatch metric evidence
- `rds_events.json`: RDS event stream for the incident window
- `performance_insights.json`: top SQL and wait-event evidence
- `fault_script.sh`: reference script showing how the failure was induced
- `answer.yml`: expected category, required keywords, and a canonical model response

## Running

Run the whole suite:

```bash
python -m tests.synthetic_testing.rds_postgres.run_suite
```

Run a single scenario:

```bash
python -m tests.synthetic_testing.rds_postgres.run_suite --scenario 001-replication-lag
```

Print JSON results:

```bash
python -m tests.synthetic_testing.rds_postgres.run_suite --json
```

## Scoring

Each scenario passes when all of the following are true:

- the model returns a non-empty root cause
- the predicted `ROOT_CAUSE_CATEGORY` matches `answer.yml`
- every required keyword from `answer.yml` appears in the root cause, validated claims, non-validated claims, or causal chain
