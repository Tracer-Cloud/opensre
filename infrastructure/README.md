# Infrastructure

`infrastructure/` contains shared runtime services that sit outside the user-facing
application package and outside the core agent harness loop.

Infrastructure code may own side effects such as telemetry emission, audit logging,
runtime display, tracing, auth verification, masking, sandbox execution, and
minimal guardrails. Configuration-only behavior belongs in `config/`; agent
orchestration, state, tool planning, and tool execution contracts belong in
`core/`.

Name packages by **what they do**. Do not add a `common/` / `shared/` / `util/`
junk drawer. Prefer leaf imports over re-export shims.

## Process & install

- `process/` — exit codes, CLI runtime flags, process-wide turn capacity
  (`process/turn_capacity/`), and installed vs latest release version
  (`process/release_version.py`).
- `setup_state.py` — install/setup facts surfaced to agents through prompt context.
- `alert_intake.py` — minimal HTTP intake (`POST /alerts`) into the process-wide
  inbox, shared by gateway and interactive shell without either surface
  importing the other.
- `asgi_server.py` — generic ASGI transport: run any ASGI app in a background
  thread (`serve_asgi_in_thread`).
- `request_body_limit.py` — shared ASGI middleware that caps mutating request
  bodies before the standalone alert listener, gateway web app, or Slack Events
  API can buffer them.
- `turn_host/` — shared turn host whose single `TurnRunner` serves the
  interactive shell and every gateway transport.
- `harness_providers/` — integration provider registries wired into the harness.

## Contracts

- `errors/` — `OpenSREError` contract (any layer may raise/catch).
- `service_families/` — tool-availability family-key normalization.

## Prompt-sized results

- `text/` — truncate / coerce / URL validation helpers.
- `evidence/` — log and evidence compaction; metric summary for tool results.

## Observability & UX

- `observability/` — logging, tracing, progress, debug output, and runtime
  display ports.
- `logging/` — shell/third-party log handlers.
- `analytics/` — product and runtime analytics.
- `terminal/` — terminal theme and display helpers.

## Safety — `safety/`

- `safety/auth/` — runtime authentication and identity checks.
- `safety/guardrails/` — minimal runtime safety checks outside the core agent loop.
- `safety/masking/` — reversible masking and identifier normalization.
- `safety/sandbox/` — constrained execution environments.

## Scheduled and background work — `scheduling/`

- `scheduling/scheduler/` — cron and agentic loop tasks, hosted by the gateway
  process. Not a gateway submodule: every surface reads and mutates the task
  store, and the gateway only supplies the process and the capacity gate.
- `scheduling/task_types.py` / `scheduling/task_registry.py` — in-flight task
  contract and persistent registry. (Not under a `tasks/` directory — root
  `.gitignore` ignores `tasks/` everywhere.)
- `scheduling/background_investigations/` — background investigation store and types.

## Delivery — `delivery/`

- `delivery/notifications/` — notification delivery transports and
  channel-specific senders.
- `delivery/reporting/` — cross-vendor report-delivery registry and
  surface-agnostic ports.

## Persistence

- `database/` — shared database connection and transaction mechanics; domain
  packages retain ownership of their schemas, migrations, and operational queries.
- `filestorage/` — syncable file storage providers and operations.

## Deploy and packaging — `deployment/`

- `deployment/ec2/` — EC2 AWS primitives and Telegram gateway AMI/systemd
  deploy (`telegram_gateway/`). Makefile: `make deploy-gateway`.
- `deployment/packaging/` — wheel validation and release manifest helpers.
- `deployment/contracts/` — shared deployment models. `SizeProfile` is read at
  runtime by the gateway capacity gate, so this is not build-time-only code.

The Cloudflare Worker for `install.opensre.com` is not Python and lives at
`deployment/cloudflare_install_proxy/`.

Future migrations should move existing modules into this folder incrementally
with import updates and tests. Avoid compatibility-only forwarding modules;
each migration should leave one canonical import path.
