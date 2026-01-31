# Airflow ECS Fargate Test Case

This test case deploys Airflow 3.1.6 on ECS Fargate and runs an ETL DAG that
processes data written to S3 by a trigger Lambda. The pipeline is designed to
fail on a schema mismatch so the agent can trace the root cause through logs,
S3 metadata, and the external vendor API audit payload.

## Quick Start

1. Deploy infrastructure:
   - `cd tests/test_case_upstream_airflow_ecs_fargate/infrastructure_code`
   - `./deploy.sh`

2. Update trigger Lambda environment variables after deployment:
   - `AIRFLOW_API_URL` (Airflow REST API URL, include `/api/v1`)
   - `AIRFLOW_API_USERNAME` / `AIRFLOW_API_PASSWORD`

3. Trigger a failing run:
   - `python -m tests.test_case_upstream_airflow_ecs_fargate.trigger_flow`

4. Run the agent investigation:
   - `python -m tests.test_case_upstream_airflow_ecs_fargate.test_agent_e2e`

## Notes

- CloudWatch logs: `/ecs/tracer-airflow`
- DAG ID: `upstream_downstream_pipeline_airflow`
- Trigger endpoint: API Gateway URL from stack outputs
# Airflow ECS Fargate Test Case

This test case deploys Airflow 3.1.6 on ECS Fargate and runs an ETL DAG that
processes data written to S3 by a trigger Lambda. The pipeline is designed to
fail on a schema mismatch so the agent can trace the root cause through logs,
S3 metadata, and the external vendor API audit payload.

## Quick Start

1. Deploy infrastructure:
   - `cd tests/test_case_upstream_airflow_ecs_fargate/infrastructure_code`
   - `./deploy.sh`

2. Update trigger Lambda environment variables after deployment:
   - `AIRFLOW_API_URL` (Airflow REST API URL, include `/api/v1`)
   - `AIRFLOW_API_USERNAME` / `AIRFLOW_API_PASSWORD`

3. Trigger a failing run:
   - `python -m tests.test_case_upstream_airflow_ecs_fargate.trigger_flow`

4. Run the agent investigation:
   - `python -m tests.test_case_upstream_airflow_ecs_fargate.test_agent_e2e`

## Notes

- CloudWatch logs: `/ecs/tracer-airflow`
- DAG ID: `upstream_downstream_pipeline_airflow`
- Trigger endpoint: API Gateway URL from stack outputs
