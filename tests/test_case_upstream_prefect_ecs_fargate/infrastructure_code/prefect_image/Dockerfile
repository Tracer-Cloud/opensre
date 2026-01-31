FROM prefecthq/prefect:3-python3.11

WORKDIR /app

# Install boto3 for S3 access
RUN pip install boto3

# Copy pipeline code
COPY ../../pipeline_code/prefect_flow /app/prefect_flow

# Configure Prefect server
RUN prefect config set PREFECT_SERVER_API_HOST=0.0.0.0 && \
    prefect config set PREFECT_SERVER_API_PORT=4200

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 4200

CMD ["/app/entrypoint.sh"]
