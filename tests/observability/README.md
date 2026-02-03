# Local Grafana Observability Stack

This stack provides a local Grafana + Alloy + Loki + Tempo + Prometheus setup for
validating traces, metrics, and logs before pushing to Grafana Cloud.

## Start the stack

```bash
cd tests/observability
docker compose up -d
```

Grafana is available at `http://localhost:3000` (anonymous admin enabled).

## Point pipelines to local OTLP

Set the following environment variables for local runs:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
```

The ECS images already default to `127.0.0.1:4317` for OTLP. For local Python
runs, use the env vars above.

## What to expect

- **Traces** show up in the Tempo datasource.
- **Logs** show up in the Loki datasource (structured JSON logs are searchable).
- **Metrics** show up in the Prometheus datasource.

## Stop the stack

```bash
docker compose down
```
