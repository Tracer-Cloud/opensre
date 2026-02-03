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

| Project | Path |
|---------|------|
| Rust Client | `/Users/janvincentfranciszek/tracer-client` |
| Web App | `/Users/janvincentfranciszek/tracer-web-app-2025` |

---

## Project Context

### Investigation Nodes (LangGraph)

The investigate node architecture:

- **Dynamic context gathering** – Parallel investigation actions (logs, traces, deployments, dependencies)
- **Adaptive action selection** – Runs CloudWatch if log_group present, falls back to local files
- **Structured synthesis** – Aggregates heterogeneous sources into unified context
- **Graceful degradation** – Continues with partial results when sources fail
- **Lean startup** – Prioritizes high-impact, easy-to-implement context first
