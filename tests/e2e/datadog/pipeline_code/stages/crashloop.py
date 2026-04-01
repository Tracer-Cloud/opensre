"""Crashloop stage: simulates a container OOMKilled by the kernel (exit 137).

This stage mimics a genomics alignment worker that runs out of memory while
loading a large reference genome index into RAM.  Kubernetes sees exit=137
(SIGKILL), marks the container as OOMKilled, and retries via backoffLimit →
BackoffLimitExceeded / CrashLoopBackOff visible in kubectl describe.
"""

import json
import sys

from config import PIPELINE_NAME, PIPELINE_RUN_ID


def main() -> None:
    print(
        f"[crashloop] Starting alignment worker for run {PIPELINE_RUN_ID}",
        flush=True,
    )
    print(
        "[crashloop] Loading reference genome index GRCh38 into memory (24 GB required)...",
        flush=True,
    )
    # Emit the OOMKill error that would appear in container stderr / k8s events
    print(
        "PIPELINE_ERROR: container OOMKilled — "
        f"alignment worker exceeded memory limit (run_id={PIPELINE_RUN_ID}, "
        "requested=24Gi, limit=8Gi). "
        "Kernel sent SIGKILL. See kubectl describe pod for OOMKilled status.",
        file=sys.stderr,
    )
    print(
        json.dumps({
            "stage": "crashloop",
            "status": "oomkilled",
            "pipeline": PIPELINE_NAME,
            "run_id": PIPELINE_RUN_ID,
            "exit_code": 137,
            "reason": "OOMKilled: alignment worker exceeded memory limit (24Gi requested, 8Gi limit)",
        })
    )
    sys.exit(137)
