FROM python:3.11-slim

WORKDIR /app

# Install boto3 for S3 access
RUN pip install --no-cache-dir boto3

# Copy Flink job code (copied into build context by CDK)
COPY flink_job /app/flink_job

# Copy entrypoint script
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
