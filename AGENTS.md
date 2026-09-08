## OpenSRE Development Reference

## CI failures and tests (mandatory — every PR / push)

Agents **must** close the loop on CI and tests. Do not treat "pushed a fix" or "opened a PR" as done.

1. **After every push** to a branch with an open PR, run:
   ```bash
   gh pr checks --watch
   # or: gh pr view --json statusCheckRollup,url
   ```
2. **On any failure** (`CI Gate`, `quality`, `test (*)`, import graph, etc.):
   - Pull the failing job log (`gh run view <id> --log-failed`).
   - Fix the root cause in product or test code (not by skipping / constant-condition toggles).
   - Re-run the **focused** local commands from [CI.md](CI.md) for the touched modules, then push.
   - Re-check `gh pr checks` until required jobs are green (or skipped for docs-only).
3. **Tests that fail under CI load** (xdist, barriers, fan-out concurrency) are real bugs in the test harness — harden synchronization; do not ignore flakes.
4. **Import / API-border failures** (`check_imports.py`, `test_integrations_api_border`) mean the wrong module edge was used — import the package API allowlisted in `.importlinter.strict` / the border allowlist, not an internal leaf, unless the ignore list explicitly names that edge.
5. Keep monitoring until merge requirements in [CI.md §8](CI.md) (green checks + Greptile 5/5) are met.
6. After merge, monitor the merge commit's `main` CI, full CodeQL, and release
   workflows. A post-merge failure is unfinished delivery: fix or revert it
   before reporting completion.

The Cursor project hook [`.cursor/hooks/check-ci-failures.sh`](.cursor/hooks/check-ci-failures.sh) (wired in `.cursor/hooks.json`) re-injects this checklist on agent stop when the current branch's PR has failing checks — treat that follow-up as blocking work, not a suggestion.

## Build and Run commands

- Build `make install` (sets up the project environment via `uv sync` and installs this repo in editable mode)
- Run **`uv run opensre …`** from the repo root while developing — preferred approach, uses this checkout even if another `opensre` is on your `PATH`.
- Use **`uv run python …`** for any Python commands.

## Code Style

- Use strict typing, follow DRY principle
- One clear purpose per file (separation of concerns)
- Keep every Python `__init__.py` as a lightweight package facade: declare the
  public interface with imports and `__all__` only. Put implementation,
  orchestration, and side effects in focused modules, then re-export only the
  intended public API; never let `__init__.py` become a god file.
- Keep docstrings concise and contract-focused. Use one sentence for straightforward
  APIs; add only non-obvious invariants, failure behavior, or layering constraints
  callers must understand. Keep bug history and implementation narration in tests,
  commits, or the PR description instead. Do not remove meaningful rationale merely
  to shorten a docstring.
- Use named constants for HTTP status codes (`http.HTTPStatus`, e.g.
  `HTTPStatus.PAYMENT_REQUIRED`) in both source and tests — never hardcoded
  numeric literals like `402`.
- Env-var names and shared static constants live under `config/`, never inline
  in a feature module or duplicated across files. Put them in a domain module
  under `config/constants/` (e.g. `config/constants/billing.py`,
  `config/constants/llm.py`) — a leaf that any layer can import without a cycle —
  and re-export via `config/constants/__init__.py`. Do **not** define shared env
  names in `config/llm_settings.py`: it imports `config.llm_auth.*`, so a name it
  and one of those modules both need would force a cyclic import. Only a name
  used solely inside `config/llm_settings.py` (nothing it imports needs it) may
  live there.
- Do not keep compatibility-only forwarding modules after refactors. Once imports and tests
  are migrated, remove the old module path in the same change and use one canonical import path.
- Test fakes: never inline a lambda that builds an ad-hoc `type(...)` object (or
  nests another lambda) into `monkeypatch.setattr` / `patch`. Extract a named
  `def` and pass it — `type(...)` inside the helper body is fine:

  ```python
  def _build_harness() -> Any:
      return type("H", (), {"resolve_env_variables": lambda _self: None})()


  monkeypatch.setattr(startup, "_build_harness", _build_harness)
  ```

  Trivial lambdas (`lambda **_kw: None`, `lambda: sentinel`) stay inline.
  Precedent: `tests/infrastructure/safety/guardrails/test_llm_integration.py`
  (`_anthropic_fake_response`),
  `tests/cli/test_integrations_setup_github.py` (`_prompt_answering`).
- Protocol methods you **add or change** use a **docstring-only body** — no
  `...`, no `pass`, no `raise NotImplementedError`, and never a docstring *plus*
  a trailing `...`/`pass`. Precedent (all fully compliant):
  `infrastructure/filestorage/contracts.py`, `core/agent/loop_host.py`,
  `infrastructure/turn_host/turn_output.py`, `core/llm/types.py`.

  ```python
  class ObjectStore(Protocol):
      def put_object(self, key: str, data: bytes) -> None:
          """Store ``data`` under ``key``."""
  ```

  **The codebase is not yet compliant, and no tool will tell you.** An AST scan
  of product code counts 89 docstring-only Protocol methods against **108
  `raise NotImplementedError` stubs in 23 files** (`core/agent_harness/ports.py`,
  `infrastructure/harness_providers/`, `gateway/core/storage/session/binding_store.py` and
  others). Those are pre-existing and out of scope for a drive-by — do not
  mass-convert them, and do not cite a file as precedent without checking it.

  Enforcement is review-only:
  - CodeQL `py/ineffectual-statement` catches **only** the bare `...` form.
  - `pass` and `raise NotImplementedError` trip nothing.
  - mypy exempts Protocol bodies from "missing return", so `pass` under a
    `-> list[str]` signature passes `make typecheck`.

### Tests (high-signal, not exhaustive)

Prefer a **small suite that pins real failure modes** over broad line coverage.
One test per distinct bug class; do not multiply cases that exercise the same
branch with different literals.

**Write / keep tests for:**

- Security and authorization (allowlists, request-scoped authority, cross-actor
  or cross-channel isolation).
- Correctness under concurrency, crash, or shutdown (cursors, in-flight work,
  ack vs replay, drain vs approval wait).
- Package / transport borders that prevent silent coupling (no peer imports,
  session key shape unique to the surface).
- Regressions that already bit review or production (the P1 that forced a fix).

**Skip or thin (unless they are the *only* coverage of a contract):**

- Happy-path “mock was called once” wrappers (client send, init posts status).
- Pure string / vocabulary tables when approve/deny/leave-open paths already
  cover the helper.
- Redundant success variants of an ack or dispatch path already covered by
  fail / cancel / on_handled cases.
- Defensive parse / “empty on fetch failure” edges that do not move durable
  state.
- Stand-in tests that restate control-flow intent without exercising the real
  loop or wiring (e.g. “`create_task` does not block the creator”).


### Docs under `docs/`

`docs/` is user-facing. Test every sentence: *does this change what the reader
does?* If not, cut it.

- Cut vendor API endpoints (`getMe`), internal function names, which credential
  tier a value lands in, and the bug a change fixed — that belongs in the PR
  description or a module docstring.
- Keep required vs optional, shortcuts that save real work, gotchas that are
  invisible until they bite, and the exact commands and env vars they type.
- Say "a chat the bot was never added to fails during setup", not "`getChat`
  returns `ok: false`".

### Performance (algorithms & data structures)

Apply on the **hot path** (per-request / per-iteration / per-tool-call); leave cold
code simple. Complexity must be deliberate — the simplest structure that meets the
asymptotic need, and no more.

- **Membership / dedup in a loop → `set`/`dict`, never `x in list`.** List `in` is
  O(n); a set is O(1). (frozen dataclasses are hashable, so `set()` works on them.)
- **Resolve once, reuse.** Don't re-scan for something already looked up — if you
  fetched an object from a map, read its fields directly instead of a second linear
  scan. Build an O(1) `{name: obj}` map instead of scanning a list by name.
- **No `deepcopy` / `json.dumps` on the hot path.** If the result is invariant after
  construction, compute it once (`functools.cached_property` / a stored field) and
  treat it read-only. Verify no caller mutates a shared cached object first.
- **Sort the light thing, once.** Sort keys/names (strings), not heavy objects, and
  don't re-sort the same collection twice for two outputs.
- **Right structure for the job:** `collections.deque` for queues/both-ends,
  `heapq` for top-k, `bisect` for sorted search, `OrderedDict.move_to_end` /
  `functools.lru_cache` for bounded caches, `"".join(parts)` never `+=` in a loop.
- **Behavior-preserving refactors are TDD-guarded:** add a characterization test that
  pins the observable behavior, confirm it passes on the *pre*-refactor code, then
  keep it green through the change. Optimize only after; keep any benchmark in the PR.

### File placement (all packages)

When adding or changing behavior, put code in the **owning module first** — not the nearest
shared file that already imports something similar.

| Kind of file | Should contain | Should not contain |
| --- | --- | --- |
| **Orchestration** (`flow.py`, `controller.py`, `lifecycle.py`, `factory.py`) | Stage ordering, wiring, dispatch to specialists | Vendor/provider/domain logic, API clients, heavy UI |
| **Shared UI / prompts** (`_ui.py`, `prompts.py`, generic `validation.py`) | Reusable prompts, tables, rendering, thin dispatch | Logic for one provider, integration, or vendor |
| **Domain / provider / vendor module** (`providers/<name>.py`, `surfaces/cli/wizard/<name>.py`, `integrations/<vendor>/`) | All behavior specific to that provider, vendor, or feature area | Unrelated providers or cross-cutting orchestration |
| **Registry / catalog** (`config.py`, `*_catalog.py`, `provider_registry.py`) | Metadata, defaults, discovery tables | Live API calls, onboarding prompts, retry loops |

**Rules:**

1. **Two or more functions for the same provider, vendor, or feature area** → add or extend
   a dedicated module (or subpackage) for that area. Do not grow a shared orchestration or UI
   file with provider-specific branches.
2. **Before editing a shared file**, check for an existing sibling pattern in the same
   package (`local_llm/`, `providers/azure_openai.py`, `integrations/<vendor>/tools/`, etc.).
   Match that layout before inventing a new one.
3. **Keep dispatch thin.** Shared entrypoints (`validate_provider_credentials`, `get_llm`,
   slash-command handlers) should delegate in a few lines; implementation lives downstream.
4. **Respect package boundaries** in [ARCHITECTURE.md](docs/ARCHITECTURE.md). Surfaces compose
   lower tiers; `core/` and `integrations/` do not import from `surfaces/`.
5. **Package-local detail** lives in that package's `AGENTS.md` when present (e.g.
   [`surfaces/interactive_shell/AGENTS.md`](surfaces/interactive_shell/AGENTS.md),
   [`core/llm/AGENTS.md`](core/llm/AGENTS.md)). Read it before structural changes in that tree.
6. **Tool location** follows [docs/tool-placement-policy.md](docs/tool-placement-policy.md)
   (vendor-specific vs `tools/system/` vs cross-vendor).

If a change would add a new provider-specific `if provider.value == ...` block to a file
that already serves multiple providers, stop and extract a dedicated module instead.

### Persistence

Persistence ownership, storage package layout, migration boundaries, and concurrency
requirements follow [PERSISTENCE.md](PERSISTENCE.md).

Before any push or PR creation follow [**CI.md**](CI.md) — lint, format, typecheck, and test commands all live there.

When opening a PR, fill out the [**PR template**](.github/PULL_REQUEST_TEMPLATE.md) — it is not optional boilerplate; it has a required AI-usage disclosure section.

## 1. Repo Map

| Path                                          | What it does                                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bootstrap/`                                  | Composition root: shared process boot (`process.py` — env, Sentry, adapters, capability warnings, LLM preload as an ordered `BootStep` table) and the registration steps themselves (`adapters.py`). Every host picks a `ProcessProfile` instead of writing its own boot order. The one package allowed to import `tools` and `integrations` together. |
| `core/`                                       | Agent orchestration, context assembly, the shared runtime tool-calling loop, and domain logic (state, types, correlation rules). `core/tool/` owns tool contracts, schema handling, the registry port, execution, and error reporting; `core/tool_framework/` keeps authoring helpers such as `@tool`, skill guidance, and shared payload utilities (`utils/`). |
| `surfaces/cli/`                               | Command-line interface, onboarding wizard, local LLM helpers, and CLI tests support. Provider onboarding → `wizard/<provider>.py` (or `wizard/local_llm/`); new subcommands → `commands/<name>.py`. Runtime LLM wiring → [`core/llm/AGENTS.md`](core/llm/AGENTS.md).                                                                                                                                                                                                                                                   |
| `surfaces/interactive_shell/`                 | Interactive terminal (REPL) loop, slash commands, chat/help surfaces, action-planning harness, and terminal UI.                                                                                                                                                                                                                        |
| `integrations/`                               | Per-integration config normalization, verification, clients, helpers, store/catalog logic, and per-vendor tool packages under `integrations/<vendor>/tools/`.                                                                                                                                                                          |
| `tools/`                                      | Tool registry, per-tool packages for cross-cutting tools that aren't vendor-specific (e.g. `tools/system/fleet_monitoring/`, `tools/system/sre_guidance_tool/`), and the interactive-shell action tools. Contracts, schema, and execution live in `core/tool/`; decorator, skill-guidance, and utility helpers live in `core/tool_framework/`.                |
| `config/`                                     | Shared constants, prompts, and UI theme.                                                                                                                                                                                                                                                                                               |
| `tests/`                                      | Unit, integration, deployment, e2e, and support tests.                                                                                                                                                                                                                                                   |
| `docs/`                                       | User-facing documentation, integration guides, and docs-site assets.                                                                                                                                                                                                                                                                   |
| `.github/`                                    | CI workflows, issue templates, pull request template, and repository automation.                                                                                                                                                                                                                                                       |
| `Dockerfile`                                  | Optional production container image (FastAPI health app via uvicorn).                                                                                                                                                                                                                                                                  |
| `pyproject.toml`                              | Python project metadata, dependency configuration, tooling, and package settings.                                                                                                                                                                                                                                                      |
| `Makefile`                                    | Canonical local automation for install, test, verify, deploy, and cleanup targets.                                                                                                                                                                                                                                                     |
| `README.md`                                   | Product overview, install, quick start, high-level capabilities, and links to deeper docs.                                                                                                                                                                                                                                             |
| `docs/DEVELOPMENT.md`                         | Contributor workflows: CI parity commands, dev container, benchmark, deployment, telemetry detail.                                                                                                                                                                                                                                     |
| `docs/ARCHITECTURE.md`                        | Package architecture: the five-tier layer table, folder diagram, per-layer responsibilities, allowed cross-layer edges, and cross-layer flows.                                                                                                                                                                                         |
| `docs/tool-placement-policy.md`               | Decision rule for where a tool lives: `integrations/<vendor>/tools/` vs. `tools/system/` vs. `tools/cross_vendor/` vs. `surfaces/shared/`.                                                                                                                                                                                             |
| `docs/NAMING.md`                              | Naming conventions for `core/`: the glossary (State/Snapshot/RunInput/RunResult/Slice/Resources/Budget), the `{domain}_{role}.py` file rule, type naming (`Mixin` suffix, role-named Protocols, no package-name prefix), and anti-patterns.                                                                                            |
| `SETUP.md`                                    | Machine setup (all platforms, Windows, MCP, troubleshooting).                                                                                                                                                                                                                                                                          |
| `CI.md`                                       | Mandatory pre-push checklist: lint, format, typecheck, tests — agents MUST follow before pushing.                                                                                                                                                                                                                                      |
| `CONTRIBUTING.md`                             | Contribution workflow, branch/PR guidance, and quality expectations.                                                                                                                                                                                                                                                                   |

Main packages one level deeper:

- `infrastructure/analytics/` — Analytics event plumbing and install helpers used by the onboarding flow.
- `infrastructure/database/` — Shared database connection and transaction mechanics;
  domain packages retain schema, migration, and query ownership.
- `infrastructure/safety/auth/` — JWT and authentication helpers for local and hosted runtime access.
- `config/constants/` — Shared prompt and other static constants.
- `infrastructure/deployment/ec2/` — EC2 AWS SDK primitives (`client`, `config`, EC2/IAM, SSM) and Telegram gateway AMI/systemd lifecycle (`telegram_gateway/`). Makefile: `make build-gateway-image`, `make deploy-gateway`.
- `infrastructure/safety/guardrails/` — Guardrail rules, evaluation engine, audit helpers, and CLI bindings.
- `infrastructure/harness_providers/` — Harness provider layer (integration resolution, tool registry, GitHub repo scope). Real implementations are wired at startup via `integrations/harness_adapters.py` and `tools/harness_adapters.py` through `install_harness_providers()` in `surfaces/shared/terminal/output/boundary.py`. See `core/agent_harness/AGENTS.md` for the import boundary.
- `integrations/llm_cli/` — Subprocess-backed LLM CLIs (e.g. Codex). Extension guide: `integrations/llm_cli/AGENTS.md`.
- `infrastructure/safety/masking/` — Masking utilities for redacting or normalizing sensitive content.
- `core/llm/` — Hosted LLM provider clients, retry/schema helpers, and tool-calling adapters.
- `infrastructure/safety/sandbox/` — Sandboxed execution helpers for controlled runtime actions.
- `core/state/` — Shared agent state: the mutable per-session conversation store and transcript-window compaction helpers.
- `core/domain/types/` — Shared typed contracts for evidence, retrieval, and tool-related payloads.
- `gateway/web/webapp.py` — Web-facing health app served by the gateway daemon; the `opensre` CLI is `surfaces/cli/app.py`.

## 2. Entry Points

### Adding a Tool

The tool registry auto-discovers modules under `tools/`, so the normal path is to add one module or package there and let discovery pick it up. See [docs/adding-tools-and-integrations.md](docs/adding-tools-and-integrations.md) for the full file list and the detailed definition of done (package structure, contract/implementation rules, live-payload parsing, required docs/tests).

Steps:

1. Pick the simplest shape that fits the tool. Use a `BaseTool` subclass (from `core.tool`) for richer behavior; use `@tool(...)` from `core.tool_framework` for a lightweight function tool. Import through those tier public APIs, not their internal submodules — the border test in `tests/shared/test_tool_api_border.py` enforces it.
2. Declare clear metadata: `name`, `description`, `source`, `input_schema`, and any `use_cases`, `requires`, `outputs`, or `retrieval_controls` you need.
3. Before opening or approving the PR, follow [docs/adding-tools-and-integrations.md](docs/adding-tools-and-integrations.md).

### Adding an Integration

Integration work usually spans config normalization, verification, integration-local clients/helpers, tools, docs, and tests. See [docs/adding-tools-and-integrations.md](docs/adding-tools-and-integrations.md) for the full file list, examples from the repo (Datadog, Grafana), and the detailed definition of done (core completeness, tool wiring, docs/tests, `make verify-integrations`, final demo gate).

Steps:

1. Add the integration config and normalization logic first so the rest of the stack can consume a consistent shape.
2. Wire the tool layer after the config path is stable.
3. Before opening or approving the PR, follow [docs/adding-tools-and-integrations.md](docs/adding-tools-and-integrations.md).

## 3. Footguns (common mistakes to avoid)

- Constant-condition toggles: never disable product logic with
  `if False and …`, `if True or …`, or `if False:` to silence a failing test.
  That hides real behavior (e.g. cancel short-circuit after action) and ships
  as dead code. Keep the real condition and fix the test, or delete the branch.
  Enforced by `tests/quality/test_no_constant_condition_toggles.py`.
- No planning-stage fail-closed safeguard (v0.1): the interactive-shell action planner never denies a turn — do **not** reintroduce a planner denial, `mark_unhandled`, or the `UNHANDLED:` convention. Full rationale: [docs/interactive-shell-action-policy.md](docs/interactive-shell-action-policy.md); package rule: `surfaces/interactive_shell/AGENTS.md` ("Action Selection And Execution").
- Docs navigation: Adding an `.mdx` file under `docs/` is not enough — Mintlify only shows pages listed in `docs/docs.json`. Forgetting the `pages` entry leaves the doc unreachable from the site sidebar.
- Tool schemas: draft-07 JSON Schema (e.g. `"type": ["object", "null"]`) can pass loose checks but fail the LLM API on first invoke because **all** available tools are sent together. Normalize in the provider adapter and extend registry contract tests.
- Action-agent path: do not implement regex/keyword/fuzzy intent routing or deterministic action bypasses around the action agent — including in the harness orchestrator / `SessionGoal` loop / evidence-tier policy. Intent belongs in the action turn (structured handoff tags such as `evidence_kind:…`, `session_goal:…`, `database_query:…`); hosts react to those tags or explicit APIs only. See `surfaces/interactive_shell/AGENTS.md` ("Action Selection And Execution") for the sanctioned literal-`/slash` exception, and `core/agent_harness/AGENTS.md`.
- Information exposure through an exception (CWE-209 / CodeQL `py/stack-trace-exposure`): never send an exception's detail — `str(exc)`, `repr(exc)`, `traceback.format_exc()`, `exc.args`, provider/model/field internals — to an **external surface**. External surfaces are HTTP responses (`JSONResponse`/`HTTPException.detail` in `gateway/web/`) and chat gateway messages delivered to Slack/Telegram users (`OutputSink.render_error` on the gateway sinks). Log full detail server-side (`logger` + `capture_exception`) and return a generic message or `type(exc).__name__` only. The local CLI/terminal sink is **not** external — it may show detail. Redact at the sink/response boundary, not per call site, so the shared turn engine keeps detail for local dev.
- Cyclic imports (CodeQL `py/cyclic-import`): CodeQL counts **function-local** and `TYPE_CHECKING` imports as part of a cycle, so making an import lazy does **not** clear the alert. Break the cycle structurally — move the shared symbol (type, exception, helper) into a **leaf** module both sides import, and never add a back-edge from a lower-level module up to a higher-level one. Precedent: `surfaces/shared/llm_setup/validation_result.py` and `surfaces/shared/llm_setup/persist.py` exist only to hold shared symbols so `validation` ↔ `azure_openai` and `_ui` → `service` stay acyclic.
- CodeQL does not model `NoReturn`: it treats `pytest.skip`, `pytest.fail`, `sys.exit`, `typer.Exit` and custom raise-helpers as if they return, so any code after them looks reachable. Two alerts come from this — `py/uninitialized-local-variable` when a name is bound in `try` and the `except` only calls such a function, and unreachable-code when a `with` body ends in a bare `raise`. Do **not** silence with a comment: bind the name on every path CodeQL can see. Prefer a sentinel over exception control flow for ordinary "not found" — `next(iterable, None)` plus an explicit `if x is None:` guard, not `try: next(...) except StopIteration:`. `mypy` narrows correctly after the guard because it *does* honour `NoReturn`. For the bare-`raise` case, extract a `_raise()` helper.
- Protocol stub bodies (CodeQL `py/ineffectual-statement`): a bare `...` on a
  `Protocol` method is a valid PEP-544 idiom but trips CodeQL as a statement
  with no effect. Do **not** write `def foo(self) -> T: ...`. Use a one-line
  docstring as the only body (see Code Style above), and do not keep both a
  docstring and a trailing `...`/`pass`. Prefer documenting the contract on the
  port over silencing the alert with a comment. Note the scope: CodeQL catches
  only `...`, so a clean scan does **not** mean the codebase follows the rule —
  `pass` and `raise NotImplementedError` are invisible to it, and 108 of the
  latter remain.
- `py/ineffectual-statement` does **not** understand `await`: a bare
  `await some_task` reads to CodeQL as a discarded expression. It is not — the
  await is the side effect (e.g. reaping a cancelled task so `client.close()`
  runs). Do **not** delete the await. Prefer a small helper that binds the
  result (`_finished = await task` in `gateway/transports/discord/worker.py`
  `_reap_cancelled_task`) over a bare expression statement; do not "fix" by
  skipping the await.
- Implicit string concatenation in a list (CodeQL
  `py/implicit-string-concatenation-in-list`): two adjacent string literals
  inside a list/tuple display are indistinguishable from a **missing comma**,
  so a long message split over two lines trips it. Do not silence it with an
  explicit `+` — extract the text to a module constant and reference that. The
  same implicit concatenation is fine in a parenthesised assignment, where no
  comma could have been intended. Precedent:
  `tools/system/python_execution_tool/__init__.py`
  (`_RUNTIME_FACTS_ANTI_EXAMPLE`). Bites hardest in `use_cases` /
  `anti_examples` / `examples` tool metadata, where entries are prose and
  routinely exceed the 100-char line limit.
- Mixed import styles (CodeQL `py/import-and-import-from`): importing one
  module with both `import X as alias` and `from X import name` — even when
  the `from` import is function-local — raises an alert. It usually happens
  when appending to an existing file: **use the import style the file already
  established** (an existing `import core.context_budget as budget` means new
  code calls `budget.name`, not `from core.context_budget import name`).
- Except block handles `BaseException` (code-quality): catch `Exception`, not
  `BaseException`. Collecting request/transport failures in a test thread still
  works — `requests.exceptions.ConnectTimeout` subclasses `Exception`. Catching
  `BaseException` also swallows `KeyboardInterrupt` / `SystemExit`. Do not keep
  `noqa: BLE001` to silence it.
- Unused global variable (CodeQL / code-quality "Unused global variable"):
  CodeQL often **does not credit cross-module imports** as a use of a module-
  level constant. A `FOO = "..."` in `text.py` that is only read via
  `from …text import FOO` in another file can still alert. Prefer keeping
  related copy in a structure that is clearly used in the defining module
  (e.g. a dict entry under `HANDOFF_GUIDANCE["database_query:"]` with prefix
  matching in the consumer), or co-locate the constant with its only reader.
  Do **not** add a no-op self-reference or `# noqa` just to silence the alert.
- Shared client state under concurrent turns: LLM clients are cached per role
  (`get_llm`) and the gateway runs turns in parallel, so **one client instance
  serves several in-flight requests**. An instance flag mutated inside an error
  handler is then read by requests that were already built under the old value.
  Branch on **what the current request carried**, not on the flag's present
  value — capture the fact locally where the request is built
  (`marked = strip_cache_markers(kwargs) != kwargs`) and consult that in the
  `except`. Precedent: the prompt-cache fallback, where a first 400 cleared
  `_cache_markers_enabled` and a second already-marked request skipped its
  uncached retry and failed the turn. Test it by having the fake dependency
  mutate the shared state *before* raising, which reproduces the race
  deterministically without threads.
- CI typecheck does **not** cover `tests/`: `make typecheck` runs mypy over `PYTHON_SOURCE_PATHS` (`config core gateway integrations infrastructure surfaces tools`) only. Type errors in test files never fail CI, so do not assume a clean `make typecheck` means the tests you just wrote are type-clean — run mypy on the test path directly when it matters.
