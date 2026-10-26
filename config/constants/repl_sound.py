"""Interactive-shell sound-notification settings."""

from __future__ import annotations

# Enable a short chime on turn completion / when input is needed. Off unless the
# env var is truthy so the shell never makes an unexpected sound by default.
SOUND_NOTIFICATIONS_ENV = "OPENSRE_SOUND"

# Only chime on turn completion for turns longer than this, so quick interactive
# replies stay silent and only a walk-away-length task announces itself.
SOUND_MIN_TURN_SECONDS = 8.0

__all__ = ["SOUND_NOTIFICATIONS_ENV", "SOUND_MIN_TURN_SECONDS"]
