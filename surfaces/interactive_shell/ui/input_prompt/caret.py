"""Rendered composer cursor used while the terminal cursor anchors resize."""

from __future__ import annotations

from typing import cast

from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text.base import OneStyleAndTextTuple
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput
from prompt_toolkit.layout.utils import explode_text_fragments


class ComposerCaret(Processor):
    """Paint the buffer cursor because the hardware cursor anchors the live region."""

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        app = get_app()
        if (
            app.is_done
            or app.layout.current_control is not ti.buffer_control
            or ti.document.cursor_position_row != ti.lineno
        ):
            return Transformation(ti.fragments)

        cursor_index = ti.source_to_display(ti.document.cursor_position_col)
        fragments = explode_text_fragments(ti.fragments)
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
