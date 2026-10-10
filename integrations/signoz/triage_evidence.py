"""Read-only evidence tools bound to one trusted SigNoz source."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from config.constants.triage import TRIAGE_TEXT_LIMIT, TRIAGE_WINDOW_SECONDS
from core.agent_harness.ports import ConfirmFn, ToolEventObserver
from core.domain.alerts.triage.models import InvestigationClaim
from core.domain.alerts.triage.storage import TriageStore
from core.tool import AgentTool, AgentToolContext
from integrations.signoz import SigNozConfig
from integrations.signoz.client import SigNozClient


class ProvenanceClient(SigNozClient):
    """Capture the actual v5 query payload before making its read-only request."""

    def __init__(self, config: SigNozConfig, store: TriageStore, claim: InvestigationClaim) -> None:
        super().__init__(config)
        self.store = store
        self.claim = claim

    def _query_range_post(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        self.store.evidence(
            self.claim,
            {"endpoint": "/api/v5/query_range", "source_url": self.config.url, "payload": payload},
        )
        return super()._query_range_post(payload)


class TriageEvidenceTools:
    """Runtime catalog containing only scoped SigNoz reads, without discovery."""

    def __init__(
        self, claim: InvestigationClaim, store: TriageStore, api_key: str, stop: Callable[[], bool]
    ) -> None:
        self.claim = claim
        self.store = store
        self.api_key = api_key
        self.stop = stop
        event = datetime.fromisoformat(claim.alert["starts_at"])
        now = datetime.now(UTC)
        if event > now + timedelta(minutes=1):
            raise ValueError("Alert startsAt lies in the future; evidence window unavailable")
        self.start = event - timedelta(minutes=10)
        self.end = min(now, event + timedelta(minutes=10))
        if self.end <= self.start:
            raise ValueError("Alert evidence window unavailable")
        self.clipped = event + timedelta(minutes=10) > now
        if (self.end - self.start).total_seconds() > TRIAGE_WINDOW_SECONDS:
            self.start = self.end - timedelta(seconds=TRIAGE_WINDOW_SECONDS)
            self.clipped = True
        self.observations: list[dict[str, Any]] = []
        self._tools = [self._tool(signal) for signal in ("logs", "metrics", "traces")]

    def _tool(self, signal: str) -> AgentTool:
        properties: dict[str, Any] = {
            "service": {"type": "string", "enum": list(self.claim.source.services)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }
        required = ["service"]
        if signal == "logs":
            properties["severity"] = {"type": "string", "enum": ["ERROR", "WARN", "INFO", "FATAL"]}
        if signal == "traces":
            properties["error_only"] = {"type": "boolean"}
        if signal == "metrics":
            properties["metric_name"] = {"type": "string", "maxLength": 200}
            required.append("metric_name")

        def execute(payload: dict[str, Any], _context: AgentToolContext) -> dict[str, Any]:
            return self.query(signal, payload)

        return AgentTool(
            name=f"query_signoz_{signal}",
            description=f"Read scoped SigNoz {signal} in the fixed alert evidence window. No credentials or URLs accepted.",
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            execute=execute,
            source="signoz",
        )

    def query(self, signal: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Enforce scope and budget again at execution, beyond model schema checks."""
        if signal not in {"logs", "metrics", "traces"}:
            raise ValueError("Evidence signal is not permitted")
        allowed = {"service", "limit"} | {
            "logs": {"severity"},
            "metrics": {"metric_name"},
            "traces": {"error_only"},
        }[signal]
        if set(payload) - allowed or payload.get("service") not in self.claim.source.services:
            raise PermissionError("Query exceeds trusted source scope")
        if self.stop():
            raise TimeoutError("Investigation cancelled")
        self.store.consume(self.claim, "tool")
        remaining = self.claim.deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("Evidence budget exhausted")
        client = ProvenanceClient(
            SigNozConfig(
                url=self.claim.source.query_url,
                api_key=self.api_key,
                timeout_seconds=min(10, max(0.1, remaining)),
            ),
            self.store,
            self.claim,
        )
        args = dict(payload)
        args["limit"] = max(1, min(int(args.get("limit", 50)), 50))
        args.update(start_time=self.start.isoformat(), end_time=self.end.isoformat())
        method = {
            "logs": client.query_logs,
            "metrics": client.query_metrics,
            "traces": client.query_traces,
        }[signal]
        result = method(**args)
        # Keep provider text bounded and never allow a echoed credential into the model.
        encoded = (
            json.dumps(result, default=str).replace(self.api_key, "[redacted]")
            if self.api_key
            else json.dumps(result, default=str)
        )
        if len(encoded) > TRIAGE_TEXT_LIMIT:
            result = {
                "available": result.get("available", False),
                "partial": True,
                "preview": encoded[:TRIAGE_TEXT_LIMIT],
                "truncation_note": "Evidence clipped to the report budget",
            }
        else:
            result = json.loads(encoded)
        record = {
            "signal": signal,
            "service": args["service"],
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "window_clipped": self.clipped,
            "source_url": self.claim.source.query_url,
            "query": payload,
            "result": result,
        }
        self.store.evidence(self.claim, {k: v for k, v in record.items() if k != "result"}, result)
        self.observations.append(record)
        return result

    def action_tools(
        self,
        *,
        confirm_fn: ConfirmFn | None,
        is_tty: bool | None,
        resolved_integrations: dict[str, Any] | None = None,
        turn_user_message: str = "",
    ) -> list[Any]:
        """Supply a fixed allowlist; no shell, MCP, memory, or remediation tools."""
        _ = (confirm_fn, is_tty, resolved_integrations, turn_user_message)
        return list(self._tools)

    def tool_resources(self) -> dict[str, Any]:
        """No ambient integration or subprocess resources are exposed."""
        return {}

    def observer(self, *, message: str) -> ToolEventObserver:
        """Provider-call accounting is enforced by the host's LLM adapter."""
        _ = message

        def observe(_kind: str, _data: dict[str, Any]) -> None:
            return None

        return observe
