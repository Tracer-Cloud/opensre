"""Classify provider failures where the request may already have run upstream."""

from __future__ import annotations

from anthropic import APIConnectionError as AnthropicConnectionError
from openai import APIConnectionError as OpenAIConnectionError

_TRANSPORT_ERRORS = (OpenAIConnectionError, AnthropicConnectionError)


def is_transport_failure(err: BaseException) -> bool:
    """True when *err*, or any error it was raised from, is a timeout or connection failure.

    Clients wrap SDK errors in ``RuntimeError(...) from err``, so the check
    follows the ``__cause__`` chain, then ``__context__`` for wrappers raised
    inside an ``except`` block without ``from``. Repeating such a request bills it again.
    """
    seen: set[int] = set()
    cause: BaseException | None = err
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, _TRANSPORT_ERRORS):
            return True
        seen.add(id(cause))
        cause = cause.__cause__ or cause.__context__
    return False
