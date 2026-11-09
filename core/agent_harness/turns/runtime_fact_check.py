"""Detects replies that contradict authoritative runtime facts.

The runtime-facts block states the host's own environment, and the model is
told to quote it. It does not always comply: a laptop turn reported
``cloud provider aws / region us-east-1`` with no such value anywhere in the
prompt — the pair is a training-data prior, so no phrasing of the fact
displaces it. This module reports the contradiction so the turn can compose a
corrected answer instead.

Only the cloud fields are checked. They are the case where the host holds
ground truth, the model has a strong competing prior, and a wrong answer is
stated as fact rather than hedged.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Provider names a reply might use, lowercase. A closed vocabulary, not a
# pattern: the check answers "did the reply name a provider", and the set of
# providers is finite and known.
_CLOUD_PROVIDER_NAMES: frozenset[str] = frozenset(
    {
        "aws",
        "amazon web services",
        "gcp",
        "google cloud",
        "azure",
        "digitalocean",
        "alibaba cloud",
        "oracle cloud",
    }
)


def _reply_quotes_runtime_identity(reply: str, runtime: Mapping[str, Any]) -> bool:
    """True when the reply repeats a host-identifying fact from the block.

    Distinguishes "what environment am I in?" — where a provider name is a
    claim about *this* process — from a reply that merely discusses a cloud
    (deployment help, a doc link). Only the former quotes the host name.
    """
    hostname = str(runtime.get("hostname") or "").strip()
    return bool(hostname) and hostname in reply


def named_cloud_providers(reply: str) -> tuple[str, ...]:
    """Return the provider names the reply mentions, in vocabulary order."""
    lowered = reply.lower()
    return tuple(sorted(name for name in _CLOUD_PROVIDER_NAMES if name in lowered))


def reply_contradicts_cloud_absence(reply: str, runtime: Mapping[str, Any]) -> bool:
    """True when the reply claims a cloud for a process the host found none for.

    Requires all three: detection ran and found nothing, the reply names a
    provider, and the reply is describing this host. A reply about AWS that
    never quotes the host name is answering a different question.
    """
    if not reply.strip():
        return False
    if "cloud_provider" not in runtime and "cloud_region" not in runtime:
        return False  # detection never ran — nothing authoritative to contradict
    if str(runtime.get("cloud_provider") or "").strip():
        return False  # a cloud really was detected
    if not named_cloud_providers(reply):
        return False
    return _reply_quotes_runtime_identity(reply, runtime)


def cloud_absence_correction(runtime: Mapping[str, Any]) -> str:
    """Instruction for the retry compose, naming the fields it got wrong."""
    hostname = str(runtime.get("hostname") or "this host").strip()
    return (
        "Your previous answer named a cloud provider or region for "
        f"{hostname}. No cloud provider and no cloud region were detected for "
        "this process — it is not running in a recognised cloud environment. "
        "Rewrite the answer without naming any provider or region, and do not "
        "add a cloud field at all."
    )


__all__ = [
    "cloud_absence_correction",
    "named_cloud_providers",
    "reply_contradicts_cloud_absence",
]
