"""The tool packages and the modules they may be imported through.

``core/tool/`` owns the tool contract, execution and the registry port;
``core/tool_framework/`` holds the authoring helpers (``@tool``, skill guidance,
shared payload utilities). Together they are one group to their consumers.

Both roots now curate a public surface, so consumers import these packages through
these modules and the border test's allowlists are empty.
"""

from __future__ import annotations

from tests.shared.api_border import ApiBorder

TOOL_PACKAGES: tuple[str, ...] = ("core.tool", "core.tool_framework")

#: The public API modules a consumer may import through. Listed by exact name so a
#: new submodule never joins the public API by accident. ``utils`` is a public API
#: in its own right: it curates ``__all__`` over the helper submodules below it, so
#: callers name one module instead of nine.
API_MODULES: frozenset[str] = frozenset({*TOOL_PACKAGES, "core.tool_framework.utils"})

TOOL_BORDER = ApiBorder(packages=TOOL_PACKAGES, api_modules=API_MODULES)

__all__ = ["API_MODULES", "TOOL_BORDER", "TOOL_PACKAGES"]
