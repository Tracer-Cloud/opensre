# Gateway Package Guidance

Gateway unit tests live under this package’s `tests/` tree, not the repo-wide
tests tree.

## Entry points (open these first)

| Role | Path |
|------|------|
| Production entry (slash ports) | CLI: `opensre gateway start` / `--foreground` (composition root outside this package) |
| Package main | `__main__.py` — **fails closed** (no slash-port glue; not a production entry) |
| Process composition root | `core/lifecycle/controller.py` (`GatewayController`; inject `slash_ports_factory`) |
| Surface startup (web + chat composer) | `startup.py` (`start_gateway` / `StartedGateway`) |
| Daemon (pidfile + spawn) | `core/process/supervision.py` — caller passes argv; never names CLI or `surfaces.gateway_entry` |
| Turn callback | `infrastructure/turn_host/turn_runner.py` |
| Turn contract (output, callback) | `infrastructure/turn_host/turn_output.py`, `infrastructure/turn_host/turn_callback.py` |
| Transport registry (name, registration, worker) | `transports/names.py`, `transports/registration.py`, `transports/startup.py` |
| Turn middleware (decision, policy, approvals, stop, locks) | `core/middleware/` |
| Config / transport errors | `core/lifecycle/errors.py` (`GatewayConfigurationError`, `GatewayTransportFailedError`) |
| Web surface (FastAPI) | `web/webapp.py` (`app`) |
| Surface facade | `startup.py` (`start_gateway` / `StartedGateway`) |
| Telegram start | `transports/telegram/startup.py` (`start_telegram_worker`) |
| Slack start | `transports/slack/startup.py` (`start_slack_worker`) |
| Discord start | `transports/discord/startup.py` (`start_discord_worker`) |
| Buzz start | `transports/buzz/startup.py` (`start_buzz_worker`) |

Bare `python -m gateway` and `controller.main()` / `start_gateway()` without
`slash_ports_factory` exit with a clear error. Unit tests may construct
`GatewayController(...)` directly (factory optional there).

## Lifecycle (`GatewayController`)

```
start_gateway()
  → configure_process(GATEWAY_PROFILE)
  → compose turn runner
  → start_surfaces()   # delegates to gateway/startup.py (web + chat)
  → start_scheduler()  # hosts infrastructure.scheduling.scheduler — not a gateway surface
  → ready
```

`gateway/startup.py` starts web and all chat transports; the controller
retains `StartedGateway`. Missing chat credentials are `not configured`;
readiness or runtime failures are `failed`; the rest still start.

The scheduler is a platform component (`infrastructure.scheduling.scheduler`).
This process may host it (`start_scheduler()` →
`scheduler_runners().gated(…).install()` plus `start_background_scheduler()`).
It is not a consumer surface or a transport, and there is no
`gateway/scheduler/` package. Do not move runner code into
`gateway/core/lifecycle/`.

The daemon is pidfile plus spawn in `core/process/supervision.py`. Do not
fold it into a scheduler package. The child argv is surface-owned
(`python -m surfaces.gateway_entry`, or `opensre gateway start --foreground`
when frozen).

`gateway.core` must not import `gateway.transports` or `gateway.web`; only
`controller.py` imports `gateway.startup`. Peer transports and `web` must
not import `gateway.startup`.

## Layout

Packages are split like `core/agent_harness/prompts/`: **core infra** vs
**peer surfaces** vs **composer**.

- `core/` — process and leaf infrastructure (`process`, `lifecycle`,
  `storage`, `billing`, `attachments`, `session`, `config`). No imports from
  transports or `web`. Only `core/lifecycle/controller.py` imports `gateway.startup`.
- `startup.py` — the facade: web + chat as one consumer set via
  `transports/startup.py` (which owns the registry and imports each peer's
  `startup` only).
- `transports/` — chat peers (`slack`, `discord`, `telegram`). Each owns
  settings, inbound worker, security, turn output, and `startup.py`. Peers
  never import each other or `gateway.startup`/`web`; anything two need belongs in
  `core/` (per-turn steps in `gateway.core.middleware`) or
  `infrastructure.turn_host` (turn runner, turn output, session agents).
- `web/` — web surface (FastAPI health app and alert intake).
  May import `core/`; must not import chat transports or `gateway.startup`.
- `core/storage/session/resolver.py` — per-conversation session binding
  keyed by platform; delegates create / resolve / rotate to `SessionManager`.
- `core/storage` — `open_database()` gives a process its one migrated
  `PostgresDatabase` (or `None` without `DATABASE_URL`); each domain's
  `repository.py` has a selector that returns the Postgres or process-local
  implementation. Hosts call those; they do not construct stores.

### Dependency rule (acyclic)

```
core.controller  →  startup.py  →  transports/startup.py  →  transports.{telegram,slack,discord,buzz}.startup
                           →  web
peer transports · web  →  core leaves
(peers never import each other, `gateway.startup`, or each other's packages)
```

Package DAG and peer isolation are pinned by border tests. Keep gateway tests
flat by surface (do not nest a directory named after the Discord PyPI package).

### What a surface may import

The package holds two different things, and only one of them faces outward:

The deployment is the daemon, the transports, the web app, and storage. A
surface may start, stop, or query that process and nothing else. The task
scheduler is hosted here when this process is the long-lived runner; CLI
and shell own loop CRUD and call `request_scheduler_reload()` after writes.

The turn service is `TurnRunner`, the middleware steps, and the
session-agent pool. A surface never imports that. A surface that runs
turns is a channel: it implements the `infrastructure.turn_host` turn contract
and is registered in `gateway.transports`, then handed to the turn
service the same way the four chat transports are.

## Channel vs producer

Two ways work reaches the agent. Mixing them is how a second turn engine appears.

| | Has a user and turn output? | Entry |
|--|------------------------|--------|
| **Channel** (Slack, Telegram, Discord, Buzz) | Yes | `TurnRunner` — `(text, session, output, logger)` |
| **Interactive shell** | Yes | *today:* `HeadlessAgent.handle` with `AgentBuildConfig`. *Target:* the **chat** verb, like any other channel — it has a user and turn output, so the rule already covers it. The build config is shared; the turn entry is not yet. |
| **Producer** (`infrastructure.scheduling.scheduler`, scheduled digest/PR runners) | No | Embed: `AgentSession.run_headless_turn` |

Agent construction hooks live in `core.agent_harness.agent_build_config.AgentBuildConfig` (not the transport registry, not a host re-export). Chat omits the config and the session-agent pool injects gateway capability withholds. The shell sets the build hooks it needs and leaves `apply_capability_policy` unset.

The gateway **process** may host the scheduler (same `process_turn_gate`). That
does not make the scheduler a channel: `infrastructure.scheduling.scheduler` must not import
`TurnRunner`. Pinned by
`tests/test_package_borders.py::test_scheduler_never_imports_the_gateway_turn_runner`.

Three modules are surface-facing today — `core.process.supervision`,
`core.lifecycle.controller`, `web.web_server` — pinned as an exact allowlist in
`tests/shared/test_surface_border.py`. Widening it is a deliberate change, not
a new import.

## Facade verbs

The gateway exposes one turn verb.

| Verb | Entry | Shape | Gets |
|------|-------|-------|------|
| **chat** | `TurnRunner.__call__(text, session, output, logger)` | returns `None`; every result reaches the user through turn output | capacity gate, capability policy, `SessionAgentPool` reuse, approvals, cancel console, identity policy, turn timeout, terminal outcome, at-capacity copy |

Anything with a live user and turn output uses **chat**. That is the rule the
interactive shell is being moved onto — see the table above for where it
stands today.

### Why `chat` returns `None`

The four chat transports are fire-and-forget: turn output *is* the reply path, so
there is nothing to hand back. A caller that needs the turn's outcome as a
value — the shell wants `TurnResult` for accounting, the prompt recorder, and
`final_intent` — is not served by this signature as written. Widening it is a
contract change for all four transports, so decide it deliberately rather than
adding a second entry beside `TurnRunner`.

## Gateway turn dispatch

Every chat transport uses one `TurnRunner` (optional `gate=` for capacity).
Slack, Discord, and Telegram dispatchers are ingress only: authorize, resolve
the session, build turn output, then call the shared callback. Do not add a
second production turn-runner class.

Logging is configured once at process start (`configure_logging` in
`GatewayController.start_gateway`). There is no long-lived gateway `Agent`:
each inbound message gets a per-chat `Session` from `SessionResolver` and
goes through headless dispatch (`core.agent_harness.turns.headless_agent`).

The callback takes exactly four arguments: `text`, `session`, `output`,
`logger`. Do not put `chat_id` on this contract; the output owns transport
details. Resolve action tools from the live per-chat `Session` each turn via
`DefaultToolProvider(session, console)`, same as the interactive shell. Do
not precompute tools at process start. Session create / resolve / rotate /
restore belong to `SessionResolver` → `SessionManager`, not `GatewayController`.

## Tenancy (principal / actor)

Slack, Discord, and Telegram each resolve a `StorageScope` in their own
`transports/<peer>/principal.py` (peer isolation — no cross-imports).

Principal is the silo `ORGANIZATION_ID` (fail closed if missing). Actor is
the platform user id. The turn runs under `bound_storage_scope` so bindings,
sessions, and integrations land on the org mount or `~/.opensre/orgs/<id>/`.

CLI / interactive shell stay unbound (legacy empty principal/actor ids).
`SessionResolver` may adopt a same-document legacy empty-id row into the scoped
key once (`adopt_unscoped_binding` removes the unscoped row) so a second actor
cannot inherit that session.

## Capacity (process gate vs transport pools)

**Cloud scale-out** is a **third** layer above these two. Each organization has
one gateway task with its own gate. When that task saturates (measure with the
`container_memory_*` properties on `gateway_turn_completed`), move agent
execution into separate worker containers. Do not unbound the in-process gate,
and do not redesign `AgentSession.chat`. `PromptQueue` (submit / take / settle /
heartbeat) is the seam a worker queue replaces.

Two different **in-process** limits — do not conflate them:

| Layer | Mechanism | Behavior when full |
|-------|-----------|-------------------|
| **Process** | `TurnConcurrencyGate` / `process_turn_gate()` from `OPENSRE_SIZE_PROFILE` (SMALL=1, MEDIUM=2, LARGE=4) | Chat: non-blocking `try_acquire` (busy drop). Scheduler runners: **blocking** `acquire` (already-claimed work waits). |
| **Per-transport** | `max_concurrent_turns` (defaults to the same profile limit via `turn_limit_for_profile`; override with `*_GATEWAY_MAX_CONCURRENT`) | Caps how many inbound messages that transport may process in parallel *before* they hit the shared turn runner. Does not replace the process gate. |
| **Remote prompts** | `PromptWorker` runs one thread per process-gate slot. `PromptQueue.take` hands out a prompt only while no other running prompt holds its conversation (the actor's own, a `new` one, or a named session id). | Waits up to `PROMPT_SLOT_WAIT_SECONDS` for a slot, then `not_admitted`. A prompt whose conversation is busy stays queued in order. |
| **Heavy work** | `heavy_work_slot` (`infrastructure/process/turn_capacity/heavy_work.py`, `OPENSRE_MAX_CONCURRENT_HEAVY_WORK`, default 2) around clones, coding-agent processes and the CI-repair worker | Waits up to `HEAVY_WORK_WAIT_SECONDS`, then the tool returns a busy error. Independent of the turn gate. |

```text
Telegram/Slack/Discord ──► TurnRunner.try_acquire ──► process_turn_gate()
Scheduler (agent runners) ──► blocking acquire ──► same gate
```

Production chat capacity is on `TurnRunner(gate=controller.turn_gate)`.
`GatewayController` uses
:func:`~infrastructure.turn_host.concurrency.process_turn_gate`.
`ConcurrencyLimitedTurnHandler` is tests-only; production uses `gate=` on
`TurnRunner` only.

Chat analytics use `gateway_turn_*` with `surface`
in {slack, telegram, discord}.

## Agent lifetime

Construct **one** `HeadlessAgent` per logical chat session
(`SessionAgentPool`), then many turns. Each inbound message:

1. `BindableOutput.bind(outer_gateway_output)` — session output on the agent;
   the transport destination changes per turn.
2. `bind_turn(session=…, accounting=…, console=…, tool_hooks=…)` — session /
   cancel / approvals. Do **not** pass `output=` here unless replacing the
   `OutputSink` object itself (then `OutputBindable` ports, e.g. reasoning,
   must follow).
3. `agent.handle(text, TurnBinding(...))` — SessionGoal turn loop +
   `dispatch` per turn. Do **not** wrap this as `AgentSession.chat`
   on the gateway path; the pool owns the agent and calls `handle` directly.

Do **not** build a fresh headless agent on every message. Same-session turns
serialize on the pool’s per-session lock; different sessions stay concurrent
under the capacity gate. Multi-turn scheduled loops should keep one agent for
the loop; true one-shot digests may use `AgentSession.run_headless_turn`.

## Host parity (chat surfaces)

Same turn engine for Slack / Telegram / Discord / interactive shell: ingress →
`TurnRunner` → `SessionAgentPool` → `agent.handle`. Values: **yes** /
**partial** / **no** / **n/a**.

| Concern | Slack | Telegram | Discord |
|---------|-------|----------|---------|
| Cancel / stop mid-turn | **yes** — soft timeout + user `/stop` via `ActiveTurnRegistry` → `output.turn_cancel` | **yes** — same | **yes** — same |
| Approvals / `before_tool_call` | **yes** — Block Kit + `approval_tool_hooks` | **yes** — inline keyboard + `approval_tool_hooks` | **yes** — components + `approval_tool_hooks` |
| Tool resolution | **yes** — live `DefaultToolProvider(session)` | **yes** — same | **yes** — same |
| Output redaction | **yes** — `user_facing_error_message` | **yes** — same | **yes** — same |
| Principal / actor | **yes** — `slack/principal.py` | **yes** — `telegram/principal.py` | **yes** — `discord/principal.py` |
| Capacity gate | **yes** — process gate + transport pool | **yes** — same + TG semaphore | **yes** — same + executor |

**Documented exceptions (do not “fix” by forking a second loop):**

- Gateway chat disables `task_cancel` / llm_provider
  (`infrastructure.turn_host.capability_policy.ensure_gateway_capability_policy`).
- Soft turn timeout **and** user `/stop` / `stop` / `/cancel` set
  `output.turn_cancel` so the ReAct loop / remaining tools stop cooperatively
  (shell `cancel_requested` parity via `CancelConsole` + `ActiveTurnRegistry`).
  Orchestrator skips gather/answer and reports `final_intent=cli_agent_cancelled`;
  live output stops draining stream chunks when the Event fires. `/stop` is
  handled **outside** the per-conversation turn lock so it can interrupt an
  in-flight turn. The executor thread is not killed; in-flight LLM/provider
  calls still finish the current request.
- Telegram write-tool approvals require a non-empty `allowed_user_ids` allowlist
  (same fail-closed posture as Discord).

**Characterization:** cover live-session tools, output redaction, Telegram
approvals, and soft timeout in the gateway test suite.

**Dogfood + smoke (turn-engine regressions):**

| Check | How |
|-------|-----|
| Borders + capacity | Gateway border + concurrency gate tests |
| Smoke gateway startup | Local smoke suite for gateway / `cli.gateway_*` tags |
| Dogfood (dev silo only) | `@mention` on **dev** Slack — thread continuity, Digging in…, `Want me to:` → `yes`; one Socket Mode consumer. |
| Not a substitute | Laptop `opensre gateway` + smoke ≠ dogfood |

## Testing

Gateway E2E regression tests should drive a normalized polled Telegram message
into `handle_polled_inbound_telegram_message(...)` and let it invoke the turn
handler. Do not test this path by swapping in fake LLM clients when validating
command dispatch; prefer explicit registered commands such as `/status` when the
test only needs to validate providers and callback plumbing.
