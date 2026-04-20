# Tracer in the Agent Design Space: Claude Code vs. OpenClaw

## Background
The research paper _Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems (arXiv:2604.14228)_ outlines fundamental recurring questions in building agentic systems. Claude Code generally opts for tight UI-loop integration with an emphasis on developer workflows, while OpenClaw takes an embedded, multi-tenant runtime approach.

Tracer operates in a fundamentally different context: autonomous Site Reliability Engineering (SRE) and Incident Response. Here, safety margins, immutable audit trails, and multi-source observability are the crucial factors.

## Recurring Design Questions & Tracer's Answers

### 1. Per-Action vs. Perimeter Safety Permissions
* **The Question**: Do you interrupt the agent for user consent on every sensitive action, or wrap the agent in a highly restrictive sandbox?
* **Tracer's Current State**: Tracer relies loosely on underlying API permissions (e.g. AWS SDK read-only rules). Many operations assume implicit trust over read-only scopes.
* **Ideal SRE State**: Strict perimeter safety. Incident response demands fast read-action loops without blocking humans for consent, but write-access (auto-remediation) must fall entirely under predefined "Runbook Hooks."
* **Gap (Current ≠ Ideal)**: We lack an explicit "Permission Boundaries" framework in pp/nodes/investigate.

### 2. Session Context: Context-Window Extensions vs. Gateway-wide Registration
* **The Question**: How are tool capabilities and memory registered? Are they simply prompted into context, or strictly registered in a middle-tier layer?
* **Tracer's Current State**: We use an InvestigateInput and InvestigateOutput dict flow wrapped entirely inside pp/nodes/investigate/node.py and pp/tools/*. Tools are statically compiled and appended.
* **Ideal SRE State**: A dynamic *skill gateway* that registers tools on-the-fly depending on the incident alert's tags (e.g., if Kubernetes alert, only load K8s diagnostic tools).
* **Gap (Current ≠ Ideal)**: The prompt context is statically bloated by loading every single tool instead of contextually relevant clusters.

### 3. Append-Only Event Sourcing vs. Mutative State
* **The Question**: Is the agent memory mutable or append-only?
* **Tracer's Current State**: We partially overwrite state (e.g. updating evidence dictionaries).
* **Ideal SRE State**: Immutable append-only audit trail! SRE post-mortems require knowing exactly *what* the agent knew and *when*. Overwriting evidence breaks compliance requirements.
* **Gap (Current ≠ Ideal)**: InvestigateState mutations destroy the timeline. Tracer needs an Event Sourced session storage module.

### 4. Subagent Topologies vs. Single Loop
* **The Question**: Does a singular loop do everything or do we aggressively spawn child actors with worktree isolation?
* **Tracer's Current State**: Single massive investigative loop with executed_hypotheses.
* **Ideal SRE State**: Parallel subagent topology. One agent investigates traces, another queries metrics, a third reads logs, and the main brain synthesizes the causal chain.
* **Gap (Current ≠ Ideal)**: Investigation node is strictly sequential.

## Summary & Roadmap Impact
While Tracer excels at providing the connective tissue for multi-source evidence, its underlying agent architecture borrows too heavily from conversational chat patterns (mutable state, static global tool context). Moving aggressively toward **Append-only Audit Storage**, **Parallel Subagents**, and **Context-Aware Dynamic Skills** is the next major frontier for Tracer's evolution.