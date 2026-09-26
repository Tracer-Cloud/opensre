"""Rendered composer cursor used while the terminal cursor anchors resize."""

from __future__ import annotations

from typing import cast

from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text.base import OneStyleAndTextTuple
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput
from prompt_toolkit.layout.utils import explode_text_fragments
from prompt_toolkit.utils import get_cwidth


class ComposerCaret(Processor):
    """Paint the buffer cursor because the hardware cursor anchors the live region."""

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        if get_app().is_done or ti.document.cursor_position_row != ti.lineno:
            return Transformation(ti.fragments)

        cursor_column = ti.source_to_display(ti.document.cursor_position_col)
        fragments = explode_text_fragments(ti.fragments)
        display_column = 0
        cursor_index = len(fragments)
        for index, (_, text, *_) in enumerate(fragments):
            if display_column >= cursor_column:
                cursor_index = index
                break
            display_column += get_cwidth(text)

        if cursor_index < len(fragments):
            style, text, *handler = fragments[cursor_index]
            fragments[cursor_index] = cast(
                OneStyleAndTextTuple,
                (f"{style} class:composer-cursor", text, *handler),
            )
        else:
            fragments.append(("class:composer-cursor", " "))
        return Transformation(fragments)


__all__ = ["ComposerCaret"]
