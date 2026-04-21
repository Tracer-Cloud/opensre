## Tracer Development Reference

## Build and Run commands

- Build `make install`
- Run `opensre`

## Lint & Format

- Lint all: `make lint`
- Fix linting: `ruff check app/ tests/ --fix`
- Type check: `make typecheck`

## Testing

- Test: `make test-cov`
- Test real alerts: `make test-rca`

## Code Style

- Use strict typing, follow DRY principle
- One clear purpose per file (separation of concerns)

### Before Push

1. Clean working tree
2. `make test-cov`
3. `make lint`
4. `make typecheck`

## 1. Repo Map

| Path | What it does |
| --- | --- |
| `app/` | Core agent logic, CLI, tools, integrations, services, graph pipeline, and runtime state. |
| `tests/` | Unit, integration, synthetic, deployment, e2e, chaos engineering, and support tests. |
| `docs/` | User-facing documentation, integration guides, and docs-site assets. |
| `.github/` | CI workflows, issue templates, pull request template, and repository automation. |
| `langgraph.json` | LangGraph deployment configuration for the hosted agent runtime. |
| `pyproject.toml` | Python project metadata, dependency configuration, tooling, and package settings. |
| `Makefile` | Canonical local automation for install, test, verify, deploy, and cleanup targets. |
| `README.md` | Product overview, quick start, capabilities, integrations, and common workflows. |
| `CONTRIBUTING.md` | Contribution workflow, branch/PR guidance, and quality expectations. |

`app/` one level deeper:

- `app/analytics/` — Analytics event plumbing and install helpers used by the onboarding flow.
- `app/auth/` — JWT and authentication helpers for local and hosted runtime access.
- `app/cli/` — Command-line interface, onboarding wizard, local LLM helpers, and CLI tests support.
- `app/constants/` — Shared prompt and other static constants.
- `app/deployment/` — Deployment configuration and health helpers for hosted runtimes.
- `app/entrypoints/` — SDK and MCP entrypoints exposed to external runtimes.
- `app/guardrails/` — Guardrail rules, evaluation engine, audit helpers, and CLI bindings.
- `app/integrations/` — Integration config normalization, verification, selectors, store, and catalog logic.
- `app/masking/` — Masking utilities for redacting or normalizing sensitive content.
- `app/nodes/` — LangGraph nodes for alert extraction, investigation, diagnosis, and publishing.
- `app/pipeline/` — Graph assembly, routing, and runner helpers; `app/graph_pipeline.py` is the compatibility shim.
- `app/remote/` — Remote-hosted runtime operations and integration points.
- `app/sandbox/` — Sandboxed execution helpers for controlled runtime actions.
- `app/services/` — Reusable API clients and service adapters consumed by integrations and tools.
- `app/state/` — Shared agent and investigation state models plus state factories.
- `app/tools/` — Tool registry, decorator, base classes, per-tool packages, shared utilities, and registry helpers.
- `app/types/` — Shared typed contracts for evidence, retrieval, and tool-related payloads.
- `app/utils/` — Cross-cutting utility helpers used across the app and test harnesses.
- `app/main.py` and `app/webapp.py` — Application entrypoints for the CLI/runtime and web-facing surface.

`tests/` is organized by capability boundary rather than by framework:

- `tests/tools/` — Tool behavior, registry, and helper coverage.
- `tests/integrations/` — Integration config, verification, store, selector, and client tests.
- `tests/e2e/` — Live end-to-end scenarios against real services and infrastructure.
- `tests/synthetic/` — Fixture-driven synthetic RCA scenarios with no live infrastructure.
- `tests/deployment/` — Deployment validation and infrastructure lifecycle tests.
- `tests/chaos_engineering/` — Chaos lab and experiment orchestration tests and assets.
- `tests/cli/` — CLI-specific behavior, smoke tests, and command wiring.
- `tests/utils/` — Shared test utilities, fixtures, and local helpers.
- `tests/nodes/`, `tests/services/`, `tests/remote/`, `tests/sandbox/`, `tests/guardrails/`, `tests/entrypoints/` — Feature-specific coverage for the corresponding app layers.

## 2. Entry Points

### Adding a Tool

The tool registry auto-discovers modules under `app/tools/`, so the normal path is to add one module or package there and let discovery pick it up.

Files to touch:

- `app/tools/<ToolName>/__init__.py` for the tool implementation, or `app/tools/<tool_file>.py` for a lighter-weight function tool.
- `app/tools/utils/` if the tool needs shared helper code.
- `app/services/<vendor>/client.py` if the tool should reuse a dedicated API client instead of inlining requests.
- `tests/tools/test_<tool_name>.py` for behavior and regression coverage.

Steps:

1. Pick the simplest shape that fits the tool. Use a `BaseTool` subclass for richer behavior; use `@tool(...)` from `app.tools.tool_decorator` for a lightweight function tool.
2. Declare clear metadata: `name`, `description`, `source`, `input_schema`, and any `use_cases`, `requires`, `outputs`, or `retrieval_controls` you need.
3. Keep the tool self-contained. Put reusable transport or parsing code in `app/services/` or `app/tools/utils/` rather than copying it into the tool body.
4. If the tool should appear in both investigation and chat surfaces, set `surfaces=("investigation", "chat")`.
5. Add tests that cover schema shape, availability, extraction, and the runtime behavior that the planner depends on.

### Adding a Node

The active graph is built in `app/pipeline/graph.py`, and routing decisions live in `app/pipeline/routing.py`.

Files to touch:

- `app/nodes/<node_group>/node.py` for the node implementation.
- `app/nodes/<node_group>/` helpers such as `processing/`, `execution/`, `models.py`, or `types.py` when the node needs local support code.
- `app/nodes/__init__.py` if the node should be exported alongside the other graph nodes.
- `app/pipeline/graph.py` to register the node and wire edges.
- `app/pipeline/routing.py` if the node changes branching, loop control, or terminal conditions.
- `app/state/*.py` if the node adds or changes state fields.
- `tests/` coverage for the specific node or the affected graph path.

Steps:

1. Implement the node in the appropriate package under `app/nodes/` and keep the node focused on one responsibility.
2. Export it from `app/nodes/__init__.py` if the graph should import it from the package root.
3. Register it in `app/pipeline/graph.py` with `graph.add_node(...)` and connect it with the right edge type.
4. Update `app/pipeline/routing.py` if the node introduces a new branch or loop outcome.
5. Extend tests for the graph path and any state transitions the new node relies on.

### Adding an Integration

Integration work usually spans config normalization, verification, service clients, tools, docs, and tests.

Files to touch:

- `app/integrations/<name>.py` for config builders, validators, selectors, and normalization helpers.
- `app/integrations/catalog.py` when the new integration must be resolved into the shared runtime config.
- `app/integrations/verify.py` when the integration needs a local verification path.
- `app/services/<name>/client.py` when the integration needs a dedicated API client.
- `app/tools/<Name>Tool/` or `app/tools/<tool_file>.py` for the user-facing tool layer.
- `docs/<name>.mdx` for user-facing setup, usage, and verification docs.
- `tests/integrations/test_<name>.py` for config, verification, and store coverage.
- `tests/tools/test_<tool_name>.py` and any relevant `tests/e2e/` or `tests/synthetic/` files if the integration is exercised by tools or scenarios.

Examples from the repo:

- Datadog: `app/services/datadog/client.py`, `app/integrations/catalog.py`, `app/integrations/verify.py`, `app/tools/DataDog*`, and `tests/integrations/test_verify.py`.
- Grafana: `app/integrations/catalog.py`, `app/integrations/verify.py`, `app/tools/Grafana*`, `app/cli/wizard/local_grafana_stack/`, and the Grafana-related tests under `tests/integrations/`.

Basic steps:

1. Add the integration config and normalization logic first so the rest of the stack can consume a consistent shape.
2. Add or update the service client only when the integration needs direct remote calls.
3. Wire the tool layer after the config path is stable.
4. Add docs and tests together so the integration is understandable and verifiable.
5. Run `make verify-integrations` before treating the integration as complete.

## 3. Rules (if X -> do Y)

- If core agent or graph logic changes -> run `make test-cov` and `make typecheck`.
- If a tool's API or schema changes -> update docs in `docs/` and update the related unit tests, usually under `tests/tools/`.
- If an integration changes -> update `tests/integrations/` and verify with `make verify-integrations`.
- If adding a new integration -> follow the New Integration Checklist below before opening the PR for review.
- If CI-only tests are added -> mark them with the right pytest marker or place them in the appropriate e2e/synthetic/chaos folder so they do not run in the default local suite.
- If node branching or loop behavior changes -> update `app/pipeline/routing.py` and the graph tests for that path.

## 4. Testing

### Commands

- Unit tests: `make test-cov`
- Integration tests: `make verify-integrations`
- E2E tests: `make test-rca` or `make test-full`
- Synthetic (no live infra): `make test-synthetic`
- Single RCA test: `make test-rca FILE=<name>`
- Lint: `make lint`
- Type check: `make typecheck`

### Fast Local Testing

The fastest local loop is `make test-cov`, which exercises the non-live unit suite and skips the heavier live-infra paths. When you need a specific RCA scenario, use `make test-rca FILE=<fixture>` with one of the bundled alert fixtures under `tests/e2e/rca/`.

## 5. Footguns (common mistakes to avoid)

- Vendored deps: No obvious vendored third-party dependencies are present. Python dependencies are managed in `pyproject.toml`, and the docs site has its own `docs/package.json` and `docs/pnpm-lock.yaml`. Do not vendor new libraries unless there is a strong reason.
- Secrets: Never commit `.env` - always use `.env.example` as the template. Use read-only credentials for production integrations.
- CI-only tests: Some e2e tests, including Kubernetes, EKS, and chaos engineering paths, require live infrastructure and are excluded from `make test-cov`. Do not expect them to pass locally without that environment.
- LangGraph dev server: `make simulate-k8s-alert` starts a background LangGraph server; kill it manually if it hangs.
- Docker requirement: Several targets, including the Grafana local stack, LangGraph build/deploy, and Chaos Mesh workflows, require a running Docker daemon.

## 6. New Integration Checklist

When adding a new integration, a PR is only ready when:

- [ ] Integration code added under `app/integrations/<name>/`
- [ ] Tool(s) added under `app/tools/` with proper typing
- [ ] Unit/mock tests added under `tests/integrations/`
- [ ] Docs added under `docs/`
- [ ] Screenshot or demo GIF showing the integration working
- [ ] E2E or synthetic test added
- [ ] `make verify-integrations` passes
- [ ] `make lint` and `make typecheck` pass
