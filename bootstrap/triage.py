"""Compose trusted SigNoz evidence with the gateway-hosted alert worker."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from config.llm_credentials import resolve_env_credential
from core.domain.alerts.triage.models import InvestigationClaim
from core.domain.alerts.triage.storage import TriageStore
from core.domain.alerts.triage.worker import TriageWorker
from infrastructure.process.turn_capacity import TurnGate
from infrastructure.turn_host.triage import run_triage_turn
from integrations.signoz.triage_evidence import TriageEvidenceTools


def build_triage_worker(gate: TurnGate) -> TriageWorker:
    """Bind source credentials outside the model and reuse process capacity."""
    store = TriageStore()

    def investigate(claim: InvestigationClaim, stop: Callable[[], bool]) -> dict[str, Any]:
        key = resolve_env_credential(claim.source.credential_ref)
        if not key:
            raise ValueError("Source query credential is unavailable")
        tools = TriageEvidenceTools(claim, store, key, stop)
        return run_triage_turn(claim, store, tools, stop)

    return TriageWorker(store, investigate, gate)
