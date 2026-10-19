# OTel payment triage pilot

Follow [the setup and fault/recovery walkthrough](../../docs/guides/otel-payment-triage.mdx).

The payment application is the unmodified OpenTelemetry Demo 3.1.0 image,
built from [upstream commit dedc0178918e260823323b8d95005a8cb924b007](https://github.com/open-telemetry/opentelemetry-demo/tree/dedc0178918e260823323b8d95005a8cb924b007).
The local JavaScript file is only a synthetic gRPC traffic generator.
All container images are digest-pinned. The ClickStack distribution runs with
a complete custom configuration and legacy Map tables; no HyperDX application,
SDK, OpAMP supervisor, or MongoDB is started.
