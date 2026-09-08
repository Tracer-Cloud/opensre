# agent_harness/ package rules

`agent_harness/` is the decoupled host for the single `core.agent.Agent` ReAct
loop: the same model calls tools, observes results, and writes the final answer.
It was extracted out of `interactive_shell` so the same harness can run the
interactive terminal and be invoked headlessly via
`agent_harness.turns.headless_agent`.

## Host API (teach this)

Prefer `AgentSession.start()` / `start_embedded_session()` → `.chat` —
not free-function turn dumps. Construct one agent per logical
session (or scheduled loop), then many turns — do not rebuild every message.
Process boot (`configure_process`) and headless construction
(`DefaultHeadlessBuild.agent`) are separate layers. `core` must not import
`bootstrap`; embedded hosts use `bootstrap.embedded.start_embedded_session`.

| Path | Call |
|------|------|
| Process boot (once) | `configure_process(PROFILE)` — adapters only; not agent construction |
| Happy path (already booted) | `AgentSession.start()` → repeated `.chat` |
| Embedded / script | `start_embedded_session()` → repeated `.chat` |
| Multi-step / keep-going | `start` / `start_embedded_session` → `.chat_until_goal(...)` (`SessionGoal` loop) |
| Custom host | **`DefaultHeadlessBuild(...).agent(...)`** (or `InMemoryHeadlessBuild` in-memory; the only construction seam) → `agent.handle(text, TurnBinding(...))` per message |
| Gateway + interactive shell | `TurnRunner` → `SessionAgentPool` → `DefaultHeadlessBuild.agent` once / session → `agent.handle(...)` per message (SessionGoal loop lives in `run_goal`, which `handle` wraps) |
| CLI `ask` (one-shot) | `AgentSession.start(..., tool_hooks=…)` → `.chat(prompt)` — uses `dispatch`, not the SessionGoal turn loop |
| Host-specific construction | Optional `AgentBuildConfig` (`agent_build_config.py`) — tools / prompts / capability policy. Expand with `resolve_agent_ports` (shared by the pool and `build_shell_agent`). `None` on a field keeps the host default; `apply_capability_policy=None` means do not mutate the session |
| Scheduled one-shot | `AgentSession.run_headless_turn(...)` (not the multi-turn pattern) |

`SessionGoal` (`session_goal/` component — `goal` + `run_until`) is
**not** the ReAct `Goal` / `goal_review`. User-facing word is `/goal` only
(show / set / pause / resume / edit / clear). Status `paused` stops session-goal
continuation but keeps state; `host_owned` blocks handoff replace while
**active or paused**. While a goal is **attached** (active or paused),
`run_turn` suppresses Want-me-to closers. Completion is judged by
`session_goal/evaluate.py`, independent of model self-report: checklist items
are ticked only through the `session_goal_complete` tool (a cheap-model
validator in `session_goal/validate.py` can refuse a tick), and a cheap-model
transcript judge (`session_goal/judge.py`) returns met / not yet / impossible
with a reason. `met` still needs successful tool work this turn or stored
findings. The judge client is injected: `HeadlessAgent(judge_llm_factory=…)`
builds the loop's evaluate with `evaluate.build_session_goal_evaluator`;
`DefaultHeadlessBuild` passes the classification-tier factory, in-memory
builds pass none (only a fully ticked checklist closes a goal). Host reason
strings live in `SessionGoalReason`. Reason derive:
`session_goal.goal.derive_session_goal_reason`. Progress (presentation only):
`session_goal/progress.py` (`SESSION_GOAL_PROGRESS_MARK`). Continuation prompts:
`session_goal/continuation.py`. Flush/restore: `session_goal/persist.py`.
Package rules: `session_goal/AGENTS.md`. Borders SoT (local notes):
`opensre-notes/goal-core-system-design-aug2026.html`.

**Evidence kinds (open/closed):** vocabulary + per-kind policy live in
`turns/evidence_kind.py` (`EvidenceKind` + `EvidenceKindPolicy`). Add a kind by
extending the enum and registering its policy row — do **not** grow
`if kind is …` branches in `classify_evidence_need`. Tool schema `enum` is
`EVIDENCE_KIND_VALUES` (derived), not a parallel hard-coded list. Preferred
integration ids stay opt-in via `infrastructure.harness_providers.register_preferred_evidence_source`.

**Evidence kinds:** vocabulary + per-kind policy still live in
`turns/evidence_kind.py`. `classify_evidence_need` is connectivity-first
(`L0_degraded` when a preferred authoritative source is missing) so the
assistant can emit a setup CTA. There is no second gather ReAct loop on the
chat path — the action agent owns tools.

**No keyword intent routing around the agent.** Do not scan user text with
regex/keywords to attach goals or bypass the ReAct loop. Session goals attach
through the structured `session_goal_set` tool or explicit host APIs.
Checklist progress uses `session_goal:done=<index>` in replies.

Do **not** duplicate the default port stack outside `DefaultHeadlessBuild`.
Expand `AgentBuildConfig` through `resolve_agent_ports` — do not re-copy the
`build_tools` / `build_prompts` branch in each host. Gateway
chat and the interactive shell share `TurnRunner` + `SessionAgentPool`
(`build_shell_agent` remains for tests / direct construction). Do not
reintroduce peer `bootstrap.adapters` copies under surfaces or gateway.

**Bind ports:** session-aware defaults implement
`SessionBindable` / `ConsoleBindable` / `OutputBindable` (`ports.py`).
`HeadlessAgent.bind_session` / `bind_turn(console=…, output=…)` only call ports
that match those Protocols. Gateway usually keeps a stable `BindableOutput` and
rebinds the transport via `BindableOutput.bind` (no `output=` each turn).
The mutable session port is **`SessionState`** (field `HeadlessAgent._session`,
headless impl `InMemorySessionState`) — not `SessionStore`. Durable JSONL is
`SessionStore` / `SessionRepo` (`docs/NAMING.md`).

**Host cancel:** one `threading.Event` on the output sink
(`ensure_turn_cancel` / `host_cancel_requested` in `turns/host_cancel.py`) —
tools (console `cancel_requested`), orchestrator, and stream guards all
read that same Event. Do not invent a second cancel channel.

**Cloud scale-out:** more Fargate tasks (fleet), not unbound in-process
concurrency or a new `chat` API.

## Hard boundary

- **No `import interactive_shell` anywhere under `agent_harness/`.** The dependency
  direction is strictly one-way: `interactive_shell -> agent_harness -> core`.
- `agent_harness/` may depend on `core/`, `config/`, and `infrastructure/`. It must not
  import `integrations/`, `tools/`, `surfaces/`, or `gateway/`. Integration and tool
  behavior reaches the harness through the providers in `infrastructure/harness_providers/`, wired at
  startup via `install_harness_providers()` in the interactive-shell output boundary.
  It must not depend on terminal UI concerns (Rich rendering, prompt-toolkit
  mutable UI state, slash dispatch, the shell `REGISTRY`).

## Layout

Top level holds the package's public surface — `__init__.py` (the curated
re-exports), `ports.py`, `agent_builder.py` — plus two small cross-cutting default
port impls that fit no single subpackage: `error_reporting.py`
(`DefaultErrorReporter`) and `llm_resolution.py` (`default_llm_factory` /
`resolve_provider_models`). Everything else lives in a responsibility-scoped
subpackage. Default port implementations live with the concern they serve, not in a
`providers/` package.

- `ports.py` — Protocols the engine talks to (output, confirmation, session
  store, tool provider, prompt-context provider, telemetry, error reporter,
  telemetry). Kept top-level as the central seam imported everywhere.
- `agent_builder.py` — `AgentConfig` dataclass + `build_agent(config)`. The
  single instantiation site for `core.agent.Agent` across all surfaces
  (action, evidence, gateway). See "Agent construction pattern" below.
- `turns/` — the turn drivers that orchestrate `core.agent.Agent`:
  - `orchestrator.py` — `run_turn` sequences three seams (do not merge them):
    1. **route decide** — pure `turn_route.route_turn` (no I/O, no stream flags)
    2. **route execute** — summarize / handled / answer effects
    3. **answer finalize** — `answer_finalize.finalize_routed_answer` (CTA,
       Want-me-to, stream flush). Stream rewrite locals
       (`text_changed_after_streaming`) stay inside finalize and must **never**
       gate route selection.
    Resolves base integrations **once** at the top of the turn, enriches that
    per-turn copy with repository scopes from the frozen message/history, and
    stores the enriched view on `turn_snapshot`. One active scope per vendor
    drives unqualified tool calls while the session retains a bounded collection
    of every repository scope it has encountered. Thus
    `turn_snapshot.resolved_integrations` is
    the single source of truth for what the turn knows without mutating the
    session's base integration cache. Downstream components (e.g.
    `action_driver._resolved_integrations_for_turn`) read it from there rather
    than re-resolving. Do NOT reintroduce per-component integration resolution.
  - `turn_route.py` / `answer_finalize.py` / `handoff_policy.py` — the seams
    above, extracted so post-answer bookkeeping cannot regress into routing.
  - `SessionGoal` vs local shell multi-step are also separate concerns:
    prompt fragments in `prompts/action/multi_step_policy.py`.
  - `action_driver.py` — `ActionTurnRunner`: one action tool-calling turn
    over the ports, via a `_build_action_agent` factory that returns an
    `ActionTurnPlan`.
  - `headless_agent.py` — headless programmatic entry point
    (`HeadlessAgent`, built by a port family; `.handle(text, binding)` per message, `.dispatch` underneath)
    plus in-memory port adapters for
    API / test runs. `tools` is required — surfaces that want a text-only
    turn pass `NullToolProvider()` explicitly.
  - `turn_snapshot.py` / `turn_results.py` — the immutable per-turn `TurnSnapshot`
    (built from any object satisfying `TurnSnapshotSource`, not `Session` directly)
    and the neutral turn-result models.
- `tools/` — action-tool wiring over the canonical registry (`action_tools.py`,
  `tool_context.py`) and `tool_provider.py` (`DefaultToolProvider`).
- `accounting/` — session-scoped token accounting and the default
  `TurnAccounting` (`turn_accounting.py`).
- `prompts/` — the single agent's prompt assembly. Layout: `kernel/`
  (envelope + surface Strategy), `action/` (assembler), `grounding/`
  (prompt providers), plus leaves `memory/` / `runtime_facts/` / `skills/`.
- `grounding/` — reusable grounding cache and rendering contracts; surfaces
  inject surface-owned command registries instead of being imported here.
- `session/` — reusable agent session state (`SessionCore`), JSONL storage, prompt
  history, task registry, session-scoped background records, integration resolution
  (:mod:`session.integration_resolution`), and `SessionManager` (the lifecycle owner).
  See "Session lifecycle" below.
- `session_goal/` — `/goal` / SessionGoal component (`SessionGoal` across many `chat`
  turns): `goal`, `evaluate` / `confirm`, `progress` / `continuation`, `persist`,
  `run_until`. **Not** the ReAct `Goal` / `turns/goal_review.py`. See
  `session_goal/AGENTS.md`.

## Session lifecycle (owned by SessionManager)

`core.agent_harness.session.SessionManager` is the single owner of session
create / resolve / rotate / restore / flush. Every surface delegates lifecycle
to it instead of re-implementing bootstrap + persistence:

- **shell** — `SessionBootstrapSpec` calls `SessionManager().bootstrap(...)` for
  the core startup mutations (persistent task registry + integration
  hydration), then layers shell-only UI concerns (theme, grounding providers,
  prompt history) on top. Interactive REPL entry calls
  :meth:`SessionManager.open_storage` once the run is confirmed interactive;
  ``/new`` calls :meth:`SessionManager.rotate_in_place`; ``/resume`` calls
  :meth:`SessionManager.rebind_for_resume` then :meth:`SessionManager.restore_context`.
  REPL exit calls :meth:`SessionManager.close` via
  :meth:`SessionManager.for_session`.
- **gateway** — process boot is
  :func:`bootstrap.process.configure_process` (``GATEWAY_PROFILE``);
  `GatewayController` stays lifecycle-only (credentials → process boot →
  transports). Per-chat session create/resolve stays on
  `gateway/core/storage/session/resolver.py::SessionResolver` →
  `SessionManager`. Turn dispatch uses `HeadlessAgent` via
  `infrastructure/turn_host/turn_runner.py`'s `TurnRunner` with
  :class:`~core.agent_harness.tools.tool_provider.DefaultToolProvider`
  built from the **live per-chat session** each turn (same tool resolution as
  shell). There is no separate gateway-owned ``Agent`` instance.
- **headless / scheduled** — non-TTY hosts use
  :meth:`AgentSession.run_headless_turn` (or ``start`` + ``chat``).
  That is the same ``run_turn`` engine as the shell; do not reassemble
  ``BufferOutputSink`` + ``DefaultHeadlessBuild`` in integrations.
  Ephemeral in-memory sessions (``headless_adapters.InMemorySessionState``)
  bypass ``SessionManager`` by design when tests need no JSONL.

`Session` (formerly `ReplSession`) is the in-memory session object used by every
surface, including headless gateway — it is not REPL-specific. Do not re-add
per-surface session bootstrap logic; extend `SessionManager` instead.

## Agent construction pattern (Pattern A — canonical)

Every surface builds its runtime `Agent` the same way: assemble surface-specific
values into an `AgentConfig` dataclass, then call `build_agent(config)`. This is
the single instantiation site — when `Agent.__init__`'s signature changes,
`agent_builder.py` is the single edit site for every harness surface.

1. Assemble surface-specific values (LLM, system prompt, tools, resolved
   integrations, iteration cap, observer).
2. Pack them into an `AgentConfig` dataclass.
3. Hand it to `build_agent(config)`.

```python
from core.agent_harness.agent_builder import AgentConfig, build_agent

config = AgentConfig(
    llm=llm_client,
    system=system_prompt,
    tools=tuple(agent_tools),
    resolved_integrations=resolved,
    max_iterations=6,
    tool_resources={},  # optional
    tool_hooks=None,  # optional
    on_runtime_event=observer_callback,  # optional
)
agent = build_agent(config)
```

Action (`turns/action_driver.py::_build_action_agent`) assembles an
``AgentConfig`` and calls ``build_agent``. The gateway turn path does not
construct a persistent ``core.agent.Agent`` — gateway chat reuses one
``HeadlessAgent`` per logical session via ``SessionAgentPool`` (each turn
``bind_turn`` + live ``DefaultToolProvider`` from the chat session). When
``Agent.__init__``'s signature changes,
``agent_builder.py`` is the single edit site for harness surfaces that call
``build_agent``.

## Agent context and data stores

Turn assembly starts in ``turns/orchestrator.py`` with
``TurnSnapshot.from_session``.

**Do NOT** reintroduce per-surface `Agent` subclasses that override
`build_llm` / `build_system_prompt` / `build_tools` / `resolved_integrations`
hooks. Those hooks were removed because they let each surface hide per-turn
configuration on `self`, which diverged routing across surfaces.

## One agent shape

`core.agent.Agent` owns the ReAct loop (think → call tools → observe → answer).
A no-tool response is its accepted conclusion; hosts must not start a second
LLM to rewrite that answer.

### Contributor checklist (agent changes)

1. Update this file when harness rules change.
2. Inject action execution through the `ExecuteActions` port; do not import
   surface code into `agent_harness/`.
3. Public host API is `AgentSession.chat`.
   Adapters build `ChatTurnBindings` and call `dispatch_chat_turn` internally —
   never add a new top-level binder that calls `run_turn` directly.

**Read order for new code:** this file → `harness.py` (`AgentSession`) →
`turns/orchestrator.py` (`run_turn`) → `core/agent/agent.py` (facade + wiring)
→ `core/agent/react_loop.py` (`run_react_loop`, the tool-calling algorithm).

## Construct once → many turns

Prefer this shape in docs, samples, and new call sites — do **not** invent a
second top-level free function that dumps the turn stack, and do **not** rebuild
a headless agent on every message for the same logical session.

**Host API shape**

```python
from bootstrap.embedded import start_embedded_session

session = start_embedded_session()  # EMBEDDED_PROFILE + default agent
result = session.chat("…")  # turn 1
result = session.chat("…")  # turn 2 — same attached agent
```

``AgentSession.start`` must not import ``bootstrap`` (layer contract). Surfaces
that already ran another process profile call ``startup()`` (or pass an
explicit ``boot_process``).

**One agent per logical session (or scheduled loop)**

| Lifetime | Construct | Then |
|----------|-----------|------|
| Chat session (gateway + shell) | `TurnRunner` → `SessionAgentPool` keeps one `HeadlessAgent` per session id; each turn rebinds transport/TTY output via `BindableOutput.bind`, then `bind_turn` (session / accounting / console / tool_hooks) | `agent.handle(...)` (SessionGoal loop via `run_goal`) |
| Embedder / script | `start_embedded_session()` or `attach_agent(HeadlessAgent…)` once | repeated `chat` / `dispatch` |
| Scheduled loop | Prefer one agent for the loop’s lifetime when multi-turn; `run_headless_turn` is OK for true one-shot digests | do not treat one-shot as the multi-turn pattern |
| CLI `ask` | `AgentSession.start` once per invocation (ephemeral) | `.chat` once (`dispatch`) |

Same-session turns must not overlap on one pooled agent (gateway holds a
per-session lock). Different sessions stay concurrent under the capacity gate.

| Name | Use |
|------|-----|
| **`AgentSession`** + **`chat`** | **Public host API** — prefer in all new code |
| **`HeadlessAgent`** + **`handle`** | Gateway / shell per-message entry; thin wrapper over `run_goal` returning its last result |
| **`HeadlessAgent`** + **`run_goal`** | The one SessionGoal loop driver; `AgentSession.chat_until_goal` delegates here |
| **`HeadlessAgent`** + **`dispatch`** | One engine turn; what `AgentSession.chat` calls |
| **`SessionAgentPool`** | One headless agent per logical session across turns (gateway + shell) |
| **`DefaultHeadlessBuild`** | The default port family for one session; `.agent(tools=…, prompts=…)` builds the agent on it |
| **`resolve_agent_ports`** | Expand `AgentBuildConfig` → `(tools, prompts)` — shared by the pool and `build_shell_agent` |
| **`run_headless_turn`** | One-shot convenience for scheduler digests — not the multi-turn pattern |
| **`dispatch_chat_turn`** | **Internal** seam over `run_turn` — adapters only |

There is no `dispatch_message_to_headless_agent` — that free-function dump was
replaced by `HeadlessAgent.dispatch` / `AgentSession.chat`.

### Two doors into a turn (Concurrency & Serialization)

There are two ways into an agent turn:
1. **The Host Loop (`TurnRunner` → `HeadlessAgent.handle`)**:
   - Used by concurrent multi-actor hosts (**Gateway** transports and **Interactive Shell**).
   - Takes the `SessionAgentPool` per-session lock (serializing turns for the same session to prevent `AgentBusyError` and state corruption).
   - Takes the process turn capacity gate (`TurnConcurrencyGate` / `OPENSRE_MAX_CONCURRENT_TURNS`).
   - Drives the full `SessionGoal` outer loop (`run_goal`).
2. **Scripted / Headless API (`AgentSession.chat` / `chat_until_goal`)**:
   - The un-gated single-turn API for programmatic scripts (`main.py`, notebooks), tests, and single-shot CLI (`opensre ask`).
   - Deliberately kept as an unguarded entry point for single-tenant, scripted use.
   - **Forbidden for concurrent hosts:** Gateway and Shell modules must never call `chat()` or `chat_until_goal()` directly (enforced by `gateway/tests/test_harness_behaviour_border.py` and `tests/interactive_shell/test_harness_api_border.py`).

**Scaling** is separate from the host API: local concurrency
(`TurnConcurrencyGate` / transport pools / `OPENSRE_SIZE_PROFILE`) and cloud
Fargate scale-out (spin more tasks; same API per task) sit *around*
`chat`. Construct-once-per-session is the reuse story;
process/task scale-out is deploy. Do not redesign the host API to “enable
scaling.”

## Hosts, one AgentSession API

**Public host contract:** :class:`~core.agent_harness.harness.AgentSession`
with ``chat``. One name per concept — the former
``AgentHarness`` / ``HarnessConfig`` / ``HarnessStartupResult`` /
``dispatch_message`` aliases are deleted.

**Internal chat seam:** adapters build
:class:`~core.agent_harness.turns.chat_api.ChatTurnBindings`, then call
:func:`~core.agent_harness.turns.chat_api.dispatch_chat_turn` (thin facade
over ``run_turn``). Do **not** add new top-level chat entrypoints that call
``run_turn`` directly — adapters only. ``agent_harness`` must not import
``tools``.

| Host | Process boot | Host call |
|------|--------------|-----------|
| Interactive shell | `configure_process(CLI_PROFILE)` + shell Rich adapters | `TurnRunner` → `SessionAgentPool` → `agent.handle` (TTY accounting / `is_tty=True`) |
| CLI `ask` | same CLI profile (already booted) | `AgentSession.start` → `.chat` (one-shot; `dispatch`) |
| Gateway chat | `configure_process(GATEWAY_PROFILE)` | `TurnRunner` → `SessionAgentPool` → `agent.handle` |
| Scheduled digests | adapters via profile; runners via `install_scheduler_runners` | `AgentSession.run_headless_turn` → `chat` |

Shell and gateway share the same turn host (`TurnRunner` + pool). The shell
passes TTY-aware accounting and `is_tty=True`; do **not** invent a parallel
REPL turn stack. `build_shell_agent` remains for tests and direct construction
characterization — production REPL submissions go through the pool.

## Keep the loop primitive in core

The ReAct loop primitive is `core.agent.Agent`. `agent_harness/` orchestrates it;
it does not re-implement it. Do not fork the loop here.

## core/agent package (Agent is a facade, not the algorithm owner)

`core/agent/` is a package with one file per responsibility. `Agent`
(in `agent.py`) is a thin facade: `__init__` stores construction-time config
and `run()` resolves per-run context (from `runtime_request=` or
`initial_messages=`) and hands it to `core.agent.react_loop.run_react_loop`,
which owns the actual think → call-tools → observe algorithm.

- `core/agent/mixins.py` — `EventEmitterMixin` (event dispatch),
  `ToolFilterMixin` (tool-narrowing hook), `SteeringMixin` (`steer`/`follow_up`
  to nudge a run in progress). `Agent` composes all three.
- `core/agent/provider_hooks.py` — `ProviderHookDelegate`, a fail-open wrapper
  around `core.provider.ProviderHooks` applied around each LLM call. A raised
  hook exception is logged and swallowed; it never breaks the loop.
- `core/agent/loop_host.py` — `LoopHost`, the `Protocol` `run_react_loop` calls
  back into. `Agent` implements it via the mixins plus its own
  `_transform_messages` / `_convert_to_llm` / `_before_request` /
  `_after_response` forwarders. The concrete `ProviderHookDelegate` type is an
  `Agent` implementation detail, not part of the host contract, so any host can
  wire those four provider hooks however it likes.
- `core/agent/run_io.py` — `AgentRunInput` (resolved per-run inputs) and
  `AgentRunResult` (the loop's outcome). `core.agent` re-exports `AgentRunResult`
  for the `from core.agent import AgentRunResult` path.
- `core/agent/react_loop.py` — `ReactLoop` (the loop as a method-object, phases
  `_think` / `_handle_conclusion` / `_observe`) and `run_react_loop` (its thin
  functional entry). A reply with no tool calls ends the turn unless a queued
  `follow_up` is waiting, or the host's `_should_accept_conclusion` rejects it
  (reviewed goals from `build_goal_reviewer`, including an unfinished task
  plan). Bare Goals without `verify` do not gate stop.
- `core/agent/agent.py` — the `Agent` facade: `__init__` (holds config), `run()`
  (builds the per-run `AgentRunInput` via `_build_run_input` and hands it to
  `run_react_loop`), and the `_should_accept_conclusion` / `_filter_tools`
  override hooks.

Do not reintroduce hook-method overrides on `Agent` itself (e.g. a subclass
overriding a private `_before_provider_request`-style method) — customize via
`provider_hooks=ProviderHooks(...)` at construction instead. Subclassing
remains the pattern for `_filter_tools` and `_should_accept_conclusion`, which
are genuine per-agent overrides, not seams `ProviderHooks` covers.
