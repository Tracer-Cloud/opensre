"""Tests for the shared live-streaming renderer used by interactive-shell handlers."""

from __future__ import annotations

import io
import re
import threading
from collections.abc import Iterator

import pytest
from rich.console import Console

from surfaces.interactive_shell.ui.streaming import (
    finish_deferred_closer,
    format_token_count_short,
    publish_full_response,
    render_markdown_block,
    render_note_block,
    render_response_header,
    stream_to_console,
    stream_to_console_state,
)


def _strip_ansi(text: str) -> str:
    """Drop ANSI escapes so assertions check the visible output."""
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def test_render_note_block_is_recessed_and_indented_but_keeps_bold() -> None:
    # Droid rhythm: warm ``·`` in the shared agent column, recessed body, bold
    # action words still bold.
    from infrastructure.terminal import theme as ui_theme

    ui_theme.set_active_theme("amber")
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
        no_color=False,
        width=80,
        highlight=False,
    )

    render_note_block(console, "I'll **load** the workflow.")

    raw = buf.getvalue()
    # Soft recessed base (SECONDARY), not ghost DIM — accept truecolor or 256.
    assert "38;2;" in raw or "38;5;" in raw
    assert re.search(r"\x1b\[[0-9;]*1[;m]", raw)  # bold action word survives
    first_line = _strip_ansi(raw).splitlines()[0]
    assert first_line.startswith("· ")  # warm · in the shared agent marker column
    assert "load the workflow" in first_line


def test_table_reply_renders_as_a_table_not_flattened_pipes() -> None:
    # A reply that leads with a Markdown table must render as an aligned table. The
    # inline ``Ω `` marker fused onto the header row breaks CommonMark block parsing
    # and flattens the table to one line of raw pipes, so the marker goes on its own
    # line before a leading block.
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=60, highlight=False)

    publish_full_response(
        console, "| Rank | Folder | Size |\n|---:|---|---:|\n| 1 | Library | 113G |"
    )

    out = buf.getvalue()
    assert "| Rank | Folder |" not in out  # not the raw flattened markdown row
    assert "Rank" in out and "Folder" in out and "Size" in out
    assert "─" in out  # rendered as a table with a header rule


def test_prose_reply_keeps_the_inline_marker() -> None:
    # Prose still carries the marker inline on the first line.
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=60, highlight=False)

    publish_full_response(console, "Root disk is 44% full.")

    assert "Ω Root disk is 44% full." in buf.getvalue()


def _tty_console() -> tuple[Console, io.StringIO]:
    """Build a Console that thinks it is a terminal so Rich.Live actually renders."""
    buf = io.StringIO()
    return (
        Console(file=buf, force_terminal=True, color_system=None, width=80, highlight=False),
        buf,
    )


def _non_tty_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, color_system=None, width=80), buf


def _yield_chunks(chunks: list[str]) -> Iterator[str]:
    yield from chunks


class TestNonTtyFallback:
    """On a non-terminal console the helper drains, prints, and returns the full text."""

    def test_drains_stream_and_prints_without_live_artifacts(self) -> None:
        console, buf = _non_tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["Hel", "lo, ", "world"]),
        )

        output = buf.getvalue()
        assert result == "Hello, world"
        # The inline ``Ω`` marker + text reach piped output so captured logs
        # are useful (the standalone label header was removed).
        assert "Ω" in output
        assert "Hello, world" in output
        # No spinner / Live cursor-movement artifacts in non-TTY captures.
        assert "thinking" not in output

    def test_strips_terminal_controls_from_streamed_model_prose(self) -> None:
        console, buf = _non_tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["Hello\x1b[2J", ", world\x07"]),
        )

        assert "\x1b" not in buf.getvalue()
        assert "\x07" not in buf.getvalue()
        assert "Hello" in result
        assert "world" in result

    def test_suppression_drains_silently_in_non_tty(self) -> None:
        """Suppressed payloads (machine-readable payloads) must not appear in piped output."""
        console, buf = _non_tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(['{"actions"', ":[]}"]),
            suppress_if_starts_with="{",
        )

        assert result == '{"actions":[]}'
        output = buf.getvalue()
        # No bullet header for suppressed responses.
        assert "Ω" not in output
        assert '{"actions"' not in output


class TestDunderFilenameMarkdownEscape:
    """``__init__.py`` must not be parsed as Markdown bold (was washing out
    path-heavy list items to a different color than neighboring items).
    """

    def test_init_py_paths_keep_underscores_in_rendered_output(self) -> None:
        console, buf = _tty_console()
        text = (
            "1. Leave module_utils/basic.py alone.\n\n"
            "2. Split galaxy/collection/__init__.py and plugins/action/__init__.py.\n\n"
            "3. Audit shims.\n"
        )
        stream_to_console(console, label="OpenSRE", chunks=_yield_chunks([text]))
        visible = _strip_ansi(buf.getvalue())
        assert "__init__.py" in visible
        # Bold-only rendering of the old bug showed bare "init.py" without dunders.
        assert "collection/init.py" not in visible.replace("__init__.py", "")


class TestTtyParagraphRender:
    """On a terminal console paragraphs render as Markdown the moment
    each ``\\n\\n`` boundary closes them; the final paragraph is
    force-flushed at end-of-stream. Code blocks are kept whole (we
    don't split mid-fence). The spinner indicator drives the live
    streaming feedback within a paragraph.
    """

    def test_renders_label_and_streamed_content_as_markdown(self) -> None:
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["Run **opensre", " setup** to start."]),
        )

        output = _strip_ansi(buf.getvalue())
        assert result == "Run **opensre setup** to start."
        # Bullet row marker pinned above the rendered paragraph.
        assert "Ω" in output
        # End-of-stream force-flush rendered Markdown — ``**`` stripped.
        assert "**opensre" not in output
        assert "opensre setup" in output

    def test_renders_first_paragraph_before_second_completes(self) -> None:
        """A complete paragraph (``\\n\\n``) flushes immediately, even
        when more chunks would still arrive after it. The second
        paragraph stays buffered until its own boundary or EOS."""
        chunks: list[str] = []

        def _capture_chunks() -> Iterator[str]:
            for c in ["First **para**.\n\n", "Second **para**."]:
                chunks.append(c)
                yield c

        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_capture_chunks(),
        )

        output = _strip_ansi(buf.getvalue())
        assert result == "First **para**.\n\nSecond **para**."
        # Both paragraphs are rendered (``**`` stripped).
        assert "First para." in output
        assert "Second para." in output
        assert "**para**" not in output

    def test_preserves_blank_line_between_list_and_following_paragraph(self) -> None:
        """One blank line between a list and the next paragraph — no double gap.

        The whole reply hangs in the ``Ω`` gutter, so the list and the following
        paragraph sit in the indented body column.
        """
        console, buf = _tty_console()
        text = "Ready:\n\n- first\n- second\n\nBlocked pending a choice."

        stream_to_console(console, label="OpenSRE", chunks=_yield_chunks([text]))

        visible = "\n".join(line.rstrip() for line in _strip_ansi(buf.getvalue()).splitlines())
        assert "Ω Ready:\n\n   • first\n   • second\n\n  Blocked pending a choice." in visible

    def test_reply_hangs_indented_under_the_marker(self) -> None:
        """A wrapped reply hangs in the Ω gutter: line one carries the marker,
        every continuation line aligns in the indented body column."""
        console, buf = _tty_console()
        text = "A fairly long assistant reply that has to wrap across several lines. " * 3

        stream_to_console(console, label="assistant", chunks=_yield_chunks([text]))

        lines = [line for line in _strip_ansi(buf.getvalue()).splitlines() if line.strip()]
        assert lines[0].startswith("Ω ")  # marker leads the first line
        assert len(lines) > 1  # the reply actually wrapped
        assert all(line.startswith("  ") for line in lines[1:])  # hang-indent at col 2

    def test_paragraph_break_across_chunk_boundary_flushes(self) -> None:
        """The cross-chunk seam — chunk N ends with ``\\n``, chunk N+1
        starts with ``\\n`` — must be detected as a paragraph break.

        Without the seam check the fast-path skips the join (no
        ``\\n\\n`` *inside* either chunk) and the boundary is missed
        until end-of-stream.
        """
        from surfaces.interactive_shell.ui import streaming as streaming_module

        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        # Patch only on this thread; restored by the test fixture's GC.
        original_markdown = streaming_module.Markdown
        streaming_module.Markdown = _SpyMarkdown
        try:
            console, _ = _tty_console()
            # ``"first.\n"`` then ``"\nsecond."`` — neither chunk
            # contains ``\n\n`` standalone, but joined they form a
            # paragraph break at the seam.
            stream_to_console(
                console,
                label="assistant",
                chunks=_yield_chunks(["first.\n", "\nsecond."]),
            )
        finally:
            streaming_module.Markdown = original_markdown

        # 2 parses: first paragraph flushed at the seam, then second
        # tail force-flushed at end-of-stream.
        assert parse_count[0] == 2, (
            f"seam check missed the cross-chunk break — got {parse_count[0]} parses"
        )

    def test_peeked_chunks_seed_prev_chunk_for_seam_detection(self) -> None:
        """When ``suppress_if_starts_with`` peeks chunks but doesn't
        suppress, those peeked chunks become history for the seam
        check on the very first main-loop chunk.

        Concretely: suppression-peek pulls ``"hello\\n"`` (didn't match
        ``"{"``); main loop starts with ``"\\nworld"``. The seam should
        be detected — ``peeked[-1]`` is the initial ``prev_chunk``.
        """
        from surfaces.interactive_shell.ui import streaming as streaming_module

        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        original_markdown = streaming_module.Markdown
        streaming_module.Markdown = _SpyMarkdown
        try:
            console, _ = _tty_console()
            stream_to_console(
                console,
                label="assistant",
                chunks=_yield_chunks(["hello\n", "\nworld"]),
                suppress_if_starts_with="{",
            )
        finally:
            streaming_module.Markdown = original_markdown

        # 2 parses — peeked chunk + first main-loop chunk form a seam,
        # producing one paragraph; tail is force-flushed at EOS.
        assert parse_count[0] == 2

    def test_open_code_block_is_not_split_mid_fence(self) -> None:
        """``\\n\\n`` inside an open code block must NOT trigger a
        flush — splitting would render a partial fenced block whose
        formatting breaks. The fence stays whole until it closes."""
        chunks_with_open_fence = [
            "Header\n\n",
            "```python\n",
            "x = 1\n\n",  # blank line inside code block — must not flush
            "y = 2\n",
            "```\n\n",
            "Trailing.",
        ]

        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(chunks_with_open_fence),
        )

        # Full text returned unchanged.
        assert "x = 1" in result
        assert "y = 2" in result
        # Both code lines must appear in the rendered output (i.e. the
        # fence wasn't split before its closing ``` was seen).
        output = _strip_ansi(buf.getvalue())
        assert "x = 1" in output
        assert "y = 2" in output
        assert "Trailing" in output

    def test_post_fence_paragraph_renders_after_block_with_embedded_blank_line(
        self,
    ) -> None:
        """A code block containing a blank line must not bury later
        paragraphs at EOS. Once the closing fence arrives, the
        completed code-block paragraph flushes and any subsequent
        ``\\n\\n``-terminated paragraph flushes mid-stream too.
        """
        from surfaces.interactive_shell.ui import streaming as streaming_module

        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        original_markdown = streaming_module.Markdown
        streaming_module.Markdown = _SpyMarkdown
        try:
            console, _ = _tty_console()
            stream_to_console(
                console,
                label="assistant",
                chunks=_yield_chunks(
                    [
                        "Intro.\n\n",
                        "```python\n",
                        "x = 1\n\n",
                        "y = 2\n",
                        "```\n\n",
                        "Conclusion.\n\n",
                    ]
                ),
            )
        finally:
            streaming_module.Markdown = original_markdown

        # 3 parses — intro flushes on its own ``\n\n``; the fenced block
        # flushes once the closing fence + ``\n\n`` arrive (skipping the
        # embedded blank line); conclusion flushes on its own ``\n\n``
        # before EOS. Without the skip-past-open-fence logic, the fenced
        # block + conclusion would defer to a single force-flush at EOS
        # → 2 parses.
        assert parse_count[0] == 3, (
            f"post-fence paragraph deferred to EOS — got {parse_count[0]} parses"
        )

    def test_multiple_blank_lines_inside_single_fence_render_as_one_block(
        self,
    ) -> None:
        """A single code block with several embedded ``\\n\\n`` must render
        once when its fence closes. Exercises ``search_from`` advancing
        repeatedly within a single ``_flush_paragraphs`` call.
        """
        from surfaces.interactive_shell.ui import streaming as streaming_module

        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        original_markdown = streaming_module.Markdown
        streaming_module.Markdown = _SpyMarkdown
        try:
            console, _ = _tty_console()
            stream_to_console(
                console,
                label="assistant",
                chunks=_yield_chunks(
                    [
                        "Intro.\n\n",
                        "```python\n",
                        "a\n\n",  # first embedded blank
                        "b\n\n",  # second embedded blank
                        "c\n\n",  # third embedded blank
                        "d\n",
                        "```\n\n",
                        "After.\n\n",
                    ]
                ),
            )
        finally:
            streaming_module.Markdown = original_markdown

        # 3 parses with the fix: intro + fenced block (rendered once when
        # fence closes, after 3 skip iterations advance search_from past
        # each embedded blank) + after. Without the fix, the inner loop
        # would break on the first odd-fence boundary and defer the block
        # + after to a single EOS force-flush → 2 parses.
        assert parse_count[0] == 3, (
            f"expected 3 parses (intro + block + after), got {parse_count[0]}"
        )

    def test_two_consecutive_fences_each_with_blank_line_render_independently(
        self,
    ) -> None:
        """Two fenced blocks back-to-back, each containing an embedded
        ``\\n\\n``. Each block must render as its own paragraph when its
        fence closes — ``search_from`` is reset to 0 after each render so
        the second block isn't blocked by stale state from the first.
        """
        from surfaces.interactive_shell.ui import streaming as streaming_module

        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        original_markdown = streaming_module.Markdown
        streaming_module.Markdown = _SpyMarkdown
        try:
            console, _ = _tty_console()
            stream_to_console(
                console,
                label="assistant",
                chunks=_yield_chunks(
                    [
                        "Intro.\n\n",
                        "```py\nfoo\n\nbar\n```\n\n",  # block 1, embedded blank
                        "```py\nbaz\n\nqux\n```\n\n",  # block 2, embedded blank
                        "End.\n\n",
                    ]
                ),
            )
        finally:
            streaming_module.Markdown = original_markdown

        # 4 parses with the fix: intro + block1 + block2 + end. Without
        # the fix, block1's leading embedded ``\n\n`` would lock the inner
        # loop on the odd-fence break for every subsequent chunk, so the
        # entire tail (block1 + block2 + end) collapses into one EOS
        # force-flush → 2 parses total.
        assert parse_count[0] == 4, (
            f"expected 4 parses (intro + 2 blocks + end), got {parse_count[0]}"
        )

    def test_inline_triple_backtick_mention_does_not_block_paragraph(self) -> None:
        """Single inline ``\\`\\`\\``` mention inside flowing text must not
        be miscounted as an open fence. The substring count would flip to
        odd (1), skipping the paragraph's ``\\n\\n`` boundary and deferring
        rendering to EOS. Only line-start fences count, so two paragraphs
        each render incrementally as their ``\\n\\n`` arrives.
        """
        from surfaces.interactive_shell.ui import streaming as streaming_module

        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        original_markdown = streaming_module.Markdown
        streaming_module.Markdown = _SpyMarkdown
        try:
            console, _ = _tty_console()
            stream_to_console(
                console,
                label="assistant",
                chunks=_yield_chunks(
                    [
                        "The ``` marker opens a code block in markdown.\n\n",
                        "Use it whenever you want to fence example code.\n\n",
                    ]
                ),
            )
        finally:
            streaming_module.Markdown = original_markdown

        # 2 parses: each paragraph flushes on its own ``\n\n``. Without
        # the line-start fence check, the single inline ``` would flip
        # the count to odd, both ``\n\n`` boundaries would be skipped,
        # and the whole stream would force-flush as 1 parse at EOS.
        assert parse_count[0] == 2, (
            f"inline ``` mention blocked paragraph flush — got {parse_count[0]} parses"
        )

    def test_mid_line_triple_backtick_does_not_count_as_fence(self) -> None:
        """A ``\\`\\`\\``` that appears mid-line (not at line start) must
        NOT be counted as a fence boundary by the parity check. Real
        code blocks must keep accumulating across paragraphs until a
        line-start closing fence arrives — the mid-line backticks are
        inline content (often quoted/embedded in prose), not Markdown
        syntax.

        Regression for the drive-by review point: a chunk like
        ``\\nresult: ok\\`\\`\\`\\nmore text`` (closing-fence-shaped
        characters mid-line because the chunk boundary fell there)
        used to be a worry. The ``^\\`\\`\\``` regex with
        ``re.MULTILINE`` matches only line-start fences, so this
        scenario stays correct.
        """
        from surfaces.interactive_shell.ui import streaming as streaming_module

        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        original_markdown = streaming_module.Markdown
        streaming_module.Markdown = _SpyMarkdown
        try:
            console, buf = _tty_console()
            stream_to_console(
                console,
                label="assistant",
                chunks=_yield_chunks(
                    [
                        # Real fence opens at line start.
                        "```py\n",
                        "x = 1\n",
                        # Mid-line backticks inside the still-open fence.
                        # MUST be ignored by the parity check — fence
                        # stays open until a real closing fence at line
                        # start.
                        "result: ok```\n",
                        "y = 2\n",
                        # Now the real closing fence at line start.
                        "```\n\n",
                        "After the block.\n\n",
                    ]
                ),
            )
        finally:
            streaming_module.Markdown = original_markdown

        # 2 parses: (1) the entire fenced code block as one Markdown
        # parse — proves the mid-line ``` didn't prematurely flush it;
        # (2) the "After the block." paragraph. Without the line-anchor,
        # the mid-line ``` would flip the parity, close the fence
        # early, and we'd see 3+ parses with broken code rendering.
        assert parse_count[0] == 2, f"mid-line ``` was miscounted — got {parse_count[0]} parses"
        # And the mid-line backticks must reach the rendered output as
        # plain text (inside the code block), not get eaten as syntax.
        output = _strip_ansi(buf.getvalue())
        assert "result: ok" in output

    def test_unclosed_fence_with_embedded_blank_line_renders_at_eos(self) -> None:
        """Unclosed fence containing an embedded ``\\n\\n`` must not hang
        the inner loop and must surface the partial buffer at end-of-stream.

        With the skip-past-open-fence logic, the inner loop advances
        ``search_from`` past each embedded ``\\n\\n``, eventually returns
        ``-1`` from ``find``, and exits cleanly. The outer ``finally``
        then force-flushes the partial buffer so the user sees the
        truncated response rather than nothing.
        """
        chunks = [
            "```py\n",
            "a = 1\n\n",  # blank inside fence
            "b = 2\n",  # stream ends without closing the fence
        ]

        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(chunks),
        )

        # Both code lines must appear in the rendered output — the
        # partial fence is force-flushed at EOS so the user sees what
        # was streamed before the LLM cut off.
        output = _strip_ansi(buf.getvalue())
        assert "a = 1" in output
        assert "b = 2" in output
        assert "a = 1" in result
        assert "b = 2" in result

    def test_returns_empty_string_when_stream_is_empty(self) -> None:
        """An empty stream must not leave a frozen spinner on screen."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks([]),
        )

        assert result == ""
        # The marker is inline on the first paragraph, so an empty stream prints
        # no marker at all — and no spinner residue at finalize.
        assert "Ω" not in _strip_ansi(buf.getvalue())


class TestMidStreamError:
    """Errors inside the stream propagate while the partial buffer stays on screen."""

    def test_exception_propagates_with_partial_visible(self) -> None:
        def _broken_stream() -> Iterator[str]:
            yield "partial "
            yield "answer"
            raise RuntimeError("upstream 503")

        console, buf = _tty_console()

        with pytest.raises(RuntimeError, match="upstream 503"):
            stream_to_console(
                console,
                label="assistant",
                chunks=_broken_stream(),
            )

        # The partial response was rendered before the exception propagated,
        # so the caller can surface an error label below it.
        output = _strip_ansi(buf.getvalue())
        assert "partial answer" in output

    def test_keyboard_interrupt_propagates_with_partial_visible(self) -> None:
        """KeyboardInterrupt mid-stream propagates after the partial renders.

        The double-press absorption logic that used to live here was moved
        to the prompt_toolkit cancel key bindings (see
        :func:`interactive_shell.ui.input_prompt.key_bindings.build_cancel_key_bindings`)
        — the streaming code just lets ``KeyboardInterrupt`` propagate,
        and the ``finally`` block in :func:`stream_to_console` ensures
        the partial buffer is rendered.
        """

        class _ChunksThenKbd:
            __slots__ = ("_i",)

            def __init__(self) -> None:
                self._i = 0

            def __iter__(self) -> Iterator[str]:
                return self

            def __next__(self) -> str:
                parts = ("partial ", "answer")
                if self._i < len(parts):
                    c = parts[self._i]
                    self._i += 1
                    return c
                raise KeyboardInterrupt

        console, buf = _tty_console()
        with pytest.raises(KeyboardInterrupt):
            stream_to_console(
                console,
                label="assistant",
                chunks=iter(_ChunksThenKbd()),
            )

        output = _strip_ansi(buf.getvalue())
        # Partial is rendered before the KI propagates — the ``finally``
        # in stream_to_console fires the Markdown render of the buffer.
        assert "partial answer" in output


class TestTimingFooter:
    """A small dim ``· Ns`` footer appears after a rendered live response."""

    def test_no_timing_footer_after_streamed_response(self) -> None:
        """The per-turn ``· Ns · ↓ tokens`` footer was removed for compactness."""
        console, buf = _tty_console()
        stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["hello"]),
        )

        output = _strip_ansi(buf.getvalue())
        assert re.search(r"·\s+\d+\.\d+s", output) is None

    def test_footer_skipped_when_stream_is_empty(self) -> None:
        """Empty stream must not print a timing footer under nothing."""
        console, buf = _tty_console()
        stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks([]),
        )

        output = _strip_ansi(buf.getvalue())
        assert re.search(r"·\s+\d+\.\d+s", output) is None

    def test_footer_skipped_when_response_is_suppressed(self) -> None:
        """Suppressed machine-readable payloads should not get a timing footer either."""
        console, buf = _tty_console()
        stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(['{"actions"', ":[]}"]),
            suppress_if_starts_with="{",
        )

        output = _strip_ansi(buf.getvalue())
        assert re.search(r"·\s+\d+\.\d+s", output) is None


class TestRenderResponseHeader:
    """``render_response_header`` is the bullet-row marker shared with
    ``action_turn.run_action_tool_turn`` — three call sites collapsed
    to one helper, so we lock in the visible output here.
    """

    def test_emits_bullet_glyph_without_role_label(self) -> None:
        console, buf = _tty_console()
        render_response_header(console, "assistant")
        output = _strip_ansi(buf.getvalue())
        assert "Ω" in output
        assert "assistant" not in output

    def test_role_label_is_accepted_but_not_painted(self) -> None:
        """Callers still pass ``STREAM_LABEL_ANSWER`` / ``STREAM_LABEL_ASSISTANT``
        for port compatibility; the shell no longer echoes the dim role word."""
        console, buf = _tty_console()
        render_response_header(console, "answer")
        assert "answer" not in _strip_ansi(buf.getvalue())
        assert "Ω" in _strip_ansi(buf.getvalue())


class TestFormatTokenCountShort:
    """Shared helper used by both the streaming footer and the live spinner."""

    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (0, "0"),
            (1, "1"),
            (999, "999"),
            (1000, "1.0k"),
            (1234, "1.2k"),
            (10000, "10.0k"),
            (123456, "123.5k"),
        ],
    )
    def test_formats_at_boundaries(self, count: int, expected: str) -> None:
        assert format_token_count_short(count) == expected


class _ProgressConsole(Console):
    """Console with the loop's :class:`StreamingConsole` shape — exposes
    ``update_streaming_progress`` and ``cancel_requested`` for the
    streaming layer's ``getattr`` dispatch.
    """

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        cancel_after_n_progress_calls: int | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.progress_calls: list[int] = []
        self._cancel_event = cancel_event or threading.Event()
        self._cancel_after = cancel_after_n_progress_calls

    def update_streaming_progress(self, bytes_received: int) -> None:
        self.progress_calls.append(bytes_received)
        if self._cancel_after is not None and len(self.progress_calls) >= self._cancel_after:
            self._cancel_event.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()


class TestProgressHook:
    """``stream_to_console`` invokes the optional ``update_streaming_progress``
    hook on the console and throttles the call rate so worker-thread → UI
    cross-thread queueing isn't flooded on long streams.
    """

    def test_progress_hook_called_with_running_byte_count(self) -> None:
        buf = io.StringIO()
        console = _ProgressConsole(file=buf, force_terminal=True, color_system=None, width=80)
        chunks = ["Hello, ", "world", "!"]
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(chunks),
        )

        assert result == "Hello, world!"
        assert console.progress_calls, "progress hook never fired"
        # Counts must be monotonically non-decreasing — the streaming
        # layer pushes a *running* byte total, never a per-chunk delta.
        assert console.progress_calls == sorted(console.progress_calls)
        # Each reported count must reflect bytes that *had* arrived by
        # that point in the stream — never exceed the final total.
        assert console.progress_calls[-1] <= len(result)

    def test_progress_hook_throttled_on_burst_streams(self) -> None:
        """A burst of 200 small chunks must not produce 200 hook calls.

        Throttling target is ~10/s; the test stream finishes well under
        a second so we expect a small handful of calls (not one per
        chunk). The exact count is timing-dependent — assert ``<= 50``
        as a generous upper bound that still proves throttling fires.
        """
        buf = io.StringIO()
        console = _ProgressConsole(file=buf, force_terminal=True, color_system=None, width=80)
        burst = ["x"] * 200
        stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(burst),
        )

        assert len(console.progress_calls) <= 50, (
            f"throttle did not fire — got {len(console.progress_calls)} calls"
        )

    def test_no_hook_when_console_lacks_method(self) -> None:
        """Plain ``Console`` (no progress method) must stream cleanly."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["alpha", "beta"]),
        )
        assert result == "alphabeta"

    def test_progress_hook_failure_does_not_truncate_response(self) -> None:
        """A flaky status widget must never lose response content."""

        class _BrokenConsole(Console):
            def __init__(self) -> None:
                super().__init__(
                    file=io.StringIO(),
                    force_terminal=True,
                    color_system=None,
                    width=80,
                )

            def update_streaming_progress(self, bytes_received: int) -> None:  # noqa: ARG002
                raise RuntimeError("widget gone")

        console = _BrokenConsole()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["full ", "answer"]),
        )
        assert result == "full answer"


class TestParagraphFlushThrottle:
    """Long single-paragraph streams must not pay O(n²) work re-joining
    the buffer on every chunk. The fast-path skips the join when no
    paragraph boundary could possibly land in the new chunk.
    """

    def _spy_markdown_parses(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        """Wrap ``streaming.Markdown`` so each construction increments a counter."""
        from surfaces.interactive_shell.ui import streaming as streaming_module

        parse_count = [0]
        real_markdown = streaming_module.Markdown

        class _SpyMarkdown(real_markdown):  # type: ignore[misc, valid-type]
            def __init__(self, text: str, **kwargs) -> None:
                parse_count[0] += 1
                super().__init__(text, **kwargs)

        monkeypatch.setattr(streaming_module, "Markdown", _SpyMarkdown)
        return parse_count

    def test_long_single_paragraph_renders_progressively(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``\\n\\n`` in any chunk still paints before end-of-stream.

        Long prose requests such as "explain OpenSRE in 10k words" may arrive as
        one giant paragraph. The renderer should not wait for EOS before the
        user sees body text, while still keeping Markdown parses bounded.
        """
        parse_count = self._spy_markdown_parses(monkeypatch)
        console, _ = _tty_console()

        # 500 chunks, each a few words, no paragraph breaks.
        chunks = [f"word{i} " for i in range(500)]
        result = stream_to_console(console, label="assistant", chunks=_yield_chunks(chunks))

        assert "word0" in result
        assert "word499" in result
        assert 2 <= parse_count[0] <= 10, (
            f"expected bounded progressive parses, got {parse_count[0]}"
        )

    def test_paragraph_boundary_per_chunk_renders_once_per_paragraph(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each ``\\n\\n`` boundary triggers exactly one Markdown
        parse — the trailing tail is force-flushed at end."""
        parse_count = self._spy_markdown_parses(monkeypatch)
        console, _ = _tty_console()

        chunks = [
            "para 1.\n\n",
            "para 2.\n\n",
            "para 3.\n\n",
            "trailing tail",
        ]
        stream_to_console(console, label="assistant", chunks=_yield_chunks(chunks))

        # 3 in-loop renders + 1 force-flush at end = 4 total.
        assert parse_count[0] == 4

    def test_chunks_with_only_single_newlines_skip_flush(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lists / code with single ``\\n`` separators don't trigger
        flush until a real ``\\n\\n`` boundary closes the block.
        Keeping multi-line list/table syntax intact is required for
        Rich's Markdown renderer to produce a proper Table / bullet
        list rather than rendering each row as a standalone block."""
        parse_count = self._spy_markdown_parses(monkeypatch)
        console, _ = _tty_console()

        chunks = [
            "- item 1\n",
            "- item 2\n",
            "- item 3\n",
            # No \n\n — only force-flush at end.
        ]
        stream_to_console(console, label="assistant", chunks=_yield_chunks(chunks))

        assert parse_count[0] == 1


class TestCancelPolling:
    """``stream_to_console`` polls ``console.cancel_requested`` between
    chunks so an Esc-driven cancel signal stops the worker-thread stream
    before it drains the iterator.
    """

    def test_cancel_set_before_stream_returns_empty_partial(self) -> None:
        buf = io.StringIO()
        cancel_event = threading.Event()
        cancel_event.set()  # cancel before any chunk is pulled
        console = _ProgressConsole(
            cancel_event=cancel_event,
            file=buf,
            force_terminal=True,
            color_system=None,
            width=80,
        )

        # If the cancel poll didn't work, the iterator below would
        # raise (it's a single-use generator).
        chunks_iter = _yield_chunks(["a", "b", "c"])
        result = stream_to_console(console, label="assistant", chunks=chunks_iter)
        assert result == ""

    def test_cancel_mid_stream_truncates_buffer(self) -> None:
        """Cancel signalled mid-stream stops further chunk reads.

        Uses a generator that flips the cancel flag from inside its own
        yield loop — that's deterministic regardless of throttling, since
        the next iteration of ``stream_to_console``'s loop checks the
        cancel flag *before* pulling the next chunk.
        """
        buf = io.StringIO()
        cancel_event = threading.Event()
        console = _ProgressConsole(
            cancel_event=cancel_event,
            file=buf,
            force_terminal=True,
            color_system=None,
            width=80,
        )

        chunks_yielded: list[int] = []

        def _chunks_with_cancel() -> Iterator[str]:
            for i in range(20):
                chunks_yielded.append(i)
                if i == 3:
                    cancel_event.set()
                yield f"chunk{i} "

        result = stream_to_console(console, label="assistant", chunks=_chunks_with_cancel())

        # The generator should not have been pumped through to chunk 19 —
        # ``stream_to_console`` should have broken out of its loop once
        # the cancel event was visible.
        assert max(chunks_yielded) < 19, (
            f"generator yielded too many chunks — got up to {max(chunks_yielded)}"
        )
        # The result must include chunks read before the cancel was
        # observed and must not include the trailing chunks.
        assert result.startswith("chunk0 ")
        assert "chunk19" not in result

    def test_no_cancel_attr_means_stream_runs_to_completion(self) -> None:
        """A console without ``cancel_requested`` must drain normally."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["one ", "two ", "three"]),
        )
        assert result == "one two three"


class TestSuppressionPeek:
    """``suppress_if_starts_with`` skips live rendering for content the caller will handle."""

    def test_suppresses_and_drains_when_first_char_matches(self) -> None:
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(['{"actions"', ":[]", "}"]),
            suppress_if_starts_with="{",
        )

        assert result == '{"actions":[]}'
        # No bullet header, no markdown, no live-region artifacts.
        output = _strip_ansi(buf.getvalue())
        assert "Ω" not in output
        assert '{"actions"' not in output

    def test_renders_normally_when_first_char_does_not_match(self) -> None:
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["Hello, ", "world"]),
            suppress_if_starts_with="{",
        )

        assert result == "Hello, world"
        output = _strip_ansi(buf.getvalue())
        assert "Ω" in output
        assert "Hello, world" in output

    def test_skips_leading_whitespace_before_deciding(self) -> None:
        """Leading whitespace must not block the suppression peek."""
        console, buf = _tty_console()
        result = stream_to_console(
            console,
            label="assistant",
            chunks=_yield_chunks(["  \n", '{"action"', ':"slash"}']),
            suppress_if_starts_with="{",
        )

        assert result == '  \n{"action":"slash"}'
        output = _strip_ansi(buf.getvalue())
        assert "Ω" not in output


class TestRenderMarkdownBlock:
    """The single-shot Markdown renderer shared with the action observer."""

    def test_renders_heading_and_body_text(self) -> None:
        console, buf = _non_tty_console()

        render_markdown_block(console, "### [2/8] Token configuration\nVerifying the token…")

        output = _strip_ansi(buf.getvalue())
        assert "[2/8] Token configuration" in output
        assert "Verifying the token" in output

    def test_blank_text_prints_nothing(self) -> None:
        console, buf = _non_tty_console()

        render_markdown_block(console, "   \n  ")

        assert buf.getvalue() == ""

    def test_escapes_dunder_filenames(self) -> None:
        """``__init__.py`` must not be eaten as Markdown strong emphasis."""
        console, buf = _non_tty_console()

        render_markdown_block(console, "Check __init__.py for the export list.")

        assert "__init__.py" in _strip_ansi(buf.getvalue())

    def test_strips_terminal_controls_from_model_prose(self) -> None:
        """Intermediate/closing model commentary must not inject ESC/BEL."""
        console, buf = _non_tty_console()

        render_markdown_block(
            console,
            "### Phase\x1b[2J\nChecking the token\x07…",
        )

        output = buf.getvalue()
        assert "\x1b" not in output
        assert "\x07" not in output
        plain = _strip_ansi(output)
        assert "Phase" in plain
        assert "Checking the token" in plain

    def test_collapses_url_heavy_json_the_model_pasted(self) -> None:
        """The real gh case: URL-heavy JSON, no inline marker. Long URLs dilute a
        char ratio, so detection keys off the ``":`` separators instead."""
        console, buf = _non_tty_console()
        dump = (
            '"login":"Tracer-Cloud",'
            '"followers_url":"https://api.github.com/users/Tracer-Cloud/followers",'
            '"following_url":"https://api.github.com/users/Tracer-Cloud/following{/other_user}",'
            '"gists_url":"https://api.github.com/users/Tracer-Cloud/gists{/gist_id}",'
            '"organizations_url":"https://api.github.com/users/Tracer-Cloud/orgs",'
            '"type":"Organization","site_admin":false'
        )

        render_markdown_block(console, dump)

        out = _strip_ansi(buf.getvalue())
        assert "tool output omitted" in out  # collapsed to a one-line marker
        assert "followers_url" not in out  # the raw blob is gone

    def test_collapses_a_dump_flagged_by_the_truncated_marker(self) -> None:
        console, buf = _non_tty_console()
        rows = "\n".join(f"repo-{i}\tmain\t2026-01-01\t{i}\t0\t0" for i in range(20))

        render_markdown_block(console, f"{rows}\n… (output truncated)")

        assert "tool output omitted" in _strip_ansi(buf.getvalue())

    def test_keeps_the_summary_and_collapses_only_the_pasted_blob(self) -> None:
        """When a summary sentence and a pasted blob arrive as one block, keep the
        summary and collapse only the blob — never nuke the whole reply."""
        console, buf = _non_tty_console()
        reply = (
            "Repository is public with 10,984 stars.\n\n"
            '"login":"Tracer-Cloud","followers_url":"https://api.github.com/users/Tracer-Cloud/f",'
            '"gists_url":"https://api.github.com/users/Tracer-Cloud/gists{/gist_id}",'
            '"organizations_url":"https://api.github.com/users/Tracer-Cloud/orgs",'
            '"type":"Organization","site_admin":false'
        )

        render_markdown_block(console, reply)

        out = _strip_ansi(buf.getvalue())
        assert "Repository is public" in out  # summary survives
        assert "tool output omitted" in out  # blob collapsed
        assert "followers_url" not in out

    def test_keeps_a_short_inline_json_snippet(self) -> None:
        console, buf = _non_tty_console()

        render_markdown_block(console, 'The API returned `{"ok": true, "count": 3}`.')

        out = _strip_ansi(buf.getvalue())
        assert '"ok"' in out
        assert "tool output omitted" not in out

    def test_keeps_ordinary_prose(self) -> None:
        console, buf = _non_tty_console()

        render_markdown_block(console, "The repository is public and its default branch is main.")

        out = _strip_ansi(buf.getvalue())
        assert "default branch is main" in out
        assert "tool output omitted" not in out

    def test_keeps_fenced_code_the_model_meant_to_show(self) -> None:
        """A fenced block signals intent — never collapse it."""
        console, buf = _non_tty_console()

        render_markdown_block(console, '```json\n{"stars": 10984, "forks": 1603}\n```')

        out = _strip_ansi(buf.getvalue())
        assert "10984" in out
        assert "tool output omitted" not in out

    def test_collapses_an_independent_dump_beside_a_fenced_block(self) -> None:
        """A fence exempts only its region — a separate dump still collapses."""
        console, buf = _non_tty_console()
        dump = (
            '"login":"Tracer-Cloud",'
            '"followers_url":"https://api.github.com/users/Tracer-Cloud/followers",'
            '"following_url":"https://api.github.com/users/Tracer-Cloud/following{/other_user}",'
            '"gists_url":"https://api.github.com/users/Tracer-Cloud/gists{/gist_id}",'
            '"organizations_url":"https://api.github.com/users/Tracer-Cloud/orgs",'
            '"type":"Organization","site_admin":false'
        )
        reply = f'Use this snippet:\n\n```json\n{{"stars": 10984, "forks": 1603}}\n```\n\n{dump}'

        render_markdown_block(console, reply)

        out = _strip_ansi(buf.getvalue())
        assert "10984" in out  # intentional fence survives
        assert "tool output omitted" in out  # independent dump collapsed
        assert "followers_url" not in out

    def test_keeps_dump_like_content_inside_a_fenced_block(self) -> None:
        """Blank lines inside a fence must not expose the inner body to collapsing."""
        console, buf = _non_tty_console()
        inner = (
            '{"note":"inside-fence",'
            '"homepage":"https://example.com/inside-fence/homepage",'
            '"clone_url":"https://example.com/inside-fence.git",'
            '"ssh_url":"git@example.com:inside-fence/repo.git",'
            '"description":"kept because it sits inside the intentional fence"}'
        )
        reply = f'```json\n{{"stars": 10984}}\n\n{inner}\n```'

        render_markdown_block(console, reply)

        out = _strip_ansi(buf.getvalue())
        assert "10984" in out
        assert "inside-fence" in out
        assert "tool output omitted" not in out


class TestDeferWantMeToCloser:
    """Gather path holds drifted Want-me-to until the canonical rewrite paints."""

    def test_tty_holds_closer_but_paints_evidence(self) -> None:
        console, buf = _tty_console()
        dual = (
            "Checkout looks unhealthy.\n\n"
            "**Want me to:**\n"
            "1. run a full investigation once you paste, or\n"
            "2. `/integrations setup grafana`?"
        )
        paint = stream_to_console_state(
            console,
            label="assistant",
            chunks=_yield_chunks([dual]),
            defer_want_me_to_closer=True,
        )
        visible = _strip_ansi(buf.getvalue())
        assert paint.deferred_closer is True
        assert "Checkout looks unhealthy" in visible
        assert "/integrations setup grafana" not in visible
        assert "· " not in visible  # footer held until finish

        finish_deferred_closer(
            console,
            "Checkout looks unhealthy.\n\n**Want me to:** run a full investigation",
            footer_elapsed_s=paint.footer_elapsed_s,
            footer_total_bytes=paint.footer_total_bytes,
        )
        after = _strip_ansi(buf.getvalue())
        assert "run a full investigation" in after
        assert "/integrations setup grafana" not in after

    def test_non_tty_defers_entire_paint_when_closer_present(self) -> None:
        console, buf = _non_tty_console()
        text = "Empty.\n\n**Want me to:** kick off a full investigation after paste?"
        paint = stream_to_console_state(
            console,
            label="assistant",
            chunks=_yield_chunks([text]),
            defer_want_me_to_closer=True,
        )
        assert paint.deferred_closer is True
        assert buf.getvalue() == ""
