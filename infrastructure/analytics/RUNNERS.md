# Runner telemetry

Use the runner launcher for OpenSRE workloads started by GitHub Actions. The job
requires `permissions: { contents: read, id-token: write }`.

```bash
uv run python -m infrastructure.analytics.runner_launcher \
  --manifest evidence/run.json -- uv run opensre ask "Your task"

uv run python -m infrastructure.analytics.runner_launcher \
  --docker your-opensre-image --manifest evidence/container.json \
  -- opensre ask "Your task"
```

Each invocation receives a fresh installation profile and renewable runner
credentials. Save the manifest as a workflow artifact; it contains the runtime
installation ID and job/run identifiers, without credentials. Use `--test` for
synthetic checks, and `--require-delivery` when the command records an install and
the job must fail unless ingestion acknowledges it.

The Docker path mounts runner context independently of CI environment variables.
Have the trusted job launch each container through this wrapper. A child does not
receive permission to mint runner credentials itself. Do not
copy only the context JSON into an image: credentials expire and the directory
mount must remain live for renewal. Native child processes must preserve the
provided `OPENSRE_HOME`, `OPENSRE_WIZARD_STORE_PATH`, and
`OPENSRE_EXECUTION_CONTEXT_PATH`. Do not override the
profile or share its installation ID across jobs.

Ingestion verifies GitHub's signature, repository and owner IDs, token lifetime,
and installation-specific audience. Runner verification grants no account or
organization access. Events retain `reported` or `unknown` origin when that
verification is unavailable or invalid; CI detection alone is not verified
runner ownership. The `Runner provenance` workflow emits marked production
canaries and saves their exact IDs for database reconciliation.
