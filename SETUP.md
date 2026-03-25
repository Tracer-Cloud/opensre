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

4. Start the local Grafana stack:

   ```bash
   make grafana-local-up
   ```

5. Run the live local Grafana RCA example:

   ```bash
   make local-grafana-live
   ```

This path uses a real local `Grafana + Loki` stack and real local Grafana queries. It still uses a synthetic alert payload and does not require a Tracer account or real Slack, Datadog, or AWS credentials.

When you are done, stop the stack:

```bash
make grafana-local-down
```

If you want a generic no-Docker bundled RCA example instead, run:

```bash
make local-rca-demo
```

## 2. Full local development setup

Use this path when you want to run the agent locally with your Tracer account and your own integrations.

### Install dependencies

```bash
make install
```

### Configure env variables

1. Copy the example env file:

   ```bash
   cp .env.example .env
   ```

2. Go to `https://app.tracer.cloud`, sign in, and create or copy your Tracer API token from settings.
3. In your local `.env`, set the tracer JWT token and other env variables(for example):

   ```bash
   JWT_TOKEN=your-tracer-token-from-app.tracer.cloud
   ANTHROPIC_API_KEY=your-anthropic-api-key
   ```

You can use `.env.example` as a reference for any other optional integrations you want to enable.

If you want help configuring the local LLM provider, run the onboarding flow:

```bash
opensre onboard
```

The onboarding flow writes your provider choice and default model to `~/.opensre/opensre.json`, then syncs the active local settings into `.env`.

Because this repo is installed in editable mode via `make install`, `opensre onboard` targets your local checkout while you are coding. If you change `pyproject.toml` entrypoints later, rerun `make install` once to refresh the launcher.

### Run the LangGraph dev UI

Start the LangGraph dev server:

```bash
make dev
```

Then open `http://localhost:2024` in your browser. From there you can send alerts to the agent and inspect the graph step by step while developing.
