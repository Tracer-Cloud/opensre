<p align="center">
  <img src="https://raw.githubusercontent.com/Tracer-Cloud/open-sre-agent/main/app/icons/BannerGithub.png" alt="Tracer Banner" width="100%">
</p>
<meta name="google-site-verification" content="05niXvrQUSPCpucsCP7t1loNt_Kke3N9i6QIJ_0Gcg8" />
<p align="center">
  <strong>The open-source SRE agent that automatically investigates incidents and finds the root cause, before your team gets paged.
</strong>
</p>
<meta name="google-site-verification" content="I3aNPV9RpFv0aanmy5B22oKjsZ9PCamozvrggjFQJq8" />
<p align="center">
  Open-source SRE agent for automated incident investigation and root cause analysis. Automatically analyzes alerts from Slack, Grafana, Datadog, and other tooling.
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

---

## Quick Start

```bash
git clone https://github.com/Tracer-Cloud/open-sre-agent
cd open-sre-agent
make install
cp .env.example .env
# run opensre onboard to configure your local LLM provider
opensre onboard
make grafana-local-up
make local-grafana-live
```

Want the live local Grafana path to a first RCA report? Start with [docs/local-grafana-live.md](docs/local-grafana-live.md).

Want a generic no-Docker bundled RCA example instead? See [docs/local-rca-demo.md](docs/local-rca-demo.md).

Want help configuring a local LLM provider quickly? Run `opensre onboard` after `make install`.

Want to run the LangGraph dev UI or connect your own systems? See [SETUP.md](SETUP.md).

---

## Why Tracer

When something breaks in production, the investigation is slow because the evidence is scattered. Logs in Datadog, metrics in Grafana, service dependencies in your infra layer, config changes in Git. Each system saw part of what happened, but none of them saw all of it.

So you do it manually. You pull logs, correlate timestamps, ping the colleague who knows the stack and piece together what happened. It takes hours. Under on-call pressure, you ship a patch just to get the system back up.

Tracer connects your systems and runs the investigation automatically. It correlates signals across your stack, builds hypotheses about what went wrong, tests them in parallel, and stops when it has enough confidence to give you a clear answer.

Root cause reports are delivered to Slack out of the box. Want them in PagerDuty, OpsGenie, or wherever your team works? Adding a new integration is one of the most straightforward contributions you can make.

---

## How Tracer Works

<img width="2444" height="881" alt="How it works" src="https://github.com/user-attachments/assets/dd79d5ab-e2a4-4ddf-a869-7afa4d7a10dc" />

### Investigation Workflow

When an alert fires, Tracer:

1. Ingests the alert from monitoring or incident systems
2. Assembles context from logs, metrics, configs, and dependencies
3. Frames potential failure modes
4. Executes investigation queries across connected systems
5. Evaluates hypotheses based on collected evidence
6. Delivers a root cause report and recommended next actions

---

## Capabilities

- Structured incident investigation
- Parallel hypothesis execution
- Cross-system failure correlation
- Evidence-backed root cause analysis
- Alert triage and MTTR reduction

Designed for production data engineering teams operating complex data platforms.

---

## Integrations

Tracer integrates with the systems that power modern data platforms.

| Category           | Integrations                                                                                                                                                                                                                   |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Data Platform**  | Apache Airflow · Apache Kafka · Apache Spark                                                                                                                                                                                   |
| **Observability**  | <img src="app/icons/grafana.webp" width="16"> Grafana · <img src="app/icons/datadog.svg" width="16"> Datadog · <img src="app/icons/cloudwatch.png" width="16"> CloudWatch · <img src="app/icons/sentry.png" width="16"> Sentry |
| **Infrastructure** | <img src="app/icons/kubernetes.png" width="16"> Kubernetes · <img src="app/icons/aws.png" width="16"> AWS · <img src="app/icons/gcp.jpg" width="16"> GCP · <img src="app/icons/azure.png" width="16"> Azure                    |
| **Dev Tools**      | <img src="app/icons/github.webp" width="16"> GitHub                                                                                                                                                                            |
| **Communication**  | <img src="app/icons/slack.png" width="16"> Slack · <img src="app/icons/pagerduty.png" width="16"> PagerDuty                                                                                                                    |

---

## Design Principles

- Deterministic investigations
- Evidence-backed conclusions
- Parallel hypothesis testing
- Production-first design
- Fully auditable workflows

---

## Contributing

We welcome contributors interested in:

- Data platform integrations
- Investigation engines
- Observability tooling
- Deterministic AI systems

Thanks goes to these amazing people:

<a href="https://github.com/Tracer-Cloud/open-sre-agent/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Tracer-Cloud/open-sre-agent" />
</a>

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Security

Tracer interacts with production systems.

Recommended:

- Use read-only credentials
- Restrict network exposure
- Log all investigations
- Review reports before automated remediation

See [SECURITY.md](SECURITY.md) for details.

---

## License

Apache License 2.0 — Tracer-Cloud (see [LICENSE](LICENSE))
