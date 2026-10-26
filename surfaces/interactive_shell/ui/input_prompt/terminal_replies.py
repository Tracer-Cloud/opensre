"""Tell terminal replies and Option chords apart from a press of the Esc key.

prompt-toolkit parses one read into one batch of keys. A reply it has no
mapping for (DA1, DECRQM, an OSC colour report) or an Option chord therefore
reaches the key processor as Escape with the rest of the sequence queued behind
it, while a deliberate Esc is flushed on its own after ``ttimeoutlen``.
"""

from __future__ import annotations

from collections import deque

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding.key_processor import KeyPress, KeyPressEvent, KeyProcessor
from prompt_toolkit.keys import Keys

# Focus in/out reports (DECSET 1004) — input escapes, not colour codes. Unmapped,
# prompt-toolkit parses each one as Escape followed by the literal keys ``[I``.
_FOCUS_REPORT_SEQUENCES = ("\x1b[I", "\x1b[O")
# Replies the key processor receives as keys of their own; neither is a keystroke.
_REPLY_KEYS = frozenset({Keys.CPRResponse, Keys.Ignore})
# After Escape, ``[`` opens a CSI reply that ends at a final byte; ``]``, ``P``,
# ``_``, ``^`` and ``X`` open OSC/DCS/APC/PM/SOS strings ended by BEL or ST.
_CSI_INTRODUCER = "["
_STRING_INTRODUCERS = frozenset("]P_^X")
_CSI_FINAL_FIRST = "@"
_CSI_FINAL_LAST = "~"
_ST_TAIL = "\\"  # ST is ESC followed by a backslash
# Upper bound on the keys one discarded reply may consume.
_REPLY_MAX_KEYS = 256


def install_focus_report_sequences() -> None:
    """Parse focus reports as ``Keys.Ignore`` so they never reach the Escape binding."""
    for sequence in _FOCUS_REPORT_SEQUENCES:
        ANSI_SEQUENCES.setdefault(sequence, Keys.Ignore)


@Condition
def escape_stands_alone() -> bool:
    """True when only CPR replies or ignored keys are queued behind the Escape being matched."""
    return all(key_press.key in _REPLY_KEYS for key_press in get_app().key_processor.input_queue)


def escape_heads_a_sequence(event: KeyPressEvent) -> bool:
    """True when this Escape was matched ahead of keys that are bound to nothing.

    The key processor reaches the Escape binding that way only through its
    no-match fallback, with the unmatched keys still in ``key_buffer``: the
    Escape led a terminal reply or an unbound Option chord, not an Esc press.
    """
    return len(event.key_processor.key_buffer) > len(event.key_sequence)


def discard_reply_tail(processor: KeyProcessor) -> None:
    """Consume the rest of an Escape-led terminal reply so none of it acts as keystrokes.

    Call only while :func:`escape_heads_a_sequence` holds. An Option chord keeps
    its key, which then types or runs its own binding. A reply's body is queued
    from the same read; one split across reads is consumed only to the end of
    this batch.
    """
    introducer = processor.key_buffer[1].key
    if introducer != _CSI_INTRODUCER and introducer not in _STRING_INTRODUCERS:
        return
    queue = processor.input_queue
    queue.extendleft(reversed(processor.key_buffer[2:]))
    del processor.key_buffer[1:]
    if introducer == _CSI_INTRODUCER:
        _discard_csi_body(queue)
    else:
        _discard_string_body(queue)


def _is_reply_char(key_press: KeyPress) -> bool:
    """Printable ASCII: the only keys a reply carries before its end."""
    key = key_press.key
    return len(key) == 1 and " " <= key <= "~"


def _discard_csi_body(queue: deque[KeyPress]) -> None:
    """Pop parameter bytes through the final byte; stop at a key no CSI contains."""
    for _ in range(_REPLY_MAX_KEYS):
        if not queue or not _is_reply_char(queue[0]):
            return
        if _CSI_FINAL_FIRST <= queue.popleft().key <= _CSI_FINAL_LAST:
            return


def _discard_string_body(queue: deque[KeyPress]) -> None:
    """Pop a string through BEL or ST; stop at any other key no reply contains."""
    for _ in range(_REPLY_MAX_KEYS):
        if not queue:
            return
        key = queue[0].key
        if key == Keys.ControlG:  # BEL
            queue.popleft()
            return
        if key == Keys.Escape:
            queue.popleft()
            if queue and queue[0].key == _ST_TAIL:
                queue.popleft()
            return
        if not _is_reply_char(queue[0]):
            return
        queue.popleft()


__all__ = [
    "discard_reply_tail",
    "escape_heads_a_sequence",
    "escape_stands_alone",
    "install_focus_report_sequences",
]
