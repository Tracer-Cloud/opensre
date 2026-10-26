# Naming conventions for `core/`

A small, enforceable vocabulary so file and type names say what they are. The
goal is that a reader can tell a data type from a process, a mutable state from
a frozen view, and a package's purpose from its name alone.

## Glossary (one meaning per term)

| Term | Means | Example |
| --- | --- | --- |
| **State** | Mutable agent/session facts that evolve during a run | `AgentState`, harness port `SessionState` |
| **Storage** / **Repo** | Durable persistence backends (not the in-memory turn port) | `SessionStore`, `SessionRepo`, `JsonlSessionStore` |
| **Snapshot** | A frozen view captured at a boundary (turn start, run start) | `TurnSnapshot` |
| **RunInput** / **RunResult** | The input to and output from one `Agent.run()` boundary | `AgentRunInput`, `AgentRunResult` |
| **Resources** | Handles passed into tool executors for one call | `ToolCallResources` |
| **Budget** | An LLM token/window policy — not application state | `enforce_token_budget` |
| **Host** | The callback contract an algorithm drives (a `Protocol`) | `LoopHost` |

## Module naming: `{domain}_{role}.py`

Name a file for the concept it holds, not with a generic bucket word.

```
core/agent/
  agent.py          # the Agent facade
  react_loop.py     # ReactLoop + run_react_loop  (the algorithm)
  loop_host.py      # LoopHost                    (the callback contract)
  run_io.py         # AgentRunInput, AgentRunResult (the run boundary's I/O)
  mixins.py         # the reusable *Mixin behaviors
  provider_hooks.py # ProviderHookDelegate
```

## Type naming

- **Mixins** carry a `Mixin` suffix — they cannot stand alone (they assume
  fields/methods the host provides). `EventEmitterMixin`, `ToolFilterMixin`,
  `SteeringMixin`.
- **Protocols** are named by their role, not with a `Protocol` suffix — matches
  the stdlib (`Iterable`, `SupportsRead`) and `agent_harness/ports.py`
  (`OutputSink`, `SessionState`). `LoopHost`, not `LoopHostProtocol`.
- **Do not prefix a type with its own package name.** Inside `core/agent/`, a
  class is `EventEmitterMixin`, not `AgentEventEmitter` — the namespace already
  says "agent."

## Anti-patterns (do not add in new code)

- `context.py` at `core/` or `core/agent/` root — "context" is overloaded across
  the repo. Name the concept (`run_io.py`, `turn_snapshot.py`).
- `models.py` when the file holds only run I/O — too vague. Say what the models
  are (`run_io.py`).
- `*Context` without a domain prefix when another `*Context` already exists.
- A package whose only child is a single sub-package — collapse the wrapper.
- Calling the mutable harness session port `SessionStore` — that word means
  durable persistence. The port is `SessionState` (`InMemorySessionState` for
  headless); JSONL durability stays `SessionStore` / `SessionRepo`.
- Module-level mutable globals for “current session” — pass `SessionState`
  explicitly (see local notes: no-globals design).

## Imports

Use fully qualified paths in code; keep short mental labels for docs.

| Mental label | Import |
| --- | --- |
| ReAct run I/O | `from core.agent.run_io import AgentRunInput, AgentRunResult` |
| ReAct loop | `from core.agent.react_loop import run_react_loop` |
| Loop callback contract | `from core.agent.loop_host import LoopHost` |
| The agent primitive | `from core.agent import Agent` |
| Harness turn snapshot | `from core.agent_harness.turns.turn_snapshot import TurnSnapshot` |

Re-export from a package `__init__.py` only for its single canonical symbol
(`Agent`), not everything — avoid `from core.agent import *`-style ambiguity.
