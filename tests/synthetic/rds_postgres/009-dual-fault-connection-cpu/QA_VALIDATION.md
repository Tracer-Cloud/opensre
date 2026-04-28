# Scenario 009 — Dual Signal with Single Shared Root Cause

## Overview

| Field | Value |
|---|---|
| Instance | `search-prod` |
| Failure Mode | Connection Pool Leak |
| Symptoms | Connection exhaustion + CPU saturation |
| Severity | Critical |

This scenario validates whether the agent can avoid splitting correlated symptoms into separate root causes.

The incident looks like two independent failures:
- connections are near the maximum limit
- CPU is very high

However, both symptoms share one root cause: a connection pool leak.

---

## Ground Truth

- Root Cause Category: `resource_exhaustion`
- Root Cause: connection pool leak
- CPU saturation is downstream of the leaked connections
- Connection exhaustion and CPU saturation are not independent faults
- Storage and replication are not contributing factors

---

## Expected Behaviour

A correct agent must:

- Identify the connection pool leak as the single shared root cause
- Explain that leaked idle/open connections accumulate until the connection ceiling is exhausted
- Explain that leaked connections hold open scan-heavy queries
- Link CPU saturation to the accumulated query load from leaked connections
- Explicitly state that this is not two independent problems
- Avoid introducing storage or replication as contributing causes

---

## Required Reasoning Elements

The response should include:

- Connection pool leak as the root cause
- Idle or open leaked connections
- `Client:ClientRead` wait evidence
- Scan-heavy queries / full-table scans
- A single causal chain linking:
  - pool leak
  - idle/open connections
  - connection exhaustion
  - accumulated query load
  - CPU saturation

---

## Strict Validation Rules

The response must treat CPU saturation as downstream of the connection leak.

The response must not describe connection exhaustion and CPU saturation as separate root causes.

The response must not introduce storage or replication as contributing factors.

---

## Failure Modes

The scenario should fail if the agent:

- Diagnoses two independent problems
- Treats CPU saturation as a separate root cause
- Identifies connection exhaustion but does not explain why CPU is high
- Fails to link CPU saturation to leaked connections holding scan-heavy queries
- Mentions storage as a contributing factor
- Mentions replication as a contributing factor
- Produces a blended explanation without a clear causal chain

---

## Passing Criteria

A correct response:

- Attributes both symptoms to a single connection pool leak
- Explains why CPU saturation follows from the leaked connections
- Uses `Client:ClientRead` as evidence of idle/open connections
- States that fixing the connection pool leak resolves both connection exhaustion and CPU saturation
