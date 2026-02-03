FROM python:3.11-slim

WORKDIR /app

# Copy telemetry package and install dependencies
COPY shared/telemetry/tracer_telemetry /app/tracer_telemetry
COPY test_case_upstream_apache_flink_ecs/pipeline_code/flink_job/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy Flink job code
COPY test_case_upstream_apache_flink_ecs/pipeline_code/flink_job /app/flink_job

# Copy entrypoint script
COPY test_case_upstream_apache_flink_ecs/infrastructure_code/flink_image/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
