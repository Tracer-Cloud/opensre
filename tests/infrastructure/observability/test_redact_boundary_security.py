"""Security characterization for redact-once sharing.

Pins the invariant that matters for secrets: any external-facing sink that
consumes :func:`redact_tool_view` must see a deep-copied redacted tree —
never an alias of the raw tool payload.
"""

from __future__ import annotations

from infrastructure.observability.trace.redaction import redact_tool_view

_SECRET = "super-secret-credential-value"
_RAW_INPUT = {"namespace": "prod", "token": _SECRET, "safe": "visible"}
_RAW_OUTPUT = {
    "logs": ["ok"],
    "password": _SECRET,
    "nested": {"api_key": _SECRET, "count": 1},
}


def test_redact_tool_view_is_deep_copy_not_alias_of_raw() -> None:
    view = redact_tool_view(_RAW_INPUT, _RAW_OUTPUT)

    assert view.tool_input is not _RAW_INPUT
    assert view.output is not _RAW_OUTPUT
    assert view.output is not None
    assert view.output["nested"] is not _RAW_OUTPUT["nested"]
    # Raw unchanged; redacted has placeholders.
    assert _RAW_INPUT["token"] == _SECRET
    assert view.tool_input["token"] == "[redacted]"
    assert view.output["password"] == "[redacted]"
    assert view.output["nested"]["api_key"] == "[redacted]"
    assert view.output["nested"]["count"] == 1
    assert view.tool_input["safe"] == "visible"
