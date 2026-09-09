"""Live token streaming for interactive-shell LLM responses.

The interactive REPL pins the input box at the bottom of the terminal
via ``patch_stdout``. To keep the input editable while a response
streams (type-ahead) we can't use :class:`rich.live.Live` — ``Live``
does cursor manipulation (cursor-up + erase-line) for in-place redraw,
which fights ``patch_stdout`` and blocks the input buffer from
accepting keystrokes.

Instead this path streams **paragraph-by-paragraph**, with a bounded-latency
fallback for very long prose paragraphs: chunks accumulate in ``para_buffer``
and a complete paragraph (text up to the next ``\\n\\n`` outside an open
code-fence) renders as ``rich.Markdown`` the moment its boundary is seen. Long
partial prose is also flushed once it grows past a small character budget, so a
10k-word single paragraph does not sit invisible until end-of-stream. Code
blocks are kept whole — we never split on ``\\n\\n`` or partial-size thresholds
while a triple-backtick fence is unclosed.

Streaming progress and cancellation are surfaced through optional
attributes on the ``console``: ``update_streaming_progress(bytes)`` is
called per chunk (throttled to ~10/s) so the bottom-toolbar token
counter updates live, and ``cancel_requested`` is polled between chunks
so an Esc press in the prompt cancels promptly. The ``getattr``
indirection keeps this module decoupled from the ``StreamingConsole`` adapter.

Gather answers may set ``defer_want_me_to_closer`` so dual/drifted Want-me-to
menus are not rendered until the harness flushes the canonical closer.
"""

from __future__ import annotations

from surfaces.interactive_shell.ui.streaming.closer import finish_deferred_closer
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.interactive_shell.ui.streaming.loop import (
    StreamRenderResult,
    publish_full_response,
    stream_to_console,
    stream_to_console_state,
)
from surfaces.interactive_shell.ui.streaming.renderer import (
    STREAM_LABEL_ANSWER,
    STREAM_LABEL_ASSISTANT,
    render_markdown_block,
    render_note_block,
    render_response_header,
)

# Not part of __all__: exists so tests can substitute the renderer's Markdown
# class via ``streaming.Markdown = ...`` — renderer._build_markdown_block reads
# it back off this package rather than importing it directly.
from surfaces.shared.terminal.components.markdown import ReplyMarkdown as Markdown  # noqa: F401
from surfaces.shared.terminal.components.token_format import (
    _CHARS_PER_TOKEN,  # noqa: F401  # not in __all__, but tests import it from this path directly
    format_token_count_short,
)

__all__ = [
    "StreamingConsole",
    "STREAM_LABEL_ANSWER",
    "STREAM_LABEL_ASSISTANT",
    "StreamRenderResult",
    "finish_deferred_closer",
    "format_token_count_short",
    "publish_full_response",
    "render_markdown_block",
    "render_note_block",
    "render_response_header",
    "stream_to_console",
    "stream_to_console_state",
]
