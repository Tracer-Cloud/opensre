## Lessons

- Avoid AWS Secrets Manager in local Grafana scripts; load `.env`/env vars only.
- When scope is endpoint reachability, delete redundant scripts and avoid extra abstractions.
- Grafana query tests must fail fast (no skips) and use valid minimal queries.
