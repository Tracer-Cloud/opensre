"""CLI helpers.

Importing this package must stay cheap: ``python -m surfaces.cli`` loads it
before ``--help`` / ``--version``. Public names resolve on first access.
"""

from surfaces.cli.exports import __all__ as __all__
from surfaces.cli.exports import __dir__ as __dir__
from surfaces.cli.exports import __getattr__ as __getattr__
