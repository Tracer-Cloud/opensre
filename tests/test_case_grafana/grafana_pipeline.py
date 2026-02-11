"""
Grafana Cloud telemetry demo - sends logs, metrics, and traces via OpenTelemetry.
Run: python -m tests.test_case_grafana.grafana_pipeline
"""

import json
import logging
import random
import time
import uuid

from app.outbound_telemetry import init_telemetry, traced_operation
from config.grafana_config import configure_grafana_cloud

SERVICE_NAME = "grafana-etl-pipeline"
log = logging.getLogger(SERVICE_NAME)

STAGES = ["extract", "validate", "transform", "load"]


def run_stage(tracer, run_id: str, stage: str, record_count: int) -> tuple[int, int]:
    with traced_operation(tracer, stage, {"pipeline.stage": stage, "execution.run_id": run_id}) as span:
        time.sleep(random.uniform(0.05, 0.2))
        failed = 0

        if stage == "validate":
            failed = random.randint(0, max(1, record_count // 5))
            if failed:
                log.error(json.dumps({"event": "validation_failures", "run_id": run_id, "stage": stage, "failed": failed}))
        elif stage == "load" and random.random() < 0.15:
            log.error(json.dumps({"event": "load_error", "run_id": run_id, "error": "connection_timeout"}))
            raise RuntimeError("Connection timeout to destination")

        passed = record_count - failed
        span.set_attribute(f"{stage}.input", record_count)
        span.set_attribute(f"{stage}.passed", passed)
        span.set_attribute(f"{stage}.failed", failed)
        log.info(json.dumps({"event": f"{stage}_complete", "run_id": run_id, "passed": passed, "failed": failed}))
        return passed, failed


def run_pipeline(telemetry, run_id: str) -> str:
    with traced_operation(telemetry.tracer, "pipeline_run", {"execution.run_id": run_id, "pipeline.name": SERVICE_NAME}):
        start = time.time()
        records, total_failed, status = random.randint(50, 200), 0, "success"

        try:
            for stage in STAGES:
                records, failed = run_stage(telemetry.tracer, run_id, stage, records)
                total_failed += failed
        except Exception as exc:
            status = "failure"
            log.error(json.dumps({"event": "pipeline_failed", "run_id": run_id, "error": str(exc)}))

        telemetry.record_run(status=status, duration_seconds=time.time() - start, record_count=records, failure_count=total_failed, attributes={"pipeline.name": SERVICE_NAME})
        return status


def main():
    configure_grafana_cloud()
    telemetry = init_telemetry(service_name=SERVICE_NAME, resource_attributes={"pipeline.type": "etl", "environment": "demo"})

    print("\n--- Running 5 pipeline executions -> Grafana Cloud ---\n")
    results = {"success": 0, "failure": 0}
    for i in range(5):
        run_id = uuid.uuid4().hex[:12]
        status = run_pipeline(telemetry, run_id)
        results[status] += 1
        print(f"  Run {i+1}/5  {run_id}  -> {status}")

    telemetry.flush()
    time.sleep(2)
    telemetry.flush()

    print(f"\n--- Done: {results['success']} ok, {results['failure']} failed ---")
    print(f"  Logs:    Explore > Loki  > {{service_name=\"{SERVICE_NAME}\"}}")
    print(f"  Traces:  Explore > Tempo > service.name = {SERVICE_NAME}")
    print("  Metrics: Explore > Mimir > pipeline_runs_total")


if __name__ == "__main__":
    main()
