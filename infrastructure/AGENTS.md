# Infrastructure package

These instructions apply to `infrastructure/` and all of its subdirectories.
The repo-root `AGENTS.md` still applies.

## Purpose

`infrastructure/` owns shared runtime services that sit **outside** the
user-facing application surfaces and **outside** the core agent harness loop.
It may own side effects: telemetry, audit logging, tracing, auth, masking,
sandbox execution, and minimal guardrails.

Configuration-only behavior belongs in `config/`.
Agent orchestration, state, tool planning, and execution contracts belong in
`core/`.

Name packages by **what they do**. Do not create `common/`, `shared/`, or
`util/` drawers. Prefer leaf imports over re-export shims.

The ownership map — which subpackage owns what — is in [`README.md`](./README.md).
Read it before adding a module.

Keep `alert_intake.py` and `asgi_server.py` at the package root: the gateway and
interactive shell share them without importing each other. Do not move them
into a subpackage.

## Import borders

Per **Tier 4** in [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md):

`infrastructure/` may import: `config` (and may cross-import its Tier-4 sibling `core`).

`infrastructure/` must not import: `surfaces`, `gateway`, `bootstrap`, `tools`,
`integrations`.

## Cross-cutting rules

- Keep import-time work light — no threads, network, or heavy I/O at module
  import time.
- Future migrations into this package must leave one canonical import path;
  do not leave compatibility-only forwarding modules.
