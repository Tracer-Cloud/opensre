# Local Grafana Live

This path runs Tracer against a real local Grafana + Loki stack using live log queries.

![Live local Grafana flow](assets/local-grafana-live-flow.gif)

Level 1 scope:

- real local `Grafana`
- real local `Loki`
- real log queries from Tracer into the local Grafana instance
- seeded accompanying pipeline and supporting telemetry logs in local Loki
- synthetic alert payload

## Prerequisites

- Docker
- Python 3.11+
- `make`
- `ANTHROPIC_API_KEY` in `.env` or your shell

## Run it

```bash
make local-grafana-live
```

To seed or run the RCA step alone (same code the Makefile calls):

```bash
python -m app.cli.wizard.local_demos grafana-seed
python -m app.cli.wizard.local_demos grafana-live --output /tmp/tracer-local-grafana-rca.md
```

This will:

1. Start a local `Grafana + Loki` stack
2. Seed real failure and supporting telemetry logs into local Loki
3. Query those logs through Grafana's datasource proxy API
4. Render an RCA report locally

## Stop the stack

```bash
make grafana-local-down
```

## Notes

This is the live local Grafana MVP. It still uses a synthetic alert payload, but the evidence comes from real queries against a local Grafana instance.

Today this local stack provisions only `Loki`, so the live telemetry here is real local log telemetry. Local Tempo and Prometheus/Mimir are not part of this MVP yet.
