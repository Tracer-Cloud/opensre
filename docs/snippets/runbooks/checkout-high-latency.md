# Checkout high latency

## Goal

Identify whether checkout latency comes from traffic, a recent deployment, or a
downstream dependency. Prefer reversible mitigation only after the evidence points
to one cause.

## Diagnostic sequence

1. Confirm the alert time window, environment, region, and affected checkout instances.
2. Compare request rate, error rate, and p95/p99 latency with the preceding healthy window.
3. Check deployments and configuration changes immediately before the latency increase.
4. Inspect database connection saturation, payment-provider latency, and queue backlog.
5. Correlate one slow request across application logs and traces when those sources exist.

## Decision points

- If latency starts immediately after one deployment and the same dependency remains
  healthy, propose a rollback through the normal approval path.
- If a dependency is saturated, identify its owner and propose load shedding or capacity
  changes through the normal approval path.
- If evidence is incomplete, state which source is missing; do not infer a root cause.

## Closeout

Report the checks performed, evidence observed, steps skipped, and any remediation that
still needs approval. Keep guidance from this document separate from observed facts.
