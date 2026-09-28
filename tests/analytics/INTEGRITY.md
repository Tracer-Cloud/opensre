# Installation and account-link integrity probes

Run `uv run python -m pytest tests/analytics/test_integrity_environments.py`.
Set `INTEGRITY_EVIDENCE_DIR` to retain captured request bodies and results.

The dedicated Telemetry integrity workflow runs this suite on native Linux,
macOS, Windows and a Linux Docker container. Each case launches fresh Python
processes using the real anonymous-ID store, install marker, property builders,
telemetry queue, destination resolver, saved account credentials and signatures.
Only the outbound HTTP transport is replaced. The receiver accepts synthetic
fixture credentials exclusively and checks request signatures. The subprocess
environment excludes inherited analytics credentials and destinations.

The cases distinguish these grains:

- Restarting processes with retained storage emits one install identity.
- Replacing storage produces one new install identity per runtime, even though
  each runtime reports `identity_persistence=disk`. Those identities are not
  evidence of separate people or downloads.
- Rejected delivery retries the same deduplication key on the next process.
- Saved personal credentials (persisted the way the login flow stores them
  after its token exchange) keep the anonymous installation ID, sign
  `account_authenticated` with the personal bearer, and sign every request in
  subsequent processes. The interactive login and token exchange are not run.
- A silo's bearer is distinct from a personal account bearer and does not emit
  `account_authenticated` simply because the runtime started.
- Real host/container detection distinguishes native, CI, container and CI
  container traffic; non-CI cases remove inherited CI environment signals.

This suite exercises emitter behavior. It does not download or execute release
installers, recreate a physical host, run the browser login or token exchange,
create Clerk accounts, assert server-side user resolution, or verify delivery
into production ClickHouse/PostHog. Process
restarts with retained versus replaced storage model the identity persistence
boundary independently of the container scheduler. Captured JSON artifacts
contain only generated fixture identities and requests.
