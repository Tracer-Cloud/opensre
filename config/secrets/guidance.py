"""User-facing guidance for when secret storage did not work as intended.

Split from the storage tiers themselves so the wizard has one import for
everything it needs to explain.
"""

from __future__ import annotations

from config.constants.secrets import OPENSRE_DISABLE_KEYRING_ENV
from config.secrets import local_file, store


def get_keyring_setup_instructions(env_var: str) -> tuple[str, ...]:
    """Guidance for restoring local credential storage.

    Only reached once the local credentials file refused the write, so the goal
    is to name the switch that is refusing storage or the path that is not
    writable.
    """
    if store.keyring_is_disabled():
        return (
            f"Local credential storage is disabled by {OPENSRE_DISABLE_KEYRING_ENV}.",
            f"Unset {OPENSRE_DISABLE_KEYRING_ENV} and rerun `opensre onboard` to save "
            f"{env_var}, or export {env_var} in your shell.",
        )

    return (
        f"OpenSRE could not write {local_file.store_path()}.",
        "Check that you have write access to that path.",
        f"Or export {env_var} in your shell to skip local storage entirely.",
    )


__all__ = ["get_keyring_setup_instructions"]
