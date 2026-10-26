"""Guard: importing the webapp ASGI app registers the vendor registries.

``MODE=web`` (the Dockerfile default) runs ``uvicorn gateway.web.webapp:app``
directly — no CLI boot, no gateway manager. Alert intake then reads registries
that integrations populate at startup (alert-source routing, incident anchors,
alert detail fields). If the module stops registering them, nothing raises:
every registry degrades to an empty-but-valid default and queued alerts silently
lose anchors, seed tools, and vendor detail fields.

This runs in a subprocess so the assertion reflects the real entrypoint. The
gateway conftest registers adapters for every test, so an in-process check would
pass even with the registration removed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

# Import the ASGI app exactly as uvicorn does, then probe one registry per
# vendor extension point alert intake depends on.
_PROBE = textwrap.dedent(
    """
    import json

    import gateway.web.webapp  # noqa: F401  - the uvicorn import target

    from core.domain.alerts.alert_source import secondary_tool_sources
    from core.domain.alerts.extraction import alert_detail_field_names
    from core.domain.types.incident_anchors import extract_anchor

    anchor = extract_anchor({"startsAt": "2026-01-01T00:00:00Z"})
    print("PROBE:" + json.dumps({
        "secondary_sources": sorted(secondary_tool_sources()),
        "detail_fields": list(alert_detail_field_names()),
        "anchor_parsed": anchor is not None,
    }))
    """
)


def _probe_fresh_process() -> dict[str, object]:
    # A subprocess inherits os.environ but not this process's runtime sys.path,
    # so pass the import roots through PYTHONPATH.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"webapp import subprocess exited {result.returncode}:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    line = next((ln for ln in result.stdout.splitlines() if ln.startswith("PROBE:")), None)
    assert line is not None, f"probe produced no output:\n{result.stdout}\n{result.stderr}"
    return json.loads(line.removeprefix("PROBE:"))


def test_webapp_import_registers_vendor_registries() -> None:
    # Act: import the ASGI app the way the container does.
    probe = _probe_fresh_process()

    # Assert: incident anchors parse — an empty parser registry returns None and
    # would silently drop the incident window's start time.
    assert probe["anchor_parsed"] is True

    # Assert: vendor-owned alert detail fields reach the extraction schema.
    for field in ("cloudwatch_log_group", "eks_cluster", "kube_namespace", "pod_name"):
        assert field in probe["detail_fields"]

    # Assert: generic fallback sources are known, so they are not ranked primary.
    assert probe["secondary_sources"] == ["google_docs", "knowledge"]
