# Local Setup Guide

This guide covers two local paths:

- A live local Grafana RCA demo
- The full local development flow with your Tracer account

## Prerequisites

- Python 3.11+
- `make`

## 1. Fastest path: live local Grafana RCA demo

If you want to see a minimal RCA report against a real local Grafana stack, start here.

- Docker
- Python 3.11+
- `make`

1. Install dependencies:

   ```bash
   make install
   ```

2. Copy the example env file:

   ```bash
   cp .env.example .env
   ```

3. Add one LLM key to `.env`:

   ```bash
   ANTHROPIC_API_KEY=your-anthropic-api-key
   ```

   Or, if you prefer OpenAI:

   ```bash
   LLM_PROVIDER=openai
   OPENAI_API_KEY=your-openai-api-key
   ```

4. Start the local Grafana stack and seed test logs:

   ```bash
   opensre onboard
   # select "Grafana Local (Docker)"
   ```

This onboarding path starts the local `Grafana + Loki` stack and seeds failure logs into Loki for local testing. It does not require a Tracer account or real Slack, Datadog, or AWS credentials.

When you are done, stop the stack:

```bash
make grafana-local-down
```

## 2. Full local development setup

Use this path when you want to run the agent locally with your Tracer account and your own integrations.

### Install dependencies

```bash
make install
```

Recommended: install the local quality gate so every commit runs lint and typecheck first.

```bash
make install-hooks
```

### Configure env variables

1. Copy the example env file:

   ```bash
   cp .env.example .env
   ```

2. Add one LLM key to `.env`:

   ```bash
   ANTHROPIC_API_KEY=your-anthropic-api-key
   ```

   Or, if you prefer OpenAI:

   ```bash
   LLM_PROVIDER=openai
   OPENAI_API_KEY=your-openai-api-key
   ```

At this stage, only one LLM API key is mandatory. Everything else depends on which path you want to test:

- Required for any RCA run: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` or one of the providers below
- Required only for the `Tracer Web App path`: `JWT_TOKEN`
- Optional per system: `DD_*`, `GRAFANA_*`, `AWS_*`, `GITHUB_MCP_*`, `SENTRY_*`
- Optional only for Slack delivery: `SLACK_WEBHOOK_URL`
- Optional only for LangGraph deploy: `LANGSMITH_API_KEY`

#### Additional LLM Providers

Beyond Anthropic and OpenAI, you can use OpenRouter, Google Gemini, or NVIDIA NIM. All three use OpenAI-compatible APIs, so they work as drop-in replacements.

**OpenRouter** (supports 100+ models via a single API):
```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_REASONING_MODEL=anthropic/claude-opus-4
OPENROUTER_TOOLCALL_MODEL=anthropic/claude-haiku-4-5
```

**Google Gemini** (using OpenAI-compatible endpoint):
```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-google-api-key
GEMINI_REASONING_MODEL=gemini-2.5-pro
GEMINI_TOOLCALL_MODEL=gemini-2.5-flash
```

**NVIDIA NIM** (Inference Microservices):
```bash
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=your-nvidia-api-key
NVIDIA_REASONING_MODEL=meta/llama-4-maverick-17b-128e-instruct
NVIDIA_TOOLCALL_MODEL=meta/llama-4-scout-17b-16e-instruct
```

You can also pick these providers interactively via `opensre onboard`.

### Choose an integration source

You can run `Flow B` in two supported ways:

- `Tracer Web App path`: use integrations already configured in Tracer and authenticate locally with `JWT_TOKEN`
- `Local config path`: store integrations in `~/.tracer/integrations.json` or define them in `.env`

Both paths are supported. During investigations, Tracer uses this priority:

1. Inbound auth token from a web request or Slack-triggered run
2. `JWT_TOKEN` for Tracer web app integrations, with `~/.tracer/integrations.json` and `.env` filling missing services
3. `~/.tracer/integrations.json`
4. `.env` fallback integrations

### Tracer Web App path

Use this path if you already configured integrations in Tracer.

1. Go to `https://app.tracer.cloud`, sign in, and create or copy your Tracer API token from settings.
2. In `.env`, set:

   ```bash
   JWT_TOKEN=your-tracer-token-from-app.tracer.cloud
   ```

3. Verify Tracer connectivity:

   ```bash
   make verify-integrations SERVICE=tracer
   ```

4. If some services are not configured in Tracer yet, you can still add them locally via `~/.tracer` or `.env` and they will be used as fallback.

### Local config path

Use this path if you want to test or build integrations without depending on the Tracer web app.

For a first real-system run, you do not need every integration:

- `Datadog` or `Grafana` is enough to prove the RCA path against a real observability source
- Add `AWS` only if you want AWS evidence
- Add `GitHub MCP` only if you want repository/code investigation during failures
- Add `Sentry` only if you want issue and event evidence from Sentry
- Add `SLACK_WEBHOOK_URL` only if you want the final report posted to Slack

You can use `.env.example` as a reference for any other optional integrations you want to enable.

If you want help configuring the local LLM provider and optional local integrations, run the onboarding flow:

```bash
opensre onboard
```

The onboarding flow writes your provider choice and default model to `~/.opensre/opensre.json`, syncs the active local LLM settings into `.env`, and can also validate and save optional Grafana, Datadog, Slack, AWS, GitHub MCP, and Sentry integration settings for local development.

Because this repo is installed in editable mode via `make install`, `opensre onboard` targets your local checkout while you are coding. If you change `pyproject.toml` entrypoints later, rerun `make install` once to refresh the launcher.

If you enabled hooks with `make install-hooks`, each commit will also run `make lint` and `make typecheck` before Git creates the commit.

### GitHub MCP setup notes

If you want the agent to inspect repository source code, commit history, and repository structure during failures, configure `GitHub MCP` in `opensre onboard` or `python -m app.integrations setup github`.

- Hosted GitHub MCP uses `https://api.githubcopilot.com/mcp/`
- Supported transport modes in this repo are `streamable-http`, `sse`, and `stdio`
- For local verification after setup, run:

```bash
make verify-integrations SERVICE=github
```

### Sentry token recommendations

If you want Sentry-backed issue or event investigation, configure `Sentry` in `opensre onboard` or `python -m app.integrations setup sentry`.

Recommended token choice:

- Use a `Sentry Organization Token` first for least-privilege automation
- Create it in `Settings > Developer Settings > Organization Tokens`
- Use an `Internal Integration` only if you need broader organization-level API scopes than the organization token provides
- The local validation and investigation helpers need a token that can read issues and events

After setup, verify it with:

```bash
make verify-integrations SERVICE=sentry
```

### Google Docs setup notes

If you want the agent to create shareable incident postmortem reports in Google Docs, configure `Google Docs` in `opensre onboard` or `python -m app.integrations setup google_docs`.

**Prerequisites:**

1. Create a Google Cloud service account:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Navigate to `IAM & Admin > Service Accounts`
   - Create a new service account (e.g., `opensre-reports`)
   - Grant it the `Editor` role for Google Drive

2. Download the service account credentials:
   - Create and download a JSON key for the service account
   - Save it securely (e.g., `~/.config/opensre/google-credentials.json`)

3. Share your target Google Drive folder:
   - Create or choose a Drive folder for incident reports
   - Get the folder ID from the URL (the string after `folders/`)
   - Share the folder with the service account email (found in the credentials JSON)

**Configuration:**

Set these environment variables in `.env` or via `opensre onboard`:

```bash
GOOGLE_CREDENTIALS_FILE=/path/to/service-account-credentials.json
GOOGLE_DRIVE_FOLDER_ID=your-drive-folder-id
```

After setup, verify it with:

```bash
make verify-integrations SERVICE=google_docs
```

You can also configure a custom timeout for Google API calls (default is 30 seconds, min 5, max 300):

```bash
# Optional: override default timeout
GOOGLE_TIMEOUT_SECONDS=60
```

### Run the LangGraph dev UI

Start the LangGraph dev server:

```bash
make dev
```

Then open `http://localhost:2024` in your browser. From there you can send alerts to the agent and inspect the graph step by step while developing.

### Deploy to LangGraph

For a fast hosted path, you can deploy this repo directly with the LangGraph CLI.

Prerequisites:

- Docker running locally
- `langgraph` CLI installed
- `LANGSMITH_API_KEY` set in `.env` or your shell

Build the agent image locally:

```bash
make langgraph-build
```

Deploy it:

```bash
make langgraph-deploy
```

As of March 25, 2026, the official LangGraph CLI docs describe `langgraph deploy` as the command that builds and deploys in one step.

### Run your own alert payload locally

Once your `.env` or local integrations are configured, you can run the RCA pipeline against your own alert JSON without changing code.

Print a starter template first if you do not already have an alert payload:

```bash
make alert-template TEMPLATE=datadog
make alert-template TEMPLATE=grafana
make alert-template TEMPLATE=generic
```

From a file:

```bash
python -m app.main --input /path/to/alert.json
make investigate-alert ALERT=/path/to/alert.json
```

Paste JSON into the terminal:

```bash
python -m app.main --interactive
```

You can also override top-level metadata if your alert payload does not include it:

```bash
python -m app.main \
  --input /path/to/alert.json \
  --alert-name "Datadog monitor: High error rate" \
  --pipeline-name payments_etl \
  --severity critical
```

### Send RCA reports to Slack

For standalone local investigations, set an incoming webhook URL:

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

With `SLACK_WEBHOOK_URL` configured, CLI investigations that do not have Slack thread context will post the RCA report to Slack as a top-level message.

### Troubleshooting

- `make verify-integrations` shows everything as `missing`
  Add credentials to `.env`, or run `python -m app.integrations setup <service>`, or set `JWT_TOKEN` for the Tracer Web App path.

- `make verify-integrations SERVICE=tracer` fails
  Check that `JWT_TOKEN` is valid and from the correct Tracer org. If you use a local-only path, this check is optional.

- `python -m app.main --input ...` says `Alert JSON file not found`
  Check the path you passed to `--input`, or start with `make alert-template TEMPLATE=datadog > /tmp/alert.json`.

- `python -m app.main --input ...` fails during LLM planning
  Check that `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set and that your machine has outbound network access.

- You see `[actions] EKS actions unavailable: No module named 'kubernetes'`
  This does not block Datadog, Grafana, AWS, or Slack paths. Install all dependencies with `make install` if you need EKS actions.

- `opensre onboard` with `Grafana Local (Docker)` says Docker is not running
  Start Docker Desktop, OrbStack, or Colima, then rerun onboarding.

- `make langgraph-build` or `make langgraph-deploy` fails immediately
  Check that Docker is running, `langgraph` is installed, and `LANGSMITH_API_KEY` is set.
