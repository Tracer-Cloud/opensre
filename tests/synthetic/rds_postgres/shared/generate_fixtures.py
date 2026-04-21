#!/usr/bin/env python3
"""Generate expanded aws_cloudwatch_metrics.json fixtures for all RDS synthetic scenarios.

Applies three layers of realism:
  Phase 1 — Add missing baseline metrics (15 series) to each faulty scenario.
  Phase 2 — Stagger fault signal onset by 1-3 minutes to match causal ordering;
             also adjust existing CPU/connection confounders to a "blip" pattern.
  Phase 3 — Jitter all baseline values (no suspiciously flat series).

Run from the repo root:
  python3 tests/synthetic/rds_postgres/shared/generate_fixtures.py

For 000-healthy, writes per-metric files to aws_cloudwatch_metrics/ directory.
For other scenarios, writes to aws_cloudwatch_metrics.json (single file).
Idempotent: safe to re-run.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

SUITE_DIR = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Deterministic noise helpers (no external deps)
# ---------------------------------------------------------------------------


def _lcg(seed: int) -> int:
    """Linear congruential generator step."""
    return (seed * 1664525 + 1013904223) & 0xFFFFFFFF


def _rand_float(seed: int) -> tuple[float, int]:
    """Return uniform [0,1) float and next seed."""
    seed = _lcg(seed)
    return seed / 0x100000000, seed


def _gauss(seed: int, mu: float = 0.0, sigma: float = 1.0) -> tuple[float, int]:
    """Box-Muller transform for a single normal sample."""
    u1, seed = _rand_float(seed)
    u2, seed = _rand_float(seed)
    u1 = max(u1, 1e-10)
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z, seed


def jittered_series(
    seed: int,
    mean: float,
    n: int,
    noise_frac: float = 0.10,
    floor: float | None = None,
    ceil: float | None = None,
    round_to: int = 2,
) -> list[float]:
    """Generate n values via a mean-reverting random walk around `mean`.

    noise_frac controls the per-step standard deviation as a fraction of mean.
    """
    sigma = mean * noise_frac
    vals: list[float] = []
    v = mean
    for _ in range(n):
        delta, seed = _gauss(seed, 0.0, sigma * 0.7)
        revert = (mean - v) * 0.3  # mean-reversion force
        v = v + delta + revert
        if floor is not None:
            v = max(v, floor)
        if ceil is not None:
            v = min(v, ceil)
        vals.append(round(v, round_to))
    return vals


def sawtooth_series(
    seed: int,
    mean: float,
    n: int,
    drop_frac: float = 0.02,
    gc_period: int = 5,
    noise_frac: float = 0.005,
    round_to: int = 0,
) -> list[float]:
    """GC-like sawtooth: gradual decline then jump back up."""
    vals: list[float] = []
    v = mean
    for i in range(n):
        noise, seed = _gauss(seed, 0.0, mean * noise_frac)
        v = v - mean * drop_frac + noise
        if (i + 1) % gc_period == 0:
            jump, seed = _gauss(seed, mean * gc_period * drop_frac, mean * 0.01)
            v = v + abs(jump)
        v = max(v, mean * 0.85)
        vals.append(round(v, int(round_to)))
    return vals


def ramp_then_flat(
    seed: int,
    base: float,
    peak: float,
    ramp_start: int,
    ramp_end: int,
    n: int,
    noise_frac: float = 0.05,
    round_to: int = 2,
) -> list[float]:
    """Ramp from base to peak between ramp_start and ramp_end, flat elsewhere."""
    vals: list[float] = []
    for i in range(n):
        if i < ramp_start:
            target = base
        elif i >= ramp_end:
            target = peak
        else:
            frac = (i - ramp_start) / (ramp_end - ramp_start)
            target = base + (peak - base) * frac
        noise, seed = _gauss(seed, 0.0, target * noise_frac)
        vals.append(round(target + noise, round_to))
    return vals


def flat_then_collapse(
    seed: int,
    start_val: float,
    end_val: float,
    collapse_start: int,
    n: int,
    noise_frac: float = 0.02,
    round_to: int = 0,
) -> list[float]:
    """Flat until collapse_start, then monotonically decline to end_val."""
    vals: list[float] = []
    v = start_val
    drop_per_step = (start_val - end_val) / max(n - collapse_start, 1)
    for i in range(n):
        if i < collapse_start:
            noise, seed = _gauss(seed, 0.0, start_val * noise_frac)
            vals.append(round(start_val + noise, int(round_to)))
        else:
            v = v - drop_per_step
            noise, seed = _gauss(seed, 0.0, abs(drop_per_step) * 0.1)
            vals.append(round(max(end_val, v + noise), int(round_to)))
    return vals


def blip_series(
    seed: int,
    baseline: float,
    peak: float,
    blip_start: int,
    blip_end: int,
    n: int,
    noise_frac: float = 0.08,
    round_to: int = 2,
) -> list[float]:
    """Flat baseline, spike during [blip_start, blip_end), return to baseline."""
    vals: list[float] = []
    for i in range(n):
        if blip_start <= i < blip_end:
            target = peak
        else:
            target = baseline
        noise, seed = _gauss(seed, 0.0, target * noise_frac)
        vals.append(round(max(0.0, target + noise), round_to))
    return vals


def timestamps(start_iso: str, n: int, period_sec: int = 60) -> list[str]:
    dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    return [
        (dt + timedelta(seconds=i * period_sec)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Baseline metric definitions
# ---------------------------------------------------------------------------
# Each entry: (metric_name, id_suffix, dimension_instance, stat, unit, mean, noise_frac, special)
# special: "sawtooth" | "flat" | None (random walk)

BASELINE_DEFS: list[dict] = [
    {
        "metric_name": "ReadIOPS",
        "id": "m_read_iops",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Count/Second",
        "mean": 1870.0,
        "noise": 0.08,
    },
    {
        "metric_name": "NetworkReceiveThroughput",
        "id": "m_net_rx",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Bytes/Second",
        "mean": 4194304.0,
        "noise": 0.10,
    },  # ~4 MB/s
    {
        "metric_name": "DiskQueueDepth",
        "id": "m_disk_queue",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Count",
        "mean": 0.10,
        "noise": 0.20,
        "floor": 0.01,
    },
    {
        "metric_name": "CommitThroughput",
        "id": "m_commit_tput",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Count/Second",
        "mean": 120.0,
        "noise": 0.09,
    },
    {
        "metric_name": "CommitLatency",
        "id": "m_commit_lat",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Milliseconds",
        "mean": 2.0,
        "noise": 0.12,
        "floor": 0.5,
    },
    {
        "metric_name": "ReadLatency",
        "id": "m_read_lat",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Milliseconds",
        "mean": 1.0,
        "noise": 0.15,
        "floor": 0.2,
    },
    {
        "metric_name": "WriteLatency",
        "id": "m_write_lat",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Milliseconds",
        "mean": 1.2,
        "noise": 0.13,
        "floor": 0.2,
    },
    {
        "metric_name": "NetworkTransmitThroughput",
        "id": "m_net_tx",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Bytes/Second",
        "mean": 8388608.0,
        "noise": 0.10,
    },  # ~8 MB/s
    {
        "metric_name": "TransactionLogsGeneration",
        "id": "m_txn_logs_base",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Bytes/Second",
        "mean": 4194304.0,
        "noise": 0.08,
    },  # ~4 MB/s
    {
        "metric_name": "FreeableMemory",
        "id": "m_freeable_mem",
        "dim": "payments-prod",
        "stat": "Minimum",
        "unit": "Bytes",
        "mean": 39728447488.0,
        "noise": 0.0,
        "special": "sawtooth",
    },  # ~37 GB
    {
        "metric_name": "WriteIOPS",
        "id": "m_write_iops_base",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Count/Second",
        "mean": 980.0,
        "noise": 0.12,
    },
    {
        "metric_name": "CPUUtilization",
        "id": "m_cpu_base",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Percent",
        "mean": 18.0,
        "noise": 0.15,
        "floor": 5.0,
        "ceil": 35.0,
    },
    {
        "metric_name": "DatabaseConnections",
        "id": "m_db_conn_base",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Count",
        "mean": 93.0,
        "noise": 0.10,
        "floor": 60.0,
        "ceil": 130.0,
    },
    {
        "metric_name": "FreeStorageSpace",
        "id": "m_free_storage",
        "dim": "payments-prod",
        "stat": "Minimum",
        "unit": "Bytes",
        "mean": 214748364800.0,
        "noise": 0.0,
        "special": "flat",
    },  # 200 GB
    {
        "metric_name": "ReplicaLag",
        "id": "m_replica_lag_base",
        "dim": "payments-prod-replica-1",
        "stat": "Maximum",
        "unit": "Seconds",
        "mean": 1.2,
        "noise": 0.20,
        "floor": 0.4,
        "ceil": 3.0,
    },
    # --- Decoy metrics (adversarial noise layer) ---
    # Each is real and observable but not the root cause in any current scenario.
    # They create plausible-looking false leads the agent must consider and dismiss.
    {
        "metric_name": "SwapUsage",
        "id": "m_swap",
        "dim": "payments-prod",
        "stat": "Maximum",
        "unit": "Bytes",
        "mean": 83886080.0,
        "noise": 0.08,
        "floor": 0.0,
    },  # ~80 MB — visible but not alarming
    {
        "metric_name": "BinLogDiskUsage",
        "id": "m_binlog",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Bytes",
        "mean": 524288000.0,
        "noise": 0.07,
    },  # ~500 MB binlog accumulation
    {
        "metric_name": "MaximumUsedTransactionIDs",
        "id": "m_max_xid",
        "dim": "payments-prod",
        "stat": "Maximum",
        "unit": "Count",
        "mean": 198000000.0,
        "noise": 0.002,
        "floor": 190000000.0,
    },  # ~198M XID — healthy but drifting upward
    {
        "metric_name": "ReadThroughput",
        "id": "m_read_tput",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Bytes/Second",
        "mean": 52428800.0,
        "noise": 0.09,
    },  # ~50 MB/s read activity
    {
        "metric_name": "WriteThroughput",
        "id": "m_write_tput",
        "dim": "payments-prod",
        "stat": "Average",
        "unit": "Bytes/Second",
        "mean": 20971520.0,
        "noise": 0.11,
    },  # ~20 MB/s write activity
]

# ---------------------------------------------------------------------------
# Per-scenario configuration
# ---------------------------------------------------------------------------
# start, n: time window
# existing: metrics already in the fixture (won't be regenerated from baseline)
# confounders: extra metric series to add as red-herring (spec below)
# onset_patches: modifications to existing series to stagger fault onset

SCENARIOS: dict[str, dict] = {
    "001-replication-lag": {
        "start": "2026-03-26T11:20:00Z",
        "n": 15,
        "existing": {
            "ReplicaLag",
            "WriteIOPS",
            "TransactionLogsGeneration",
            "DatabaseConnections",
            "CPUUtilization",
            "FreeStorageSpace",
            "FreeableMemory",
            "NetworkTransmitThroughput",
        },
        # Stagger: WriteIOPS+TransactionLogs spike first (min 0), ReplicaLag climbs from min 2
        "onset_patches": {
            "ReplicaLag": {
                "type": "ramp_delayed",
                # flat for first 2 mins at baseline, then existing ramp
                "flat_until": 2,
                "flat_val": 1.2,
            },
        },
    },
    "002-connection-exhaustion": {
        "start": "2026-03-26T11:50:00Z",
        "n": 15,
        "existing": {
            "DatabaseConnections",
            "CPUUtilization",
            "ReplicaLag",
            "FreeStorageSpace",
            "WriteLatency",
            "ReadIOPS",
        },
        # Need: WriteIOPS (baseline), NetworkTx, DiskQueue, CommitTput, CommitLat,
        #        ReadLat, NetRx, TxnLogs, FreeableMemory, NetworkTransmitThroughput
        # CPU already present — it's the confounder (mild elevation ~35%)
        # Stagger: connections start climbing immediately; CPU elevation begins at min 4
        "onset_patches": {
            "CPUUtilization": {
                "type": "ramp_delayed",
                "flat_until": 4,
                "flat_val": 18.0,
            },
        },
    },
    "003-storage-full": {
        "start": "2026-03-27T02:00:00Z",
        "n": 15,
        "existing": {
            "FreeStorageSpace",
            "WriteIOPS",
            "CPUUtilization",
            "WriteLatency",
        },
        # Need: DatabaseConnections, ReplicaLag, ReadIOPS, FreeableMemory,
        #        NetworkTx, NetRx, DiskQueue, CommitTput, CommitLat, ReadLat, TxnLogs
        # Stagger: WriteIOPS elevated from min 0; FreeStorage starts declining from min 2;
        #          WriteLatency stays low until min 10
        "onset_patches": {
            "FreeStorageSpace": {
                "type": "flat_then_collapse",
                "flat_until": 2,
            },
            "WriteLatency": {
                "type": "flat_then_ramp",
                "flat_until": 10,
                "flat_val": 1.2,
            },
        },
    },
    "004-cpu-saturation-bad-query": {
        "start": "2026-03-27T14:00:00Z",
        "n": 20,
        "existing": {
            "CPUUtilization",
            "ReadIOPS",
            "DatabaseConnections",
        },
        # Need: WriteIOPS, ReplicaLag, FreeStorageSpace, FreeableMemory,
        #        NetworkTx, NetRx, DiskQueue, CommitTput, CommitLat, ReadLat,
        #        WriteLatency, TxnLogs, CommitThroughput
        # DatabaseConnections is the confounder (already in fixture, spikes mid-window)
        # Stagger: ReadIOPS spikes immediately (min 0); CPU rises from min 1
        "onset_patches": {
            "CPUUtilization": {
                "type": "ramp_delayed",
                "flat_until": 1,
                "flat_val": 18.0,
            },
        },
    },
    "005-failover": {
        "start": "2026-03-27T08:00:00Z",
        "n": 15,
        "existing": {
            "DatabaseConnections",
            "CPUUtilization",
            "WriteIOPS",
        },
        # Need: ReplicaLag (pre-failover blip confounder), ReadIOPS,
        #        FreeStorageSpace, FreeableMemory, NetworkTx, NetRx,
        #        DiskQueue, CommitTput, CommitLat, ReadLat, WriteLatency, TxnLogs
        # Confounder: ReplicaLag blips just before failover (health check degradation)
        # Skip baseline ReplicaLag — only the blip series should appear
        "skip_baseline": {"ReplicaLag"},
        # Failover event at ~min 4:18 — metrics drop simultaneously at min 4-5
        "extra_series": [
            {
                "id": "m_replica_lag_confounder",
                "label": "ReplicaLag",
                "metric_name": "ReplicaLag",
                "dimensions": [
                    {"Name": "DBInstanceIdentifier", "Value": "payments-prod-replica-1"}
                ],
                "stat": "Maximum",
                "unit": "Seconds",
                "type": "blip",
                "baseline": 1.2,
                "peak": 28.0,
                "blip_start": 3,
                "blip_end": 6,
                "noise_frac": 0.10,
                "seed_offset": 9901,
            },
        ],
    },
    "006-replication-lag-cpu-redherring": {
        "start": "2026-03-27T10:00:00Z",
        "n": 20,
        "existing": {
            "ReplicaLag",
            "WriteIOPS",
            "CPUUtilization",
            "TransactionLogsGeneration",
        },
        # CPU confounder already present (analytics SELECT). No changes to existing.
        # Need: DatabaseConnections, ReadIOPS, FreeStorageSpace, FreeableMemory,
        #        NetworkTx, NetRx, DiskQueue, CommitTput, CommitLat, ReadLat,
        #        WriteLatency, NetworkReceiveThroughput
    },
    "007-connection-pressure-noisy-healthy": {
        "start": "2026-03-27T16:00:00Z",
        "n": 20,
        "existing": {
            "DatabaseConnections",
            "CPUUtilization",
            "ReadLatency",
        },
        # All existing metrics are the "confounders" (they look worrying but aren't faults).
        # Need: WriteIOPS, ReplicaLag, FreeStorageSpace, FreeableMemory,
        #        NetworkTx, NetRx, DiskQueue, CommitTput, CommitLat, WriteLatency,
        #        TxnLogs, ReadIOPS
    },
    "008-storage-full-missing-metric": {
        "start": "2026-03-27T03:00:00Z",
        "n": 15,
        "existing": {
            "WriteIOPS",
            "WriteLatency",
            "CPUUtilization",
        },
        # FreeStorageSpace intentionally ABSENT (the missing-metric scenario).
        # Need: DatabaseConnections, ReplicaLag, ReadIOPS, FreeableMemory,
        #        NetworkTx, NetRx, DiskQueue, CommitTput, CommitLat, ReadLat, TxnLogs
        # Confounder: WriteLatency elevation from a concurrent bulk DELETE (brief)
        # — already handled by staggering: WriteLatency starts low, ramps mid-window
        "onset_patches": {
            "WriteLatency": {
                "type": "flat_then_ramp",
                "flat_until": 3,
                "flat_val": 1.2,
            },
        },
        # Do NOT add FreeStorageSpace — that's the missing-metric by design
        "skip_baseline": {"FreeStorageSpace"},
    },
    "009-dual-fault-connection-cpu": {
        "start": "2026-03-27T20:00:00Z",
        "n": 20,
        "existing": {
            "DatabaseConnections",
            "CPUUtilization",
            "ReadIOPS",
        },
        # Both root causes are already in existing metrics (no artificial confounders).
        # Need: WriteIOPS, ReplicaLag, FreeStorageSpace, FreeableMemory,
        #        NetworkTx, NetRx, DiskQueue, CommitTput, CommitLat, ReadLat,
        #        WriteLatency, TxnLogs
    },
    "010-replication-lag-missing-metric": {
        "start": "2026-03-28T07:00:00Z",
        "n": 20,
        "existing": {
            "WriteIOPS",
            "TransactionLogsGeneration",
            "CPUUtilization",
        },
        # ReplicaLag intentionally ABSENT (the missing-metric scenario).
        # Need: DatabaseConnections, ReadIOPS, FreeStorageSpace, FreeableMemory,
        #        NetworkTx, NetRx, DiskQueue, CommitTput, CommitLat, ReadLat,
        #        WriteLatency, NetworkReceiveThroughput
        "skip_baseline": {"ReplicaLag"},
        # Stagger: WriteIOPS spikes from min 0; TransactionLogs climbs from min 1
        "onset_patches": {
            "TransactionLogsGeneration": {
                "type": "ramp_delayed",
                "flat_until": 1,
                "flat_val": 4194304.0,  # ~4 MB/s baseline
            },
        },
    },
}


def _make_seed(scenario_id: str, metric_name: str) -> int:
    """Deterministic seed from scenario + metric name."""
    h = 0
    for c in scenario_id + ":" + metric_name:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h or 1


def _gen_baseline_series(defn: dict, scenario_id: str, n: int, start_iso: str) -> dict:
    seed = _make_seed(scenario_id, defn["metric_name"])
    ts = timestamps(start_iso, n)
    special = defn.get("special")
    if special == "flat":
        values = [defn["mean"]] * n
    elif special == "sawtooth":
        values = sawtooth_series(seed, defn["mean"], n, noise_frac=0.003, round_to=0)
    else:
        values = jittered_series(
            seed,
            defn["mean"],
            n,
            noise_frac=defn.get("noise", 0.10),
            floor=defn.get("floor"),
            ceil=defn.get("ceil"),
        )

    # Use metric_name as the id if there's only one series for that metric
    # but use the explicit id if provided (avoids collision when adding confounder blip)
    return {
        "id": defn["id"],
        "label": defn["metric_name"],
        "metric_name": defn["metric_name"],
        "dimensions": [{"Name": "DBInstanceIdentifier", "Value": defn["dim"]}],
        "stat": defn["stat"],
        "unit": defn["unit"],
        "status_code": "Complete",
        "timestamps": ts,
        "values": values,
    }


def _patch_onset(series: dict, patch: dict, n: int, start_iso: str) -> dict:
    """Apply an onset-stagger patch to an existing series."""
    patch_type = patch["type"]
    old_values = series["values"]

    if patch_type == "ramp_delayed":
        flat_until = patch["flat_until"]
        flat_val = patch["flat_val"]
        # Replace the first flat_until values with the flat_val (jittered)
        seed = _make_seed("onset", series["metric_name"] + start_iso)
        new_values = list(old_values)
        for i in range(flat_until):
            noise, seed = _gauss(seed, 0.0, flat_val * 0.05)
            new_values[i] = round(flat_val + noise, 2)
        series["values"] = new_values

    elif patch_type == "flat_then_collapse":
        flat_until = patch["flat_until"]
        start_val = old_values[flat_until]  # first value that was declining
        # The original series declines monotonically — make it flat for first flat_until mins
        seed = _make_seed("onset_collapse", series["metric_name"] + start_iso)
        new_values = list(old_values)
        for i in range(flat_until):
            noise, seed = _gauss(seed, 0.0, start_val * 0.005)
            new_values[i] = round(start_val + abs(noise), 0)
        series["values"] = new_values

    elif patch_type == "flat_then_ramp":
        flat_until = patch["flat_until"]
        flat_val = patch["flat_val"]
        seed = _make_seed("onset_ramp", series["metric_name"] + start_iso)
        new_values = list(old_values)
        for i in range(min(flat_until, len(new_values))):
            noise, seed = _gauss(seed, 0.0, flat_val * 0.08)
            new_values[i] = round(flat_val + noise, 3)
        series["values"] = new_values

    return series


def process_scenario(scenario_id: str, config: dict) -> None:
    scenario_dir = SUITE_DIR / scenario_id
    metrics_path = scenario_dir / "aws_cloudwatch_metrics.json"

    data = json.loads(metrics_path.read_text())
    existing_results = data["metric_data_results"]
    existing_names = {s["metric_name"] for s in existing_results}

    n = config["n"]
    start = config["start"]
    skip_baseline = config.get("skip_baseline", set())

    # -----------------------------------------------------------------------
    # Phase 3 (onset patches) — patch EXISTING series timing
    # -----------------------------------------------------------------------
    onset_patches = config.get("onset_patches", {})
    for series in existing_results:
        patch = onset_patches.get(series["metric_name"])
        if patch:
            _patch_onset(series, patch, n, start)

    # -----------------------------------------------------------------------
    # Phase 1 — Add missing baseline metrics
    # -----------------------------------------------------------------------
    new_series: list[dict] = []
    for defn in BASELINE_DEFS:
        metric_name = defn["metric_name"]
        if metric_name in existing_names:
            continue  # already present in scenario
        if metric_name in skip_baseline:
            continue  # intentionally absent (e.g. FreeStorageSpace in 008)
        # Avoid adding a "base" version of a metric that has a different id when
        # an extra_series with the same metric_name will be added
        new_series.append(_gen_baseline_series(defn, scenario_id, n, start))

    # -----------------------------------------------------------------------
    # Phase 2 — Add extra confounder series (e.g. ReplicaLag blip for 005)
    # -----------------------------------------------------------------------
    extra_series_specs = config.get("extra_series", [])
    for spec in extra_series_specs:
        seed = _make_seed(scenario_id, spec["metric_name"] + str(spec.get("seed_offset", 0)))
        ts = timestamps(start, n)
        if spec["type"] == "blip":
            values = blip_series(
                seed,
                baseline=spec["baseline"],
                peak=spec["peak"],
                blip_start=spec["blip_start"],
                blip_end=spec["blip_end"],
                n=n,
                noise_frac=spec.get("noise_frac", 0.08),
            )
        else:
            raise ValueError(f"Unknown extra_series type: {spec['type']}")

        new_series.append(
            {
                "id": spec["id"],
                "label": spec["metric_name"],
                "metric_name": spec["metric_name"],
                "dimensions": [
                    {
                        "Name": "DBInstanceIdentifier",
                        "Value": spec.get("dim", "payments-prod-replica-1"),
                    }
                ],
                "stat": spec["stat"],
                "unit": spec["unit"],
                "status_code": "Complete",
                "timestamps": ts,
                "values": values,
            }
        )

    data["metric_data_results"] = existing_results + new_series
    metrics_path.write_text(json.dumps(data, indent=2))
    total = len(data["metric_data_results"])
    added = len(new_series)
    print(f"  {scenario_id}: {total - added} existing + {added} added = {total} series")


def generate_shared_baseline() -> None:
    """Write shared/baseline_metrics.json using the 000-healthy time window as reference."""
    out: list[dict] = []
    n = 15
    start = "2026-03-26T10:00:00Z"
    for defn in BASELINE_DEFS:
        series = _gen_baseline_series(defn, "000-healthy", n, start)
        out.append(series)

    shared_dir = Path(__file__).parent
    (shared_dir / "baseline_metrics.json").write_text(
        json.dumps({"metric_data_results": out}, indent=2)
    )
    print(f"  shared/baseline_metrics.json: {len(out)} series written")


def generate_healthy_per_metric_files() -> None:
    """Write 000-healthy/aws_cloudwatch_metrics_<Metric>.json files."""
    n = 15
    start = "2026-03-26T10:00:00Z"
    out_dir = SUITE_DIR / "000-healthy"

    envelope = {
        "namespace": "AWS/RDS",
        "period": 60,
        "start_time": start,
        "end_time": "2026-03-26T10:15:00Z",
    }
    (out_dir / "aws_cloudwatch_metrics_envelope.json").write_text(
        json.dumps(envelope, indent=2) + "\n"
    )

    for defn in BASELINE_DEFS:
        series = _gen_baseline_series(defn, "000-healthy", n, start)
        fname = f"aws_cloudwatch_metrics_{defn['metric_name']}.json"
        (out_dir / fname).write_text(json.dumps(series, indent=2) + "\n")

    print(f"  000-healthy/: {len(BASELINE_DEFS)} aws_cloudwatch_metrics_*.json files + envelope")


def main() -> None:
    print("=== Generating shared baseline ===")
    generate_shared_baseline()

    print("\n=== Generating 000-healthy per-metric files ===")
    generate_healthy_per_metric_files()

    print("\n=== Expanding scenario fixtures ===")
    for scenario_id, config in SCENARIOS.items():
        process_scenario(scenario_id, config)

    print(
        "\nDone. Run: python -m pytest tests/synthetic/rds_postgres/test_suite.py -m synthetic -q"
    )


if __name__ == "__main__":
    main()
