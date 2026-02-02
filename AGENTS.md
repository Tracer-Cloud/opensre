# Tracer Agent – Project Overview for AI Coding Assistant

## Workflow Orchestration

### Plan Mode Default
- Enter plan mode for any non-trivial task (3+ steps or architectural decisions).
- If something goes sideways, stop and re-plan immediately; do not push through.
- Use plan mode for verification steps, not just implementation.
- Write detailed specs upfront to reduce ambiguity.

### Subagent Strategy
- Use subagents liberally to keep the main context clean.
- Offload research, exploration, and parallel analysis to subagents.
- For complex problems, increase parallelism via subagents.
- One task per subagent for focused execution.

### Self-Improvement Loop
- After any correction, update `tasks/lessons.md` with the pattern.
- Write explicit rules to prevent repeating the same mistake.
- Iterate ruthlessly until the mistake rate drops.
- Review relevant lessons at the start of each session.

### Verification Before Done
- Never mark a task complete without proving it works.
- Diff behavior before and after changes when relevant.
- Ask: “Would a staff engineer approve this?”
- Run tests, check logs, and demonstrate correctness.

### Demand Elegance (Balanced)
- For non-trivial changes, pause and ask: “Is there a more elegant way?”
- If it feels hacky, re-implement using best current knowledge.
- Skip this for obvious fixes; do not over-engineer.
- Challenge your own work before presenting it.

### Autonomous Bug Fixing
- When given a bug report, fix it directly; no hand-holding.
- Start from logs, errors, and failing tests.
- Require zero context switching from the user.
- Fix failing CI without being told how.

## Task Management
- Plan first: write a plan in `tasks/todo.md` with checkable items.
- Verify plan: review the plan before implementation.
- Track progress: mark items complete as you go.
- Explain changes: add a high-level summary at each step.
- Document results: add a review section to `tasks/todo.md`.
- Capture lessons: update `tasks/lessons.md` after corrections.

## Core Principles
- Simplicity first: minimal change, minimal code, maximal clarity.
- No laziness: fix root causes; no temporary fixes.
- Minimal impact: touch only what’s necessary; avoid introducing bugs.

## Workflow Expectations
- When “push” is mentioned, it means pushing the commit to GitHub and verifying that all linting checks and GitHub Actions pass for that commit.
- Before pushing any changes, always run `make demo` locally.

## Sensitive Data
- Never commit API keys, tokens, or secrets of any kind.

## Testing Approach
- Write tests as integration tests only. Do not use mock services.
- Tests should live alongside the code they validate.
- If the source file is large, create a separate test file in the same directory using the `_test.py` suffix.

Example:
```
app/agent/nodes/frame_problem/frame_problem.py
app/agent/nodes/frame_problem/frame_problem_test.py
```

## Linting
- Ruff is the only linter used in this project.
- Linting must pass before any push.

## Environment
- Do not use virtual environments.
- Use the system `python3` directly.

## Best Practices

### Coding
- Focus on separation of concerns; files should have one clear purpose.
- Keep the code self-explanatory and descriptive.

### Comments and Print Statements
- Keep print statements to 3–4 per file max, unless debugging.
- Use logging for debug info (configurable) but remove it after debugging.
- Let functions run silently unless they fail.
- Only show results, not process.

### Committing
- Always run linters before committing.
- Always validate changes with `make test`.
- Follow Go-style discipline in structure and formatting where applicable.
- Review all changes for potential security implications.

## What Not to Do
- Do not introduce fallback logic that relies on mock or fake data.
- Do not bypass tests or CI checks.

## GitHub Push and CI Verification Protocol
“Push” means completing the full push cycle, not just running `git push`.

### Required Steps Before Declaring a Push Successful
1. Ensure working tree is clean.
2. Run `make test` locally.
3. Run linters locally (`ruff`).
4. Push the branch to GitHub.

### Required Steps After Pushing
1. Verify GitHub authentication is working.
2. If `gh` reports HTTP 401, run `gh auth login`.
3. Ensure `GITHUB_TOKEN` is correctly scoped or unset if using `gh` credentials.
4. Check GitHub Actions for the pushed branch:
   - `gh run list --branch <branch> --limit N`
5. Identify the most recent workflow run for the commit.
6. Confirm CI status:
   - All required workflows must complete successfully.
   - A failed or cancelled workflow means the push is not complete.

### Failure Handling
- If CI fails, investigate and fix before proceeding.
- Do not proceed assuming CI will “probably pass”.
- If authentication blocks CI inspection, resolve auth first before continuing work.

### Completion Definition
A push is only considered complete when:
- Code is pushed.
- CI has run.
- CI has passed.

Optional but recommended:
- Capture CI run ID in commit or task notes.
- Call out infra or CI failures explicitly if unrelated to code changes.

### Why This Helps
- Prevents silent CI failures.
- Prevents broken demo branches.
- Forces agents to treat CI as part of the development loop, not an afterthought.

### Hard Rule
Never say “pushed” unless CI has been checked and verified green.

## Local Repositories (Vincent Only)
The following local repository paths apply only to Vincent’s development environment and must not be assumed for any other user, agent, or runtime.

Hard rule: These paths must never be hard-coded into commits, configuration files, tests, or documentation intended for general use.

Rust Client:
- `/Users/janvincentfranciszek/tracer-client`

Backend + Web App (Next.js):
- `/Users/janvincentfranciszek/tracer-web-app-2025`

Any agent operating outside Vincent’s local machine must treat repository discovery as dynamic and environment-driven.

## Investigations LangGraph Nodes
- **Dynamic context gathering**: The investigate node collects relevant context by executing multiple investigation actions in parallel (logs, traces, recent deployments, dependency health), adapting to whatever data sources are available rather than requiring specific systems.
- **Action selection based on available data**: Automatically determines which investigation actions to run based on what's present in the alert annotations and state (e.g., runs CloudWatch actions only if `log_group` is present, falls back to local file actions if `log_path` exists).
- **Structured information synthesis**: Aggregates results from multiple heterogeneous data sources (error logs, metrics, deployment history, runbooks) into a unified investigation context that the AI agent can reason about.
- **Graceful degradation**: Continues investigation even when some actions fail or data sources are unavailable, returning partial results rather than failing completely—maximizing usefulness of available information.
- **Lean startup principle**: Prioritizes easy-to-implement, high-impact context (error logs, recent deploys, basic dependency checks, team runbooks) over complex automated discovery, allowing rapid iteration and immediate value.