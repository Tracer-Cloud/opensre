# Agent State

`core/state/` owns the provider-agnostic conversation state that OpenSRE carries
between agent turns.

Use this package for the cross-turn transcript and its compaction helpers. This is
**state** — not REPL session state, CLI prompt grounding, or generic agent-runtime
request assembly.

## Belongs Here

- The mutable per-session conversation store (`MutableAgentState`).
- The structured record of each completed turn (`TurnEvidence`): the tool calls
  and bounded results that later turns replay.
- Transcript-window compaction helpers and the history switches and budgets
  (`history_settings`).

## Does Not Belong Here

- Context trimming, ranking, and budget logic; keep that in `core/context_budget.py`.
- The LLM/tool-calling loop and runtime request contracts; keep those in sibling
  `core/` runtime modules.
- Terminal UI, REPL session state, prompt history, and slash commands; keep
  those in `surfaces/interactive_shell/`.
- External clients, config normalization, and verification; keep those in
  `integrations/`.
- Agent-callable tool implementations; keep those in `tools/`.
- Infrastructure services such as guardrails, masking, auth, telemetry, notifications,
  and sandboxing; keep those in `infrastructure/`.

## Also exported (temporary)

- ``MutableAgentState`` in ``agent_state.py``. Now slimmed to the cross-turn
  transcript (``messages``), its turn evidence (``turn_evidence``), and
  ``clear()`` — the per-turn tool/prompt machinery has been removed. It still
  lives here for historical import paths; the remaining move to
  ``core/agent_harness/session/`` is pending
  ([#3685](https://github.com/Tracer-Cloud/opensre/issues/3685)).

## Naming Rule

New names here should make the state boundary obvious. Prefer terms such as
`state`, `slice`, and `snapshot`. Avoid adding generic `prompt`, `session`,
`runtime`, or `grounding` modules here; those belong to their owning surface or
runtime package.
