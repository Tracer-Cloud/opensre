<div align="center">

<p align="center">
  <img src="docs/logo/opensre-logo-white.svg" alt="OpenSRE" width="360" />
</p>

<h1>OpenSRE v0.1: Build Your Own AI SRE Agents</h1>

<p>The open-source framework for AI SRE agents, and the training and evaluation environment they need to improve. Connect the 60+ tools you already run, define your own workflows, and answer production questions on your own infrastructure.</p>

<p align="center">
  <a href="https://github.com/Tracer-Cloud/opensre/actions/workflows/ci.yml?branch=main"><img src="https://img.shields.io/github/actions/workflow/status/Tracer-Cloud/opensre/ci.yml?branch=main&style=for-the-badge" alt="CI status"></a>
<a href="https://github.com/Tracer-Cloud/opensre/releases"><img src="https://img.shields.io/badge/status-public%20alpha-orange?style=for-the-badge" alt="Project status: public alpha"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=for-the-badge" alt="Apache 2.0 License"></a>
  <a href="https://discord.gg/7NTpevXf7w"><img src="https://img.shields.io/badge/Discord-Join%20Us-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
    <a href="https://greptile.com"><img src="https://img.shields.io/badge/Sponsored%20by-Greptile-27E99F?style=for-the-badge&labelColor=3D3B4F&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB3aWR0aD0iMzY3IiBoZWlnaHQ9IjQyMCIgdmlld0JveD0iMCAwIDM2NyA0MjAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI%2BCjxwYXRoIGQ9Ik0yNDAuMjY5IDQ5LjgxNTRMMTY2LjgwNCAxMTUuOTYzTDExNS45NjYgMTU5LjQ0TDE4MS4zMzUgMjIwLjU4NUwyNDkuNzg0IDE2Mi4wNDhMMTk2Ljc4IDExMi40N0wyNTMuMDY4IDYxLjc4ODFMMzYyLjYwNSAxNjQuMjQ2TDE3OC43MzkgMzIxLjQ4OUwzLjE0NTAyIDE1Ny4yNDJMMTg3LjAxMSAwTDI0MC4yNjkgNDkuODE1NFoiIGZpbGw9IiNGRkZGRkYiLz4KPHJlY3Qgd2lkdGg9IjIzNi40NTMiIGhlaWdodD0iODMuNDU2NiIgdHJhbnNmb3JtPSJtYXRyaXgoMC43NTQ3MSAtMC42NTYwNTkgMCAxIDE4OC4wMTcgMzM2LjU0NCkiIGZpbGw9IiNGRkZGRkYiLz4KPHJlY3Qgd2lkdGg9IjIzNi40NTMiIGhlaWdodD0iODMuNDU2NiIgdHJhbnNmb3JtPSJtYXRyaXgoMC43MzEzNTQgMC42ODE5OTggMCAxIDAgMTc0Ljk2MikiIGZpbGw9IiNGRkZGRkYiLz4KPC9zdmc%2BCg%3D%3D" alt="Sponsored by Greptile"></a>
</p>

<p align="center">
  <a href="https://trendshift.io/repositories/25889" target="_blank">
    <img
      src="https://trendshift.io/api/badge/repositories/25889"
      alt="Tracer-Cloud%2Fopensre | Trendshift"
      style="height: 30px; width: auto;"
      height="30"
    />
  </a>
</p>

<p align="center">
  <strong>
    <a href="https://www.opensre.com/docs/quickstart">Quickstart</a> ·
    <a href="https://www.opensre.com/docs">Docs</a> ·
    <a href="https://opensre.com/docs/faq">FAQ</a> ·
    <a href="https://trust.tracer.cloud/">Security</a>
  </strong>
</p>

</div>

---

> 🚧 Public Alpha: Core workflows are usable for early exploration, though not yet fully stable. The project is in active development, and APIs and integrations may evolve

---

## Table of Contents

- [Why OpenSRE?](#why-opensre)
- [Install](#install)
- [Quick Start](#quick-start)
- [Deployment](#deployment)
- [How OpenSRE Works](#how-opensre-works)
- [Capabilities & integrations](#capabilities--integrations)
- [Contributing & development](#contributing--development)
- [Security](#security)
- [Telemetry](#telemetry)
- [License](#license)
- [Citations](#citations)

---

## Why OpenSRE?

When something breaks in production, the evidence is scattered across logs, metrics, traces, runbooks, and Slack threads. OpenSRE is an open-source framework for AI SRE agents that resolve production incidents, built to run on your own infrastructure.

We do that because SWE-bench<sup>1</sup> gave coding agents scalable training data and clear feedback. Production incident response still lacks an equivalent.

Distributed failures are slower, noisier, and harder to simulate and evaluate than local code tasks, which is why AI SRE, and AI for production debugging more broadly, remains unsolved.

OpenSRE is building _that_ missing layer:

> an open reinforcement learning environment for agentic infrastructure incident response, with end-to-end tests for realistic production failures

We do that by:

- building easy-to-deploy, customizable AI SRE agents for production operations and incident response
- running real-world end-to-end tests across cloud-backed scenarios [(tests/e2e)](tests/e2e)
- keeping semantic test-catalog naming so e2e vs unit and local vs cloud boundaries stay obvious [(tests/README.md)](tests/README.md)

Our mission is to build AI SRE agents on top of this, scale it to thousands of realistic infrastructure failure scenarios, and establish OpenSRE as the benchmark and training ground for AI SRE.

<sup>1</sup> https://arxiv.org/abs/2310.06770

---

## Install

The root installer URL auto-detects Unix shell vs PowerShell and installs the latest build from `main`. OpenSRE moves quickly, so `main` is the latest stable version for normal installs.

macOS / Linux:

```bash
curl -fsSL https://install.opensre.com | bash
```

The macOS/Linux installer does not require sudo. If no writable bin directory is already on `PATH`, it installs to `~/.local/bin` and prints the shell command to apply the PATH update.

Equivalent explicit main-channel form:

```bash
curl -fsSL https://install.opensre.com | bash -s -- --main
```

Homebrew:

```bash
brew tap tracer-cloud/tap
brew install tracer-cloud/tap/opensre
```

Windows (PowerShell):

```powershell
irm https://install.opensre.com | iex
```

<!--
```bash
pipx install opensre
``` -->

---

## Quick Start

Contributors: start at [`main.py`](main.py) for the process entrypoint map.

Create or sign in to your OpenSRE account once. Setup activates the hosted model:

```bash
opensre setup
```

**Interactive shell** — with no subcommand, `opensre` validates your account and starts a REPL (TTY required). You can exit and stay signed out, but the shell only opens for an active account. Describe incidents in plain language, watch the agent work, and use slash commands for session control (`/help`, `/status`, `/cost`, `/sessions`, `/resume`, `/compact`, `/new`, `/exit`), integrations (`/integrations list`, `/integrations verify`), and local agent fleet monitoring (`/agents`). Ctrl+C cancels an in-flight turn without losing session state. See **[interactive shell commands](https://www.opensre.com/docs/interactive-shell-commands)** for the full reference.

```bash
opensre
```

**Headless CLI** — run one agent turn non-interactively from a terminal, script, or CI job:

```bash
opensre ask "why is checkout-api slow?"
```

See **[Headless CLI](https://www.opensre.com/docs/headless-cli)** for stdin prompts, JSON output, and tool approvals.

**From Python** — drive the agent in-process from your own code (source checkout required):

```python
from core.agent_harness import AgentSession

session = AgentSession.start()
result = session.chat("why is checkout-api slow?")
if result.answered:
    print(result.primary_response_text)
```

See **[Python API](https://www.opensre.com/docs/python-api)** for sessions, conversations, and custom output sinks.

**For your team's daily loop:** embed OpenSRE in the Python services and automations your teammates already use.
Start with the in-repo [Python API guide](docs/python-api.mdx), then use it every day to make incident response repeatable.

Other useful commands:

```bash
opensre integrations setup
opensre agents scan
opensre update
opensre uninstall   # remove opensre and all local data
```

---

## Deployment

Two primary AWS EC2 paths and a general hosted option:

- **Gateway (AMI + systemd):** `make build-gateway-image` then `make deploy-gateway` — Telegram gateway only, no Docker; the gateway is installed into a server image that new servers start from.
- **Hosted (Railway / ECS / Vercel):** deploy with the repo `Dockerfile`; set `LLM_PROVIDER` and the matching API key (see [`.env.example`](.env.example)), plus `DATABASE_URI` and `REDIS_URI` if persistence is needed.

**[Full deployment steps and prerequisites → DEPLOYMENT.md](DEPLOYMENT.md)**

---

## How OpenSRE Works

<img
  src="https://github.com/user-attachments/assets/936ab1f2-9bda-438d-9897-e8e9cd98e335"
  width="1064"
  height="568"
  alt="opensre-how-it-works-github"
/>

When you ask a question or an alert arrives, OpenSRE automatically:

1. **Fetches** the relevant context and correlated logs, metrics, traces, and recent deploys
2. **Masks** sensitive identifiers (optional) before external LLM calls
3. **Reasons** across your connected systems to test hypotheses in a tool-calling loop
4. **Answers** with an evidence-linked response in the conversation
5. **Suggests** next steps and, optionally, executes remediation actions
6. **Posts** a summary directly to Slack, PagerDuty, or Telegram — no context switching needed

For the current code-level agent architecture after removing the old graph and chain
framework layers, see [AGENTS.md](AGENTS.md).

---

## Capabilities & integrations

|                                          |                                                                                  |
| ---------------------------------------- | -------------------------------------------------------------------------------- |
| 🔍 **Evidence-driven diagnosis**         | Correlated analysis across logs, metrics, traces, deploys, and config            |
| 📋 **Runbook-aware reasoning**           | OpenSRE reads your runbooks and applies them automatically                       |
| 🔗 **Evidence-backed root cause**        | Every conclusion is linked to the data behind it                                 |
| 🛡️ **Reversible identifier masking**     | Redact pods, clusters, and account IDs before external LLM calls; restore in output |
| 📊 **Session cost & history**            | Per-session token tracking (`/cost`) and resumable REPL sessions (`/sessions`) |
| 👥 **Local agent fleet**                 | Monitor Claude Code, Cursor, Codex, and other coding agents on your machine      |
| 🤖 **Full LLM flexibility**              | Bring your own model — Anthropic, OpenAI, Codex, Ollama, Gemini, OpenRouter, TrustedRouter, NVIDIA NIM, Bedrock |

OpenSRE connects to **60+** tools across LLMs, observability, cloud infrastructure, data platforms, incident management, and MCP. The full matrix (with roadmap links) lives in the **[product docs](https://www.opensre.com/docs)**; a detailed catalog is also maintained in-repo as the project grows.

---

## Integrations

OpenSRE connects to 60+ tools and services across the modern cloud stack, from LLM providers and observability platforms to infrastructure, databases, and incident management.

| Category                | Integrations                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Roadmap                                                                                                                                                                                                                                                            |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **AI / LLM Providers**  | Anthropic · OpenAI · OpenAI Codex · Ollama · Google Gemini · OpenRouter · TrustedRouter · NVIDIA NIM · Bedrock                                                                                                                                                                                                                                                                                                                                                                               |                                                                                                                                                                                                                                                                    |
| **Observability**       | <img src="docs/assets/icons/grafana.webp" width="16"> Grafana (Loki · Mimir · Tempo · annotations) · <img src="docs/assets/icons/datadog.svg" width="16"> Datadog · Honeycomb · Coralogix · <img src="docs/assets/icons/groundcover.webp" width="16"> groundcover · <img src="docs/assets/icons/cloudwatch.png" width="16"> CloudWatch · <img src="docs/assets/icons/sentry.png" width="16"> Sentry · Elasticsearch · Better Stack · Splunk · Victoria Logs · SignOz · OpenObserve · OpenSearch · Azure Monitor · New Relic |                                                                                                                                                                                                             |
| **Infrastructure**      | <img src="docs/assets/icons/kubernetes.png" width="16"> Kubernetes · <img src="docs/assets/icons/aws.png" width="16"> AWS (S3 · Lambda · EKS · EC2 · CloudTrail · Bedrock) · <img src="docs/assets/icons/gcp.jpg" width="16"> GCP · <img src="docs/assets/icons/azure.png" width="16"> Azure · Yandex Cloud · ArgoCD · Helm · Jenkins                                                                                                                                                  |                                                                                                                                                                                                                                                                    |
| **Database**            | MongoDB · ClickHouse · PostgreSQL · MySQL · MariaDB · MongoDB Atlas · Azure SQL · Snowflake · Redis · RDS · Supabase                                                                                                                                                                                                                                                                                                                                                         |                                                                                                                                                                                                                                                                    |
| **Data Platform**       | Apache Airflow · Apache Kafka · Apache Spark · Prefect · RabbitMQ · Dagster                                                                                                                                                                                                                                                                                                                                                                                                |                                                                                                                                                                                                                                                                    |
| **Dev Tools**           | <img src="docs/assets/icons/github.webp" width="16"> GitHub · GitHub MCP · Bitbucket · GitLab                                                                                                                                                                                                                                                                                                                                                                                |                                                                                                                                                                                                                                                                    |
| **Incident Management** | <img src="docs/assets/icons/pagerduty.png" width="16"> PagerDuty · Opsgenie · Jira · Alertmanager · incident.io · ServiceNow                                                                                                                                                                                                                                                                                                                                                | [Trello](https://github.com/Tracer-Cloud/opensre/issues/361) · [Linear](https://github.com/Tracer-Cloud/opensre/issues/124)                                                                 |
| **Communication**       | <img src="docs/assets/icons/slack.png" width="16"> Slack · Google Docs · Discord · Telegram · <img src="docs/assets/icons/rocketchat.png" width="16"> Rocket.Chat · WhatsApp · Buzz                                                                                                                                                                                                                                                                                                                                                                     | [Notion](https://github.com/Tracer-Cloud/opensre/issues/286) · [Teams](https://github.com/Tracer-Cloud/opensre/issues/138) · [Confluence](https://github.com/Tracer-Cloud/opensre/issues/313)                                                                   |
| **Agent Deployment**    | <img src="docs/assets/icons/vercel.png" width="16"> Vercel · <img src="docs/assets/icons/aws.png" width="16"> EC2 · <img src="docs/assets/icons/aws.png" width="16"> ECS · Railway                                                                                                                                                                                                                                                                                         |                                                                                                                                                                                                                                                                    |
| **Protocols**           | <img src="docs/assets/icons/mcp.svg" width="16"> MCP · <img src="docs/assets/icons/acp.png" width="16"> ACP                                                                                                                                                                                                                                                                                                                                                     |                                                                                                                                                                                                                                                                    |

OpenSRE is community-built. Looking for a safe first contribution? Browse [`good first issue`](https://github.com/Tracer-Cloud/opensre/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) tickets or see the [Good First Issues guide](docs/good-first-issues/README.md). See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the full workflow.

**Local environment:** **[SETUP.md](SETUP.md)** (all platforms, Windows, MCP).

**Developing in this repo:** **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** (install from source, CI parity checks, dev container, deployment detail, telemetry reference).

<p>
  <a href="https://discord.gg/7NTpevXf7w">
    <img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Join our Discord" />
  </a>
</p>

<p align="center">
  <a href="https://www.star-history.com/?type=date&repos=Tracer-Cloud%2Fopensre">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Tracer-Cloud/opensre&type=date&theme=dark&legend=top-left&sealed_token=LHlhQArnQVcZZDuHHjU19dJHIIzQx9WzB2xacqhOnA8REEAfcVO94FgGmjAMsR8iiPA3ELR-RmF_t2rtnLLj6ieZt6S4PGbFZ5Ev1HgIrg8KJNkJDYQob_BcV8MtWc2wQnahPyKX6B9PgqvoQxI7E6B6SkW7gXQEq1xLLGDZ2JHvv5b3kr7SYnUpq98y" />
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Tracer-Cloud/opensre&type=date&legend=top-left&sealed_token=LHlhQArnQVcZZDuHHjU19dJHIIzQx9WzB2xacqhOnA8REEAfcVO94FgGmjAMsR8iiPA3ELR-RmF_t2rtnLLj6ieZt6S4PGbFZ5Ev1HgIrg8KJNkJDYQob_BcV8MtWc2wQnahPyKX6B9PgqvoQxI7E6B6SkW7gXQEq1xLLGDZ2JHvv5b3kr7SYnUpq98y" />
      <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Tracer-Cloud/opensre&type=date&legend=top-left&sealed_token=LHlhQArnQVcZZDuHHjU19dJHIIzQx9WzB2xacqhOnA8REEAfcVO94FgGmjAMsR8iiPA3ELR-RmF_t2rtnLLj6ieZt6S4PGbFZ5Ev1HgIrg8KJNkJDYQob_BcV8MtWc2wQnahPyKX6B9PgqvoQxI7E6B6SkW7gXQEq1xLLGDZ2JHvv5b3kr7SYnUpq98y" />
    </picture>
  </a>
</p>

Thanks goes to these amazing people:

<!-- readme: contributors -start -->
<a href="https://github.com/Tracer-Cloud/opensre/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Tracer-Cloud/opensre&max=200" alt="Contributors" />
</a>
<!-- readme: contributors -end -->

---

## Security

OpenSRE is designed with production environments in mind: structured and auditable LLM prompts, local transcript handling by default, and no silent bulk export of raw logs. See **[SECURITY.md](SECURITY.md)** for responsible disclosure.

---

## Telemetry

PostHog (product analytics) and Sentry (errors) are **opt-out**. Quick disable:

```bash
export OPENSRE_NO_TELEMETRY=1
```

**[Full matrix, DSN override, and local event logging → docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#telemetry-and-privacy)**

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Citations

<sup>1</sup> https://arxiv.org/abs/2310.06770
