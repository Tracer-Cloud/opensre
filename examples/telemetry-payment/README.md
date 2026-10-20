# OTel payment triage pilot

Follow [the setup and fault/recovery walkthrough](../../docs/guides/otel-payment-triage.mdx).

```text
Payment service → OTLP → Collector → ClickHouse
OpenSRE watch → reads ClickHouse → incident report → Telegram (optional)
```

OpenSRE checks the payment error rate every 30 seconds and investigates new
incidents. It reads stored telemetry, not the Collector's health endpoint.
The guide explains both configuration files, the detection rule, and what
healthy, failing, and missing-data checks do.

The payment application is the unmodified OpenTelemetry Demo 3.1.0 image,
built from [upstream commit dedc0178918e260823323b8d95005a8cb924b007](https://github.com/open-telemetry/opentelemetry-demo/tree/dedc0178918e260823323b8d95005a8cb924b007).
The local JavaScript file is only a synthetic gRPC traffic generator.
All container images are digest-pinned. The ClickStack distribution runs with
a complete custom configuration and legacy Map tables; no HyperDX application,
SDK, OpAMP supervisor, or MongoDB is started.
