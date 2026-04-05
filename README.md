<div align="center">

<p align="center">
  <img width="2136" height="476" alt="github-readme-tracer-banner" src="https://github.com/user-attachments/assets/fac67ac2-e40e-4d58-8421-829ed0ce2a4d" />
</p>

<h1>OpenSRE: Build Your Own AI SRE Agents</h1>

<p>The open-source framework for AI SRE agents, and the training and evaluation environment they need to improve. Connect the 40+ tools you already run, define your own workflows, and investigate incidents on your own infrastructure.</p>

<p>
  <a href="https://github.com/Tracer-Cloud/opensre/stargazers"><img src="https://img.shields.io/github/stars/Tracer-Cloud/opensre?style=flat-square&color=FF6B00" alt="Stars"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="License"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/blob/main/.github/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Tracer-Cloud/opensre/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/open%20source-forever-brightgreen?style=flat-square" alt="Open Source">
</p>

<p align="center">
  <strong>
    <a href="https://app.tracer.cloud/">Getting Started</a> ·
    <a href="https://tracer.cloud/">Tracer Agent</a> ·
    <a href="https://tracer.mintlify.app/">Docs</a> ·
    <a href="https://tracer.mintlify.app/faq">FAQ</a> ·
    <a href="https://trust.tracer.cloud/">Security</a>
  </strong>
</p>

<p>
  <a href="https://join.slack.com/t/tracer-cloud/shared_invite/zt-2xj0w3i3p-~EH0GFbZodFCXWkR7j3~w">
    <img src="https://img.shields.io/badge/➜_Click_To_Join_Our_Slack-white?style=for-the-badge&logo=slack&logoColor=4A154B" alt="Join Slack">
  </a>
</p>

</div>

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

## Development

> **New to Tracer?** See [SETUP.md](SETUP.md) for detailed platform-specific setup instructions, including Windows setup, environment configuration, and more.

```bash
git clone https://github.com/Tracer-Cloud/opensre
cd opensre
make install
# run opensre onboard to configure your local LLM provider
# and optionally validate/save Grafana, Datadog, Honeycomb, Coralogix, Slack, AWS, GitHub MCP, and Sentry integrations
opensre onboard
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

---

## How OpenSRE Works

<img width="4096" height="2187" alt="tracer-how-it-works-illustration" src="https://github.com/user-attachments/assets/8b50fe5c-470c-4982-866f-4f90c3e251d1" />

### Investigation Workflow

When an alert fires, Tracer automatically:

1. **Fetches** the alert context and correlated logs, metrics, and traces
2. **Reasons** across your connected systems to identify anomalies
3. **Generates** a structured investigation report with probable root cause
4. **Suggests** next steps and, optionally, executes remediation actions
5. **Posts** a summary directly to Slack or PagerDuty - no context switching needed

---

## Capabilities

|                                          |                                                           |
| ---------------------------------------- | --------------------------------------------------------- |
| 🔍 **Structured incident investigation** | Correlated root-cause analysis across all your signals    |
| 📋 **Runbook-aware reasoning**           | Tracer reads your runbooks and applies them automatically |
| 🔮 **Predictive failure detection**      | Catch emerging issues before they page you                |
| 🔗 **Evidence-backed root cause**        | Every conclusion is linked to the data behind it          |
| 🤖 **Full LLM flexibility**              | Bring your own model - OpenAI, Anthropic, and more        |

---

## Integrations

Tracer integrates with the systems that power modern cloud platforms.

| Category           | Integrations                                                                                                                                                                                                                                                                           |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Data Platform**  | Apache Airflow · Apache Kafka · Apache Spark                                                                                                                                                                                                                                           |
| **Observability**  | <img src="docs/assets/icons/grafana.webp" width="16"> Grafana · <img src="docs/assets/icons/datadog.svg" width="16"> Datadog · Honeycomb · Coralogix · <img src="docs/assets/icons/cloudwatch.png" width="16"> CloudWatch · <img src="docs/assets/icons/sentry.png" width="16"> Sentry |
| **Infrastructure** | <img src="docs/assets/icons/kubernetes.png" width="16"> Kubernetes · <img src="docs/assets/icons/aws.png" width="16"> AWS · <img src="docs/assets/icons/gcp.jpg" width="16"> GCP · <img src="docs/assets/icons/azure.png" width="16"> Azure                                            |
| **Dev Tools**      | <img src="docs/assets/icons/github.webp" width="16"> GitHub                                                                                                                                                                                                                            |
| **Communication**  | <img src="docs/assets/icons/slack.png" width="16"> Slack · <img src="docs/assets/icons/pagerduty.png" width="16"> PagerDuty · Google Docs                                                                                                                                              |

---

## Contributing

Tracer is community-built. Every integration, improvement, and bug fix makes it better for thousands of engineers. We actively review PRs and welcome contributors of all experience levels.

<p>
  <a href="https://join.slack.com/t/tracer-cloud/shared_invite/zt-3ifakcs2e-igoUEH8ygMlfxgJGLxsqeA">
    <img src="https://img.shields.io/badge/Join%20our%20Community%20Slack-4A154B?style=for-the-badge&logo=slack&logoColor=white" alt="Join our Community Slack" />
  </a>
</p>

Good first issues are labeled [`good first issue`](https://github.com/Tracer-Cloud/opensre/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22). Ways to contribute:

- 🐛 Report bugs or missing edge cases
- 🔌 Add a new tool integration
- 📖 Improve documentation or runbook examples
- ⭐ Star the repo - it helps other engineers find Tracer

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

Thanks goes to these amazing people:

<!-- readme: contributors -start -->
<table>
	<tbody>
		<tr>
            <td align="center">
        <a href="https://github.com/davincios">
            <img src="https://avatars.githubusercontent.com/u/33206282?v=4" width="100;" alt="davincios"/>
            <br />
            <sub><b>davincios</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/VaibhavUpreti">
            <img src="https://avatars.githubusercontent.com/u/85568177?v=4" width="100;" alt="VaibhavUpreti"/>
            <br />
            <sub><b>VaibhavUpreti</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/aliya-tracer">
            <img src="https://avatars.githubusercontent.com/u/233726347?v=4" width="100;" alt="aliya-tracer"/>
            <br />
            <sub><b>aliya-tracer</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/arnetracer">
            <img src="https://avatars.githubusercontent.com/u/203629234?v=4" width="100;" alt="arnetracer"/>
            <br />
            <sub><b>arnetracer</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/kylie-tracer">
            <img src="https://avatars.githubusercontent.com/u/256781109?v=4" width="100;" alt="kylie-tracer"/>
            <br />
            <sub><b>kylie-tracer</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/paultracer">
            <img src="https://avatars.githubusercontent.com/u/214484440?v=4" width="100;" alt="paultracer"/>
            <br />
            <sub><b>paultracer</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/zeel2104">
            <img src="https://avatars.githubusercontent.com/u/72783325?v=4" width="100;" alt="zeel2104"/>
            <br />
            <sub><b>zeel2104</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/iamkalio">
            <img src="https://avatars.githubusercontent.com/u/89003403?v=4" width="100;" alt="iamkalio"/>
            <br />
            <sub><b>iamkalio</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/w3joe">
            <img src="https://avatars.githubusercontent.com/u/84664178?v=4" width="100;" alt="w3joe"/>
            <br />
            <sub><b>w3joe</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/yeoreums">
            <img src="https://avatars.githubusercontent.com/u/62932875?v=4" width="100;" alt="yeoreums"/>
            <br />
            <sub><b>yeoreums</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/anandgupta1202">
            <img src="https://avatars.githubusercontent.com/u/39819996?v=4" width="100;" alt="anandgupta1202"/>
            <br />
            <sub><b>anandgupta1202</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/rrajan94">
            <img src="https://avatars.githubusercontent.com/u/25589618?v=4" width="100;" alt="rrajan94"/>
            <br />
            <sub><b>rrajan94</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/vrk7">
            <img src="https://avatars.githubusercontent.com/u/108936058?v=4" width="100;" alt="vrk7"/>
            <br />
            <sub><b>vrk7</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/cerencamkiran">
            <img src="https://avatars.githubusercontent.com/u/150190567?v=4" width="100;" alt="cerencamkiran"/>
            <br />
            <sub><b>cerencamkiran</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/edgarmb14">
            <img src="https://avatars.githubusercontent.com/u/268297669?v=4" width="100;" alt="edgarmb14"/>
            <br />
            <sub><b>edgarmb14</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/lukegimza">
            <img src="https://avatars.githubusercontent.com/u/68860070?v=4" width="100;" alt="lukegimza"/>
            <br />
            <sub><b>lukegimza</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/ebrahim-sameh">
            <img src="https://avatars.githubusercontent.com/u/23136098?v=4" width="100;" alt="ebrahim-sameh"/>
            <br />
            <sub><b>ebrahim-sameh</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/shoaib050326">
            <img src="https://avatars.githubusercontent.com/u/266381026?v=4" width="100;" alt="shoaib050326"/>
            <br />
            <sub><b>shoaib050326</b></sub>
        </a>
    </td>
		</tr>
		<tr>
            <td align="center">
        <a href="https://github.com/venturevd">
            <img src="https://avatars.githubusercontent.com/u/269883753?v=4" width="100;" alt="venturevd"/>
            <br />
            <sub><b>venturevd</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/shriyashsoni">
            <img src="https://avatars.githubusercontent.com/u/138931443?v=4" width="100;" alt="shriyashsoni"/>
            <br />
            <sub><b>shriyashsoni</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/Devesh36">
            <img src="https://avatars.githubusercontent.com/u/142524747?v=4" width="100;" alt="Devesh36"/>
            <br />
            <sub><b>Devesh36</b></sub>
        </a>
    </td>
            <td align="center">
        <a href="https://github.com/KindaJayant">
            <img src="https://avatars.githubusercontent.com/u/136953152?v=4" width="100;" alt="KindaJayant"/>
            <br />
            <sub><b>KindaJayant</b></sub>
        </a>
    </td>
		</tr>
	</tbody>
</table>
<!-- readme: contributors -end -->

---

## Security

Tracer is designed with production environments in mind:

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
