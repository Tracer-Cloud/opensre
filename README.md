<div align="center">

<p align="center">
  <img width="2136" height="476" alt="github-readme-tracer-banner" src="https://github.com/user-attachments/assets/fac67ac2-e40e-4d58-8421-829ed0ce2a4d" />
</p>

<h1>Open SRE — Build Your Own AI SRE Agents</h1>

<p>The open-source framework that AI SRE agents are built on. Connect the tools you already run, define your own workflows, and let agents handle incident investigation and root cause analysis - your way, on your infrastructure.</p>

<p>
  <a href="https://github.com/Tracer-Cloud/opensre/stargazers"><img src="https://img.shields.io/github/stars/Tracer-Cloud/opensre?style=flat-square&color=FF6B00" alt="Stars"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="License"></a>
  <a href="https://github.com/Tracer-Cloud/opensre/blob/main/.github/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Tracer-Cloud/opensre/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/open%20source-forever-brightgreen?style=flat-square" alt="Open Source">
</p>

<p align="center">
  <strong>
    <a href="https://tracer-cloud.slack.com/archives/C0AL8S64936">Slack</a> ·
    <a href="https://app.tracer.cloud/">Getting Started</a> ·
    <a href="https://tracer.cloud/">Tracer Agent</a> ·
    <a href="https://tracer.cloud/docs/">Docs</a> ·
    <a href="docs/FAQ.md">FAQ</a> ·
    <a href="https://trust.tracer.cloud/">Security</a>
  </strong>
</p>

</div>

---

## Quick Start

> **New to Tracer?** See [SETUP.md](SETUP.md) for detailed platform-specific setup instructions, including Windows setup, environment configuration, and more.

```bash
git clone https://github.com/Tracer-Cloud/opensre
cd opensre
make install
# run opensre onboard to configure your local LLM provider
# and optionally validate/save Grafana, Datadog, Slack, AWS, GitHub MCP, and Sentry integrations
opensre onboard
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

**Choose a path:**

- 🏃 **Local** - Run Tracer locally with a live Grafana environment, no cloud infra needed
- ☁️ **Self-hosted** - Deploy to your own infrastructure for continuous monitoring
- 🔌 **LangGraph / LlamaIndex** - Use Tracer as an agent in your existing AI stack (see [Agent Docs](https://tracer.cloud/docs))

---

## Why Tracer?

When something breaks in production, the pressure is immediate - but the evidence is scattered. Logs in Datadog. Metrics in Grafana. Runbooks in Notion. Context in Slack threads already 200 messages deep.

**Tracer is the open-source answer to that chaos.** It's an AI SRE agent that correlates signals across your entire stack, reasons through root cause, and surfaces a clear diagnosis - in the time it used to take just to _find_ the right dashboard.

Unlike closed SRE platforms, Tracer is **fully open source and self-hostable**. No vendor lock-in. No black-box reasoning. You own the agent, the data, and the workflow.

> Whether you're an SRE triaging a P0, a platform engineer building internal tooling, a developer who just got paged, or an EM trying to reduce MTTR - Tracer works for your whole team.

**Built in the open. Trusted in production.**

---

## How Tracer Works

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

Tracer integrates with the systems that power modern data platforms.

| Category           | Integrations                                                                                                                                                                                                                                                   |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Data Platform**  | Apache Airflow · Apache Kafka · Apache Spark                                                                                                                                                                                                                   |
| **Observability**  | <img src="docs/assets/icons/grafana.webp" width="16"> Grafana · <img src="docs/assets/icons/datadog.svg" width="16"> Datadog · <img src="docs/assets/icons/cloudwatch.png" width="16"> CloudWatch · <img src="docs/assets/icons/sentry.png" width="16"> Sentry |
| **Infrastructure** | <img src="docs/assets/icons/kubernetes.png" width="16"> Kubernetes · <img src="docs/assets/icons/aws.png" width="16"> AWS · <img src="docs/assets/icons/gcp.jpg" width="16"> GCP · <img src="docs/assets/icons/azure.png" width="16"> Azure                    |
| **Dev Tools**      | <img src="docs/assets/icons/github.webp" width="16"> GitHub                                                                                                                                                                                                    |
| **Communication**  | <img src="docs/assets/icons/slack.png" width="16"> Slack · <img src="docs/assets/icons/pagerduty.png" width="16"> PagerDuty                                                                                                                                    |

---

## Design Principles

We've tried to be intentional about how Tracer is built, not just what it does.

- **Real-world testing over mocks** - we're big fans of end-to-end testing against real environments, whether that's a local observability stack (Grafana, Prometheus) or actual cloud infrastructure. If it doesn't work in the real world, it doesn't count.
- **Show your work** - every conclusion Tracer reaches should be traceable back to the signals that led there. No black boxes.
- **Bring your own everything** - your LLM, your tools, your runbooks. Tracer fits around your stack, not the other way around.
- **Open by default** - the code is yours to read, fork, and improve. We'd rather have a smaller, more trusted tool than a bigger, opaque one.

---

## Contributing

Tracer is community-built. Every integration, improvement, and bug fix makes it better for thousands of engineers. We actively review PRs and welcome contributors of all experience levels.

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
        <a href="https://github.com/anandgupta1202">
            <img src="https://avatars.githubusercontent.com/u/39819996?v=4" width="100;" alt="anandgupta1202"/>
            <br />
            <sub><b>anandgupta1202</b></sub>
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
        <a href="https://github.com/w3joe">
            <img src="https://avatars.githubusercontent.com/u/84664178?v=4" width="100;" alt="w3joe"/>
            <br />
            <sub><b>w3joe</b></sub>
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
        <a href="https://github.com/zeel2104">
            <img src="https://avatars.githubusercontent.com/u/72783325?v=4" width="100;" alt="zeel2104"/>
            <br />
            <sub><b>zeel2104</b></sub>
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
        <a href="https://github.com/kylie-tracer">
            <img src="https://avatars.githubusercontent.com/u/256781109?v=4" width="100;" alt="kylie-tracer"/>
            <br />
            <sub><b>kylie-tracer</b></sub>
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
        <a href="https://github.com/aliya-tracer">
            <img src="https://avatars.githubusercontent.com/u/233726347?v=4" width="100;" alt="aliya-tracer"/>
            <br />
            <sub><b>aliya-tracer</b></sub>
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
        <a href="https://github.com/davincios">
            <img src="https://avatars.githubusercontent.com/u/33206282?v=4" width="100;" alt="davincios"/>
            <br />
            <sub><b>davincios</b></sub>
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
What we collect: command name, CLI version, Python version, and OS family.
A randomly generated anonymous ID is created on first run and stored in
`~/.config/opensre/`. We never collect alert contents, hostnames,
credentials, or any personally identifiable information.

To opt out, set the environment variable before running:

```bash
export OPENSRE_NO_TELEMETRY=1
```

## License

Apache 2.0 - see [LICENSE](LICENSE) for details.
