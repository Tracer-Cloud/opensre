# Scenario 005 — Multi-AZ Failover

## Overview

| Field | Value |
|---|---|
| Instance | `payments-prod` |
| Outage Duration | ~45 seconds (RDS promotion); connections recovered by 08:07Z |
| Root Cause Category | `infrastructure` |
| Status | ✅ Resolved |

**Root Cause:** RDS Multi-AZ automatic failover triggered by a health check failure on the primary host.

---

## How to Run

```bash
python -m app.cli tests synthetic --scenario 005-failover
```

---

## The Trap

> ⚠️ This scenario is intentionally misleading.

During the failover window, metrics show:

- Connections → zero
- CPU and I/O → sharp drop

This pattern resembles connection exhaustion or resource saturation — but it is not. These are **symptoms**, not causes.

The true root cause is only visible in **RDS control-plane events**, which must take precedence over metrics.

---

## What Actually Happened

```
08:04:18Z  Health check failure detected on primary host
08:04:21Z  Failover initiated
08:04:58Z  Failover completed (standby promoted)
08:05:04Z  Instance available — workload resumed
```

Total downtime: **~45 seconds** (expected for Multi-AZ failover)

---

## Expected Reasoning

A correct agent should:

- Identify this as an infrastructure-level event
- Recognize a Multi-AZ failover triggered by a health check failure
- Use RDS events as the primary evidence source
- Treat CloudWatch metrics as secondary signals
- The RDS control-plane event timeline must be the decisive signal driving the diagnosis
- Explain the causal chain:

```
health check failure
→ failover initiated
→ standby promoted
→ DNS endpoint updated
→ brief connection drop (~45s)
→ recovery
```

---

## Reviewer Checklist

### ✅ Root Cause
- [ ] Classified as `infrastructure` (not connection/resource exhaustion)
- [ ] Multi-AZ failover explicitly mentioned

### ✅ Evidence
- [ ] `aws_rds_events` explicitly used as the primary reasoning signal
- [ ] Failover diagnosis is derived from the RDS event timeline (not inferred from metrics)
- [ ] Metrics/logs are used only as supporting context
- [ ] Metrics-only reasoning should be considered incorrect

### ✅ Reasoning
- [ ] Health check failure → failover trigger chain explained
- [ ] Connection drop attributed to failover window, not exhaustion

### ✅ Resolution
- [ ] System recognized as already recovered
- [ ] No unnecessary remediation suggested

---

## Common Failure Modes

| Misdiagnosis | Why It's Wrong |
|---|---|
| Connection exhaustion | Connections dropped *because of* failover, not vice versa |
| Resource saturation | CPU/I/O drop is a *symptom* of failover |
| Ongoing outage | System recovered at `08:05:04Z` |
| Metrics-only analysis | Control-plane events are the decisive signal |

---

## Reviewer Notes

This scenario evaluates whether the agent can correctly prioritize control-plane signals (RDS events) over data-plane metrics (CloudWatch).

Correct handling demonstrates:

- Strong signal prioritization
- Accurate causal reasoning
- Understanding of AWS Multi-AZ failover behavior

Agents that rely primarily on metrics without explicitly referencing RDS control-plane events should fail this scenario.

---

## What This Tests

- Signal prioritization (control-plane vs metrics)
- Correct identification of Multi-AZ failover behavior
- Causal reasoning under misleading metric patterns
- Ability to distinguish symptoms from root causes
- Recognition of resolved vs active incidents
