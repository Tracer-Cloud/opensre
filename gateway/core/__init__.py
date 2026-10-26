"""Process, storage, and shared helpers used by every gateway surface.

Subpackages:

* ``lifecycle/`` — composition root, credential hydration, gateway errors
* ``process/`` — daemon supervision, polling thread, readiness
* ``middleware/`` — per-turn steps every transport runs
* ``storage/`` — session bindings and shared event stores
* ``billing/`` — credits client
* ``attachments/`` — attachment helpers
* ``session/`` — gateway chat context helpers
* ``config/`` — logging and gateway config helpers

Must not import ``gateway.transports.*`` or ``gateway.web``.
Sole exception: :mod:`gateway.core.lifecycle.controller`, which starts those
surfaces.
"""

from __future__ import annotations

__all__: list[str] = []
