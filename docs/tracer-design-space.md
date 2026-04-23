# Dive into Claude Code

Claude Code is an agentic coding tool that can run shell commands, edit files, and call external services on behalf of the user. This analysis identifies five human values/philosophies and thirteen design principles that motivate the architecture, and organizes the findings in three parts:

1. Design-space analysis recurring design questions (where reasoning lives, how the iteration loop is structured, what safety posture to adopt, how the extension surface is partitioned, how context is managed, how work is delegated across subagents, and how sessions persist) analyzed through a 7 component high level structure and a 5-layer subsystem architecture, tracing each choice to specific source files.
2. Architectural contrast with OpenClaw comparison of Claude Code's design philosophy with that of OpenClaw (a multi-channel personal assistant gateway) across six design dimensions, showing how the same recurring questions produce different answers under different deployment contexts.
3. Open directions for future agent systems six open directions spanning the observability-evaluation gap, cross-session persistence, harness boundary evolution, horizon scaling, governance, and the evaluative lens, each drawing on empirical, architectural, and policy literature.

> "Agents must be able to work autonomously; their independent operation is exactly what makes them valuable. But humans should retain control over how their goals are pursued."

> "good judgment and sound values that can be applied contextually"

---

## Five Values and Philosophies

**Human Decision Authority.** The human retains ultimate decision authority over what the system does, organized through a principal hierarchy. The system is designed so that humans can exercise informed control: they can observe actions in real time, approve or reject proposed operations, interrupt compatible in-progress operations, and audit after the fact.

**Safety, Security, and Privacy.** The system protects humans, their code, their data, and their infrastructure from harm, even when the human is inattentive or makes mistakes. The auto-mode threat model targets four risk categories: overeager behavior, honest mistakes, prompt injection, and model misalignment.

**Reliable Execution.** The agent does what the human actually meant, stays coherent over time, and supports verifying its work before declaring success. This value spans both single-turn correctness and long-horizon dependability.

**Capability Amplification.** The system materially increases what the human can accomplish per unit of effort and cost.

**Contextual Adaptability.** The system fits the user's specific context (their project, tools, conventions, and skill level) and the relationship improves over time. The extension architecture provides configurability at multiple levels of context cost. Longitudinal data shows that the human-agent relationship evolves: auto-approve rates increase.

---

## Design-Space Analysis

Several recurring design questions emerge: where should reasoning live, how many execution engines are needed, what safety posture to adopt, and what resource to treat as the binding constraint.

### Where Does Reasoning Live?

The model reasons about what to do; the harness is responsible for executing actions. The model emits tool_use blocks as part of its response, and the harness parses them, checks permissions, dispatches them to tool implementations, and collects results. The model never directly accesses the filesystem, runs shell commands, or makes network requests. This separation has a security consequence: because reasoning and enforcement occupy separate code paths, a compromised or adversarially manipulated model cannot override the sandboxing, permission checks, or deny-first rules implemented in the harness.

**Tracer Perspective**

*Current:* Tracer already implements a staged investigation flow (planning → evidence gathering → diagnosis), but reasoning remains relatively shallow and mostly linear.

*Ideal:* An SRE agent should follow a hypothesis-driven RCA loop, explicitly forming and testing multiple competing explanations based on evidence.

---

### How Many Execution Engines?

Claude Code uses a single `queryLoop()` function that executes regardless of whether the user is interacting through an interactive terminal, a headless CLI invocation, the Agent SDK, or an IDE integration (query.ts). Only the rendering and user-interaction layer varies.

**Tracer Perspective**

*Current:* Tracer exposes a coherent investigation flow at the user level, but underlying execution paths are not clearly unified into a single explicit agent loop abstraction.

*Ideal:* A single explicit agent loop coordinating all investigation stages and execution modes.

---

### What Is the Default Safety Posture?

Claude Code's default safety posture is deny-first with human escalation: deny rules override ask rules override allow rules, and unrecognized actions are escalated to the user rather than allowed silently. Multiple independent safety layers (permission rules, PreToolUse hooks, the auto-mode classifier when enabled, and optional shell sandboxing) apply in parallel, so any one can block an action.

**Tracer Perspective**

*Current:* Tracer includes safeguards and error handling, but safety is not yet expressed as a unified, layered permission architecture.

*Ideal:* A layered, deny-first safety system that actively shapes agent behavior and decisions.

---

### What Is the Binding Resource Constraint?

In Claude Code, the context window is the binding resource constraint. Five distinct context-reduction strategies execute before every model call, and several other subsystem decisions (lazy loading of instructions, deferred tool schemas, summary-only subagent returns) exist to limit context consumption. The five-layer pipeline exists because no single compaction strategy addresses all types of context pressure:

- **Budget reduction** targets individual tool outputs that overflow size limits.
- **Snip** handles temporal depth.
- **Microcompact** reacts to cache overhead.
- **Context collapse** manages very long histories.
- **Auto-compact** performs semantic compression as a last resort.

**Tracer Perspective**

*Current:* Tracer forwards logs and metrics effectively but treats context mostly as raw input.

*Ideal:* A structured context pipeline with prioritization, compaction, and evidence hierarchy.

---

## Permission and Safety Layers

1. **Tool pre-filtering:** Blanket-denied tools are removed from the model's view before any call, preventing the model from attempting to invoke them.
2. **Deny-first rule evaluation:** Deny rules always take precedence over allow rules, even when the allow rule is more specific.
3. **Permission mode constraints:** The active mode determines baseline handling for requests matching no explicit rule.
4. **Auto-mode classifier:** An ML-based classifier evaluates tool safety, potentially denying requests the rule system would allow.
5. **Shell sandboxing:** Approved shell commands may still execute inside a sandbox restricting filesystem and network access.
6. **Not restoring permissions on resume:** Session-scoped permissions are not restored on resume or fork.
7. **Hook-based interception:** PreToolUse hooks can modify permission decisions; PermissionRequest hooks can resolve decisions asynchronously alongside the user dialog (or before it, in coordinator mode).

---

## Context as Bottleneck: Beyond Compaction

**CLAUDE.md lazy loading.** The base CLAUDE.md hierarchy is loaded at session start, but additional nested-directory instruction files and conditional rules are loaded only when the agent reads files in those directories, preventing unused instructions from consuming context.

**Deferred tool schemas.** When ToolSearch is enabled, some tools include only their names in the initial context; full schemas are loaded on demand.

**Subagent summary-only return.** Subagents return only summary text to the parent, not their full conversation history.

**Per-tool-result budget.** Individual tool results are capped at a configurable size, preventing a single verbose output from consuming disproportionate context.

---

## The Query Pipeline

1. **Settings resolution.** The `queryLoop()` function destructures immutable parameters including the system prompt, user context, permission callback, and model configuration.
2. **Mutable state initialization.** A single State object stores all mutable state across iterations, including messages, tool context, compaction tracking, and recovery counters. The loop's seven continue points each overwrite this object in one whole-object assignment rather than mutating fields individually.
3. **Context assembly.** The function `getMessagesAfterCompactBoundary()` retrieves messages from the last compact boundary forward, ensuring that compacted content is represented by its summary rather than the original messages.
4. **Pre-model context shapers.** Five shapers execute sequentially.
5. **Model call.** A `for await` loop over `deps.callModel()` streams the model's response, passing assembled messages, the full system prompt, thinking configuration, the available tool set, an abort signal, the current model specification, and additional options including fast-mode settings, effort value, and fallback model.
6. **Tool-use dispatch.** If the response contains tool_use blocks, they flow to the tool orchestration layer.
7. **Permission gate.** Each tool request passes through the permission system.
8. **Tool execution and result collection.** Tool results are added to the conversation as tool_result messages, and the loop continues.
9. **Stop condition.** If the response contains no tool_use blocks (text only), the turn is complete.

Claude Code's reactive loop follows the ReAct pattern: the model generates reasoning and tool invocations, the harness executes actions, and results feed the next iteration. When the model emits tool calls, the system executes them either via a streaming path or a fallback sequential path.

Tools are classified as parallel-safe (read-only) or exclusive (state-changing): read operations run in parallel, writes are serialized. The streaming executor starts tools as they arrive, reducing latency. It includes an abort mechanism that stops all tools if one fails, and progress signaling that streams results incrementally. Even with parallel execution, outputs are returned in original order to match model expectations.

**Tracer Perspective**

*Current:* Tracer already follows a multi-step investigation flow (planning, gathering evidence, diagnosing), as seen in synthetic scenarios.

*Ideal:* A formalized RCA pipeline: `context → reasoning → permission → execution → evaluation`

---

## Recovery Mechanisms

**Max output tokens escalation.** When the response hits the output token cap, the system can retry with an escalated limit, subject to a GrowthBook flag and the absence of an existing override or environment-variable cap. Up to three recovery attempts are allowed per turn (`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`).

**Reactive compaction (gated by REACTIVE_COMPACT).** When the context is near capacity, reactive compact summarizes just enough to free space. The `hasAttemptedReactiveCompact` flag ensures this fires at most once per turn.

**Prompt-too-long handling.** If the API returns a `prompt_too_long` error, the loop first attempts context-collapse overflow recovery and reactive compaction. Only after these fail does it terminate with `reason: 'prompt_too_long'`.

**Streaming fallback.** The `onStreamingFallback` callback handles streaming API issues, allowing the loop to retry with a different strategy.

**Fallback model.** The `fallbackModel` parameter enables switching to an alternative model if the primary model fails.

**Tracer Perspective**

*Current:* Tracer handles failures (e.g., failed tool calls) and continues execution, but fallback strategies are limited.

*Ideal:* A robust recovery system with adaptive fallback strategies and alternative evidence paths.

---

## Permission Modes and Rule Evaluation

1. **plan:** The model must create a plan; execution proceeds only after user approval.
2. **default:** Standard interactive use. Most operations require user approval.
3. **acceptEdits:** Edits within the working directory and certain filesystem shell commands (`mkdir`, `rmdir`, `touch`, `rm`, `mv`, `cp`, `sed`) are auto-approved; other shell commands require approval.
4. **auto:** An ML-based classifier evaluates requests that do not pass fast-path checks (gated by TRANSCRIPT_CLASSIFIER).
5. **dontAsk:** No prompting, but deny rules are still enforced.
6. **bypassPermissions:** Skips most permission prompts, but safety-critical checks and bypass-immune rules still apply.
7. **bubble:** Internal-only mode for subagent permission escalation to the parent terminal.

The system pre-filters tool usage, applies rule-based checks, and requests user approval when needed. A denial doesn't just stop execution — it provides feedback so the model can adjust and try a safer approach.

---

## Memory Architecture

A key design principle shapes the memory system: stored context should be inspectable and editable by the user. The system does not use embeddings or a vector similarity index for memory retrieval; instead it uses an LLM-based scan of memory-file headers to select up to five relevant files on demand, surfacing them at file granularity rather than entry granularity.

CLAUDE.md content is delivered as user context (a user message), not as system prompt content. This architectural choice has a significant implication: because CLAUDE.md content is delivered as conversational context rather than system-level instructions, model compliance with these instructions is probabilistic rather than guaranteed. Permission rules evaluated in deny-first order provide the deterministic enforcement layer. This creates a deliberate separation between guidance (CLAUDE.md, probabilistic) and enforcement (permission rules, deterministic).

**Tracer Perspective**

*Current:* Tracer operates primarily on transient investigation context.

*Ideal:* A multi-layer memory system including past incidents and learned runbooks.

---

## Subagent Types

Claude Code provides up to six built-in subagent types, depending on feature flags and entrypoint:

- **Explore:** primarily read/search-oriented investigation, with write and edit tools in its deny-list.
- **Plan:** creates structured plans; execution proceeds through the standard permission model.
- **General-purpose:** broadly capable, used when explicitly requested.
- **Claude Code Guide:** onboarding and documentation assistance, with its own permissionMode override.
- **Verification:** runs validation checks (test suites, linting).
- **Statusline-setup:** specialized for terminal status line configuration.

**Tracer Perspective**

*Current:* Tracer implicitly performs multiple roles (planning, investigation, diagnosis) within a single flow.

*Ideal:* Explicit multi-agent role separation (investigator, planner, verifier).

---

## Architectural Contrast with OpenClaw

AI agent systems share the same core design questions: safety, context management, and where reasoning should reside but different systems answer them in different ways.

Claude Code places the agent loop at the center, giving the model more autonomy while relying on a strong operational system to support it. In contrast, OpenClaw centers its architecture around a gateway and control layer, focusing more on access control and external management. These systems are not direct competitors; they can work together.

A key insight is that Claude Code's architecture is mostly deterministic infrastructure (around 98%), with only a small portion dedicated to decision-making logic. This means the real differentiator is not just the model itself, but the surrounding system tool routing, safety mechanisms, and context management.

---

## Open Directions for Future Agent Systems

### Observability and Evaluation Gap

A major challenge is silent failure: most errors in deployed agents are not crashes but unnoticed mistakes. This highlights the need for better evaluation, monitoring, and validation systems rather than relying solely on model improvements.

### Cross-Session Persistence

Memory is evolving into a first-class subsystem rather than just a byproduct of context. Future agents are expected to build and use accumulated experience, forming structured "playbooks" from past interactions.

### Harness Boundary Evolution

Systems are becoming more modular and distributed, with components (session, sandbox, harness) increasingly decoupled:

- **Where:** Components operate more independently, similar to how operating systems virtualize resources.
- **When:** Agents can act proactively, not just react to user input — improving performance but potentially affecting user experience.
- **What:** Agents are expanding beyond text to visual and physical actions, increasing capability but also risk.
- **With whom:** Systems are moving toward multi-agent setups with specialized roles, though coordination becomes harder.

The open question remains whether one unified architecture can handle all these dimensions, or if specialized systems will emerge.

### Horizon Scaling

Larger context windows and multimodal tools (images, UI previews, diagrams) will expand capabilities but also introduce new challenges in context management.

### Governance

Governance and regulation will increasingly shape agent design, requiring stronger transparency, logging, and human oversight as agents become more autonomous.

### The Evaluative Lens

Agents are moving toward more proactive behavior acting even without direct user input. However, this must be carefully balanced with user experience and cost, using signals like user presence and token usage to control when the agent should act or remain idle.

---

## Design Pattern Summary

The system uses layered designs (not single mechanisms) for safety, context, and extensibility — more robust, but more complex.

It follows an append-only approach past data is never modified, improving auditability and replay, but making queries harder.

It combines model autonomy with a deterministic harness the model makes decisions, while the system handles routing, permissions, context, and recovery.

**Tracer Perspective**

*Current:* Tracer is a capable investigation system with a staged workflow and evidence-based outputs.

*Ideal:* A coherent, hypothesis-driven SRE agent architecture with explicit reasoning, evaluation, and control loops.

---

## Tracer in the Design Space

Tracer currently operates as a structured, evidence-driven investigation system, but sits between a tool-orchestrator and a fully autonomous agent.

Its evolution direction is toward a hypothesis-driven SRE agent with an explicit reasoning loop, structured context management, and stronger evaluation and safety layers.

---

## Tracer Roadmap

- Make the agent loop explicit (plan → act → observe → evaluate)
- Introduce hypothesis-driven RCA with evidence-based reasoning
- Build a structured context pipeline (logs, metrics, alerts)
- Implement layered, deny-first safety and permission system
- Add evaluation layer to validate RCA outputs
- Improve fallback strategies for failed tools
- Enable multi-agent role separation (planner, investigator, verifier)
- Add persistent memory (incidents, runbooks)
- Standardize execution pipeline across flows
- Improve observability and reasoning transparency
