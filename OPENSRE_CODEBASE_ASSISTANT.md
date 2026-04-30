# 🔧 OpenSRE Codebase Assistant Instructions

You are analyzing a real production-level repository (OpenSRE) — an open-source SRE agent for automated incident investigation and root cause analysis, built on LangGraph. Your goal is to help me work efficiently on specific tasks, not to explain everything broadly.

---

## 🎯 Core Objective

- Focus only on relevant files and logic
- Avoid scanning or summarizing the entire repository
- Help me trace execution paths and dependencies

---

## 🧠 How to Think

When I ask a question or provide an issue:

1. **Identify the entry point** (CLI, API, webhook, or function)
2. **Trace the flow step-by-step:**
   - CLI (`app/cli/__main__.py`) → command handler (`app/cli/commands/`) → investigation runner (`app/cli/investigate.py`) → pipeline (`app/pipeline/runners.py`) → graph nodes (`app/nodes/`) → tools (`app/tools/`) → service clients (`app/services/`)
3. **Highlight only:**
   - Relevant files
   - Key functions/classes
   - Data flow between components

---

## 🏗️ Architecture Quick Reference

### Entry Points
| Entry Point | File | Purpose |
|---|---|---|
| CLI (`opensre`) | `app/cli/__main__.py` | Click-based CLI with REPL, subcommands |
| Legacy CLI | `app/main.py` | argparse-based `run_investigation_cli` |
| Web API | `app/webapp.py` | FastAPI/webhook receiver |
| MCP Server | `app/entrypoints/mcp.py` | Model Context Protocol entrypoint |
| SDK | `app/entrypoints/sdk.py` | Programmatic SDK entrypoint |
| Remote Server | `app/remote/server.py` | Remote investigation server (Slack/Discord/Telegram) |

### Investigation Pipeline (LangGraph)
Defined in `app/pipeline/graph.py` → `build_graph()`:

```
inject_auth → route_by_mode
  ├─ chat mode → router → chat_agent ↔ tool_executor → END
  └─ investigation mode → extract_alert → resolve_integrations → plan_actions
       → investigate_hypothesis → merge_hypothesis_results → diagnose
           ├─ loop → adapt_window → plan_actions (re-enter)
           ├─ opensre_eval → publish → END
           └─ publish → END
```

### State
- `app/state/agent_state.py` — `AgentState` TypedDict + `AgentStateModel` Pydantic model
- Key fields: `mode`, `raw_alert`, `alert_json`, `planned_actions`, `context`, `evidence`, `root_cause`, `incident_window`, `slack_message`, `problem_md`

### Key Directories
| Directory | Purpose |
|---|---|
| `app/cli/` | CLI commands, REPL, argument parsing, layout |
| `app/cli/commands/` | Click subcommands (investigate, health, deploy, etc.) |
| `app/nodes/` | LangGraph node implementations (extract_alert, investigate, diagnose, etc.) |
| `app/pipeline/` | Graph construction, routing logic, runners |
| `app/tools/` | LangChain tools (one folder per tool, e.g. `CloudWatchLogsTool/`) |
| `app/services/` | External service clients (Grafana, Datadog, EKS, etc.) |
| `app/integrations/` | Integration catalog, config store, verification |
| `app/remote/` | Remote server, Slack/Discord/Telegram delivery, streaming |
| `app/guardrails/` | Safety guardrails engine, rules, audit |
| `app/masking/` | PII/infrastructure identifier masking |
| `app/state/` | AgentState definition, factory, types |
| `app/utils/` | Delivery transports, config helpers, truncation |
| `app/types/` | Evidence, retrieval, tool type definitions |
| `tests/` | Mirrors `app/` structure — unit, integration, e2e, synthetic, benchmarks |

---

## 📂 File Selection Rules

- Do NOT open or explain unrelated files
- **Prioritize:**
  - `app/` → core logic
  - `app/services/` → external integrations & LLM logic
  - `app/nodes/` → pipeline step implementations
  - `app/tools/` → tool definitions and execution
  - `tests/` → expected behavior and usage patterns
- **Ignore unless explicitly needed:**
  - `infra/`
  - `scripts/`
  - `packaging/`
  - `docs/`

---

## 🔍 Output Style

For every response:

1. Start with a **short explanation of the flow**
2. List only **relevant files**
3. Show **function-level understanding**
4. Explain **how data moves**
5. Suggest **where to modify code**

**Avoid:**
- Long theory
- Rewriting entire files
- Generic explanations

---

## ⚡ When Debugging

- Help me reproduce the issue
- Point to **exact file + function**
- Suggest **minimal changes**
- Mention edge cases if relevant
- Check `tests/` for existing coverage of the area

---

## 🚀 When Implementing Features

- Suggest **smallest possible change**
- Reuse existing patterns in repo (tool decorator, node structure, state fields)
- Avoid introducing unnecessary abstractions
- Follow existing conventions:
  - Tools: one folder per tool under `app/tools/`, use `@tool_decorator`
  - Nodes: folder under `app/nodes/`, wire into `app/pipeline/graph.py`
  - Services: client class under `app/services/`
  - Integrations: register in `app/integrations/catalog.py`
  - State fields: add to both `AgentState` and `AgentStateModel` (they must stay in sync)

---

## 🧩 Example Behavior

**If I say:** "Fix investigate command bug"

You should:
1. Locate CLI entry → `app/cli/__main__.py` → `app/cli/commands/` (investigate subcommand)
2. Trace into `app/cli/investigate.py` → `run_investigation_cli()` or `run_investigation_cli_streaming()`
3. Follow into `app/pipeline/runners.py` → `run_investigation()` / `astream_investigation()`
4. Identify which graph node fails → check `app/nodes/` and `app/pipeline/routing.py`
5. Suggest fix location with exact function name

**If I say:** "Add a new tool for PagerDuty"

You should:
1. Look at an existing tool folder (e.g. `app/tools/OpsGenieAlertsTool/`) for the pattern
2. Create a new folder `app/tools/PagerDutyAlertsTool/`
3. Register it in `app/tools/registry.py`
4. Add the service client under `app/services/pagerduty/`
5. Wire integration config in `app/integrations/`

---

## ❌ What NOT to Do

- Do not summarize entire repo
- Do not explain obvious Python basics
- Do not guess without referencing code
- Do not jump across unrelated modules
- Do not suggest changes that break `AgentState` / `AgentStateModel` sync

---

## ✅ What GOOD Looks Like

- Precise
- Context-aware
- Minimal but insightful
- Actionable
- References actual file paths and function names

---

> Work like a senior engineer reviewing a codebase, not like a tutorial generator.
