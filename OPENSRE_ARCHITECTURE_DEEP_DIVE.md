# OpenSRE Architecture Deep Dive

## How They Built an Agentic Workflow for Automated Incident Investigation

This document is a thorough technical breakdown of how the OpenSRE project builds its agentic AI workflow — the frameworks used, design patterns, architecture decisions, backend structure, and how every layer connects.

---

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Framework Choice: LangGraph](#2-framework-choice-langgraph)
3. [The State Machine — AgentState](#3-the-state-machine--agentstate)
4. [The Graph Pipeline — How Nodes Are Wired](#4-the-graph-pipeline--how-nodes-are-wired)
5. [Node-by-Node Breakdown](#5-node-by-node-breakdown)
6. [Routing and Conditional Logic](#6-routing-and-conditional-logic)
7. [The Tool System — Design Pattern](#7-the-tool-system--design-pattern)
8. [The Service Layer — External Clients](#8-the-service-layer--external-clients)
9. [LLM Client Architecture — Multi-Provider](#9-llm-client-architecture--multi-provider)
10. [Integration System — Plugin Architecture](#10-integration-system--plugin-architecture)
11. [Entry Points — How the System Is Triggered](#11-entry-points--how-the-system-is-triggered)
12. [Remote Server and Streaming](#12-remote-server-and-streaming)
13. [Safety: Guardrails and Masking](#13-safety-guardrails-and-masking)
14. [Configuration and Validation](#14-configuration-and-validation)
15. [Design Patterns Summary](#15-design-patterns-summary)
16. [Key Takeaways for Building Your Own](#16-key-takeaways-for-building-your-own)

---

## 1. High-Level Overview

OpenSRE is an **open-source SRE agent** that automates incident investigation and root cause analysis (RCA). When an alert fires (from Grafana, Datadog, PagerDuty, CloudWatch, etc.), OpenSRE:

1. Parses the alert payload
2. Resolves which monitoring integrations are available
3. Plans which tools to run (log queries, metric fetches, deployment checks)
4. Executes those tools in parallel
5. Diagnoses the root cause using an LLM
6. Publishes a structured report

The entire pipeline is built as a **LangGraph state machine** — a directed graph where each node is a Python function that reads from and writes to a shared state dict.

```
Alert Payload
    |
    v
[inject_auth] --> [route_by_mode]
                      |
          +-----------+-----------+
          |                       |
     Chat Mode            Investigation Mode
          |                       |
     [router]              [extract_alert]
      /     \                     |
[chat_agent] [general]    [resolve_integrations]
      |                           |
[tool_executor]            [plan_actions]
      |                           |
     END               [investigate_hypothesis] (parallel)
                                  |
                        [merge_hypothesis_results]
                                  |
                            [diagnose]
                           /    |     \
                     [loop]  [eval]  [publish]
                       |       |        |
                [adapt_window] |       END
                       |       |
                [plan_actions] [publish]
                                  |
                                 END
```

---

## 2. Framework Choice: LangGraph

### Why LangGraph?

OpenSRE uses **LangGraph** (from LangChain) as its orchestration framework. LangGraph provides:

- **StateGraph**: A typed state machine where nodes are functions and edges are transitions
- **Conditional edges**: Route to different nodes based on state values
- **Parallel execution via `Send`**: Fan-out to multiple nodes simultaneously
- **Built-in streaming**: `astream_events` for real-time progress
- **Compilation**: The graph compiles into an optimized execution engine

### How the Graph Is Built

File: `app/pipeline/graph.py`

```python
from langgraph.graph import END, StateGraph

def build_graph(config=None):
    graph = StateGraph(AgentState)

    # Register all nodes
    graph.add_node("inject_auth", inject_auth_node)
    graph.add_node("extract_alert", node_extract_alert)
    graph.add_node("plan_actions", node_plan_actions)
    # ... more nodes

    # Set entry point
    graph.set_entry_point("inject_auth")

    # Add conditional routing
    graph.add_conditional_edges(
        "inject_auth", route_by_mode,
        {"chat": "router", "investigation": "extract_alert"}
    )

    # Add fixed edges
    graph.add_edge("resolve_integrations", "plan_actions")

    # Compile
    return graph.compile()

# Module-level singleton
graph = build_graph()
```

**Key pattern**: The graph is compiled once at module import time and reused for every invocation. This is the **Singleton pattern** applied to the execution engine.

---

## 3. The State Machine — AgentState

### The Core Design Decision

The entire system revolves around a single **TypedDict** called `AgentState`. Every node reads from it and returns a partial dict that gets merged back. This is the **Blackboard pattern** — a shared workspace that all agents (nodes) read from and write to.

File: `app/state/agent_state.py`

```python
class AgentState(TypedDict, total=False):
    # Mode selection
    mode: AgentMode  # "chat" | "investigation"
    route: str

    # Auth context
    org_id: str
    user_id: str

    # Chat mode
    messages: Annotated[list, add_messages]  # LangGraph reducer: appends

    # Investigation input
    alert_name: str
    raw_alert: str | dict[str, Any]
    alert_json: dict[str, Any]

    # Planning
    planned_actions: list[str]
    plan_rationale: str
    available_sources: dict[str, dict]

    # Evidence gathering
    context: dict[str, Any]
    evidence: dict[str, Any]
    hypothesis_results: Annotated[list[dict], merge_results_reducer]

    # Diagnosis
    root_cause: str
    root_cause_category: str
    validated_claims: list[dict[str, Any]]
    investigation_loop_count: int

    # Time window
    incident_window: dict[str, Any] | None

    # Outputs
    slack_message: str
    problem_md: str
    report: str
```

### Dual Validation: TypedDict + Pydantic

They maintain TWO representations of the same state:

1. **`AgentState` (TypedDict)** — Used by LangGraph at runtime. Lightweight, no validation overhead.
2. **`AgentStateModel` (Pydantic BaseModel)** — Used by state factory functions. Validates all fields at construction time.

```python
class AgentStateModel(StrictConfigModel):
    mode: AgentMode = "chat"
    alert_name: str = ""
    planned_actions: list[str] = Field(default_factory=list)
    tool_budget: int = Field(default=10, ge=1, le=50)
    # ... mirrors every field in AgentState
```

A test (`tests/app/test_agent_state_sync.py`) enforces that both definitions stay in sync. This is the **Mirror Validation pattern** — runtime speed of TypedDict with construction-time safety of Pydantic.

### Custom Reducers

LangGraph uses **reducers** to merge node outputs into state. OpenSRE defines custom ones:

```python
# Messages use LangGraph's built-in append reducer
messages: Annotated[list, add_messages]

# Hypothesis results use a custom reducer that supports clearing
hypothesis_results: Annotated[list[dict], merge_results_reducer]

def merge_results_reducer(existing, new):
    if new and len(new) == 1 and new[0].get("__clear"):
        return []  # Signal to clear accumulated results
    return (existing or []) + (new or [])
```

### State Factory Functions

File: `app/state/factory.py`

```python
def make_initial_state(alert_name, pipeline_name, severity, raw_alert=None, *, opensre_evaluate=False):
    """Create initial state for investigation mode."""
    state = AgentStateModel.model_validate({
        "mode": "investigation",
        "alert_name": alert_name,
        "raw_alert": alert_payload,
        # ... defaults
    })
    return state.model_dump(mode="python", by_alias=True, exclude_none=True)

def make_chat_state(org_id="", user_id="", messages=None):
    """Create initial state for chat mode."""
    # Similar pattern
```

This is the **Factory pattern** — callers never construct state dicts manually.

---

## 4. The Graph Pipeline — How Nodes Are Wired

### Two Modes in One Graph

The graph serves two completely different use cases through a single entry point:

1. **Chat mode**: Conversational Q&A with tool access (Tracer data queries)
2. **Investigation mode**: Automated multi-step RCA pipeline

The `route_by_mode` function at the entry point splits the flow:

```python
def route_by_mode(state):
    return "investigation" if state.get("mode") == "investigation" else "chat"
```

### Investigation Pipeline Flow

```
inject_auth
    |
route_by_mode == "investigation"
    |
extract_alert ──(is_noise?)──> END
    |
resolve_integrations
    |
plan_actions ──(distribute_hypotheses)──> [parallel investigate_hypothesis nodes]
    |                                              |
    |                                    merge_hypothesis_results
    |                                              |
    |                                          diagnose
    |                                         /   |   \
    |                              (loop)  (eval) (publish)
    |                                |       |       |
    |                          adapt_window  |      END
    |                                |       |
    +<-------------------------------+   publish
                                              |
                                             END
```

### Chat Pipeline Flow

```
inject_auth
    |
route_by_mode == "chat"
    |
router ──(intent classification)──> "tracer_data" | "general"
    |                                      |
chat_agent (with tools)            general (no tools)
    |                                      |
(tool_calls?) ──> tool_executor            END
    |                  |
    +<-----------------+
    |
   END
```


---

## 5. Node-by-Node Breakdown

Each node is a Python function with the signature `(state: AgentState, config?: RunnableConfig) -> dict`. The returned dict is merged into state.

### 5.1 `inject_auth` — Auth Injection

File: `app/nodes/auth.py`

Extracts JWT auth context from LangGraph's `RunnableConfig` and injects `org_id`, `user_id`, `user_email`, `thread_id`, `run_id` into state. This decouples auth from business logic — nodes never read auth from config directly.

```python
def inject_auth_node(state, config):
    configurable = config.get("configurable", {})
    auth = configurable.get("langgraph_auth_user", {})
    return {
        "org_id": auth.get("org_id") or state.get("org_id", ""),
        "user_id": auth.get("identity") or state.get("user_id", ""),
        # ...
    }
```

**Pattern**: Gateway / Middleware — centralizes cross-cutting concerns.

### 5.2 `extract_alert` — Alert Parsing

File: `app/nodes/extract_alert/`

Parses the raw alert payload (JSON from Grafana, Datadog, PagerDuty, CloudWatch, etc.) into structured fields. Also:
- Classifies noise alerts (returns `is_noise: True` to short-circuit)
- Resolves the **incident time window** using anchor parsers
- Strips OpenRCA rubric data if running in evaluation mode

The incident window system (`app/incident_window.py`) is particularly well-designed:

```python
# Anchor parsers try each alert format in priority order
_ANCHOR_PARSERS = (
    _alertmanager_anchor,   # startsAt — most reliable
    _pagerduty_anchor,      # triggered_at
    _datadog_anchor,        # event_time (epoch)
    _cloudwatch_anchor,     # StateUpdatedTimestamp (nested in SNS)
)

def resolve_incident_window(raw_alert, *, override=None, lookback_minutes=120):
    """Try each parser. First match wins. Fallback to 'now - lookback'."""
```

**Pattern**: Chain of Responsibility — each parser tries to handle the alert; first success wins.

### 5.3 `resolve_integrations` — Integration Discovery

File: `app/nodes/resolve_integrations/`

Loads the user's configured integrations (Grafana, Datadog, AWS, etc.) from the local credential store (`~/.tracer/integrations.json`) and classifies them into a normalized runtime config dict.

The output is `resolved_integrations` — a flat dict like:
```python
{
    "grafana": {"endpoint": "https://...", "api_key": "..."},
    "aws": {"region": "us-east-1", "role_arn": "..."},
    "datadog": {"api_key": "...", "app_key": "...", "site": "datadoghq.com"},
    "_all_grafana_instances": [{"name": "prod", "config": {...}}, {"name": "staging", "config": {...}}],
}
```

**Pattern**: Service Locator — discovers available services at runtime rather than hardcoding.

### 5.4 `plan_actions` — LLM-Driven Action Planning

File: `app/nodes/plan_actions/node.py`

This is where the **agentic reasoning** happens. The node:

1. Builds an `InvestigateInput` from current state (alert info, evidence so far, available tools)
2. Applies **PII masking** before sending to the LLM
3. Asks the LLM to select which tools to run and why
4. Enforces a **tool budget** (default: 10 tools per step, configurable 1-50)
5. Supports **rerouting** — if new evidence changes the likely source family, the planner can switch strategies
6. Has a **fallback mechanism** — if the LLM returns an empty plan, it forces a verification action to prevent infinite loops

The LLM returns a structured `InvestigationPlan`:
```python
class InvestigationPlan(BaseModel):
    actions: list[str]           # e.g. ["get_cloudwatch_logs", "list_eks_pods"]
    rationale: str               # Why these actions
    retrieval_controls: dict     # Optional structured query params per action
```

**Pattern**: Planner-Executor — LLM plans, tools execute. The LLM never directly calls APIs.

### 5.5 `investigate_hypothesis` — Parallel Tool Execution

File: `app/nodes/investigate/parallel.py`

Each planned action is dispatched as a **separate LangGraph `Send`** — they run in parallel:

```python
# In routing.py
def distribute_hypotheses(state):
    actions = state.get("planned_actions", [])
    return [
        Send("investigate_hypothesis", {"action_to_run": action, "available_sources": sources})
        for action in actions
    ]
```

Each hypothesis node:
1. Looks up the action in the tool registry
2. Executes it with extracted parameters
3. Returns `hypothesis_results` (merged by the custom reducer)

**Pattern**: Fan-Out / Scatter-Gather — parallel execution with result collection.

### 5.6 `merge_hypothesis_results` — Evidence Aggregation

File: `app/nodes/investigate/merge.py`

Collects all parallel results, merges them into the evidence dict, applies masking, and updates executed hypotheses. Also handles:
- Loading OpenRCA telemetry seed data
- Updating Grafana service names if discovery found new ones
- Clearing `hypothesis_results` via the `__clear` signal for the next loop

### 5.7 `diagnose` — Root Cause Analysis

File: `app/nodes/root_cause_diagnosis/`

Sends all accumulated evidence to the **reasoning LLM** (the heavy model — Claude Sonnet, GPT-4, etc.) with a structured prompt asking for:
- Root cause description
- Root cause category (one of: `configuration_error`, `code_defect`, `data_quality`, `resource_exhaustion`, `dependency_failure`, `infrastructure`, `healthy`, `unknown`)
- Validated vs non-validated claims
- Causal chain
- Investigation recommendations (if more evidence is needed)

The response is parsed by `parse_root_cause()` which extracts structured sections from the LLM's text output.

**Pattern**: Two-Model Strategy — cheap model for planning (Haiku/GPT-4o-mini), expensive model for reasoning (Sonnet/GPT-4).

### 5.8 `adapt_window` — Time Window Expansion

File: `app/nodes/adapt_window/`

Between investigation loops, if tools returned empty results, this node **widens the incident time window** (e.g., 120min → 240min → 480min) so the next iteration queries a broader range.

Bounded by `MAX_EXPANSIONS = 2` to prevent runaway widening.

**Pattern**: Adaptive Strategy — the system self-corrects based on feedback from tool results.

### 5.9 `publish` — Report Generation

File: `app/nodes/publish_findings/`

Generates the final structured report (Slack message, markdown problem description) and writes it to state. Also handles delivery to Slack, Discord, Telegram via the delivery transport system.

### 5.10 `opensre_eval` — Benchmark Evaluation (Optional)

File: `app/nodes/evaluate_opensre/`

When `--evaluate` is passed, runs an LLM judge against OpenRCA scoring rubrics to benchmark the investigation quality. This is for offline evaluation, not production use.

---

## 6. Routing and Conditional Logic

File: `app/pipeline/routing.py`

All routing decisions are pure functions that read state and return a string:

```python
def route_by_mode(state) -> str:
    return "investigation" if state.get("mode") == "investigation" else "chat"

def route_after_extract(state) -> str:
    return "end" if state.get("is_noise") else "investigate"

def should_continue_investigation(state) -> str:
    if not available_action_names:
        return "publish"  # Safety: no tools available
    if loop_count > MAX_INVESTIGATION_LOOPS:  # MAX = 4
        return "publish"  # Budget exhausted
    if investigation_recommendations:
        return "investigate"  # LLM wants more evidence
    return "publish"  # Done

def route_investigation_loop(state) -> str:
    nxt = should_continue_investigation(state)
    if nxt == "investigate":
        return "investigate"  # Goes through adapt_window first
    if state.get("opensre_evaluate"):
        return "opensre_eval"
    return "publish"
```

**Pattern**: Strategy pattern via conditional edges — routing logic is decoupled from node logic.

### Investigation Loop Budget

The system has hard limits to prevent infinite loops:
- `MAX_INVESTIGATION_LOOPS = 4` — maximum re-planning iterations
- `MAX_EXPANSIONS = 2` — maximum time window widenings
- `tool_budget = 10` — maximum tools per planning step (configurable 1-50)
- Empty plan fallback — forces a verification action if LLM returns nothing


---

## 7. The Tool System — Design Pattern

The tool system is one of the most well-architected parts of the codebase. It supports two registration styles unified under one registry.

### 7.1 Class-Based Tools (BaseTool)

File: `app/tools/base.py`

```python
class BaseTool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict]       # JSON Schema for LLM planner
    source: ClassVar[EvidenceSource]   # e.g. "grafana", "aws", "datadog"
    use_cases: ClassVar[list[str]]
    requires: ClassVar[list[str]]
    retrieval_controls: ClassVar[RetrievalControls]

    def run(self, **kwargs) -> dict:
        """Execute the tool. Subclasses define their own signatures."""

    def is_available(self, sources: dict) -> bool:
        """Can this tool run given current integrations?"""
        return True

    def extract_params(self, sources: dict) -> dict:
        """Extract kwargs from available sources."""
        return {}

    def __call__(self, **kwargs):
        return self.run(**kwargs)
```

Each tool lives in its own folder under `app/tools/` (e.g., `CloudWatchLogsTool/`, `GrafanaMetricsTool/`). The folder contains:
- `__init__.py` — exports the tool instance
- `tool.py` — the `BaseTool` subclass with `run()` implementation

**Pattern**: Template Method — `BaseTool` defines the contract; subclasses implement `run()`, optionally override `is_available()` and `extract_params()`.

### 7.2 Function-Based Tools (Decorator)

File: `app/tools/tool_decorator.py`

```python
@tool(
    name="get_error_logs",
    description="Fetch error logs from Tracer",
    source="tracer_web",
    surfaces=("investigation", "chat"),
)
def get_error_logs(run_id: str, limit: int = 100) -> dict:
    """Implementation here."""
```

The `@tool` decorator creates a `RegisteredTool` wrapper and attaches it as a hidden attribute (`__opensre_registered_tool__`).

### 7.3 Unified Registry

File: `app/tools/registry.py`

The registry auto-discovers all tools at import time:

```python
@lru_cache(maxsize=1)
def _load_registry_snapshot():
    tools_by_name = {}
    for module_name in _iter_tool_module_names():
        module = importlib.import_module(f"app.tools.{module_name}")
        for tool in _collect_registered_tools_from_module(module):
            tools_by_name[tool.name] = tool
    return tuple(sorted(tools_by_name.values(), key=lambda t: t.name))
```

Tools are tagged with **surfaces** — `"investigation"` and/or `"chat"`:
- Investigation tools: Used by the plan_actions → investigate pipeline
- Chat tools: Bound to the LangChain chat model via `bind_tools()`

### 7.4 RegisteredTool — The Uniform Runtime

File: `app/tools/registered_tool.py`

Both class-based and function-based tools are normalized into `RegisteredTool`:

```python
@dataclass
class RegisteredTool:
    name: str
    description: str
    input_schema: dict
    source: EvidenceSource
    run: Callable           # The actual execution function
    surfaces: tuple[ToolSurface, ...]
    is_available: Callable  # Check if tool can run
    extract_params: Callable  # Extract params from sources
    tags: tuple[str, ...]
    cost_tier: CostTier | None  # "cheap" | "moderate" | "expensive"
```

**Pattern**: Adapter — normalizes two different tool definition styles into one uniform interface.

### 7.5 Tool Execution in Investigation

The investigation pipeline does NOT use LangChain's tool-calling mechanism. Instead:

1. `plan_actions` asks the LLM to pick tool names from a list
2. `distribute_hypotheses` creates `Send` messages for each action
3. `investigate_hypothesis` looks up the tool in the registry and calls `tool.run(**params)`
4. Results are collected in `merge_hypothesis_results`

This is deliberate — it gives full control over parallel execution, error handling, and budget enforcement without being constrained by LangChain's ReAct loop.

### 7.6 Tool Execution in Chat

Chat mode DOES use LangChain's tool-calling:

```python
def get_chat_tools():
    return [StructuredTool.from_function(
        func=tool.run, name=tool.name, description=tool.description
    ) for tool in get_registered_tools("chat")]

# Bound to the LLM
llm_with_tools = base_llm.bind_tools(get_chat_tools())
```

The `tool_executor_node` processes `AIMessage.tool_calls` and returns `ToolMessage` results.

---

## 8. The Service Layer — External Clients

### Architecture

Service clients live under `app/services/` and follow a consistent pattern:

```
app/services/
    __init__.py          # Re-exports key clients
    llm_client.py        # LLM provider abstraction
    env.py               # Environment validation helpers
    cloudwatch_client.py # AWS CloudWatch
    lambda_client.py     # AWS Lambda
    s3_client.py         # AWS S3
    aws_sdk_client.py    # Generic AWS SDK wrapper
    grafana/             # Grafana (Loki, Mimir, Tempo)
    datadog/             # Datadog API
    elasticsearch/       # Elasticsearch
    honeycomb/           # Honeycomb traces
    coralogix/           # Coralogix logs
    eks/                 # Amazon EKS (Kubernetes)
    jira/                # Jira issue management
    opsgenie/            # OpsGenie alerts
    splunk/              # Splunk search
    vercel/              # Vercel deployments
    tracer_client/       # Tracer platform API
    google_docs/         # Google Docs report generation
    notion/              # Notion pages
```

### Client Pattern

Each service client:
1. Takes credentials from `resolved_integrations` (not from env directly)
2. Makes HTTP/SDK calls to the external service
3. Returns structured data (dicts, dataclasses, or Pydantic models)
4. Handles retries and error formatting internally

Example — Grafana client hierarchy:
```
app/services/grafana/
    __init__.py          # Exports GrafanaClient, GrafanaAccountConfig
    client.py            # Main GrafanaClient class
    loki.py              # Loki log queries
    mimir.py             # Mimir metric queries
    tempo.py             # Tempo trace queries
```

**Pattern**: Facade — each service folder provides a unified client that hides the complexity of multiple sub-APIs (Loki, Mimir, Tempo for Grafana).

---

## 9. LLM Client Architecture — Multi-Provider

File: `app/services/llm_client.py`

### Two-Model Strategy

OpenSRE uses TWO LLM models simultaneously:

1. **Reasoning model** (`get_llm_for_reasoning()`) — Heavy model for root cause diagnosis
   - Anthropic: Claude Sonnet 4.6
   - OpenAI: GPT-5.4
   - Bedrock: us.anthropic.claude-sonnet-4-6

2. **Tool-call model** (`get_llm_for_tools()`) — Lightweight model for planning and routing
   - Anthropic: Claude Haiku 4.5
   - OpenAI: GPT-5.4-mini
   - Bedrock: us.anthropic.claude-haiku-4-5

### Provider Abstraction

```python
# All providers implement the same interface
class LLMClient:          # Anthropic (direct API)
class OpenAILLMClient:    # OpenAI, OpenRouter, Gemini, NVIDIA, Ollama, MiniMax
class BedrockLLMClient:   # AWS Bedrock (IAM auth)
class CLIBackedLLMClient: # CLI subprocess (Codex)

# Factory function
def _create_llm_client(model_type: str):
    settings = LLMSettings.from_env()
    if settings.provider == "openai":
        return OpenAILLMClient(model=..., max_tokens=...)
    elif settings.provider == "bedrock":
        return BedrockLLMClient(model=..., max_tokens=...)
    elif settings.provider == "anthropic":
        return LLMClient(model=..., max_tokens=...)
    # ... 9 providers total
```

### Supported Providers (9 total)

| Provider | Auth | Base URL |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | Default Anthropic API |
| OpenAI | `OPENAI_API_KEY` | Default OpenAI API |
| OpenRouter | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| Gemini | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| NVIDIA NIM | `NVIDIA_API_KEY` | `https://integrate.api.nvidia.com/v1` |
| MiniMax | `MINIMAX_API_KEY` | `https://api.minimax.io/v1` |
| Ollama | None (local) | `http://localhost:11434/v1` |
| AWS Bedrock | IAM role | AWS SDK |
| Codex | CLI auth | Subprocess |

### Structured Output

```python
class StructuredOutputClient:
    """Wraps any LLM client for Pydantic JSON parsing."""
    def invoke(self, prompt):
        schema = self._model.model_json_schema()
        wrapped = f"{prompt}\n\nReturn ONLY valid JSON matching: {schema}"
        response = self._base.invoke(wrapped)
        payload = _extract_json_payload(response.content)
        return self._model.model_validate(payload)
```

**Pattern**: Decorator / Wrapper — adds structured output capability to any LLM client without modifying it.

### Retry Logic

All clients implement exponential backoff with 3 attempts:
```python
backoff_seconds = 1.0
for attempt in range(3):
    try:
        response = self._client.messages.create(**kwargs)
        break
    except AuthenticationError:
        raise  # Don't retry auth failures
    except Exception:
        if attempt == 2:
            raise
        time.sleep(backoff_seconds)
        backoff_seconds *= 2
```

### Credential Resolution

File: `app/llm_credentials.py`

API keys are resolved with a two-tier fallback:
1. Environment variable (e.g., `ANTHROPIC_API_KEY`)
2. System keychain via `keyring` library

```python
def resolve_llm_api_key(env_var: str) -> str:
    env_value = os.getenv(env_var, "").strip()
    if env_value:
        return env_value
    return (keyring.get_password("opensre.llm", env_var) or "").strip()
```

This allows secure local storage without `.env` files.


---

## 10. Integration System — Plugin Architecture

The integration system is how OpenSRE knows which external services are available for a given user/org.

### 10.1 Local Credential Store

File: `app/integrations/store.py`

Integrations are stored in `~/.tracer/integrations.json` with a versioned schema:

```json
{
  "version": 2,
  "integrations": [
    {
      "id": "grafana-1",
      "service": "grafana",
      "status": "active",
      "instances": [
        {
          "name": "prod",
          "tags": {"env": "prod"},
          "credentials": {"endpoint": "https://...", "api_key": "..."}
        },
        {
          "name": "staging",
          "tags": {"env": "staging"},
          "credentials": {"endpoint": "https://...", "api_key": "..."}
        }
      ]
    }
  ]
}
```

The store supports:
- **Multi-instance**: Multiple Grafana clusters, multiple AWS accounts
- **Schema migration**: v1 records are auto-migrated to v2 on load
- **Instance selection**: By name, by tags, or default (first)
- **File permissions**: `chmod 0o600` on the store file

### 10.2 Integration Catalog — Classification

File: `app/integrations/catalog.py`

The catalog takes raw integration records and classifies them into normalized runtime configs:

```python
def classify_integrations(integrations: list[dict]) -> dict:
    """Classify active integrations by service."""
    resolved = {}
    for integration in active_integrations:
        key = _SERVICE_KEY_MAP.get(service_lower)  # "grafana", "aws", etc.
        flat_view, flat_key = _classify_service_instance(key, credentials)
        resolved[flat_key] = flat_view
    return resolved
```

Each service has its own classification logic with Pydantic validation:
- Grafana: Splits into `grafana` (cloud) vs `grafana_local` (localhost)
- AWS: Supports both role ARN and static credentials
- Datadog: Requires both `api_key` and `app_key`
- etc.

### 10.3 Integration Models

File: `app/integrations/models.py`

Every integration has a strict Pydantic model:

```python
class GrafanaIntegrationConfig(StrictConfigModel):
    endpoint: str
    api_key: str = ""
    integration_id: str = ""

    @property
    def is_local(self) -> bool:
        host = urlparse(self.endpoint).hostname
        return host in {"localhost", "127.0.0.1", "0.0.0.0"}

class AWSIntegrationConfig(StrictConfigModel):
    region: str = "us-east-1"
    role_arn: str = ""
    credentials: AWSStaticCredentials | None = None

    @model_validator(mode="after")
    def _require_auth_method(self):
        if not self.role_arn and not self.credentials:
            raise ValueError("AWS requires role_arn or credentials")
        return self
```

### 10.4 Multi-Instance Selectors

File: `app/integrations/selectors.py`

Clean API for selecting instances from resolved integrations:

```python
# Get default instance
config = get_default_instance(resolved, "grafana")

# Get by name
config = get_instance_by_name(resolved, "grafana", "prod")

# Get by tag
configs = get_instances_by_tag(resolved, "grafana", "env", "staging")

# Smart selection: name > tags > default
config = select_instance(resolved, "grafana", name="prod", tags={"env": "prod"})
```

**Pattern**: Repository + Strategy — the store is the repository; selectors are strategies for finding the right instance.

### 10.5 Supported Integrations (35+)

Monitoring: Grafana, Datadog, Honeycomb, Coralogix, BetterStack, Splunk, OpenObserve, OpenSearch, Azure Monitor
Cloud: AWS (CloudWatch, EKS, Lambda, S3), Vercel
Alerting: Alertmanager, OpsGenie, PagerDuty
Code: GitHub, GitLab, Bitbucket
Databases: PostgreSQL, MySQL, MariaDB, MongoDB, MongoDB Atlas, Azure SQL, ClickHouse, Snowflake
Messaging: Kafka, RabbitMQ
Issue Tracking: Jira, Trello, Notion
Delivery: Slack, Discord, Telegram
Orchestration: Airflow, Prefect, ArgoCD
Other: Sentry, Google Docs, OpenClaw

---

## 11. Entry Points — How the System Is Triggered

OpenSRE has 6 distinct entry points, all converging on the same LangGraph pipeline:

### 11.1 CLI (`opensre` command)

File: `app/cli/__main__.py`

Built with **Click** framework. Supports:
- Interactive REPL mode (default when TTY is attached)
- Subcommands: `investigate`, `health`, `deploy`, `integrations`, etc.
- Tab completion for bash/zsh/fish

```python
@click.group(cls=RichGroup, invoke_without_command=True)
@click.version_option(version=get_version())
def cli(ctx, json_output, verbose, debug, yes, interactive, layout):
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty():
            run_repl(config=config)  # Interactive REPL
        else:
            render_landing()  # Non-interactive landing page
```

### 11.2 Legacy CLI (`python -m app.main`)

File: `app/main.py`

Simpler argparse-based entry point:
```python
def main(argv=None):
    args = parse_args(argv)
    payload = load_payload(input_path=args.input)
    result = run_investigation_cli(raw_alert=payload)
    write_json(result, args.output)
```

### 11.3 Web API (FastAPI health check)

File: `app/webapp.py`

Minimal FastAPI app with a `/health` endpoint:
```python
@app.get("/health")
def health():
    return HealthResponse(
        ok=graph_loaded and llm_configured,
        version=get_version(),
        graph_loaded=_graph_loaded(),
        llm_configured=_llm_configured(),
        env=get_environment().value,
    )
```

### 11.4 Remote Server (Full FastAPI)

File: `app/remote/server.py`

Production-grade FastAPI server for remote investigations:

```python
# Endpoints:
POST /investigate              # Blocking investigation
POST /investigate/stream       # SSE streaming investigation
GET  /investigations           # List past investigations
GET  /investigations/{id}      # Get investigation report (.md)
POST /discord/interactions     # Discord slash command webhook
GET  /ok                       # Health check with instance metadata
GET  /health/deep              # Deep health (LLM, disk, memory)
GET  /version                  # Version info
```

Features:
- API key authentication (`X-API-Key` header)
- Investigation persistence as `.md` files
- Discord bot integration (slash commands with deferred responses)
- Vercel deployment poller (background task)
- EC2 instance metadata (IMDS v2)
- SSE streaming compatible with `StreamRenderer`

### 11.5 MCP Server (Model Context Protocol)

File: `app/entrypoints/mcp.py`

Exposes the investigation as an MCP tool:
```python
mcp = FastMCP("opensre")

@mcp.tool(name="run_rca")
def run_rca(alert_payload: dict, alert_name=None, pipeline_name=None, severity=None):
    """Run the existing OpenSRE investigation workflow over MCP."""
    result = run_investigation_cli(raw_alert=payload)
    return RunRCAOutput(ok=True, result=result).model_dump()
```

### 11.6 SDK (Programmatic)

File: `app/entrypoints/sdk.py`

```python
def run_investigation(*args, **kwargs):
    """Lazily import the full runner stack."""
    from app.pipeline.runners import run_investigation as _run
    return _run(*args, **kwargs)
```

**Pattern**: Facade — all entry points converge on `run_investigation_cli()` or `run_investigation()`, which calls `graph.invoke(initial_state)`.

---

## 12. Remote Server and Streaming

### 12.1 Streaming Architecture

File: `app/remote/stream.py`

The streaming system uses **Server-Sent Events (SSE)** compatible with LangGraph's format:

```python
@dataclass
class StreamEvent:
    event_type: str      # "events", "updates", "metadata", "end"
    data: dict           # Parsed JSON payload
    node_name: str       # Which graph node produced this
    kind: str            # "on_tool_start", "on_chat_model_stream", etc.
    run_id: str
    tags: list[str]
```

SSE format:
```
event: events
data: {"event": "on_chain_start", "name": "extract_alert", ...}

event: events
data: {"event": "on_tool_start", "name": "get_cloudwatch_logs", ...}

event: end
data: {}
```

### 12.2 Stream Renderer

File: `app/remote/renderer.py`

The `StreamRenderer` consumes SSE events and renders live terminal progress:

```python
class StreamRenderer:
    def render_stream(self, events: Iterator[StreamEvent]) -> dict:
        for event in events:
            self._handle_event(event)
        self._print_report()
        return self._final_state

    def _handle_events_mode(self, event):
        # Node lifecycle from on_chain_start / on_chain_end
        # Sub-node callbacks update spinner subtext in real time
        text = reasoning_text(kind, event.data, canonical)
        self._tracker.update_subtext(canonical, text)
```

The same renderer works for both local and remote investigations — local uses `astream_events` directly; remote uses SSE over HTTP.

### 12.3 Local Streaming Bridge

File: `app/cli/investigate.py`

Bridges async LangGraph streaming into a synchronous iterator using a background thread + queue:

```python
def stream_investigation_cli(*, raw_alert, ...):
    event_queue = queue.Queue()

    def _run_async():
        loop = asyncio.new_event_loop()
        async def _pump():
            async for evt in astream_investigation(...):
                event_queue.put(evt)
        loop.run_until_complete(_pump())
        event_queue.put(None)  # Sentinel

    thread = threading.Thread(target=_run_async, daemon=True)
    thread.start()

    while True:
        item = event_queue.get()
        if item is None:
            break
        yield item
```

**Pattern**: Producer-Consumer with async bridge — async LangGraph events are pumped into a thread-safe queue consumed by the synchronous CLI.

---

## 13. Safety: Guardrails and Masking

### 13.1 Guardrail Engine

File: `app/guardrails/engine.py`

A rule-based content scanning engine that runs on all LLM inputs:

```python
class GuardrailEngine:
    def scan(self, text: str) -> ScanResult:
        """Scan against all enabled rules."""
        for rule in self._rules:
            for pattern in rule.patterns:
                # Regex matching
            for keyword in rule.keywords:
                # Keyword matching

    def apply(self, text: str) -> str:
        """Scan, redact, audit. Raises on block."""
        result = self.scan(text)
        if result.blocked:
            raise GuardrailBlockedError(result.blocking_rules)
        # Apply redactions
        for match in redact_matches:
            redacted = redacted[:match.start] + "[REDACTED:rule_name]" + redacted[match.end:]
        return redacted
```

Three actions per rule:
- **Redact**: Replace matched text with `[REDACTED:rule_name]`
- **Block**: Raise `GuardrailBlockedError` — stops the pipeline
- **Audit**: Log the match for compliance

Rules are loaded from a YAML config file. The engine is a module-level singleton.

### 13.2 PII Masking

File: `app/masking/`

Infrastructure identifier masking that's reversible:

```python
class MaskingContext:
    def mask_value(self, value):
        """Replace sensitive identifiers with placeholders."""
        # e.g., "prod-db-cluster-1" -> "MASKED_DB_1"

    def unmask_value(self, value):
        """Reverse the masking using the stored map."""

    def to_state(self) -> dict[str, str]:
        """Return placeholder->original map for state storage."""
```

The masking map is stored in `AgentState.masking_map` so it persists across nodes.

**Pattern**: Reversible Proxy — sensitive data is masked before LLM sees it, unmasked in the final report.

---

## 14. Configuration and Validation

### 14.1 StrictConfigModel

File: `app/strict_config.py`

The base class for ALL Pydantic models in the project:

```python
class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")  # No unknown fields

    @field_validator("*", mode="before")
    def _strip_string_values(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="before")
    def _reject_unknown_fields(cls, data):
        # Suggests close matches for typos
        # e.g., "api_ky" -> "did you mean 'api_key'?"
```

This is applied everywhere — integration configs, LLM settings, tool metadata, state models. It catches configuration errors early with helpful suggestions.

### 14.2 LLMSettings

File: `app/config.py`

```python
class LLMSettings(StrictConfigModel):
    provider: LLMProvider  # 9 supported providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # ... per-provider keys and model overrides

    @field_validator("provider")
    def _normalize_provider(cls, value):
        # Suggests close matches: "antrhopic" -> "did you mean 'anthropic'?"

    @model_validator(mode="after")
    def _require_api_key_for_selected_provider(self):
        # Validates that the selected provider has its API key set

    @classmethod
    def from_env(cls):
        """Build validated settings from environment variables."""
```

### 14.3 Evidence and Retrieval Types

File: `app/types/retrieval.py`

Structured retrieval controls that tools can declare support for:

```python
class RetrievalIntent(BaseModel):
    time_bounds: TimeBounds | None       # Start/end time or lookback
    filters: list[FilterCondition] | None  # Field-level filters
    limit: int | None                    # Max results
    fields: FieldSelection | None        # Include/exclude fields
    aggregation: AggregationSpec | None  # Group-by, count, avg, percentiles

class RetrievalControls(BaseModel):
    """Declares which controls a tool supports."""
    time_bounds: bool = False
    filters: bool = False
    limit: bool = False
    fields: bool = False
    aggregation: bool = False
```

This allows the planner to send structured query parameters to tools that support them, rather than relying on the tool to guess defaults.

---

## 15. Design Patterns Summary

| Pattern | Where Used | Purpose |
|---|---|---|
| **Blackboard** | `AgentState` | Shared workspace all nodes read/write |
| **State Machine** | LangGraph `StateGraph` | Explicit transitions between processing steps |
| **Factory** | `make_initial_state()`, `make_chat_state()` | Validated state construction |
| **Mirror Validation** | `AgentState` + `AgentStateModel` | Runtime speed + construction safety |
| **Chain of Responsibility** | Incident window anchor parsers | First parser to match wins |
| **Strategy** | Routing functions, LLM provider selection | Swappable behavior via configuration |
| **Template Method** | `BaseTool` | Define contract, subclasses implement |
| **Adapter** | `RegisteredTool` | Unify class-based and function-based tools |
| **Facade** | Service clients, entry points | Hide complexity behind simple interfaces |
| **Service Locator** | `resolve_integrations` | Discover available services at runtime |
| **Repository** | Integration store | CRUD for credential records |
| **Singleton** | Compiled graph, LLM clients, guardrail engine | One instance, reused |
| **Fan-Out / Scatter-Gather** | `distribute_hypotheses` → parallel → merge | Parallel tool execution |
| **Planner-Executor** | `plan_actions` → `investigate_hypothesis` | LLM plans, tools execute |
| **Two-Model Strategy** | Reasoning vs tool-call LLM | Cost/speed optimization |
| **Decorator/Wrapper** | `StructuredOutputClient`, `@tool` | Add capabilities without modifying base |
| **Producer-Consumer** | Async streaming bridge | Thread-safe async-to-sync event passing |
| **Reversible Proxy** | PII masking | Mask before LLM, unmask in output |
| **Gateway/Middleware** | `inject_auth` | Centralize cross-cutting concerns |
| **Adaptive Strategy** | `adapt_window` | Self-correct based on tool feedback |

---

## 16. Key Takeaways for Building Your Own

### Architecture Decisions Worth Copying

1. **Single state dict (Blackboard)** — Every node reads/writes the same `AgentState`. No message passing between nodes. Simple, debuggable, serializable.

2. **Two-model strategy** — Use a cheap fast model for planning/routing, expensive model for reasoning. Cuts cost 5-10x on the planning steps.

3. **Planner-Executor separation** — The LLM picks tool names from a list. A separate execution layer runs them. This gives you control over parallelism, retries, budgets, and error handling that you lose with ReAct loops.

4. **Investigation loop with hard limits** — Max 4 loops, max 10 tools per step, max 2 window expansions. Without these, agentic systems spiral.

5. **Parallel tool execution via `Send`** — LangGraph's `Send` primitive lets you fan out to N parallel nodes. Much better than sequential tool calls.

6. **Strict Pydantic everywhere** — `extra="forbid"` on every model catches typos and drift. The "did you mean?" suggestions are a nice touch.

7. **Multi-entry-point convergence** — CLI, API, MCP, SDK all funnel into the same `graph.invoke()`. One pipeline, many interfaces.

8. **Reversible masking** — Mask sensitive data before the LLM sees it, unmask in the final report. The masking map travels in state.

9. **Incident time window** — Parse the alert's own timestamps instead of using "last 60 minutes from now." Adaptive widening when tools return empty.

10. **Tool registry with auto-discovery** — Drop a folder in `app/tools/`, it's automatically registered. No manual wiring needed.

### What Makes This Production-Grade

- **Guardrails engine** with redact/block/audit actions
- **Credential resolution** from env → keychain fallback
- **Schema migration** for the integration store (v1 → v2)
- **Exponential backoff** on all LLM calls
- **Budget enforcement** at every level (loops, tools, window expansions)
- **Streaming parity** — local and remote investigations render identically
- **Health checks** — deep health probes LLM connectivity, disk, memory
- **Path traversal protection** on investigation file access
- **API key authentication** on the remote server
- **Observability** — LangSmith tracing via `@traceable` decorators

---

> This document was generated by analyzing the actual OpenSRE source code. All file paths, class names, and code snippets reference real implementations in the repository.
