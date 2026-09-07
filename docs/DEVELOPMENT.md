# Development guide

Contributor-focused workflows: local setup details stay in [SETUP.md](https://github.com/Tracer-Cloud/opensre/blob/main/SETUP.md) at the repo root (Windows, troubleshooting, MCP).

## Clone and install

```bash
git clone https://github.com/Tracer-Cloud/opensre.git
cd opensre
make install
```

[`make install`](https://github.com/Tracer-Cloud/opensre/blob/main/Makefile) runs `uv sync --frozen --extra dev` and the analytics install helper. Use **`uv run opensre …`** from the repo root so you always hit this checkout’s `.venv`, not another `opensre` on your `PATH`.

```bash
opensre onboard
uv run opensre   # open the interactive shell
```

## Quality gates (same as CI)

From the repo root:

```bash
make lint          # ruff check
make format-check  # ruff format --check (CI-enforced)
make typecheck     # mypy config core gateway integrations infrastructure surfaces tools
make test-cov      # pytest + coverage (default unit suite)
```

One-shot (includes heavier `test-full`): `make check`.

Before a PR, run at least `make lint`, `make format-check`, `make typecheck`, and `make test-cov` (see [CONTRIBUTING.md](https://github.com/Tracer-Cloud/opensre/blob/main/CONTRIBUTING.md)).

## Interactive shell action policy

Action-planner behavior, postprocessing transforms, compatibility seams, and the rule-extension checklist are documented in [`docs/interactive-shell-action-policy.md`](https://github.com/Tracer-Cloud/opensre/blob/main/docs/interactive-shell-action-policy.md).

## Package architecture

The eight first-party packages, the five-tier layering (which package may
import which), the folder diagram, per-layer responsibilities, and cross-layer
flows are documented in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).

## Tool registry — surface-scoped, lazy loading

Loading every vendor tool at startup was slow. A static index
(`tools/registry_index.py`) reads tool metadata by scanning the source, without importing executors, so a turn loads only the tools it needs.

- `get_registered_tools(surface)` imports only that surface's tool modules.
- `get_tool_descriptors(surface)` returns metadata with no executor import.
- `load_tool(descriptor)` imports the executor, only when a tool runs.

Adding a vendor tool is a `@tool`/`BaseTool` module; the index finds it and no other vendor is imported. `tests/tools/test_registry_index.py` checks the index matches the imported registry exactly, so they cannot drift.

## VS Code dev container

The dev container is defined under [`.devcontainer/`](https://github.com/Tracer-Cloud/opensre/tree/main/.devcontainer). It builds from [`.devcontainer/Dockerfile`](https://github.com/Tracer-Cloud/opensre/blob/main/.devcontainer/Dockerfile) (Python **3.13**), then **`postCreateCommand`** creates `.venv-devcontainer` and runs **`pip install -e '.[dev]'`** (not `uv`). Docker Desktop, OrbStack, Colima, or another compatible runtime must be available on the host.

## Deployment

Full deployment instructions, prerequisites, and environment variable reference:
**[DEPLOYMENT.md](../DEPLOYMENT.md)**

Quick reference:

| Path | Commands |
| ---- | -------- |
| Gateway (AMI + systemd — gateway only) | `make build-gateway-image` → `make deploy-gateway` / `make destroy-gateway` |
| Hosted (Railway / ECS / Vercel) | Deploy with repo `Dockerfile`; set `LLM_PROVIDER` + API key |

### Hosted runtime (Railway / ECS / Vercel)

1. Deploy this repository as a standard Python/FastAPI app using the repo `Dockerfile` or your host's native Python workflow.
2. Set `LLM_PROVIDER` and the matching API key (for example `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` — see [`.env.example`](https://github.com/Tracer-Cloud/opensre/blob/main/.env.example)).
3. Add `DATABASE_URI` and `REDIS_URI` for hosted layouts that need persistence.
4. Add integration and storage env vars your deployment needs.

Minimal LLM env:

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=...
```

For Railway: ensure the project has Postgres and Redis services and that the OpenSRE
service has `DATABASE_URI` and `REDIS_URI` set before deploying. Set
`OPENSRE_DEPLOYMENT_METHOD=railway` for telemetry labeling.

## macOS signing and notarization

Release builds sign the macOS onedir bundle with the project's Developer ID
and notarize it. The credentials live only in the release workflow's GitHub
Actions secrets. A build without them (a fork, a local dry run) is ad-hoc
signed instead: it still runs, but every Mac validates all bundled files on
first use (about 15 seconds at install and several seconds on first launch).

The hardened-runtime exceptions the frozen interpreter needs live in
`packaging/macos/opensre.entitlements`. The installer leaves a Developer ID
signature untouched; it only re-signs ad-hoc bundles.

To check a published build:

```bash
codesign -dv --verbose=2 ~/.local/bin/.opensre-app/opensre 2>&1 | grep Authority
```

## Telemetry and privacy

`opensre` ships with two telemetry stacks, both opt-out:

- **PostHog** — anonymous product analytics (commands used, success/failure, rough runtime, CLI/Python/OS/arch, and limited command metadata).
- **Sentry** — crashes and errors (stack traces, environment, release).

Events are tagged with `entrypoint`, `opensre.runtime`, and `deployment_method`. Sensitive headers, paths, and secret-shaped keys are scrubbed before send.

PostHog product events also carry `execution_environment` (`local`, `ci`, `container`,
or `ci_container`), `is_ci`, `is_container`, and `container_runtime`. Use these
first-party fields to exclude automated environments from product funnels; PostHog's
virtual traffic classification intentionally treats CLI HTTP clients as automation.

A random install ID is stored under `~/.opensre/anonymous_id`. PostHog `distinct_id` is scoped to that ID. Telemetry is off in GitHub Actions and pytest.

When a user signs in to GitHub (wizard or `/integrations setup`), OpenSRE sets `github_username` as a PostHog **person property** (via `$identify`/`$set`). That is the only intentional PII it sends.

### Kill-switch matrix

| Env var                        | PostHog    | Sentry     |
| ------------------------------ | ---------- | ---------- |
| `OPENSRE_NO_TELEMETRY=1`       | disabled   | disabled   |
| `DO_NOT_TRACK=1`               | disabled   | disabled   |
| `OPENSRE_ANALYTICS_DISABLED=1` | disabled   | unaffected |
| `OPENSRE_SENTRY_DISABLED=1`    | unaffected | disabled   |
| `OPENSRE_SENTRY_LOGGING_DISABLED=1` | unaffected | disables `logger.error`/`logger.exception` forwarding to Sentry; `capture_exception` unaffected |

Full opt-out:

```bash
export OPENSRE_NO_TELEMETRY=1
```

### Sentry DSN

Self-hosted users can set `SENTRY_DSN` to their project; unset uses the bundled default. `SENTRY_DSN=` (empty) drops events in `before_send`.

### Deployment tagging

Set `OPENSRE_DEPLOYMENT_METHOD` to `railway`, `ec2`, `vercel`, or `local` (default `local`) to label Sentry events.

### Local PostHog event log

By default, outbound PostHog payloads are also appended to `~/.opensre/posthog_events.txt` (rotates at 1000 lines). Disable:

```bash
export OPENSRE_ANALYTICS_LOG_EVENTS=0
```

We do not collect alert contents, file contents, hostnames, credentials, raw CLI arguments, or PII by design.
