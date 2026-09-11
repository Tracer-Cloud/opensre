# Interactive Shell Action Policy (ADR)

## Status
Superseded — Jun 18, 2026. The declarative-rule-pack deterministic mapper and
the regex-based planner postprocessing overrides described in the original
decision have been removed. See "Decision (current): LLM is the sole tool
selector" below. The original decision is retained for historical context.

## Context
The interactive-shell action policy had grown through layered heuristics in
single modules: a regex/keyword deterministic mapper inferred tools from
free-form text, and planner postprocessing rewrote the model's chosen actions
with more regex. These heuristics competed with the LLM and caused
misclassifications (e.g. "investigate a sample test alert?" being treated as an
informational question instead of running the sample alert), and they were a
recurring source of precedence drift.

## Decision (current): The shell action agent is the sole tool selector
1. There is no regex/keyword intent inference. Non-command turns are
   selected entirely by the shell action agent via native tool-calling.
2. Tool selection is driven by the action-agent system prompt
   (`core/agent_harness/prompts/action/assemble.py`) and the per-tool descriptions
   in the tool catalog (`tools/interactive_shell/*`). Keep both precise — they
   are the only selection signal.
3. The action path does not post-hoc rewrite the model's tool calls. Tool calls
   execute as first-class `AgentTool`s through the shared `core`
   tool-calling loop; argument shape and availability are enforced by the
   AgentTool runtime contract and per-tool gates.
4. When the action-agent prompt overflows the context window, the turn falls
   through to a conversational reply rather than guessing an action. When the
   action-agent LLM itself is unavailable, the REPL renders and persists a
   failed assistant turn so `/resume` can show the outage.
5. Literal `/slash` command text the user types verbatim is dispatched
   deterministically, without the action-agent LLM (see the "Deterministic
   literal-`/slash` dispatch" addendum below). This is an explicit-command
   bypass, not natural-language intent inference: free-form text is still
   selected entirely by the action agent. The runtime's literal-`/slash`
   detection in `runtime/input_policy._literal_slash_command_text` remains
   terminal-UI policy (spinner suppression and exclusive-stdin gating); the
   execution-side deterministic dispatch lives in
   `core/agent_harness/turns/action_driver.py`.

## What this means for changes
- To change how a phrasing maps to a tool, edit the action-agent system prompt and/or
  the relevant tool description — never add a regex.
- To add a new tool, add it to the tool catalog with a clear, self-describing
  `description` and `input_schema`; the action agent selects it from that text
  and receives it as an AgentTool.
- Live turn scenarios under
  `tests/core/agent/scenarios/` are the regression
  surface for action-agent behavior. Deterministic scenarios (`intent_class:
  deterministic`) assert literal command dispatch only.

## Original decision (historical, superseded)
1. Deterministic mapping was split into declarative rule packs with one explicit precedence table.
2. Rule matching windows were named typed strategies instead of inline numeric slices.
3. Planner postprocessing ran as pure transforms over a typed `PlannerState`.
4. Fail-closed policy transforms and normalization transforms were registered separately and executed in one ordered list.
5. Legacy planner-result tuple compatibility was collapsed behind a single adapter.
6. Planner contracts included policy-trace artifacts to detect silent precedence drift.

## Integration awareness and LLM-driven read-only discovery

Addendum — Jun 18, 2026.

Factual questions about live state (for example "is sentry installed?") are
answered without adding keyword/regex rules. Two complementary mechanisms:

1. Context grounding (not action planning). At REPL boot, `run_repl_async`
   (`surfaces/interactive_shell/main.py`) hydrates
   `session.configured_integrations` from the shared
   `configured_integration_services()` helper in `integrations/catalog.py`
   (the same source the welcome banner uses, so they never diverge). The agent
   prompt lists the configured set as facts, letting the model answer directly
   when state is already known.
2. LLM-driven discovery. The agent system prompt
   (`core/agent_harness/prompts/action/assemble.py`) lets the model, at its own
   discretion, emit a read-only discovery action (for example
   `slash_invoke("/integrations", ["list"])` or `["verify"]`) to discover the
   answer instead of deflecting. There is no keyword mapping for this — the LLM
   decides. Under the alpha allow-all policy every discovery action runs without
   confirmation (`execution_policy.allow_tool("slash")` returns `allow`); the
   former `ExecutionTier`/`resolve_slash_execution_tier` classification was
   removed because it gated nothing. No fail-closed regex rule is involved; the
   agent decides whether to emit a discovery action.

### Observe→answer summary loop

Addendum — Jun 18, 2026.

When the agent runs a read-only discovery command, the tool result is appended
to the same ReAct conversation. The same model then writes the user-facing
answer. There is no second assistant pass or deterministic pre-agent dispatch.

Discovery commands also no longer dump validator stack traces into the REPL: a
vendor/config failure during verification (for example a GitHub MCP `401`) is
logged as a one-line warning instead of a full traceback, because
`report_validation_failure` now defaults to `include_traceback=False` while still
capturing the exception to Sentry.

### Auto-launching interactive setup ("can you configure X?")

Addendum — Jun 18, 2026.

When the user asks to configure, connect, set up, or add an integration
("can you configure sentry?", "connect datadog"), the action agent does not just
hand off to the conversational assistant — it launches the setup wizard for
them. The action agent emits a `slash_invoke` tool call for
`/integrations setup <service>` or `/mcp connect <server>`. The model chooses
the service; there is no per-vendor hardcoding.

The setup wizard is a child process that needs exclusive stdin, so it cannot run
inline mid-turn (the live prompt is competing for stdin). Instead
`tools/interactive_shell/actions/slash.py` queues the command via
`session.queue_auto_command(...)`, which prefills the next prompt and marks it
for auto-submit. The prompt refresh hook
(`wire_prompt_refresh` in `surfaces/interactive_shell/ui/input_prompt/refresh.py`) then submits it, so the
command flows through the normal exclusive-stdin turn path of the REPL
(`turn_needs_exclusive_stdin` recognizes `/integrations setup`) — the only
place an interactive child process gets clean stdin. In a non-TTY/scripted
context (no prompt to submit into), the slash command path degrades to normal
non-interactive slash behavior.

### Removal of the planning-stage fail-closed safeguard (v0.1)

Addendum — Jun 18, 2026.

The action agent does not deny a turn. Previously, any clause
the old planner could not map to an executable tool — flagged via the `mark_unhandled`
tool, an `UNHANDLED:` text marker, or an unavailable tool call — collapsed the
whole turn into a hard denial that printed *"I couldn't safely decide actions for
that request."* In practice this fired on legitimate input (most often a
conversational question that embedded a quoted, list-style directive such as
*figure out why X is crashing by querying (a) sentry, (b) github, (c) posthog*),
producing a dead end with no safety benefit.

Every terminal action in v0.1 is **read-only**, so an unmatched, ambiguous, or
chatty clause is not a safety risk. The action agent now:

- runs every clause it *can* map to an executable action, and
- lets everything else fall through to the conversational assistant (or simply
  drops a chatty clause in a compound request).

Removed as part of this change: the `denied` field on `ActionPlanningDecision`,
`enforce_plan_fail_closed_policy`, `normalize_terminal_plan`, `render_plan_denied`,
the `mark_unhandled` planner tool, and the `UNHANDLED:` convention. The
`fail_closed`, `has_unhandled_clause`, and `turn.expected_signals` fields were
also removed from turn scenario fixtures, since the oracle never asserted on
them; the fixture `policy` block now carries a single `executes_terminal_action`
`boolean` (true only when a shell action AgentTool is expected to run).

If write/mutating actions are introduced later, gate them with the
execution-stage confirmation policy (`tools/interactive_shell/shared/execution_policy.py`), **not**
an action-selection denial.

### Removal of the shell-command safety policy (alpha)

Addendum — Jun 27, 2026.

**Decision:** while OpenSRE is in **alpha**, the interactive REPL runs **every**
shell command with **no guardrails**. The shell-command safety policy — the
read-only / mutating / restricted classification, the command allowlist, and the
hard `deny` floor — has been removed. This is a deliberate trade-off: alpha
prioritizes developer velocity over command sandboxing, and the REPL already
runs on the developer's own machine with their own privileges.

What changed:

- `shell_policy.py` (classification, allowlists, `classify_command`,
  `evaluate_policy`, `PolicyDecision`) was deleted. Input normalization now
  lives in `tools/interactive_shell/shell/parsing.py`, alongside the shell
  execution policy in `tools/interactive_shell/shell/policy.py`.
- `evaluate_shell_from_parsed` now returns `allow` for every
  command — read-only, mutating, `restricted` (`sudo`, `systemctl`, `kill`,
  `dd`, …), shell operators (`| && ; > <`), and command substitution
  (`` ` ``/`$(...)`). Every nonempty command runs through the host shell, so
  shell grammar is not duplicated with operator-detection heuristics. The `!`
  prefix is still honored but is optional.
- The **only** remaining non-execution outcome is genuinely empty input (a bare
  `!` or whitespace), which is rejected as input validation, not as a guardrail.

Each `shell_run` call starts a new shell in the task workspace. A directory
change therefore applies only within the same command (`cd path && command`);
start a new task to continue work from another persistent workspace.

The `ask`/confirmation machinery is retained and used by **`/auto`** (Off/Low/Med)
plus `trust_mode`. It is split across two layers: the pure decision lives in
`tools/interactive_shell/shared/execution_policy.py` (`resolve_confirmation`,
`apply_auto_level`), and the terminal interaction (`execution_allowed` — console
output, the approval prompt, analytics) lives in
`surfaces/interactive_shell/ui/execution_confirm.py`.

### `/auto` autonomy (tool-type confirmations)

Addendum — Aug 2026.

**Decision:** the REPL exposes `/auto off|low|med|high` as session-scoped
tool-approval autonomy. Default is **high** (alpha: no confirmation). Lower
levels promote a default-`allow` policy result to `ask` based on `tool_type`
(`config/constants/repl_autonomy.py`), not a shell-command allowlist.

| Level | Asks before |
| --- | --- |
| `high` | Nothing |
| `med` | Mutating agent tools (`shell`, `code_agent`, `slash`, `cli_command`, `opensre_cli`, `switch_llm_provider`, `synthetic_test`, `sentry_issue_fix`, …) |
| `low` | Same as `med` |
| `off` | Every tool type |

**Interaction with `/trust`:** `trust_mode` still short-circuits `ask` to allow
(skip the prompt). Non-TTY `ask` remains fail-closed.

**Still true:** there is no shell argv allowlist/deny floor under alpha. `/auto`
only gates whether the existing confirmation UX runs before a tool launch.

If a shell-command allowlist is reintroduced after alpha, gate it here at the
execution stage — never with an action-selection denial in the planner.

### Deterministic literal-`/slash` dispatch (no LLM)

Addendum — Jun 28, 2026.

**Decision:** input the user types as a literal `/slash` command is dispatched
deterministically, **without consulting the action-agent LLM**. This supersedes
the earlier "the literal-`/slash` detection must never become an action
execution shortcut" wording.

**Why:** all REPL turns previously routed through the action-agent LLM, so when
that LLM was unavailable (a provider with no credit, a failed auth, an outage)
every slash command failed — including the exact commands needed to recover
(`/login`, `/auth`, `/onboard`, `/model`). That is a deadlock: you could not log
in because logging in required the LLM you were trying to fix. Typed commands
should not depend on a funded LLM.

**Scope — the line that keeps the original concern intact.** The original ADR
removed regex/keyword heuristics because they *inferred intent from natural
language* and competed with the LLM. This bypass does the opposite: it fires
**only** when the message text itself is a literal `/command` the user typed
verbatim. There is no inference. Free-form natural language ("log me in",
"show my integrations") is still selected entirely by the action agent. The line
is "explicit typed command" vs "natural language", identical to the line the
terminal-UI policy (`_literal_slash_command_text`) already draws.

**How it works.** `core/agent_harness/turns/action_driver.ActionTurnRunner` recognizes
literal `/slash` input and emits a deterministic `slash_invoke` tool call through
the same static-LLM path as the explicit `!cmd` shell escape
(`_StaticToolCallLLM`). Execution then flows through the normal `slash_invoke`
AgentTool → `dispatch_slash`, so recording, execution policy, exclusive-stdin
gating, pickers, and exit behavior are identical to an LLM-selected slash call —
the only difference is the tool *selection* is deterministic instead of
LLM-driven. The bypass no-ops (falls back to the LLM path) when `slash_invoke` is
not an available tool that turn.

**Consequences.**

- Literal slash commands are faster, free, and reliable even with no LLM credit.
- Compound requests that *start with* a literal slash are dispatched as that one
  command (a single `slash_invoke`); compound phrasing that does not start with a
  slash (e.g. `run /health and then investigate …`) is unaffected and still
  LLM-routed. No turn scenario uses a prompt beginning with a literal `/`.
- A literal discovery command (e.g. `/integrations list`) shows its raw output
  directly; the optional LLM summary pass only runs if the action-agent LLM is
  available, and degrades cleanly (no summary) when it is not.

**Still forbidden:** regex/keyword/fuzzy intent routing for natural language,
post-hoc rewriting of LLM-selected tool calls, and any deterministic mapping from
non-`/`-prefixed text to an action. Those compete with the LLM and were removed
for good reason.
