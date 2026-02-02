FROM python:3.11-slim

WORKDIR /app

# Copy telemetry package and install dependencies
COPY ../../shared/telemetry/tracer_telemetry /app/tracer_telemetry
COPY ../../pipeline_code/flink_job/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy Flink job code (copied into build context by CDK)
COPY flink_job /app/flink_job

# Copy entrypoint script
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
