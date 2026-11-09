"""How this gateway process comes up and goes down.

:mod:`gateway.core.lifecycle.controller` (``GatewayController``) is the
composition root: credential hydrate, shared process boot, one turn runner,
then surfaces and scheduler. It owns signals, ``stop`` and ``wait``.

Distinct from :mod:`gateway.core.process`, which is the view from outside —
starting, supervising and reporting on a gateway process. This package is the
view from inside the process already running.

Production boot is ``opensre gateway start``, which injects slash ports;
``python -m gateway`` fails closed.
"""

from __future__ import annotations
