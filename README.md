<p align="center">
  <img src="https://github.com/user-attachments/assets/f4ccf4d4-137a-47a8-a000-a6cffccd8485" alt="Tracer Banner"/>
</p>

<p align="center">
  <strong>The open-source SRE agent that investigates and debugs your data pipelines.</strong>
</p>

<p align="center">
  <strong>
    <a href="https://join.slack.com/t/tracer-cloud/shared_invite/zt-3sajw192q-2RCGVzSlp3EPtkh1SXMJHg">Slack</a> ·
    <a href="https://app.tracer.cloud/">Getting Started</a> ·
    <a href="https://tracer.cloud/">Tracer Agent</a> ·
    <a href="https://tracer.cloud/docs/">Docs</a> ·
    <a href="https://trust.tracer.cloud/">Security</a>
  </strong>
</p>

---

## Quick Start

```bash
git clone https://github.com/Tracer-Cloud/open-sre-agent
cd open-sre-agent
make dev
```

Documentation → `/docs`

---

## The Problem

Production data incidents often involve multiple interconnected systems.

Resolving them requires correlating operational signals — logs, metrics, traces, configuration state, and recent changes — across orchestration frameworks, compute engines, and infrastructure.

This investigation process is typically manual and tool-fragmented.

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

| Category | Integrations |
|----------|-------------|
| **Data Platform** | Apache Airflow · Apache Kafka · Apache Spark |
| **Observability** | <img src="app/icons/grafana.webp" width="16"> Grafana · <img src="app/icons/datadog.svg" width="16"> Datadog · <img src="app/icons/cloudwatch.png" width="16"> CloudWatch · <img src="app/icons/sentry.png" width="16"> Sentry |
| **Infrastructure** | <img src="app/icons/kubernetes.png" width="16"> Kubernetes · <img src="app/icons/aws.png" width="16"> AWS · <img src="app/icons/gcp.jpg" width="16"> GCP · <img src="app/icons/azure.png" width="16"> Azure |
| **Dev Tools** | <img src="app/icons/github.webp" width="16"> GitHub |
| **Communication** | <img src="app/icons/slack.png" width="16"> Slack · <img src="app/icons/pagerduty.png" width="16"> PagerDuty |



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

MIT — Tracer-Cloud (see [LICENSE](LICENSE))
