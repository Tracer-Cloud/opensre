"""Local probe for the ambient AWS credential chain the role mode relies on."""

from __future__ import annotations

# What each ambient source is, in the words a user needs to act on it.
AMBIENT_SOURCES_HINT = (
    "environment keys (AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY), "
    "an `aws configure` profile in ~/.aws/credentials, "
    "or an attached instance/task role"
)


def has_ambient_credentials() -> bool:
    """True when base credentials resolve from *local* sources, instantly.

    Env, the keyring-paired static key, and ``~/.aws`` profiles — not the ECS
    or EC2 metadata endpoints. This answers "should setup warn before the user
    types anything?", so it must not wait out network timeouts on a laptop or
    behind a proxy. An attached instance role is therefore reported as absent
    here; the role-mode gate offers "continue with the role anyway" for exactly
    that machine, and the verifier's real STS call still uses the full chain.
    """
    from integrations.aws.env import base_session

    try:
        session = base_session(local_only=True)
        return session is not None and session.get_credentials() is not None
    except Exception:
        return False


__all__ = ["AMBIENT_SOURCES_HINT", "has_ambient_credentials"]
