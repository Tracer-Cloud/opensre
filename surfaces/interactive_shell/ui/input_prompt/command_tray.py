"""Bounded, composer-attached presentation of prompt-toolkit completions."""

from __future__ import annotations

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import GetLinePrefixCallable, UIContent, UIControl

from surfaces.interactive_shell.ui.input_prompt.completion import completion_preview_text
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_text_width

_MAX_VISIBLE_ITEMS = 6
_INLINE_DESCRIPTION_MIN_WIDTH = 60


class CommandTrayControl(UIControl):
    """Render the current completion window without covering live status or input."""

    def __init__(self, buffer: Buffer) -> None:
        self.buffer = buffer
        self._start = 0

    def reset(self) -> None:
        self._start = 0

    def preferred_height(
        self,
        width: int,
        max_available_height: int,
        wrap_lines: bool,  # noqa: ARG002 — prompt-toolkit override signature
        get_line_prefix: GetLinePrefixCallable | None,  # noqa: ARG002 — same signature
    ) -> int:
        state = self.buffer.complete_state
        if state is None or not state.completions:
            return 0
        # Header, a separated navigation hint, and (on narrow terminals) detail.
        return min(
            max_available_height,
            min(_MAX_VISIBLE_ITEMS, len(state.completions))
            + 3
            + (width < _INLINE_DESCRIPTION_MIN_WIDTH),
        )

    def create_content(self, width: int, height: int) -> UIContent:
        state = self.buffer.complete_state
        if state is None or not state.completions or width < 1 or height < 1:
            return UIContent()

        selected = state.complete_index or 0
        show_chrome = height >= 3
        show_detail = width < _INLINE_DESCRIPTION_MIN_WIDTH and height >= 4
        desired_rows = min(_MAX_VISIBLE_ITEMS, len(state.completions))
        show_hint_spacer = height >= desired_rows + 3 + show_detail
        rows = min(
            _MAX_VISIBLE_ITEMS,
            len(state.completions),
            height - 2 * show_chrome - show_detail - show_hint_spacer,
        )
        self._start = max(0, min(self._start, selected, len(state.completions) - rows))
        if selected >= self._start + rows:
            self._start = selected - rows + 1
        visible = state.completions[self._start : self._start + rows]
        lines: list[StyleAndTextTuples] = []
        if show_chrome:
            counter = f"{selected + 1} / {len(state.completions)}"
            title = "Commands" if state.original_document.text == "/" else "Completions"
            header = title + " " * max(1, width - len(title) - len(counter) - 2) + counter
            lines.append(self._line(header, width, "class:command-tray.hint"))

        name_width = min(24, max(prompt_text_width(c.display_text) for c in visible))
        for offset, completion in enumerate(visible):
            current = self._start + offset == selected
            style = "class:command-tray.current" if current else "class:command-tray"
            marker = "› " if current else "  "
            name = clip_prompt_text(completion.display_text, name_width)
            text = " " + marker + name
            if width >= _INLINE_DESCRIPTION_MIN_WIDTH:
                text += " " * (name_width - prompt_text_width(name) + 2)
                meta = clip_prompt_text(
                    completion.display_meta_text, width - prompt_text_width(text) - 1
                )
                padding = " " * max(0, width - prompt_text_width(text + meta))
                lines.append([(style, text), (style + ".description", meta), (style, padding)])
            else:
                lines.append(self._line(marker + completion.display_text, width, style))

        if show_detail:
            lines.append(
                self._line(
                    completion_preview_text(include_label=False), width, "class:command-tray.hint"
                )
            )
        if show_hint_spacer:
            lines.append(self._line("", width, "class:command-tray.hint"))
        if show_chrome:
            navigation = "↑↓ navigate   Tab complete   Esc close"
            if width < 44:
                navigation = "↑↓ move  Tab fill  Esc close"
            if (
                self._start + rows < len(state.completions)
                and width >= _INLINE_DESCRIPTION_MIN_WIDTH
            ):
                navigation += " " * max(2, width - len(navigation) - 8) + "↓ more"
            lines.append(self._line(navigation, width, "class:command-tray.hint"))
        return UIContent(get_line=lines.__getitem__, line_count=len(lines), show_cursor=False)

    @staticmethod
    def _line(text: str, width: int, style: str) -> StyleAndTextTuples:
        # Keep each row within terminal cells, including wide glyphs and untrusted metadata.
        text = " " + clip_prompt_text(text, max(0, width - 2))
        return [(style, text + " " * max(0, width - prompt_text_width(text)))]
