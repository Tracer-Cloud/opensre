"""A per-run ``CODEX_HOME`` holding only the config OpenSRE writes.

On the hosted route Codex needs nothing from ``~/.codex``: the provider comes
from ``-c`` overrides and the token from the child environment. A personal
Codex home can still load plugins, MCP servers, skills, notify hooks and large
state databases on every run, which costs minutes before the first model call.
``HOME`` itself is left alone so git and ``gh`` keep the user's credentials.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from config.constants import CODEX_ISOLATED_HOME_PREFIX

logger = logging.getLogger(__name__)

#: Everything a coding run needs and nothing that starts work of its own:
#: no plugins, apps, MCP servers, notify hook, web search, sub-agents or skill
#: catalog. The repository's own ``AGENTS.md`` is still read.
_MINIMAL_CONFIG = """\
# Written by OpenSRE for one coding-agent run; removed when the run ends.
approval_policy = "never"
web_search = "disabled"
check_for_update_on_startup = false

[features]
multi_agent = false
plugins = false
apps = false

[skills]
include_instructions = false

[skills.bundled]
enabled = false
"""


def _create_home() -> str | None:
    """A fresh directory holding the minimal ``config.toml``, or ``None`` when it cannot be made."""
    try:
        home = tempfile.mkdtemp(prefix=CODEX_ISOLATED_HOME_PREFIX)
    except OSError:
        logger.warning("codex: isolated CODEX_HOME unavailable; using the default", exc_info=True)
        return None
    try:
        (Path(home) / "config.toml").write_text(_MINIMAL_CONFIG, encoding="utf-8")
    except OSError:
        logger.warning("codex: isolated CODEX_HOME unavailable; using the default", exc_info=True)
        shutil.rmtree(home, ignore_errors=True)
        return None
    return home


@contextmanager
def isolated_codex_home() -> Iterator[str | None]:
    """Yield a minimal Codex home for one run and remove it afterwards.

    Yields ``None`` when the directory cannot be created, so the caller keeps
    Codex's default home rather than failing the run.
    """
    home = _create_home()
    try:
        yield home
    finally:
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)


__all__ = ["isolated_codex_home"]
