FROM prefecthq/prefect:3-python3.11

WORKDIR /app

# Copy app and config
COPY app/__init__.py /app/app/__init__.py
COPY config /app/config

# Copy pipeline code
COPY tests/e2e/upstream_prefect_ecs_fargate/pipeline_code/prefect_flow /app/prefect_flow

# Install pipeline dependencies
RUN pip install --no-cache-dir -r /app/prefect_flow/requirements.txt

# Configure Prefect server
RUN prefect config set PREFECT_SERVER_API_HOST=0.0.0.0 && \
    prefect config set PREFECT_SERVER_API_PORT=4200

COPY tests/e2e/upstream_prefect_ecs_fargate/infrastructure_code/prefect_image/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 4200

CMD ["/app/entrypoint.sh"]
