"""The tenant scope is a ContextVar; a thread hop must carry it explicitly.

Thread pools and ``run_in_executor`` do not propagate ContextVars, so a turn
body handed to a worker sees no scope — and an unbound resolution falls back to
the shared home directory. ``in_current_scope`` binds the caller's context to
the callable on the calling thread, so the worker sees the same scope.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from config import scope_context
from config.principal import Actor, Principal, StorageScope


def _org(org_id: str = "org_a", actor: str = "U1") -> StorageScope:
    return StorageScope(principal=Principal.org(org_id), actor=Actor(id=actor))


def _scope_id_on_worker() -> str:
    scope = scope_context.current_scope()
    return scope.principal.id if scope else "NONE"


def test_a_bare_executor_hop_drops_the_scope() -> None:
    """The Python fact this module guards against — pinned so it stays visible."""
    with scope_context.bound_storage_scope(_org()), ThreadPoolExecutor(1) as pool:
        seen = pool.submit(_scope_id_on_worker).result()

    assert seen == "NONE"


def test_in_current_scope_carries_the_scope_onto_the_worker() -> None:
    with scope_context.bound_storage_scope(_org("org_b")), ThreadPoolExecutor(1) as pool:
        seen = pool.submit(scope_context.in_current_scope(_scope_id_on_worker)).result()

    assert seen == "org_b"


def test_in_current_scope_works_through_asyncio_run_in_executor() -> None:
    """The gateway's real hop shape."""

    async def _turn() -> str:
        loop = asyncio.get_running_loop()
        with scope_context.bound_storage_scope(_org("org_c")), ThreadPoolExecutor(1) as pool:
            return await loop.run_in_executor(
                pool, scope_context.in_current_scope(_scope_id_on_worker)
            )

    assert asyncio.run(_turn()) == "org_c"


def test_the_bound_callable_captures_the_scope_at_binding_time() -> None:
    """Binding happens on the caller's thread, before the hop, not on the worker."""
    with scope_context.bound_storage_scope(_org("org_d")):
        bound = scope_context.in_current_scope(_scope_id_on_worker)
    # Scope is no longer bound here — the callable still carries it.
    assert scope_context.current_scope() is None
    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(bound).result() == "org_d"
