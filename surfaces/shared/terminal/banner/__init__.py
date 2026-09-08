"""OpenSRE launch banner.

Eager imports stay off this package ``__init__`` so
``banner_state.load_launch_status`` (two integer chips) does not pull Rich,
prompt_toolkit, or the wordmark animation stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from surfaces.shared.terminal.banner.banner import (
        WordmarkSpinFrame,
        animate_launch_wordmark,
        build_launch_banner,
        build_wordmark_spin_frames,
        render_launch_banner,
    )

__all__ = [
    "WordmarkSpinFrame",
    "animate_launch_wordmark",
    "build_launch_banner",
    "build_wordmark_spin_frames",
    "render_launch_banner",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from surfaces.shared.terminal.banner import banner as _banner

        return getattr(_banner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
