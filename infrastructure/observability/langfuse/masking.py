"""Export-stage credential masking for spans leaving this process to Langfuse.

Runs on the Langfuse exporter thread over the final OpenTelemetry attributes,
so it also covers text a call site did not think to redact (tool output that
echoes a token, an exception message carrying a bearer header).
"""

from __future__ import annotations

from collections.abc import Sequence

from langfuse.types import (
    MaskOtelSpansParams,
    MaskOtelSpansResult,
    OtelSpanIdentifier,
    OtelSpanPatch,
)

from infrastructure.safety.secret_redaction import redact_text

_AttributeValue = (
    str | bool | int | float | Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float]
)


def _masked(value: _AttributeValue) -> _AttributeValue | None:
    """Redacted copy of ``value`` when a credential pattern matched, else ``None``."""
    if isinstance(value, str):
        redacted = redact_text(value)
        return redacted if redacted != value else None
    if isinstance(value, Sequence) and value and all(isinstance(item, str) for item in value):
        originals = [str(item) for item in value]
        redacted_items = [redact_text(item) for item in originals]
        return redacted_items if redacted_items != originals else None
    return None


def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult:
    """Replace credential-shaped substrings in every string attribute of the batch."""
    patches: dict[OtelSpanIdentifier, OtelSpanPatch | None] = {}
    for identifier, span in params.spans.items():
        replacements: dict[str, _AttributeValue] = {}
        for key, value in span.attributes.items():
            masked = _masked(value)
            if masked is not None:
                replacements[key] = masked
        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
    return MaskOtelSpansResult(span_patches=patches)


__all__ = ["mask_otel_spans"]
