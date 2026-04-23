<div align="center">

<p align="center">
  <img src="docs/logo/opensre-logomark-full.svg" alt="OpenSRE" width="360" />
</p>

<h1>OpenSRE: Build Your Own AI SRE Agents</h1>


<p>The open-source framework for AI SRE agents, and the training and evaluation environment they need to improve. Connect the 60+ tools you already run, define your own workflows, and investigate incidents on your own infrastructure.</p>

<p>
  <a href="https://github.com/Tracer-Cloud/opensre/stargazers"><img src="https://img.shields.io/github/stars/Tracer-Cloud/opensre?style=flat-square&color=FF6B00" alt="Stars"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="License"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/blob/main/.github/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Tracer-Cloud/opensre/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/open%20source-forever-brightgreen?style=flat-square" alt="Open Source">
  <a href="https://discord.gg/7NTpevXf7w"><img src="https://img.shields.io/badge/Discord-Join%20Us-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Discord"></a>
 
</p>
<a href="https://trendshift.io/repositories/25889" target="_blank">
  <img
    src="https://trendshift.io/api/badge/repositories/25889"
    alt="Tracer-Cloud%2Fopensre | Trendshift"
    style="height: 30px; width: auto;"
    height="30"
  />
</a>

<p align="center">
  <strong>
    <a href="https://www.opensre.com/docs/quickstart">Quickstart</a> ·
    <a href="https://www.opensre.com/docs">Docs</a> ·
    <a href="https://tracer.mintlify.app/faq">FAQ</a> ·
    <a href="https://trust.tracer.cloud/">Security</a>
  </strong>
</p>

</div>

---

> 🚧 Public Alpha: Core workflows are usable for early exploration, though not yet fully stable. The project is in active development, and APIs and integrations may evolve

---

## Why OpenSRE?

When something breaks in production, the evidence is scattered across logs, metrics, traces, runbooks, and Slack threads. OpenSRE is an open-source framework for AI SRE agents that resolve production incidents, built to run on your own infrastructure.

We do that because SWE-bench<sup>1</sup> gave coding agents scalable training data and clear feedback. Production incident response still lacks an equivalent.

Distributed failures are slower, noisier, and harder to simulate and evaluate than local code tasks, which is why AI SRE, and AI for production debugging more broadly, remains unsolved.

OpenSRE is building _that_ missing layer:

> an open reinforcement learning environment for agentic infrastructure incident response, with end-to-end tests and synthetic incident simulations for realistic production failures

We do that by:

- building easy-to-deploy, customizable AI SRE agents for production incident investigation and response
- running scored synthetic RCA suites that check root-cause accuracy, required evidence, and adversarial red herrings [(tests/synthetic)](tests/synthetic/rds_postgres)
- running real-world end-to-end tests across cloud-backed scenarios including Kubernetes, EC2, CloudWatch, Lambda, ECS Fargate, and Flink [(tests/e2e)](tests/e2e)
- keeping semantic test-catalog naming so e2e vs synthetic and local vs cloud boundaries stay obvious [(tests/README.md)](tests/README.md)

Our mission is to build AI SRE agents on top of this, scale it to thousands of realistic infrastructure failure scenarios, and establish OpenSRE as the benchmark and training ground for AI SRE.

<sup>1</sup> https://arxiv.org/abs/2310.06770

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Tracer-Cloud/opensre/main/install.sh | bash
```

```bash
brew install Tracer-Cloud/opensre/opensre
```

```powershell
irm https://raw.githubusercontent.com/Tracer-Cloud/opensre/main/install.ps1 | iex
```

<!--
```bash
pipx install opensre
``` -->

---

## Quick Start

```bash
opensre onboard
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
opensre update
```

---

## Railway Deployment

Before running `opensre deploy railway`, make sure the target Railway project has
both Postgres and Redis services, and that your OpenSRE service has `DATABASE_URI`
and `REDIS_URI` set to those connection strings. The containerized LangGraph
runtime will not boot without those backing services wired in.

```bash
# create/link Railway Postgres and Redis first, then set DATABASE_URI and REDIS_URI
opensre deploy railway --project <project> --service <service> --yes
```

If the deploy starts but the service never becomes healthy, verify that
`DATABASE_URI` and `REDIS_URI` are present on the Railway service and point to the
project Postgres and Redis instances.

### Remote Hosted Ops

After deploying a hosted service, you can run post-deploy operations from the CLI:

```bash
# inspect service status, URL, deployment metadata
opensre remote ops --provider railway --project <project> --service <service> status

# tail recent logs
opensre remote ops --provider railway --project <project> --service <service> logs --lines 200

# stream logs live
opensre remote ops --provider railway --project <project> --service <service> logs --follow

# trigger restart/redeploy
opensre remote ops --provider railway --project <project> --service <service> restart --yes
```

OpenSRE saves your last used `provider`, so you can run:

```bash
opensre remote ops status
opensre remote ops logs --follow
```

---

## Development

> **New to OpenSRE?** See [SETUP.md](SETUP.md) for detailed platform-specific setup instructions, including Windows setup, environment configuration, and more.

```bash
git clone https://github.com/Tracer-Cloud/opensre
cd opensre
make install
# run opensre onboard to configure your local LLM provider
# and optionally validate/save Grafana, Datadog, Honeycomb, Coralogix, Slack, AWS, GitHub MCP, and Sentry integrations
opensre onboard
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

If you use VS Code, the repo now includes a ready-to-use devcontainer under [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json). Open the repo in VS Code and run `Dev Containers: Reopen in Container` to get the project on Python 3.13 with the contributor toolchain preinstalled. Keep Docker Desktop, OrbStack, Colima, or another Docker-compatible runtime running on the host, since VS Code devcontainers rely on your local Docker engine.

---

## How OpenSRE Works

<img src="https://github.com/user-attachments/assets/04234934-c603-491e-a08f-b145d1158d7a" width="1064" alt="opensre-how-it-works" style="border-radius: 0" />

### Investigation Workflow

When an alert fires, OpenSRE automatically:

1. **Fetches** the alert context and correlated logs, metrics, and traces
2. **Reasons** across your connected systems to identify anomalies
3. **Generates** a structured investigation report with probable root cause
4. **Suggests** next steps and, optionally, executes remediation actions
5. **Posts** a summary directly to Slack or PagerDuty - no context switching needed

---

## Benchmark

Generate the benchmark report:

```shell
make benchmark
```

---

## Capabilities

|                                          |                                                                                  |
| ---------------------------------------- | -------------------------------------------------------------------------------- |
| 🔍 **Structured incident investigation** | Correlated root-cause analysis across all your signals                           |
| 📋 **Runbook-aware reasoning**           | OpenSRE reads your runbooks and applies them automatically                       |
| 🔮 **Predictive failure detection**      | Catch emerging issues before they page you                                       |
| 🔗 **Evidence-backed root cause**        | Every conclusion is linked to the data behind it                                 |
| 🤖 **Full LLM flexibility**              | Bring your own model — Anthropic, OpenAI, Ollama, Gemini, OpenRouter, NVIDIA NIM |

---

## Integrations

OpenSRE connects to 60+ tools and services across the modern cloud stack, from LLM providers and observability platforms to infrastructure, databases, and incident management.

| Category | Integrations | Roadmap |
| --- | --- | --- |
| **AI / LLM Providers** | Anthropic · OpenAI · Ollama · Google Gemini · OpenRouter · NVIDIA NIM · Bedrock | |
| **Observability** | <img src="docs/assets/icons/grafana.webp" width="16"> Grafana (Loki · Mimir · Tempo) · <img src="docs/assets/icons/datadog.svg" width="16"> Datadog · Honeycomb · Coralogix · <img src="docs/assets/icons/cloudwatch.png" width="16"> CloudWatch · <img src="docs/assets/icons/sentry.png" width="16"> Sentry · Elasticsearch · Better Stack Telemetry | [Splunk](https://github.com/Tracer-Cloud/opensre/issues/319) · [New Relic](https://github.com/Tracer-Cloud/opensre/issues/139) · [Victoria Logs](https://github.com/Tracer-Cloud/opensre/issues/126) |
| **Infrastructure** | <img src="docs/assets/icons/kubernetes.png" width="16"> Kubernetes · <img src="docs/assets/icons/aws.png" width="16"> AWS (S3 · Lambda · EKS · EC2 · Bedrock) · <img src="docs/assets/icons/gcp.jpg" width="16"> GCP · <img src="docs/assets/icons/azure.png" width="16"> Azure | [Helm](https://github.com/Tracer-Cloud/opensre/issues/321) · [ArgoCD](https://github.com/Tracer-Cloud/opensre/issues/320) |
| **Database** | MongoDB · ClickHouse | [PostgreSQL · MySQL](https://github.com/Tracer-Cloud/opensre/issues/322) · [MariaDB](https://github.com/Tracer-Cloud/opensre/issues/331) · [MongoDB Atlas](https://github.com/Tracer-Cloud/opensre/issues/332) · [Azure SQL](https://github.com/Tracer-Cloud/opensre/issues/333) · [RDS](https://github.com/Tracer-Cloud/opensre/issues/125) · [Snowflake](https://github.com/Tracer-Cloud/opensre/issues/364) |
| **Data Platform** | Apache Airflow · Apache Kafka · Apache Spark · Prefect · RabbitMQ | |
| **Dev Tools** | <img src="docs/assets/icons/github.webp" width="16"> GitHub · GitHub MCP · Bitbucket | [GitLab](https://github.com/Tracer-Cloud/opensre/issues/318) |
| **Incident Management** | <img src="docs/assets/icons/pagerduty.png" width="16"> PagerDuty · Opsgenie · Jira | [ServiceNow](https://github.com/Tracer-Cloud/opensre/issues/314) · [incident.io](https://github.com/Tracer-Cloud/opensre/issues/317) · [Alertmanager](https://github.com/Tracer-Cloud/opensre/issues/316) · [Linear](https://github.com/Tracer-Cloud/opensre/issues/124) · [Trello](https://github.com/Tracer-Cloud/opensre/issues/361) |
| **Communication** | <img src="docs/assets/icons/slack.png" width="16"> Slack · Google Docs | [Discord](https://github.com/Tracer-Cloud/opensre/issues/359) · [Teams](https://github.com/Tracer-Cloud/opensre/issues/138) · [WhatsApp](https://github.com/Tracer-Cloud/opensre/issues/360) · [Confluence](https://github.com/Tracer-Cloud/opensre/issues/313) · [Notion](https://github.com/Tracer-Cloud/opensre/issues/286) |
| **Agent Deployment** | <img src="docs/assets/icons/vercel.png" width="16"> Vercel · <img src="docs/assets/icons/langsmith.png" width="16"> LangSmith · <img src="docs/assets/icons/aws.png" width="16"> EC2 · <img src="docs/assets/icons/aws.png" width="16"> ECS | [Railway](https://github.com/Tracer-Cloud/opensre/issues/271) |
| **Protocols** | <img src="docs/assets/icons/mcp.svg" width="16"> MCP · <img src="docs/assets/icons/acp.png" width="16"> ACP · <img src="docs/assets/icons/openclaw.jpg" width="16"> OpenClaw | |

---

## Contributing

OpenSRE is community-built. Every integration, improvement, and bug fix makes it better for thousands of engineers. We actively review PRs and welcome contributors of all experience levels.

<p>
  <a href="https://discord.gg/7NTpevXf7w">
    <img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Join our Discord" />
  </a>
</p>

Good first issues are labeled [`good first issue`](https://github.com/Tracer-Cloud/opensre/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22). Ways to contribute:

- 🐛 Report bugs or missing edge cases
- 🔌 Add a new tool integration
- 📖 Improve documentation or runbook examples
- ⭐ Star the repo - it helps other engineers find OpenSRE

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

Thanks goes to these amazing people:

<!-- readme: contributors -start -->
<table>
	<tbody>
		<tr>
            <td align="center">
        <a href="https://github.com/davincios">
            <img src="https://avatars.githubusercontent.com/u/33206282?v=4" width="100" alt="davincios"/>
            <br />
            <sub><b>davincios</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/VaibhavUpreti">
            <img src="https://avatars.githubusercontent.com/u/85568177?v=4" width="100" alt="VaibhavUpreti"/>
            <br />
            <sub><b>VaibhavUpreti</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/aliya-tracer">
            <img src="https://avatars.githubusercontent.com/u/233726347?v=4" width="100" alt="aliya-tracer"/>
            <br />
            <sub><b>aliya-tracer</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/arnetracer">
            <img src="https://avatars.githubusercontent.com/u/203629234?v=4" width="100" alt="arnetracer"/>
            <br />
            <sub><b>arnetracer</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/kylie-tracer">
            <img src="https://avatars.githubusercontent.com/u/256781109?v=4" width="100" alt="kylie-tracer"/>
            <br />
            <sub><b>kylie-tracer</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/paultracer">
            <img src="https://avatars.githubusercontent.com/u/214484440?v=4" width="100" alt="paultracer"/>
            <br />
            <sub><b>paultracer</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/zeel2104">
            <img src="https://avatars.githubusercontent.com/u/72783325?v=4" width="100" alt="zeel2104"/>
            <br />
            <sub><b>zeel2104</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/iamkalio">
            <img src="https://avatars.githubusercontent.com/u/89003403?v=4" width="100" alt="iamkalio"/>
            <br />
            <sub><b>iamkalio</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/w3joe">
            <img src="https://avatars.githubusercontent.com/u/84664178?v=4" width="100" alt="w3joe"/>
            <br />
            <sub><b>w3joe</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/yeoreums">
            <img src="https://avatars.githubusercontent.com/u/62932875?v=4" width="100" alt="yeoreums"/>
            <br />
            <sub><b>yeoreums</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/anandgupta1202">
            <img src="https://avatars.githubusercontent.com/u/39819996?v=4" width="100" alt="anandgupta1202"/>
            <br />
            <sub><b>anandgupta1202</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/rrajan94">
            <img src="https://avatars.githubusercontent.com/u/25589618?v=4" width="100" alt="rrajan94"/>
            <br />
            <sub><b>rrajan94</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/vrk7">
            <img src="https://avatars.githubusercontent.com/u/108936058?v=4" width="100" alt="vrk7"/>
            <br />
            <sub><b>vrk7</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/cerencamkiran">
            <img src="https://avatars.githubusercontent.com/u/150190567?v=4" width="100" alt="cerencamkiran"/>
            <br />
            <sub><b>cerencamkiran</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/edgarmb14">
            <img src="https://avatars.githubusercontent.com/u/268297669?v=4" width="100" alt="edgarmb14"/>
            <br />
            <sub><b>edgarmb14</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/lukegimza">
            <img src="https://avatars.githubusercontent.com/u/68860070?v=4" width="100" alt="lukegimza"/>
            <br />
            <sub><b>lukegimza</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/ebrahim-sameh">
            <img src="https://avatars.githubusercontent.com/u/23136098?v=4" width="100" alt="ebrahim-sameh"/>
            <br />
            <sub><b>ebrahim-sameh</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/shoaib050326">
            <img src="https://avatars.githubusercontent.com/u/266381026?v=4" width="100" alt="shoaib050326"/>
            <br />
            <sub><b>shoaib050326</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/venturevd">
            <img src="https://avatars.githubusercontent.com/u/269883753?v=4" width="100" alt="venturevd"/>
            <br />
            <sub><b>venturevd</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/shriyashsoni">
            <img src="https://avatars.githubusercontent.com/u/138931443?v=4" width="100" alt="shriyashsoni"/>
            <br />
            <sub><b>shriyashsoni</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Devesh36">
            <img src="https://avatars.githubusercontent.com/u/142524747?v=4" width="100" alt="Devesh36"/>
            <br />
            <sub><b>Devesh36</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/KindaJayant">
            <img src="https://avatars.githubusercontent.com/u/136953152?v=4" width="100" alt="KindaJayant"/>
            <br />
            <sub><b>KindaJayant</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/overcastbulb">
            <img src="https://avatars.githubusercontent.com/u/99129410?v=4" width="100" alt="overcastbulb"/>
            <br />
            <sub><b>overcastbulb</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Yashkapure06">
            <img src="https://avatars.githubusercontent.com/u/61585443?v=4" width="100" alt="Yashkapure06"/>
            <br />
            <sub><b>Yashkapure06</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/Davda-James">
            <img src="https://avatars.githubusercontent.com/u/151067328?v=4" width="100" alt="Davda-James"/>
            <br />
            <sub><b>Davda-James</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Abhinnavverma">
            <img src="https://avatars.githubusercontent.com/u/138097198?v=4" width="100" alt="Abhinnavverma"/>
            <br />
            <sub><b>Abhinnavverma</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/devankitjuneja">
            <img src="https://avatars.githubusercontent.com/u/55021449?v=4" width="100" alt="devankitjuneja"/>
            <br />
            <sub><b>devankitjuneja</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/ramandagar">
            <img src="https://avatars.githubusercontent.com/u/89700171?v=4" width="100" alt="ramandagar"/>
            <br />
            <sub><b>ramandagar</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/mvanhorn">
            <img src="https://avatars.githubusercontent.com/u/455140?v=4" width="100" alt="mvanhorn"/>
            <br />
            <sub><b>mvanhorn</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/abhishek-marathe04">
            <img src="https://avatars.githubusercontent.com/u/175933950?v=4" width="100" alt="abhishek-marathe04"/>
            <br />
            <sub><b>abhishek-marathe04</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/yashksaini-coder">
            <img src="https://avatars.githubusercontent.com/u/115717039?v=4" width="100" alt="yashksaini-coder"/>
            <br />
            <sub><b>yashksaini-coder</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/haliaeetusvocifer">
            <img src="https://avatars.githubusercontent.com/u/20953018?v=4" width="100" alt="haliaeetusvocifer"/>
            <br />
            <sub><b>haliaeetusvocifer</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Bahtya">
            <img src="https://avatars.githubusercontent.com/u/34988899?v=4" width="100" alt="Bahtya"/>
            <br />
            <sub><b>Bahtya</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/mayankbharati-ops">
            <img src="https://avatars.githubusercontent.com/u/245952278?v=4" width="100" alt="mayankbharati-ops"/>
            <br />
            <sub><b>mayankbharati-ops</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/harshareddy832">
            <img src="https://avatars.githubusercontent.com/u/53609097?v=4" width="100" alt="harshareddy832"/>
            <br />
            <sub><b>harshareddy832</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/sundaram2021">
            <img src="https://avatars.githubusercontent.com/u/93595231?v=4" width="100" alt="sundaram2021"/>
            <br />
            <sub><b>sundaram2021</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/micheal000010000-hub">
            <img src="https://avatars.githubusercontent.com/u/249460313?v=4" width="100" alt="micheal000010000-hub"/>
            <br />
            <sub><b>micheal000010000-hub</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/ljivesh">
            <img src="https://avatars.githubusercontent.com/u/96004270?v=4" width="100" alt="ljivesh"/>
            <br />
            <sub><b>ljivesh</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/gautamjain1503">
            <img src="https://avatars.githubusercontent.com/u/97388837?v=4" width="100" alt="gautamjain1503"/>
            <br />
            <sub><b>gautamjain1503</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/mudittt">
            <img src="https://avatars.githubusercontent.com/u/96051296?v=4" width="100" alt="mudittt"/>
            <br />
            <sub><b>mudittt</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/hamzzaaamalik">
            <img src="https://avatars.githubusercontent.com/u/147706212?v=4" width="100" alt="hamzzaaamalik"/>
            <br />
            <sub><b>hamzzaaamalik</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/octo-patch">
            <img src="https://avatars.githubusercontent.com/u/266937838?v=4" width="100" alt="octo-patch"/>
            <br />
            <sub><b>octo-patch</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/fuleinist">
            <img src="https://avatars.githubusercontent.com/u/1163738?v=4" width="100" alt="fuleinist"/>
            <br />
            <sub><b>fuleinist</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/yas789">
            <img src="https://avatars.githubusercontent.com/u/84193712?v=4" width="100" alt="yas789"/>
            <br />
            <sub><b>yas789</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/sharkello">
            <img src="https://avatars.githubusercontent.com/u/159360024?v=4" width="100" alt="sharkello"/>
            <br />
            <sub><b>sharkello</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/kaushal-bakrania">
            <img src="https://avatars.githubusercontent.com/u/71706867?v=4" width="100" alt="kaushal-bakrania"/>
            <br />
            <sub><b>kaushal-bakrania</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/darthwade">
            <img src="https://avatars.githubusercontent.com/u/2220776?v=4" width="100" alt="darthwade"/>
            <br />
            <sub><b>darthwade</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/aniruddhaadak80">
            <img src="https://avatars.githubusercontent.com/u/127435065?v=4" width="100" alt="aniruddhaadak80"/>
            <br />
            <sub><b>aniruddhaadak80</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/chaosreload">
            <img src="https://avatars.githubusercontent.com/u/6723037?v=4" width="100" alt="chaosreload"/>
            <br />
            <sub><b>chaosreload</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/paulovitorcl">
            <img src="https://avatars.githubusercontent.com/u/47778440?v=4" width="100" alt="paulovitorcl"/>
            <br />
            <sub><b>paulovitorcl</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/gbsierra">
            <img src="https://avatars.githubusercontent.com/u/182822327?v=4" width="100" alt="gbsierra"/>
            <br />
            <sub><b>gbsierra</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/alexanderkreidich">
            <img src="https://avatars.githubusercontent.com/u/126781073?v=4" width="100" alt="alexanderkreidich"/>
            <br />
            <sub><b>alexanderkreidich</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/afif1400">
            <img src="https://avatars.githubusercontent.com/u/51887071?v=4" width="100" alt="afif1400"/>
            <br />
            <sub><b>afif1400</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/gauravch-code">
            <img src="https://avatars.githubusercontent.com/u/180489802?v=4" width="100" alt="gauravch-code"/>
            <br />
            <sub><b>gauravch-code</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/divijgera">
            <img src="https://avatars.githubusercontent.com/u/46404484?v=4" width="100" alt="divijgera"/>
            <br />
            <sub><b>divijgera</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/daxp472">
            <img src="https://avatars.githubusercontent.com/u/177292922?v=4" width="100" alt="daxp472"/>
            <br />
            <sub><b>daxp472</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Som-0619">
            <img src="https://avatars.githubusercontent.com/u/143019791?v=4" width="100" alt="Som-0619"/>
            <br />
            <sub><b>Som-0619</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Gust-svg">
            <img src="https://avatars.githubusercontent.com/u/265007695?v=4" width="100" alt="Gust-svg"/>
            <br />
            <sub><b>Gust-svg</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Sayeem3051">
            <img src="https://avatars.githubusercontent.com/u/169171880?v=4" width="100" alt="Sayeem3051"/>
            <br />
            <sub><b>Sayeem3051</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/MachineLearning-Nerd">
            <img src="https://avatars.githubusercontent.com/u/37579156?v=4" width="100" alt="MachineLearning-Nerd"/>
            <br />
            <sub><b>MachineLearning-Nerd</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/F4tal1t">
            <img src="https://avatars.githubusercontent.com/u/109851148?v=4" width="100" alt="F4tal1t"/>
            <br />
            <sub><b>F4tal1t</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/MestreY0d4-Uninter">
            <img src="https://avatars.githubusercontent.com/u/241404605?v=4" width="100" alt="MestreY0d4-Uninter"/>
            <br />
            <sub><b>MestreY0d4-Uninter</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/qorexdevs">
            <img src="https://avatars.githubusercontent.com/u/277760369?v=4" width="100" alt="qorexdevs"/>
            <br />
            <sub><b>qorexdevs</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Agnuxo1">
            <img src="https://avatars.githubusercontent.com/u/166046035?v=4" width="100" alt="Agnuxo1"/>
            <br />
            <sub><b>Agnuxo1</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Ryjen1">
            <img src="https://avatars.githubusercontent.com/u/114498519?v=4" width="100" alt="Ryjen1"/>
            <br />
            <sub><b>Ryjen1</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/nandanadileep">
            <img src="https://avatars.githubusercontent.com/u/110280757?v=4" width="100" alt="nandanadileep"/>
            <br />
            <sub><b>nandanadileep</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/Maharshi-Project">
            <img src="https://avatars.githubusercontent.com/u/156591746?v=4" width="100" alt="Maharshi-Project"/>
            <br />
            <sub><b>Maharshi-Project</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/udit-rawat">
            <img src="https://avatars.githubusercontent.com/u/84604012?v=4" width="100" alt="udit-rawat"/>
            <br />
            <sub><b>udit-rawat</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/muddlebee">
            <img src="https://avatars.githubusercontent.com/u/8139783?v=4" width="100" alt="muddlebee"/>
            <br />
            <sub><b>muddlebee</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Jah-yee">
            <img src="https://avatars.githubusercontent.com/u/166608075?v=4" width="100" alt="Jah-yee"/>
            <br />
            <sub><b>Jah-yee</b></sub>
        </a>
    </td>
		</tr>
	</tbody>
</table>
<!-- readme: contributors -end -->

---

## Security

OpenSRE is designed with production environments in mind:

- No storing of raw log data beyond the investigation session
- All LLM calls use structured, auditable prompts
- Log transcripts are kept locally - never sent externally by default

See [SECURITY.md](SECURITY.md) for responsible disclosure.

---

## Telemetry

`opensre` collects anonymous usage statistics with Posthog to help us understand adoption
and demonstrate traction to sponsors and investors who fund the project.
What we collect: command name, success/failure, rough runtime, CLI version,
Python version, OS family, machine architecture, and a small amount of
command-specific metadata such as which subcommand ran. For `opensre onboard`
and `opensre investigate`, we may also collect the selected model/provider and
whether the command used flags such as `--interactive` or `--input`.

A randomly generated anonymous ID is created on first run and stored in
`~/.config/opensre/`. We never collect alert contents, file contents,
hostnames, credentials, or any personally identifiable information.

Telemetry is automatically disabled in GitHub Actions and pytest runs.

To opt out locally, set the environment variable before running:

```bash
export OPENSRE_NO_TELEMETRY=1
```

The legacy alias `OPENSRE_ANALYTICS_DISABLED=1` also still works.

To inspect the payload locally without sending anything, use:

```bash
export OPENSRE_TELEMETRY_DEBUG=1
```

## License

Apache 2.0 - see [LICENSE](LICENSE) for details.

## Citations

<sup>1</sup> https://arxiv.org/abs/2310.06770
