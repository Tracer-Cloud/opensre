# Tracer Agent – AI Coding Assistant Rules

## Hard Rules

- Never commit API keys, tokens, or secrets
- Never create `.md` files in project root (except CLAUDE.md, AGENTS.md, README.md)
- Never use mock services or fake data fallbacks
- Never bypass tests or CI checks
- Never say "pushed" unless CI is verified green

## Documentation

- Status reports and retrospectives go in `docs/status/`
- Tests live alongside the code they validate

## Code Style

- One clear purpose per file (separation of concerns)
- Code should be self-explanatory—minimal comments
- Max 3-4 print statements per file (use logging for debug, remove after)
- Let functions run silently unless they fail
- Only show results, not process
- Never use `sys.path` manipulation for imports—use proper package structure instead

## Testing

- Integration tests only, no mocks
- Test files use `_test.py` suffix in same directory as source

```
app/agent/nodes/frame_problem/frame_problem.py
app/agent/nodes/frame_problem/frame_problem_test.py
```

## Environment

- Use system `python3` directly (no virtual environments)
- Ruff is the only linter

## Git & CI Protocol

"Push" = code pushed + CI run + CI passed.

### Before Push

1. Clean working tree
2. `make test`
3. `ruff` passes
4. `make demo` runs

### After Push

1. `gh run list --branch <branch> --limit 5`
2. Verify workflow passed
3. If CI fails, fix before proceeding

## Local Paths (Vincent Only)

These paths are for local orientation only. Never hard-code in commits.

| Project     | Path                                              |
| ----------- | ------------------------------------------------- |
| Rust Client | `/Users/janvincentfranciszek/tracer-client`       |
| Web App     | `/Users/janvincentfranciszek/tracer-web-app-2025` |

---

## Project Context

### Investigation Nodes (LangGraph)

The investigate node architecture:

- **Dynamic context gathering** – Parallel investigation actions (logs, traces, deployments, dependencies)
- **Adaptive action selection** – Runs CloudWatch if log_group present, falls back to local files
- **Structured synthesis** – Aggregates heterogeneous sources into unified context
- **Graceful degradation** – Continues with partial results when sources fail
- **Lean startup** – Prioritizes high-impact, easy-to-implement context first

# How To Implement Open Telemetry (Otel In Test Pipelines):

Telemetry Instrumentation Review & Remediation
Context
Our Prefect pipeline has telemetry instrumentation that works but doesn't align with OpenTelemetry best practices. This creates maintenance burden, confusing traces, and fragile context propagation that will bite us as the system scales.
Problems We Need to Address

1. Context Propagation Architecture
   We're manually managing span context instead of trusting OpenTelemetry's built-in propagation. The execution_run_id is being threaded through calls and manually attached via ensure_execution_run_id(). This is fragile - if we add new tasks or refactor the call chain, we'll forget to propagate context and lose trace continuity. OpenTelemetry was designed to handle this automatically through context managers and thread-local storage.
2. Instrumentation Layer Confusion
   We're instrumenting at multiple layers simultaneously - wrapping Prefect tasks with spans AND wrapping the domain logic those tasks call. This creates nested spans that don't add value and make traces harder to read. We need to stop doing this immediately.
3. Error Handling Gaps
   Our spans don't record exceptions or set error status. When failures occur, we lose critical debugging information - stack traces, error types, error messages aren't captured in the trace. This defeats much of the purpose of distributed tracing. Every span that might fail needs proper exception handling.
4. Mixed Telemetry Concerns
   We're conflating tracing with metrics. The telemetry.record_run() calls are metrics operations masquerading as tracing. These should use proper OpenTelemetry metrics APIs - counters, histograms, gauges - not a custom abstraction that obscures what's actually being measured.
5. Semantic Convention Adherence
   Our attribute naming is inconsistent. Sometimes we use dots, sometimes underscores. We're not following OpenTelemetry semantic conventions for AWS S3 operations, which means our traces won't integrate cleanly with standard observability tooling that expects conventional attribute names.
   What Needs to Happen
   Instrument Domain Logic Only, Remove Task-Level Spans
   Stop wrapping Prefect tasks with spans. The task decorators already provide execution boundaries - adding spans there just creates noise. Instead, instrument the domain functions: validate_data, transform_data, the S3 adapter functions. This gives us visibility into what the code actually does, not just that Prefect executed a task. The orchestration layer is Prefect's concern, not ours.
   Remove Manual Context Management
   Strip out the ensure_execution_run_id() calls and manual context threading. Rely on OpenTelemetry's automatic context propagation. If Prefect's async execution breaks context propagation, use proper context tokens and explicit attach/detach rather than custom solutions.
   Implement Proper Error Recording
   Every span needs exception handling that records the exception on the span and sets error status. This isn't optional - it's what makes distributed tracing valuable when things go wrong.
   Separate Metrics from Traces
   Extract the metrics recording into proper OpenTelemetry meters with semantic names. Pipeline success rate, duration, record counts - these should be counters and histograms, not custom method calls bundled with tracing logic.
   Adopt Semantic Conventions
   Review OpenTelemetry semantic conventions for AWS, database operations, and messaging. Rename attributes to match. This isn't pedantry - it's what enables vendor-neutral observability and integration with standard tooling.
   Use Prefect's Native OpenTelemetry Integration
   Prefect 3.x has OpenTelemetry integration built-in. Enable it and let it handle task-level instrumentation automatically. We should be configuring it, not reimplementing it. Our custom spans should complement the automatic instrumentation, not duplicate it.
