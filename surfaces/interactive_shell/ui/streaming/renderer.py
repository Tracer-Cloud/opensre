"""Markdown block and response-header rendering shared by the streamed and unstreamed reply paths."""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

import infrastructure.terminal.theme as ui_theme
from core.agent_harness.spi.prompt_chrome import normalize_three_tier_spacing
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.text import looks_like_data_blob
from surfaces.interactive_shell.ui.transcript import (
    TranscriptRole,
    transcript_gutter,
)

if TYPE_CHECKING:
    from rich.markdown import Markdown

STREAM_LABEL_ASSISTANT = "assistant"
STREAM_LABEL_ANSWER = "answer"

# Rich Markdown treats ``__init__.py`` as bold emphasis around ``init``, which
# strips the underscores and restyles that span. Escape dunder filenames so
# path-heavy reports (architecture audits, etc.) keep uniform body color.
_DUNDER_FILENAME_RE = re.compile(r"__([A-Za-z0-9_]+)__(?=\.py\b)")


def _escape_markdown_dunder_filenames(text: str) -> str:
    """Neutralize ``__name__.py`` so Markdown does not parse it as strong emphasis."""
    return _DUNDER_FILENAME_RE.sub(r"\_\_\1\_\_", text)


# The model sometimes pastes a tool's raw result (JSON, listings) into its reply
# instead of answering in prose. Collapse such a block to a compact marker.
_DUMP_TRUNCATED_MARKER = "output truncated"
_DUMP_MIN_CHARS = 200
_DUMP_MIN_JSON_KEYS = 3
_DUMP_STRUCTURAL_RATIO = 0.15
# Line-start fences only — matches the streaming splitter so an inline
# ``Use ``` to fence code`` mention does not freeze dump collapsing.
_FENCE_LINE_RE = re.compile(r"^```", re.MULTILINE)


def _looks_like_raw_dump(text: str) -> bool:
    """Whether a paragraph in the model's reply is an echoed tool result.

    Conservative by design: a paragraph collapses only when it is large and
    dense (or carries the ``output truncated`` marker), so real prose is never
    mistaken for a dump. The tool-result hider in ``display_text`` runs the same
    mechanism with a smaller floor, since a raw payload is always data.
    """
    return looks_like_data_blob(
        text,
        min_chars=_DUMP_MIN_CHARS,
        min_json_keys=_DUMP_MIN_JSON_KEYS,
        structural_ratio=_DUMP_STRUCTURAL_RATIO,
        truncation_marker=_DUMP_TRUNCATED_MARKER,
    )


def _collapse_paragraph(paragraph: str) -> str:
    """Collapse *paragraph* to a marker when it is a raw dump, else return it."""
    stripped = paragraph.strip()
    if not _looks_like_raw_dump(stripped):
        return paragraph
    lines = stripped.count("\n") + 1
    noun = "line" if lines == 1 else "lines"
    return f"_[tool output omitted — {lines} {noun}]_"


def _collapse_raw_dumps(text: str) -> str:
    """Replace echoed raw tool-output paragraphs with a one-line marker.

    Fenced regions stay intact — a fence means the model meant to show them.
    Independent paragraphs still collapse, including when they sit beside a
    fence in the same reply. Works per blank-line-separated paragraph so a
    summary sentence beside a pasted blob keeps the summary and collapses
    only the blob, whether the reply arrives whole (finalize path) or
    paragraph-by-paragraph (streaming).
    """
    parts = re.split(r"\n[ \t]*\n", text)
    collapsed: list[str] = []
    inside_fence = False
    for part in parts:
        fence_count = len(_FENCE_LINE_RE.findall(part))
        if inside_fence or fence_count:
            collapsed.append(part)
        else:
            collapsed.append(_collapse_paragraph(part))
        if fence_count % 2:
            inside_fence = not inside_fence
    return "\n\n".join(collapsed)


def _build_markdown_block(text: str) -> Markdown:
    """Build a Markdown renderable with the shared escaping and code theme.

    Strips terminal controls (ESC/CR/BEL/C1) while keeping LF/Tab so multi-line
    model prose cannot spoof the TTY. All whole and streamed markdown paths
    build through this helper.

    Reads the ``Markdown`` class off the already-loaded package module via
    ``sys.modules`` rather than importing it here (directly, or by importing
    the package back) — tests substitute the class by patching
    ``surfaces.interactive_shell.ui.streaming.Markdown``, and any import
    binding in this module would bind a copy that patch never reaches. A
    ``sys.modules`` lookup carries no static import edge back to the package,
    so it does not create the back-edge an ``import`` statement here would.
    """
    assert __package__  # always set for a package submodule
    package = sys.modules[__package__]
    safe = strip_terminal_controls(text, keep_whitespace=True)
    safe = _collapse_raw_dumps(safe)
    spaced = normalize_three_tier_spacing(safe)
    return package.Markdown(  # type: ignore[no-any-return]
        _escape_markdown_dunder_filenames(spaced.rstrip()),
        code_theme=ui_theme.MARKDOWN_CODE_THEME,
    )


def render_markdown_block(console: Console, text: str) -> None:
    """Render one complete Markdown block using the shared markdown theme.

    The single rendering path for model prose that arrives whole (not
    chunk-streamed) — e.g. the action agent's intermediate phase headers —
    so every markdown surface shares one escaping/theme policy. Terminal
    controls are stripped inside ``_build_markdown_block``.
    """
    visible = text
    if not visible.strip():
        return
    with console.use_theme(ui_theme.MARKDOWN_THEME):
        console.print(_build_markdown_block(visible))


# Reply rows stop short of the last column: a row padded to the full terminal
# width followed by a newline leaves a blank line and a shifted continuation
# in Terminal.app.
_RIGHT_MARGIN = 2
_MIN_REPLY_WIDTH = 20


def reply_width(console: Console) -> int:
    """Render width for reply rows: the console width less the right margin."""
    return max(_MIN_REPLY_WIDTH, console.width - _RIGHT_MARGIN)


def render_note_block(console: Console, text: str) -> None:
    """Render intermediate narration with an explicit working-state label."""
    visible = text
    if not visible.strip():
        return
    with console.use_theme(ui_theme.MARKDOWN_THEME):
        console.print(
            transcript_gutter(
                _build_markdown_block(visible),
                lead=True,
                role=TranscriptRole.WORKING,
                label_style=_transcript_label_style(),
            ),
            style=str(ui_theme.SECONDARY),
            width=reply_width(console),
        )


def _transcript_label_style() -> str:
    """Bold accent for the assistant marker and working-state labels."""
    return ui_theme.reply_marker_style()


def render_reply_block(console: Console, text: str, *, lead: bool = True) -> None:
    """Render a whole assistant reply inside the marked transcript gutter."""
    visible = text
    if not visible.strip():
        return
    with console.use_theme(ui_theme.MARKDOWN_THEME):
        # Explicit TEXT on the row so plain paragraphs never fall through to the
        # terminal default white, which would flatten the label/body hierarchy.
        console.print(
            transcript_gutter(
                _build_markdown_block(visible),
                lead=lead,
                label_style=_transcript_label_style(),
            ),
            style=str(ui_theme.TEXT),
            width=reply_width(console),
        )


def render_response_header(console: Console, label: str) -> None:
    """Print the marker that opens every assistant response.

    Shared with ``action_turn.run_action_tool_turn`` so the planned-actions path
    and the streaming response path use the exact same prefix.

    ``label`` is accepted for port compatibility (callers still pass
    ``answer`` / ``assistant``) but is not painted.
    """
    del label
    console.print(Text(TranscriptRole.ASSISTANT.value, style=_transcript_label_style()))
