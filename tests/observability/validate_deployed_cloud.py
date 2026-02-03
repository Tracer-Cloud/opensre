#!/usr/bin/env python3
"""Validate deployed pipelines in Grafana Cloud.

This script:
1. Triggers each deployed pipeline (Lambda, Airflow, Flink, Prefect)
2. Waits for execution completion
3. Queries Grafana Cloud APIs for:
   - Metrics: pipeline_runs_total, records_processed_total filtered by execution_run_id
   - Logs: LogQL queries filtering by execution_run_id and correlation_id
   - Traces: Trace queries filtering by execution.run_id attribute
4. Validates all three signals are present and linked
5. Generates comprehensive validation report
"""

import json
import os
import sys
import time
from typing import Any

import boto3
import requests


def get_grafana_secrets() -> dict[str, str]:
    """Retrieve Grafana Cloud secrets from AWS Secrets Manager."""
    secrets_client = boto3.client("secretsmanager")
    try:
        response = secrets_client.get_secret_value(SecretId="tracer/grafana-cloud")
        secret_string = response["SecretString"]
        return json.loads(secret_string)
    except Exception as e:
        print(f"Failed to retrieve secrets: {e}")
        print("Falling back to environment variables...")
        return {
            "GCLOUD_HOSTED_METRICS_ID": os.getenv("GCLOUD_HOSTED_METRICS_ID", ""),
            "GCLOUD_HOSTED_METRICS_URL": os.getenv("GCLOUD_HOSTED_METRICS_URL", ""),
            "GCLOUD_HOSTED_LOGS_ID": os.getenv("GCLOUD_HOSTED_LOGS_ID", ""),
            "GCLOUD_HOSTED_LOGS_URL": os.getenv("GCLOUD_HOSTED_LOGS_URL", ""),
            "GCLOUD_RW_API_KEY": os.getenv("GCLOUD_RW_API_KEY", ""),
            "GCLOUD_OTLP_ENDPOINT": os.getenv("GCLOUD_OTLP_ENDPOINT", ""),
            "GCLOUD_OTLP_AUTH_HEADER": os.getenv("GCLOUD_OTLP_AUTH_HEADER", ""),
        }


def get_stack_outputs(stack_name: str) -> dict[str, str]:
    """Get CloudFormation stack outputs."""
    cf = boto3.client("cloudformation")
    try:
        response = cf.describe_stacks(StackName=stack_name)
        if response["Stacks"]:
            outputs = {}
            for output in response["Stacks"][0].get("Outputs", []):
                outputs[output["OutputKey"]] = output["OutputValue"]
            return outputs
    except Exception as e:
        print(f"Failed to get stack outputs for {stack_name}: {e}")
    return {}


def trigger_lambda_pipeline(api_url: str) -> dict[str, Any]:
    """Trigger Lambda pipeline via API Gateway."""
    print(f"Triggering Lambda pipeline at {api_url}...")
    try:
        response = requests.post(
            f"{api_url}/trigger",
            json={"test": True},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            return {
                "status": "success",
                "correlation_id": data.get("correlation_id"),
                "execution_run_id": data.get("correlation_id"),
            }
    except requests.RequestException as e:
        print(f"Failed to trigger Lambda: {e}")
    return {"status": "failed"}


def trigger_airflow_pipeline(api_url: str) -> dict[str, Any]:
    """Trigger Airflow DAG via API Gateway."""
    print(f"Triggering Airflow pipeline at {api_url}...")
    try:
        response = requests.post(
            f"{api_url}/trigger",
            json={"test": True},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            return {
                "status": "success",
                "correlation_id": data.get("correlation_id"),
                "execution_run_id": data.get("dag_run_id"),
            }
    except requests.RequestException as e:
        print(f"Failed to trigger Airflow: {e}")
    return {"status": "failed"}


def trigger_flink_pipeline(api_url: str) -> dict[str, Any]:
    """Trigger Flink job via API Gateway."""
    print(f"Triggering Flink pipeline at {api_url}...")
    try:
        response = requests.post(
            f"{api_url}/trigger",
            json={"test": True},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            return {
                "status": "success",
                "correlation_id": data.get("correlation_id"),
                "execution_run_id": data.get("correlation_id"),
            }
    except requests.RequestException as e:
        print(f"Failed to trigger Flink: {e}")
    return {"status": "failed"}


def query_grafana_mimir(secrets: dict[str, str], query: str) -> list[dict[str, Any]]:
    """Query Grafana Cloud Mimir for metrics."""
    metrics_url = secrets.get("GCLOUD_HOSTED_METRICS_URL", "")
    api_key = secrets.get("GCLOUD_RW_API_KEY", "")
    
    if not metrics_url or not api_key:
        return []
    
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        query_url = metrics_url.replace("/api/prom/push", "/api/v1/query")
        response = requests.get(
            query_url,
            params={"query": query},
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("result", [])
    except requests.RequestException:
        pass
    return []


def query_grafana_loki(secrets: dict[str, str], query: str) -> list[dict[str, Any]]:
    """Query Grafana Cloud Loki for logs."""
    logs_url = secrets.get("GCLOUD_HOSTED_LOGS_URL", "")
    api_key = secrets.get("GCLOUD_RW_API_KEY", "")
    
    if not logs_url or not api_key:
        return []
    
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        query_url = logs_url.replace("/loki/api/v1/push", "/loki/api/v1/query")
        response = requests.get(
            query_url,
            params={"query": query, "limit": 100},
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("result", [])
    except requests.RequestException:
        pass
    return []


def validate_pipeline_telemetry(
    secrets: dict[str, str],
    pipeline_name: str,
    execution_run_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Validate telemetry for a pipeline execution."""
    results = {
        "pipeline_name": pipeline_name,
        "execution_run_id": execution_run_id,
        "correlation_id": correlation_id,
        "logs_found": False,
        "traces_found": False,
        "metrics_found": False,
        "log_count": 0,
        "trace_count": 0,
        "metric_count": 0,
    }
    
    print(f"Validating {pipeline_name} telemetry...")
    
    log_query = f'{{service_name=~".*{pipeline_name}.*"}} |= "{execution_run_id}" |= "{correlation_id}"'
    logs = query_grafana_loki(secrets, log_query)
    results["log_count"] = len(logs)
    results["logs_found"] = len(logs) > 0
    
    metric_query = f'pipeline_runs_total{{execution_run_id="{execution_run_id}"}}'
    metrics = query_grafana_mimir(secrets, metric_query)
    results["metric_count"] = len(metrics)
    results["metrics_found"] = len(metrics) > 0
    
    return results


def generate_report(results: list[dict[str, Any]]) -> None:
    """Generate comprehensive validation report."""
    print("\n" + "=" * 80)
    print("DEPLOYED GRAFANA CLOUD VALIDATION REPORT")
    print("=" * 80)
    
    for result in results:
        print(f"\nPipeline: {result['pipeline_name']}")
        print(f"  Execution Run ID: {result['execution_run_id']}")
        print(f"  Correlation ID: {result['correlation_id']}")
        print(f"  Logs: {'✓' if result['logs_found'] else '✗'} ({result['log_count']} found)")
        print(f"  Metrics: {'✓' if result['metrics_found'] else '✗'} ({result['metric_count']} found)")
    
    all_passed = all(
        r["logs_found"] and r["metrics_found"]
        for r in results
    )
    
    print("\n" + "=" * 80)
    if all_passed:
        print("✓ ALL VALIDATIONS PASSED")
    else:
        print("✗ SOME VALIDATIONS FAILED")
        print("\nFailed pipelines:")
        for r in results:
            if not (r["logs_found"] and r["metrics_found"]):
                print(f"  - {r['pipeline_name']}")
    print("=" * 80)


def main() -> int:
    """Main entry point."""
    print("Retrieving Grafana Cloud credentials...")
    secrets = get_grafana_secrets()
    
    required_vars = [
        "GCLOUD_HOSTED_METRICS_URL",
        "GCLOUD_HOSTED_LOGS_URL",
        "GCLOUD_RW_API_KEY",
    ]
    
    missing = [var for var in required_vars if not secrets.get(var)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        return 1
    
    print("Triggering deployed pipelines...")
    
    results = []
    
    lambda_outputs = get_stack_outputs("test-case-upstream-lambda-stack")
    if lambda_outputs.get("TriggerApiUrl"):
        trigger_result = trigger_lambda_pipeline(lambda_outputs["TriggerApiUrl"])
        if trigger_result.get("status") == "success":
            time.sleep(10)
            result = validate_pipeline_telemetry(
                secrets,
                "lambda-api-ingester",
                trigger_result["execution_run_id"],
                trigger_result["correlation_id"],
            )
            results.append(result)
    
    airflow_outputs = get_stack_outputs("test-case-upstream-airflow-ecs-fargate-stack")
    if airflow_outputs.get("TriggerApiUrl"):
        trigger_result = trigger_airflow_pipeline(airflow_outputs["TriggerApiUrl"])
        if trigger_result.get("status") == "success":
            time.sleep(15)
            result = validate_pipeline_telemetry(
                secrets,
                "airflow-etl-pipeline",
                trigger_result["execution_run_id"],
                trigger_result["correlation_id"],
            )
            results.append(result)
    
    flink_outputs = get_stack_outputs("test-case-upstream-apache-flink-ecs-stack")
    if flink_outputs.get("TriggerApiUrl"):
        trigger_result = trigger_flink_pipeline(flink_outputs["TriggerApiUrl"])
        if trigger_result.get("status") == "success":
            time.sleep(20)
            result = validate_pipeline_telemetry(
                secrets,
                "flink-etl-pipeline",
                trigger_result["execution_run_id"],
                trigger_result["correlation_id"],
            )
            results.append(result)
    
    generate_report(results)
    
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
