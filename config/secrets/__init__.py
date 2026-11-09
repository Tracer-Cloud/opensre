"""Local secret storage: an owner-only credentials file and the policy around it.

Import :mod:`config.llm_credentials` from feature code — it is the stable façade
every surface already uses. Reach into this package directly only when you need
a tier or the policy result itself (the wizard needs to know *which* tier
accepted a write so it can tell the user).
"""

from __future__ import annotations

__all__: list[str] = []
