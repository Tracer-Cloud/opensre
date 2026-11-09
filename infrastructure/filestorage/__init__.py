"""Optional mirroring of conversation context to a store the user owns.

Off by default. When switched on, conversation history and memory are copied
to the user's own object store (built-in: AWS/S3, GCS, and Vercel Blob; others
register under :mod:`infrastructure.filestorage.providers`). Credentials never leave
the machine - see :mod:`infrastructure.filestorage.syncable`.

Surfaces share one **stateless, thread-safe** service
(:mod:`infrastructure.filestorage.operations`): ``opensre remote-sync``, REPL
``/remote-sync``, and gateway headless slash ports. Setup writes the stored
``remote_sync`` section; each sync re-reads settings and builds a fresh backend.
Roots follow the active principal scope (``sessions_dir`` / ``get_memory_dir``).

This is the object-store counterpart to a mounted org context root: same idea,
different mechanism, because a laptop has no provisioned filesystem and the
stores here write by atomic rename.

Leaf modules are the source of truth. ``from infrastructure.filestorage import
NAME`` still works; importing this package does not load the sync engine.
"""

from infrastructure.filestorage.exports import __all__ as __all__
from infrastructure.filestorage.exports import __dir__ as __dir__
from infrastructure.filestorage.exports import __getattr__ as __getattr__
