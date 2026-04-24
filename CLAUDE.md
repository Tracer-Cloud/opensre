@AGENTS.md @CLAUDE_PERSONAL.md

Workflow Orchestration

1. Plan Mode DefaultEnter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
   If something goes sideways, STOP and re-plan immediately - don't keep pushing
   Use plan mode for verification steps, not just building
   Write detailed specs upfront to reduce ambiguity

2. Subagent Strategy to keep main context window cleanOffload research, exploration, and parallel analysis to subagents
   For complex problems, throw more compute at it via subagents
   One task per subagent for focused execution

3. Self-Improvement LoopAfter ANY correction from the user: update 'tasks/lessons.md' with the pattern
   Write rules for yourself that prevent the same mistake
   Ruthlessly iterate on these lessons until mistake rate drops
   Review lessons at session start for relevant project

4. Verification Before DoneNever mark a task complete without proving it works
   Diff behavior between main and your changes when relevant
   Ask yourself: "Would a staff engineer approve this?"
   Run `make test-cov`, `make lint`, and `make typecheck` before claiming changes are valid

5. Demand Elegance (Balanced)For non-trivial changes: pause and ask "is there a more elegant way?"
   If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
   Skip this for simple, obvious fixes - don't over-engineer
   Challenge your own work before presenting it

6. Autonomous Bug FixingWhen given a bug report: just fix it. Don't ask for hand-holding
   Point at logs, errors, failing tests -> then resolve them
   Zero context switching required from the user
   Go fix failing CI tests without being told how

Task Management

- Plan First: Write plan to 'tasks/todo.md' with checkable items
- Verify Plan: Check in before starting implementation
- Track Progress: Mark items complete as you go
- Explain Changes: High-level summary at each step
- Document Results: Add review to 'tasks/todo.md'
- Capture Lessons: Update 'tasks/lessons.md' after corrections

Core Principles

- Simplicity First: Make every change as simple as possible. Impact minimal code.
- No Laziness: Find root causes. No temporary fixes. Senior developer standards.
- Minimal Impact: Changes should only touch what's necessary. Avoid introducing bugs.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
