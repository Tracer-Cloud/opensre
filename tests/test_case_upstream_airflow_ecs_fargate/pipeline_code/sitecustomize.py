from __future__ import annotations

import json
import logging
import os
import sys

from tracer_telemetry import init_telemetry


def _bootstrap_airflow_telemetry() -> None:
    service_name = os.getenv("OTEL_SERVICE_NAME", "airflow-orchestrator")
    resource_attributes = {
        "airflow.process": "orchestrator",
    }

    try:
        init_telemetry(service_name=service_name, resource_attributes=resource_attributes)
        logger = logging.getLogger("airflow.telemetry")
        logger.setLevel(logging.INFO)
        logger.info(
            json.dumps(
                {
                    "event": "airflow_telemetry_bootstrap",
                    "service_name": service_name,
                    "otel_endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                    "otel_protocol": os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", ""),
                    "argv": " ".join(sys.argv[:3]),
                    "pid": os.getpid(),
                }
            )
        )
    except Exception as exc:
        logging.getLogger("airflow.telemetry").warning(
            "Telemetry bootstrap failed: %s", exc
        )


_bootstrap_airflow_telemetry()
