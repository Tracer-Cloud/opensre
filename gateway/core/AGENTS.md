# gateway/core/ — process and leaf infrastructure

Gateway core machinery used by every surface. **Must not** import
`gateway.transports.*` or `gateway.web`. Only `lifecycle/controller.py` starts
those surfaces (via `gateway.startup`). Pinned by
`gateway/tests/test_package_borders.py`.

| Package | Role |
|---------|------|
| `lifecycle/` | Composition root (`controller`), credential hydration, gateway errors |
| `process/` | Daemon, polling thread, readiness |
| `middleware/` | Per-turn steps every transport runs (inbound decision, identity policy, approvals, attention, locks) |
| `storage/` | Session bindings + investigation, event and feedback stores |
| `billing/` | Credits client |
| `attachments/` | Attachment helpers |
| `session/` | Gateway chat-context helpers |
| `config/` | Logging / gateway config helpers |
| `infrastructure.turn_host` | Turn handler, session-agent pool, bindable output, cancel console, capacity |

Transports and `web/` may import the packages above. Chat transport code does
not belong in `gateway/core/`.

## Who may drive the agent

Only `infrastructure.turn_host` builds ports, binds a turn, runs or flushes a
session, and formats goal progress. Other `gateway/` code may import harness
**types** (`SessionCore`, `OutputSink`, `SlashPortsFactory`, `SessionGoal`)
for signatures. A transport that executes a turn itself is a second handler.

Pinned by `gateway/tests/test_harness_behaviour_border.py`, whose allowlist can
only shrink. `web/` is on it today because `POST /investigate` embeds the agent
directly and therefore gets none of the turn-host guarantees (agent reuse,
approvals hooks, cancel console, capability policy).

## Taking a capacity slot

Every turn in this process — chat, `POST /investigate`, the investigation
worker, a scheduled run — takes one permit from the same
`process_turn_gate()`. Pick a policy from
`infrastructure.process.turn_capacity`; do not pair `acquire`/`release` by hand:

- `turn_slot(gate)` — **drop** when full. For a caller holding a connection or
  a conversation: it yields `False` and the caller answers (chat finalizes
  `AT_CAPACITY_MESSAGE`, web returns it as a 503 body).
- `queued_turn_slot(gate)` — **wait** for a slot. For work already claimed from
  a queue, which cannot be told to try again.

A missing `finally` leaks a permit, and a leaked permit is a process that
answers "at capacity" forever.

## Process boot vs lifecycle

Shared process setup (env → Sentry → harness adapters → capability warnings →
LLM preload) is :func:`bootstrap.process.configure_process` with
``GATEWAY_PROFILE``. After logging and credentials,
`GatewayController.start_gateway` configures the process, builds **one**
`TurnRunner(gate=…)`, then `start_surfaces()` and `start_scheduler()`.
Do not wrap the turn runner. Do not duplicate process boot in the controller.
Hosting is a thin call: `scheduler_runners().gated(turn_gate).install()` then
:func:`infrastructure.scheduling.scheduler.runner.start_background_scheduler`. Reload is
:func:`infrastructure.scheduling.scheduler.reload_signal.request_scheduler_reload` (shell/CLI
writers); the controller only polls and resyncs.

Process boot has one entrypoint: :func:`bootstrap.process.configure_process`
with ``GATEWAY_PROFILE``. Do not add a gateway-local wrapper around it.
