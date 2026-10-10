"""Lazy SigNoz triage capability loading without eager demo imports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_TRIAGE_EXPORTS = {
    "SourceFieldError": "triage_setup",
    "parse_notification": "notifications",
    "TriageEvidenceTools": "triage_evidence",
    "connect_source": "triage_setup",
    "validate_urls": "triage_setup",
    "validate_source_fields": "triage_setup",
    "PaymentDemo": "triage_demo.runtime",
    "demo_root": "triage_demo.runtime",
    "wait_for": "triage_demo.runtime",
}


def __getattr__(name: str) -> Any:
    """Load triage capabilities on demand without importing their client cycle."""
    module = _TRIAGE_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'integrations.signoz' has no attribute {name!r}")
    return getattr(import_module(f"integrations.signoz.{module}"), name)
