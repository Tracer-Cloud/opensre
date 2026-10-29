<div align="center">

<p align="center">
  <img src="docs/logo/opensre-logo-white.svg" alt="OpenSRE" width="360" />
</p>

<h1>OpenSRE: The open-source agent for CI/CD reliability</h1>

<p>Your CI tells you it broke, but not why or how to fix it. OpenSRE is a CLI agent that reads the failed run, traces the cause and pushes a fix for you to review. Self-hosted, with your own models.</p>

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
  <a href="README.md">English</a> | <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <strong>
    <a href="https://www.opensre.com/docs/getting-started/quickstart">Quickstart</a> ·
    <a href="https://www.opensre.com/docs">Docs</a> ·
    <a href="https://opensre.com/docs/guides/faq">FAQ</a> ·
    <a href="https://trust.tracer.cloud/">Security</a>
  </strong>
</p>

</div>

---

> 🚧 Public Alpha: Core workflows are usable for early exploration, though not yet fully stable. The project is in active development, and APIs and integrations may evolve.

---

## Before you begin

Make sure you have:

- A terminal open in a code project
- A Git repository (recommended for the full workflow demonstration)

## Step 1: Install and start opensre

macOS or Linux:

```bash
curl -fsSL https://install.opensre.com | bash -s -- -gh
```

Windows (PowerShell):

```powershell
& ([scriptblock]::Create((irm https://install.opensre.com/install.ps1))) -gh
```

Then start OpenSRE:

```bash
opensre
```

![OpenSRE CLI welcome screen with the sign-in prompt](docs/images/opensre-welcome.png)

Windows details: [Windows](docs/install/windows-local.mdx).

The installer fetches the latest build from `main` without requiring sudo. If `opensre` is not found, follow the PATH instructions printed by the installer or open a new terminal.

For supported platforms and troubleshooting, see [Install locally](https://www.opensre.com/docs/install/install-local).

---

## Table of Contents

- [Before you begin](#before-you-begin)
- [Install and start opensre](#step-1-install-and-start-opensre)
- [Why OpenSRE?](#why-opensre)
- [What OpenSRE does](#what-opensre-does)
- [Quick Start](#quick-start)
- [Deployment](#deployment)
- [How OpenSRE Works](#how-opensre-works)
- [Capabilities & integrations](#capabilities--integrations)
- [Integrations](#integrations)
- [Security](#security)
- [Telemetry](#telemetry)
- [License](#license)

---

## Why OpenSRE?

AI writes more of the code every month. Someone still has to fix the build.

A red pipeline tells you _that_ something failed, not _why_. Under deadline pressure, teams hit re-run and move on, flaky checks pile up, `main` stays red, and the real investigation waits for whichever senior engineer has time.

OpenSRE is the CI/CD reliability agent you run on your own terms:

- **Recover from red builds faster.** It investigates the moment a run fails: reading logs, testing hypotheses and preparing a fix before you've opened the pipeline.
- **Fix the cause, not just the rerun.** It separates CI-caused failures from code-caused ones, so you fix the right thing instead of retrying until green.
- **Senior-level context for everyone.** Every engineer gets the same depth of analysis, without waiting on the one person who knows the pipeline.
- **Open source, on your infrastructure.** Apache 2.0, self-hosted, with your own models.

---

## What OpenSRE does

**Fix broken builds before you look.** OpenSRE reads the failing checks on a pull request or branch, traces the cause, applies a fix with a coding agent, pushes it to the PR branch (or a fresh repair branch) and waits for the new checks. You review and merge. → [Fix GitHub CI](docs/cicd/github-ci-fix.mdx)

**Know exactly what CI is costing you.** A 30-day report per repository: failure rate, CI-caused versus code-caused failures, developer hours lost to unreliable CI, and how long `main` stays red. → [CI/CD analytics](docs/cicd/cicd-analytics-demo.mdx)

**Keep CI green on a schedule.** A recurring reliability report, a bounded repair loop that watches one PR until it's green, or a managed gateway that runs repairs for your whole organization. → [Keep CI green with an agent](docs/cicd/ci-repair-agent.mdx)

**Find every red PR.** One read-only scan across every repository your token can see: which PRs and branches are failing, and on which checks. → [CI health scan](docs/cicd/github-ci-health-scan.mdx)

Try it in the shell with your own repository:

```text
analyze Tracer-Cloud/opensre CI/CD performance for the last 30 days
how much developer time does flaky CI cost us
fix failing checks on Tracer-Cloud/opensre#4597
set up an agent that improves CI/CD reliability for Tracer-Cloud/opensre
```

Analysis and scans are read-only. Fixes and repair loops push commits, but never to a fork PR or directly to a protected branch like `main`. See the **[CI/CD overview](docs/cicd/overview.mdx)** for the full picture.

---

## Quick Start

Contributors: start at [`main.py`](main.py) for the process entrypoint map.

Run `opensre` to sign in and get started. The first launch activates the hosted model.

```bash
opensre
```

Webapp contributors can run `opensre setup --dev` to authenticate through `http://localhost:3000`.

**Interactive shell** — with no subcommand, `opensre` validates your account and starts a REPL (TTY required). You can exit and stay signed out, but the shell only opens for an active account. Describe incidents in plain language, watch the agent work, and use slash commands for session control (`/help`, `/status`, `/cost`, `/sessions`, `/resume`, `/compact`, `/new`, `/exit`) and integrations (`/integrations list`, `/integrations verify`). Ctrl+C cancels an in-flight turn without losing session state. See **[interactive shell commands](https://www.opensre.com/docs/getting-started/interactive-shell-commands)** for the full reference.

**Headless CLI** — run one agent turn non-interactively from a terminal, script, or CI job:

```bash
opensre ask "why is checkout-api slow?"
```

See **[Headless CLI](https://www.opensre.com/docs/guides/headless-cli)** for stdin prompts, JSON output, and tool approvals.

**From Python** — drive the agent in-process from your own code (source checkout required):

```python
from bootstrap.embedded import start_embedded_session

session = start_embedded_session()
result = session.chat("why is checkout-api slow?")
if result.answered:
    print(result.primary_response_text)
```

See **[Python API](https://www.opensre.com/docs/guides/python-api)** for sessions, conversations, and custom output sinks.

**For your team's daily loop:** embed OpenSRE in the Python services and automations your teammates already use.
Start with the in-repo [Python API guide](docs/guides/python-api.mdx), then use it every day to make incident response repeatable.

Other useful commands:

```bash
opensre integrations setup
opensre update
opensre uninstall   # remove opensre and all local data
```

---

## Deployment

An OpenSRE Cloud managed gateway, an AWS EC2 path, and a general self-hosted option:

- **OpenSRE Cloud managed gateway:** an organization admin provisions it in the web app; signed-in shell users can check, start, or stop it without handling infrastructure credentials locally.
- **Gateway (AMI + systemd):** `make build-gateway-image` then `make deploy-gateway` — Telegram gateway only, no Docker; the gateway is installed into a server image that new servers start from.
- **Self-hosted container (Railway / ECS / Vercel):** deploy the repo `Dockerfile`, select `MODE`, and set `LLM_PROVIDER` plus its API key. Use `DATABASE_URL` only when the gateway needs shared Postgres-backed state, such as Slack Events API replicas.

**[Full deployment steps and prerequisites → DEPLOYMENT.md](DEPLOYMENT.md)**

---

## How OpenSRE Works

<img
  src="https://github.com/user-attachments/assets/936ab1f2-9bda-438d-9897-e8e9cd98e335"
  width="1064"
  height="568"
  alt="opensre-how-it-works-github"
/>

When a run fails, or you ask about one, OpenSRE:

1. **Reads** the failing checks, compact log excerpts, and the commits and history around them
2. **Masks** sensitive identifiers (optional) before external LLM calls
3. **Reasons** across your connected systems to test hypotheses and separate CI-caused failures from code-caused ones
4. **Fixes** the cause in an isolated checkout, using your logged-in coding agent (Pi, Claude Code, Codex, Cursor) or its built-in agent on your own model
5. **Verifies** by pushing to the PR or repair branch and waiting for the new checks to pass
6. **Reports** an evidence-linked summary in the shell, or in Slack, PagerDuty or Telegram

Connect your observability, code and chat tools once, and every skill runs on them.

For the current code-level agent architecture after removing the old graph and chain
framework layers, see [AGENTS.md](AGENTS.md).

---

## Capabilities & integrations

|                                          |                                                                                  |
| ---------------------------------------- | -------------------------------------------------------------------------------- |
| 🔧 **CI fixes you review**               | Reads failing checks, applies a fix, pushes to the PR branch and waits for green |
| 📈 **CI reliability analytics**          | Failure rate, CI- vs code-caused failures, developer hours lost, red time on `main` |
| 🔁 **Scheduled repair agents**           | Recurring reliability reports and bounded repair loops, locally or on a hosted gateway |
| 🔍 **Evidence-backed root cause**        | Correlated analysis across CI logs, commits, metrics, traces and deploys, every conclusion linked to its data |
| 🛡️ **Reversible identifier masking**     | Redact pods, clusters, and account IDs before external LLM calls; restore in output |
| 📊 **Session cost & history**            | Per-session token tracking (`/cost`) and resumable REPL sessions (`/sessions`) |
| 🤖 **Full LLM flexibility**              | Bring your own model — Anthropic, OpenAI, Codex, Ollama, Gemini, OpenRouter, TrustedRouter, NVIDIA NIM, Bedrock |

OpenSRE connects to **60+** tools across LLMs, observability, cloud infrastructure, data platforms, incident management, and MCP. The full matrix (with roadmap links) lives in the **[product docs](https://www.opensre.com/docs)**; a detailed catalog is also maintained in-repo as the project grows.

---

## Integrations

OpenSRE connects to 60+ tools and services across the modern cloud stack, from LLM providers and observability platforms to infrastructure, databases, and incident management.

| Category                | Integrations                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Roadmap                                                                                                                                                                                                                                                            |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **AI / LLM Providers**  | Anthropic · OpenAI · OpenAI Codex · Ollama · Google Gemini · OpenRouter · TrustedRouter · NVIDIA NIM · Bedrock                                                                                                                                                                                                                                                                                                                                                                               |                                                                                                                                                                                                                                                                    |
| **Observability**       | <img src="docs/assets/icons/grafana.webp" width="16"> Grafana (Loki · Mimir · Tempo · annotations) · <img src="docs/assets/icons/datadog.svg" width="16"> Datadog · Honeycomb · Coralogix · <img src="docs/assets/icons/groundcover.webp" width="16"> groundcover · <img src="docs/assets/icons/cloudwatch.png" width="16"> CloudWatch · <img src="docs/assets/icons/sentry.png" width="16"> Sentry · Elasticsearch · Better Stack · Splunk · Victoria Logs · SignOz · OpenObserve · OpenSearch · Azure Monitor · New Relic |                                                                                                                                                                                                             |
| **Infrastructure**      | <img src="docs/assets/icons/kubernetes.png" width="16"> Kubernetes · <img src="docs/assets/icons/aws.png" width="16"> AWS (S3 · Lambda · EKS · EC2 · CloudTrail · Bedrock) · <img src="docs/assets/icons/azure.png" width="16"> Azure · ArgoCD · Helm                                                                                                                                                  |                                                                                                                                                                                                                                                                    |
| **Database**            | MongoDB · ClickHouse · PostgreSQL · MySQL · MariaDB · MongoDB Atlas · Azure SQL · Snowflake · Redis · RDS · Supabase                                                                                                                                                                                                                                                                                                                                                         |                                                                                                                                                                                                                                                                    |
| **Data Platform**       | Apache Airflow · Apache Kafka · Prefect · RabbitMQ · Dagster · Temporal                                                                                                                                                                                                                                                                                                                                                                                                |                                                                                                                                                                                                                                                                    |
| **Dev Tools**           | <img src="docs/assets/icons/github.webp" width="16"> GitHub · GitHub MCP · Bitbucket · GitLab · Jenkins                                                                                                                                                                                                                                                                                                                                                                                |                                                                                                                                                                                                                                                                    |
| **Incident Management** | <img src="docs/assets/icons/pagerduty.png" width="16"> PagerDuty · Jira · Alertmanager · incident.io · ServiceNow · Trello (client helpers only)                                                                                                                                                                                                                                                                                                                                                | [Linear](https://github.com/Tracer-Cloud/opensre/issues/124)                                                                 |
| **Communication**       | <img src="docs/assets/icons/slack.png" width="16"> Slack · Google Docs · Discord · Telegram · <img src="docs/assets/icons/rocketchat.png" width="16"> Rocket.Chat · WhatsApp · Buzz                                                                                                                                                                                                                                                                                                                                                                     | [Notion](https://github.com/Tracer-Cloud/opensre/issues/286) · [Teams](https://github.com/Tracer-Cloud/opensre/issues/138) · [Confluence](https://github.com/Tracer-Cloud/opensre/issues/313)                                                                   |
| **Agent Deployment**    | <img src="docs/assets/icons/vercel.png" width="16"> Vercel · <img src="docs/assets/icons/aws.png" width="16"> EC2 · <img src="docs/assets/icons/aws.png" width="16"> ECS · Railway                                                                                                                                                                                                                                                                                         |                                                                                                                                                                                                                                                                    |
| **Protocols**           | <img src="docs/assets/icons/mcp.svg" width="16"> MCP                                                                                                                                                                                                                                                                                                                                                     |                                                                                                                                                                                                                                                                    |

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

First-party product analytics and Sentry (errors) are **opt-out**. Quick disable:

```bash
export OPENSRE_NO_TELEMETRY=1
```

**[Full matrix, DSN override, and local event logging → docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#telemetry-and-privacy)**

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
