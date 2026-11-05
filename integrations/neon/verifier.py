"""Neon integration verifier: reads the configured project with the API key."""

from __future__ import annotations

from integrations.neon.client import NeonClient
from integrations.neon.config import build_neon_config
from integrations.verification import register_probe_verifier

verify_neon = register_probe_verifier(
    "neon",
    config=build_neon_config,
    client=NeonClient,
)
