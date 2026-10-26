"""Fake Anthropic ``messages.stream`` contexts for agent-client tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from types import SimpleNamespace
from typing import Any


class FakeMessageStream:
    """One ``messages.stream(...)`` context: text events, then the final message.

    ``error`` is raised after the pieces, like a connection dropping mid-reply.
    """

    def __init__(
        self,
        final: Any,
        *,
        texts: Sequence[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._final = final
        self._texts = list(texts) if texts is not None else _text_pieces(final)
        self._error = error

    def __enter__(self) -> FakeMessageStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def __iter__(self) -> Iterator[Any]:
        for text in self._texts:
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text=text),
            )
        if self._error is not None:
            raise self._error

    def get_final_message(self) -> Any:
        return self._final


def _text_pieces(message: Any) -> list[str]:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    return [block.text for block in content if getattr(block, "type", None) == "text"]


def streaming_messages(create: Callable[..., Any]) -> SimpleNamespace:
    """A ``client.messages`` whose ``stream`` replays what ``create`` returns or raises."""

    def stream(**kwargs: Any) -> FakeMessageStream:
        return FakeMessageStream(create(**kwargs))

    return SimpleNamespace(stream=stream, create=create)
