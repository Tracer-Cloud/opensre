"""Tests for the interactive shell terminal runtime helpers."""

from __future__ import annotations

import asyncio
import io
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput

from infrastructure.terminal import theme as ui_theme
from infrastructure.terminal.theme import (
    ANSI_RESET,
    THEME_REGISTRY,
    get_active_theme_name,
    set_active_theme,
)


def _rgb(hex_color: str) -> str:
    """``"#RRGGBB"`` → the ``"r;g;b"`` triple as it appears in a truecolor escape."""
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}"


from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.runtime.core import confirmation as controller_runtime
from surfaces.interactive_shell.runtime.core import state as loop_state
from surfaces.interactive_shell.runtime.core import turn_detection as loop_turn_detection
from surfaces.interactive_shell.runtime.startup import initial_input as startup_initial_input
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import input_prompt
from surfaces.interactive_shell.ui.input_prompt import completion as prompt_completion
from surfaces.interactive_shell.ui.input_prompt.completion import ShellCompleter
from surfaces.interactive_shell.ui.input_prompt.key_bindings import (
    _SHIFT_ENTER_SEQUENCE,
    _build_prompt_key_bindings,
    _tab_expand_or_menu,
    build_cancel_key_bindings,
)
from surfaces.interactive_shell.ui.input_prompt.lexer import ReplInputLexer
from surfaces.interactive_shell.ui.input_prompt.rendering import _prompt_message
from surfaces.interactive_shell.ui.input_prompt.style import _build_prompt_style
from surfaces.interactive_shell.ui.streaming import _CHARS_PER_TOKEN
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.shared.terminal.components.cpr_stdin import (
    strip_cpr_escape_sequences,
    strip_cpr_sequences,
)


def test_agent_presentation_import_does_not_load_shell_turn_execution() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                + "import surfaces.interactive_shell.runtime.agent_presentation; "
                + "print('surfaces.interactive_shell.runtime.shell_turn_execution' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_streaming_console_status_does_not_recurse(monkeypatch) -> None:
    """Regression: overriding Console.print broke Rich's status spinner."""
    spinner = loop_state.SpinnerState()
    console = StreamingConsole(
        spinner,
        threading.Event(),
        file=io.StringIO(),
        force_terminal=False,
        width=80,
    )
    with console.status("working", spinner="dots"):
        pass


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\x1b[32;1R", ""),
        ("[32;1R", ""),
        ("\x9b32;1R", ""),
        ("what is our current model?[32;1R", "what is our current model?"),
        ("before \x1b[12;80R after", "before  after"),
        ("7R[25;57R23;57R", ""),
        ("25;57R", ""),
        # Regression (issue #2942): CPR-shaped substrings embedded in ordinary
        # input must NOT be stripped. A bare ``rowR``/``row;colR`` followed by a
        # lone digit, or trailed by a word, is real user text, not a leak — only a
        # full ``row;colR`` continuation counts as a CPR fragment.
        ("retry 5R3 times", "retry 5R3 times"),
        ("ranges 12;34R okay", "ranges 12;34R okay"),
        ("use r5.xlarge", "use r5.xlarge"),
        ("scale to 16RAM nodes", "scale to 16RAM nodes"),
        ("scale 12;34R5 nodes", "scale 12;34R5 nodes"),
        # Consecutive bare fragments (no introducer) are a real leaked burst and
        # must still strip fully.
        ("25;57R12;80R", ""),
    ],
)
def test_strip_cpr_sequences_removes_terminal_cursor_replies(
    text: str,
    expected: str,
) -> None:
    assert strip_cpr_sequences(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("before \x1b[12;80R after", "before  after"),
        ("what is 12;80R?", "what is 12;80R?"),
        ("[12;80R", "[12;80R"),
    ],
)
def test_strip_cpr_escape_sequences_only_removes_escaped_variants(
    text: str,
    expected: str,
) -> None:
    assert strip_cpr_escape_sequences(text) == expected


def test_repl_input_lexer_highlights_first_slash_token() -> None:
    lexer = ReplInputLexer()
    get_line = lexer.lex_document(Document("/model show", len("/model")))
    fragments = get_line(0)
    cmd_frags = [(frag[0], frag[1]) for frag in fragments if frag[0] == "class:repl-slash-command"]
    assert cmd_frags == [("class:repl-slash-command", "/model")]
    rest = "".join(frag[1] for frag in fragments if frag[0] == "")
    assert " show" in rest or rest.endswith(" show")


def test_build_prompt_session_uses_persistent_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config.constants as const_module

    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        prompt = input_prompt.build_prompt_session()

    assert isinstance(prompt.history, FileHistory)
    assert prompt.history.filename == str(tmp_path / "interactive_history")
    assert tmp_path.exists()
    assert isinstance(prompt.completer, ShellCompleter)
    assert prompt.multiline is True
    assert prompt.reserve_space_for_menu == 0
    assert prompt.app.key_bindings is not None


def test_build_prompt_session_installs_growing_bordered_composer() -> None:
    from prompt_toolkit.layout.containers import (
        FloatContainer,
        HSplit,
        VSplit,
    )

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        prompt = input_prompt.build_prompt_session()

    root = prompt.layout.container
    assert isinstance(root, HSplit)
    framed_input = root.children[0]
    assert isinstance(framed_input, FloatContainer)
    chrome = framed_input.content
    assert isinstance(chrome, HSplit)
    # Status rows, then the bordered composer — send hints live in the
    # empty-box placeholder, not a third footer child.
    assert len(chrome.children) == 2
    composer = chrome.children[1]
    assert isinstance(composer, HSplit)
    assert composer.height is None
    editable_row = composer.children[1]
    assert isinstance(editable_row, VSplit)
    surface_body = editable_row.children[1]
    assert isinstance(surface_body, HSplit)
    editable_body = surface_body.children[0]
    default_buffer_slot = editable_body.children[0]
    assert default_buffer_slot.content.height.min == 1
    assert default_buffer_slot.content.height.max == 8
    assert chrome.preferred_width(80).preferred == 79


@pytest.mark.asyncio
async def test_bordered_composer_grows_with_input_up_to_eight_edit_rows() -> None:
    with (
        create_pipe_input() as pipe_input,
        create_app_session(input=pipe_input, output=DummyOutput()),
    ):
        prompt = input_prompt.build_prompt_session()
        task = asyncio.create_task(prompt.prompt_async(""))
        await asyncio.sleep(0)

        composer = prompt.layout.container.children[0].content.children[1]
        prompt.default_buffer.text = "first"
        single_line_height = composer.preferred_height(79, 30).preferred
        prompt.default_buffer.text = "x" * 200
        wrapped_line_height = composer.preferred_height(79, 30).preferred
        prompt.default_buffer.text = "first\nsecond\nthird"
        multiline_height = composer.preferred_height(79, 30).preferred
        prompt.default_buffer.text = "\n".join(str(index) for index in range(12))
        capped_height = composer.preferred_height(79, 30).preferred

        pipe_input.send_text("\r")
        await asyncio.wait_for(task, timeout=5.0)

    assert single_line_height == 3
    assert single_line_height < wrapped_line_height <= 10
    assert multiline_height == 5
    assert capped_height == 10


def test_build_prompt_session_falls_back_to_memory_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config.constants as const_module

    blocked_home = tmp_path / "not-a-directory"
    blocked_home.write_text("", encoding="utf-8")
    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", blocked_home)
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", blocked_home)

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        prompt = input_prompt.build_prompt_session()

    assert isinstance(prompt.history, InMemoryHistory)


def test_repl_session_prompt_history_backend_matches_prompt_toolkit_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config.constants as const_module

    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        session = Session()
        prompt = input_prompt.build_prompt_session()
        session.terminal.prompt_history_backend = prompt.history
    assert session.terminal.prompt_history_backend is prompt.history


def test_prompt_message_uses_accent_glyph() -> None:
    set_active_theme("blue")
    rendered = _prompt_message(Session()).value

    assert ui_theme.PROMPT_ACCENT_ANSI in rendered
    assert ">" in rendered
    assert ANSI_RESET in rendered


@pytest.mark.parametrize(
    "newline_sequence",
    [
        _SHIFT_ENTER_SEQUENCE,
        "\x1b[13;2u",  # CSI-u Shift+Enter
        "\x1b[27;5;13~",  # xterm Ctrl+Enter
        "\x1b[13;5u",  # CSI-u Ctrl+Enter
        "\x1b\r",  # Alt/Option+Enter
        "\n",  # Shift+Enter in terminals that emit LF
    ],
)
def test_modified_enter_inserts_newline_before_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    newline_sequence: str,
) -> None:
    import config.constants as const_module

    monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

    async def _collect() -> str:
        with (
            create_pipe_input() as pipe_input,
            create_app_session(input=pipe_input, output=DummyOutput()),
        ):
            prompt = input_prompt.build_prompt_session()
            task = asyncio.create_task(prompt.prompt_async(""))
            # Yield so prompt_async attaches readers before keystrokes arrive;
            # under loaded ``test-cov`` (xdist + coverage) a 1s wait_for flakes.
            await asyncio.sleep(0)
            pipe_input.send_bytes(b"first line")
            pipe_input.send_bytes(newline_sequence.encode())
            pipe_input.send_bytes(b"second line\r")
            return await asyncio.wait_for(task, timeout=5.0)

    assert asyncio.run(_collect()) == "first line\nsecond line"


def test_shell_completer_previews_all_commands() -> None:
    completions = list(
        ShellCompleter().get_completions(
            Document("/"),
            CompleteEvent(text_inserted=True),
        )
    )
    names = [completion.text for completion in completions]

    assert "/help" in names
    assert "/effort" in names
    assert "/integrations" in names
    assert "/tools" in names
    assert "/model" in names
    assert all(name.startswith("/") for name in names)


def test_shell_completer_filters_by_prefix() -> None:
    completions = list(
        ShellCompleter().get_completions(
            Document("/to"),
            CompleteEvent(text_inserted=True),
        )
    )

    assert [completion.text for completion in completions] == ["/tools"]


def test_shell_completer_suggests_subcommands_for_tools() -> None:
    completions = list(
        ShellCompleter().get_completions(
            Document("/tools "),
            CompleteEvent(text_inserted=True),
        )
    )
    names = sorted({c.text for c in completions})
    assert names == ["list", "ls", "tool", "tools"]


def test_shell_completer_hides_inline_picker_autocomplete_in_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompt_completion, "repl_tty_interactive", lambda: True)

    completions = list(
        ShellCompleter().get_completions(
            Document("/model "),
            CompleteEvent(text_inserted=True),
        )
    )

    assert completions == []


def test_shell_completer_keeps_inline_picker_autocomplete_when_arg_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompt_completion, "repl_tty_interactive", lambda: True)

    completions = list(
        ShellCompleter().get_completions(
            Document("/model s"),
            CompleteEvent(text_inserted=True),
        )
    )

    assert sorted({c.text for c in completions}) == ["set", "show"]


def test_shell_completer_suggests_effort_levels() -> None:
    completions = list(
        ShellCompleter().get_completions(
            Document("/effort "),
            CompleteEvent(text_inserted=True),
        )
    )
    names = sorted({c.text for c in completions})
    assert names == ["high", "low", "max", "medium", "xhigh"]


def test_tab_applies_unique_slash_command_completion() -> None:
    buff = Buffer(completer=ShellCompleter())
    buff.insert_text("/mod")
    _tab_expand_or_menu(buff)
    assert buff.text == "/model"


def test_tab_with_open_completion_menu_applies_current_item() -> None:
    from prompt_toolkit.buffer import CompletionState
    from prompt_toolkit.completion import Completion

    buff = Buffer()
    buff.insert_text("/mo")
    orig_doc = buff.document
    c_model = Completion("/model", start_position=-3)
    c_mcp = Completion("/mcp", start_position=-3)
    # Assign directly — updating ``buff.document`` afterward clears ``complete_state``.
    buff.complete_state = CompletionState(orig_doc, [c_model, c_mcp], 0)

    _tab_expand_or_menu(buff)

    assert buff.complete_state is None
    assert buff.text == "/model"


def test_tab_with_menu_and_no_index_applies_first_choice() -> None:
    from prompt_toolkit.buffer import CompletionState
    from prompt_toolkit.completion import Completion

    buff = Buffer()
    buff.insert_text("/mo")
    orig_doc = buff.document
    c_model = Completion("/model", start_position=-3)
    c_mcp = Completion("/mcp", start_position=-3)
    buff.complete_state = CompletionState(orig_doc, [c_model, c_mcp], None)

    _tab_expand_or_menu(buff)

    assert buff.complete_state is None
    assert buff.text == "/model"


def test_completion_includes_tab_navigation() -> None:
    key_bindings = _build_prompt_key_bindings()
    keys = {binding.keys for binding in key_bindings.bindings}

    assert (Keys.ControlM,) in keys
    assert (Keys.Down,) in keys
    assert (Keys.Up,) in keys
    assert (Keys.Tab,) in keys
    assert (Keys.BackTab,) in keys


def test_build_prompt_style_tracks_active_theme() -> None:
    set_active_theme("amber")
    amber_attrs = _build_prompt_style().get_attrs_for_style_str("class:prompt-frame-line")
    set_active_theme("teal")
    teal_attrs = _build_prompt_style().get_attrs_for_style_str("class:prompt-frame-line")
    assert (
        amber_attrs.color
        and amber_attrs.color.lower() == THEME_REGISTRY["amber"].HIGHLIGHT.lstrip("#").lower()
    )
    assert (
        teal_attrs.color
        and teal_attrs.color.lower() == THEME_REGISTRY["teal"].HIGHLIGHT.lstrip("#").lower()
    )
    assert amber_attrs.color != teal_attrs.color


def test_completion_menu_current_item_uses_highlight_style() -> None:
    from infrastructure.terminal.theme import BG, HIGHLIGHT, INPUT_SURFACE

    set_active_theme("green")
    style = _build_prompt_style()
    attrs = style.get_attrs_for_style_str("class:repl-slash-command")

    assert attrs.color == HIGHLIGHT.lstrip("#")
    # Slash tokens sit on the composer plate, not the terminal bg.
    assert attrs.bgcolor == str(INPUT_SURFACE).lstrip("#")
    assert attrs.bold is True

    attrs_menu = style.get_attrs_for_style_str("class:completion-menu.completion.current")

    assert attrs_menu.color == HIGHLIGHT.lstrip("#")
    assert attrs_menu.bgcolor == BG.lstrip("#")
    assert attrs_menu.reverse is False
    assert attrs_menu.bold is True


def test_composer_uses_input_surface_fill() -> None:
    """Composer plate uses INPUT_SURFACE — same role as Droid/Claude/Cursor input bg."""
    from infrastructure.terminal.theme import INPUT_SURFACE

    set_active_theme("green")
    style = _build_prompt_style()
    surface = str(INPUT_SURFACE).lstrip("#")

    for style_name in (
        "class:frame",
        "class:frame.border",
        "class:composer",
        "class:composer-body",
        "class:placeholder",
    ):
        assert style.get_attrs_for_style_str(style_name).bgcolor == surface

    # Help line under the plate stays on terminal bg.
    assert not style.get_attrs_for_style_str("class:composer-footer").bgcolor


def test_lazy_rich_style_split_tracks_active_theme() -> None:
    from infrastructure.terminal import theme as ui_theme

    set_active_theme("green")
    assert bool(ui_theme.DIM) is True
    assert ui_theme.DIM.split() == [ui_theme.get_active_theme().DIM]
    assert ui_theme.BOLD_BRAND.split() == ["bold", ui_theme.get_active_theme().BRAND]

    set_active_theme("blue")
    assert bool(ui_theme.DIM) is True
    assert ui_theme.DIM.split() == [ui_theme.get_active_theme().DIM]
    assert ui_theme.BOLD_BRAND.split() == ["bold", ui_theme.get_active_theme().BRAND]


def test_lazy_rich_style_parses_as_real_rich_style() -> None:
    from rich.style import Style

    from infrastructure.terminal import theme as ui_theme

    # Rich resolves a _LazyRichStyle via its underlying ``str`` value, so the
    # contract at call sites is to pass ``str(...)``. Verify that contract
    # produces a non-null Style across themes.
    set_active_theme("amber")
    assert Style.parse(str(ui_theme.DIM)) != Style.null()
    assert Style.parse(str(ui_theme.BOLD_BRAND)) != Style.null()

    set_active_theme("blue")
    assert Style.parse(str(ui_theme.DIM)) != Style.null()
    assert Style.parse(str(ui_theme.BOLD_BRAND)) != Style.null()


def test_run_initial_input_dispatches_as_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_handle_message(*args: object, **kwargs: object) -> None:
        _ = args
        calls.append(kwargs)

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.shell_turn_execution.execute_shell_turn",
        _fake_handle_message,
    )

    assert startup_initial_input.run_initial_input("/remote", Session()) == 0
    assert len(calls) == 1
    assert calls[0]["is_tty"] is False


class TestLooksLikeConfirmationAnswer:
    """Unit tests for the y/n token recognizer.

    Type-ahead text submitted while a ``Proceed? [Y/n]`` worker is
    parked used to be silently delivered to the confirmation handler
    and declined the pending action. The recognizer is the
    gate that keeps that from happening — only deliberate y/n tokens
    (and empty Enter, which the upstream ``[Y/n]`` prompt accepts as
    "yes") are treated as answers.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "y",
            "Y",
            "yes",
            "YES",
            "n",
            "N",
            "no",
            "No",
            " y ",
            "  yes\n",
            "",
            "   ",
            None,
        ],
    )
    def test_recognised_tokens_match(self, text: str | None) -> None:
        assert loop_turn_detection.looks_like_confirmation_answer(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "what is opensre?",
            "yeah do it",  # extra words even after "yeah" — ambiguous, not delivered
            "yep",
            "yup",
            "nope",
            "/help",
            "show me logs",
            "y'all should run this",
        ],
    )
    def test_unrecognised_text_does_not_match(self, text: str) -> None:
        assert loop_turn_detection.looks_like_confirmation_answer(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            # Multi-line type-ahead whose first token reads as ``y``/``yes``
            # must NOT be classified as a confirmation. The gate compares the
            # whole stripped/lowered string against the token set, so any
            # trailing words or internal newlines disqualify it.
            "yes please run that against staging instead",
            "yes\nbut do X first",
            "y\nrun it now",
            "no\nactually wait, let me check the logs first",
            "Y but also rotate the keys after",
            # Embedded newlines preserved through ``strip()`` so the join
            # still fails the membership check.
            "yes\nplease",
        ],
    )
    def test_multiline_type_ahead_starting_with_y_or_n_does_not_match(self, text: str) -> None:
        """Regression for the type-ahead-as-confirmation footgun: a pasted
        or typed sentence beginning with ``y``/``yes`` (or ``n``/``no``)
        must be treated as a new turn, not silently delivered as the
        Proceed? answer. ``str.strip()`` only trims outer whitespace, so
        any inner non-whitespace content keeps the lowered string out
        of the token set. (Pure outer-whitespace cases like
        ``"  yes\\n  "`` correctly DO match — that's just ``yes`` with
        stray whitespace, not a multi-line message.)
        """
        assert loop_turn_detection.looks_like_confirmation_answer(text) is False


class TestLooksLikeCancelRequest:
    """Unit tests for the bare-cancel slash recognizer.

    The recognizer is the gate that lets the prompt loop intercept
    ``/cancel``-style slashes typed while a dispatch is parked
    (e.g. on a ``Proceed? [y/N]`` confirmation) and send them through
    ``state.cancel_current_dispatch()`` instead of queueing them
    behind the dispatch they're trying to interrupt.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "/cancel",
            "/CANCEL",
            "/Cancel",
            "/stop",
            "/STOP",
            "/abort",
            "  /cancel  ",
            "/cancel\n",
            "\t/stop\t",
        ],
    )
    def test_recognised_cancel_slashes_match(self, text: str) -> None:
        assert loop_turn_detection.looks_like_cancel_request(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            # Targeted background-task cancel — must keep flowing
            # through the normal slash dispatch path so the existing
            # ``_cmd_cancel`` handler resolves the task id.
            "/cancel 8f5fe574",
            "/cancel abc123",
            "/stop now",
            # Other slashes — unrelated to interrupt.
            "/help",
            "/tasks",
            # Natural-language uses of the same words must NOT be
            # intercepted; the user might be talking about cancelling
            # a deploy or stopping a service in their environment.
            "cancel this please",
            "cancel",
            "stop the deploy",
            "stop",
            "abort",
            # Empty / whitespace / None — nothing to intercept.
            "",
            "   ",
            None,
        ],
    )
    def test_unrecognised_text_does_not_match(self, text: str | None) -> None:
        assert loop_turn_detection.looks_like_cancel_request(text) is False


# ── Spinner state tests ──────────────────────────────────────────────────────


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


class TestSpinnerState:
    """``_SpinnerState`` holds the live-stream indicator state and renders
    two ANSI views: the inline spinner above the input frame
    (``inline_spinner_ansi``) and the bottom toolbar hint
    (``toolbar_ansi``).
    """

    def test_idle_state_emits_no_inline_spinner(self) -> None:
        spinner = loop_state.SpinnerState()
        assert spinner.streaming is False
        assert spinner.inline_spinner_ansi() == ""

    def test_inline_spinner_thinking_uses_warm_reply_marker(self) -> None:
        from infrastructure.terminal.theme import reply_marker_hex, set_active_theme

        set_active_theme("blue")
        spinner = loop_state.SpinnerState()
        spinner.start()
        spinner.set_phase(loop_state.SpinnerState.THINKING_PHASE)
        raw = spinner.inline_spinner_ansi()
        assert _rgb(reply_marker_hex()) in raw  # Factory-warm glyph accent
        assert _rgb(THEME_REGISTRY["green"].HIGHLIGHT) not in raw

    def test_inline_spinner_invoking_tools_uses_same_warm_accent(self) -> None:
        from infrastructure.terminal.theme import reply_marker_hex, set_active_theme

        set_active_theme("blue")
        spinner = loop_state.SpinnerState()
        spinner.start()
        spinner.set_phase(loop_state.SpinnerState.INVOKING_TOOLS_PHASE)
        raw = spinner.inline_spinner_ansi()
        # One sunny accent for every phase — not a second cold brand strip.
        assert _rgb(reply_marker_hex()) in raw
        assert _rgb(THEME_REGISTRY["blue"].BRAND) not in raw.split("(Press ESC")[0]

    def test_streaming_inline_spinner_includes_glyph_and_token_count(self) -> None:
        spinner = loop_state.SpinnerState()
        spinner.start()
        spinner.bytes_in = 1234 * _CHARS_PER_TOKEN  # = 1234 tokens
        rendered = _strip_ansi(spinner.inline_spinner_ansi())
        assert loop_state.SpinnerState.EXECUTING_PHASE in rendered
        # 1234 tokens → "1.2k" via format_token_count_short.
        assert "1.2k tokens" in rendered
        # Spinner glyph from the brail palette.
        assert any(g in rendered for g in spinner._SPINNER_FRAMES)

    def test_streaming_inline_spinner_hides_token_count_when_zero(self) -> None:
        """When no tokens have been received yet, the spinner should not
        display ``↓ 0 tokens`` — the count is meaningless until streaming
        starts. Only the elapsed time is shown in the parenthetical.
        """
        spinner = loop_state.SpinnerState()
        spinner.start()
        # bytes_in defaults to 0 → token_count = 0
        rendered = _strip_ansi(spinner.inline_spinner_ansi())
        assert "tokens" not in rendered, (
            "expected no token count in spinner before any tokens arrive"
        )
        assert "0" not in rendered or "0s" in rendered or "0." in rendered, (
            "zero-token count leaked into spinner display"
        )

    def test_streaming_inline_spinner_shows_token_count_once_tokens_arrive(self) -> None:
        """Once bytes arrive, the token count reappears in the spinner."""
        spinner = loop_state.SpinnerState()
        spinner.start()
        spinner.bytes_in = 100 * _CHARS_PER_TOKEN  # = 100 tokens
        rendered = _strip_ansi(spinner.inline_spinner_ansi())
        assert "tokens" in rendered

    def test_streaming_inline_spinner_phase_stays_stable_across_calls(self) -> None:
        """Phase labels are the product UX — no rotating verbs mid-stream."""
        spinner = loop_state.SpinnerState()
        spinner.start()
        for _ in range(20):
            rendered = _strip_ansi(spinner.inline_spinner_ansi())
            assert loop_state.SpinnerState.EXECUTING_PHASE in rendered

    def test_inline_spinner_folds_live_tool_onto_status_row(self) -> None:
        spinner = loop_state.SpinnerState()
        spinner.start()
        spinner.set_phase(loop_state.SpinnerState.INVOKING_TOOLS_PHASE)
        spinner.set_active_action("GitHub CLI · gh api repos/x")
        rendered = _strip_ansi(spinner.inline_spinner_ansi())
        assert "Invoking tools…" in rendered
        assert "GitHub CLI · gh api repos/x" in rendered

    def test_inline_spinner_glyph_animates_with_elapsed_time(self) -> None:
        """The frame is a function of elapsed time, not of render-call count.

        prompt_toolkit evaluates the prompt message several times per render
        pass, so a per-call counter freezes the on-screen glyph (it advances a
        whole number of cycles between visible renders). Repeated calls at one
        instant must render one frame; advancing the clock must animate.
        """
        spinner = loop_state.SpinnerState()
        spinner.start()
        same_instant = {
            _extract_glyph(spinner.inline_spinner_ansi(), spinner._SPINNER_FRAMES)
            for _ in range(len(spinner._SPINNER_FRAMES) * 2)
        }
        assert len(same_instant) == 1

        seen = set()
        for step in range(len(spinner._SPINNER_FRAMES)):
            spinner.started_at = time.monotonic() - step * spinner._FRAME_INTERVAL_SECONDS * 1.001
            seen.add(_extract_glyph(spinner.inline_spinner_ansi(), spinner._SPINNER_FRAMES))
        assert seen == set(spinner._SPINNER_FRAMES)

    def test_stop_returns_to_idle_state(self) -> None:
        spinner = loop_state.SpinnerState()
        spinner.start()
        assert spinner.streaming is True
        spinner.stop()
        assert spinner.streaming is False
        assert spinner.inline_spinner_ansi() == ""

    def test_toolbar_always_returns_empty_string(self) -> None:
        """toolbar_ansi() must always return '' in both idle and streaming states.

        A visible toolbar causes prompt_toolkit to emit \\033[6n (CPR)
        cursor-position queries on every refresh_interval; those responses
        corrupt the vt100 input parser with stray keystrokes.  Hiding the
        toolbar unconditionally also keeps its height at zero in *both* states,
        which prevents the one-row height delta that causes prompt_toolkit to
        misplace the cursor and leave stale spinner lines on screen.  Idle hints
        are surfaced through idle_hint_ansi() rendered in the prompt message's
        reserved first line instead.
        """
        spinner = loop_state.SpinnerState()
        assert spinner.toolbar_ansi() == ""

        spinner.start()
        assert spinner.toolbar_ansi() == ""

    def test_idle_hint_lists_shortcut_keys_when_buffer_empty(self) -> None:
        """When idle and the input buffer is empty (no prompt-toolkit app
        running in this test → ``get_app_or_none()`` returns None →
        treated as empty), the idle hint advertises the always-useful keys
        but hides ``esc to clear`` since Esc is a no-op on empty buffer.
        """
        spinner = loop_state.SpinnerState()
        rendered = _strip_ansi(spinner.idle_hint_ansi())
        assert rendered.startswith("Ready")
        assert "/ for commands" in rendered
        assert "tab tool details" in rendered
        assert "history" in rendered
        # Hidden — buffer is empty, Esc would be a no-op, so the hint
        # would mislead the user.
        assert "esc to clear" not in rendered

    def test_idle_hint_includes_esc_to_clear_when_buffer_has_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When idle and the input buffer has text, the idle hint appends
        ``esc to clear`` so the user knows the shortcut exists.
        ``get_app_or_none`` is monkeypatched to return a fake app whose
        ``current_buffer.text`` is non-empty.
        """

        class _FakeBuffer:
            text = "partially typed"

        class _FakeApp:
            current_buffer = _FakeBuffer()

        monkeypatch.setattr(loop_state, "get_app_or_none", lambda: _FakeApp())

        spinner = loop_state.SpinnerState()
        rendered = _strip_ansi(spinner.idle_hint_ansi())
        assert "esc to clear" in rendered
        assert "/ for commands" in rendered

    def test_inline_spinner_contains_stop_hint_when_streaming(self) -> None:
        """During streaming the inline spinner (shown in the prompt's first
        reserved line) carries ``(Press ESC to stop)`` so the user can
        interrupt the dispatch.
        """
        spinner = loop_state.SpinnerState()
        spinner.start()
        rendered = _strip_ansi(spinner.inline_spinner_ansi())
        assert "(Press ESC to stop)" in rendered
        # Idle hint text should NOT appear in the spinner row.
        assert "/ for commands" not in rendered

    def test_prompt_prefix_is_consistent_height(self) -> None:
        """The first line of the prompt message is always exactly one row —
        it shows the idle hint when not streaming and the spinner when
        streaming.  Using ``idle_hint_ansi()`` or ``inline_spinner_ansi()``
        as the prefix means height never changes between states, so
        prompt_toolkit's cursor management stays accurate and stale spinner
        lines do not accumulate on screen.
        """
        spinner = loop_state.SpinnerState()

        idle_prefix = _strip_ansi(spinner.idle_hint_ansi())
        assert "\n" not in idle_prefix, f"idle prefix must be 1 row, got: {idle_prefix!r}"

        spinner.start()
        streaming_prefix = _strip_ansi(spinner.inline_spinner_ansi())
        assert "\n" not in streaming_prefix, (
            f"streaming prefix must be 1 row, got: {streaming_prefix!r}"
        )


def _extract_glyph(ansi_text: str, frames: tuple[str, ...]) -> str:
    plain = _strip_ansi(ansi_text)
    for g in frames:
        if g in plain:
            return g
    return ""


# ── Streaming-console adapter tests ──────────────────────────────────────────


class TestStreamingConsole:
    """``_StreamingConsole`` is the bridge between the streaming layer and
    the prompt-toolkit spinner. It is the only way the dispatch worker
    thread can signal back to the prompt: progress updates and
    cancellation polling go through this object's optional methods.
    """

    def test_update_progress_writes_to_spinner_state(self) -> None:
        import threading as _threading

        spinner = loop_state.SpinnerState()
        spinner.start()
        cancel = _threading.Event()
        console = StreamingConsole(
            spinner,
            cancel,
            highlight=False,
            force_terminal=True,
            color_system=None,
        )
        console.update_streaming_progress(4096)
        assert spinner.bytes_in == 4096

    def test_cancel_requested_reflects_event_state(self) -> None:
        import threading as _threading

        spinner = loop_state.SpinnerState()
        cancel = _threading.Event()
        console = StreamingConsole(
            spinner,
            cancel,
            highlight=False,
            force_terminal=True,
            color_system=None,
        )
        assert console.cancel_event is cancel
        assert console.cancel_requested is False
        cancel.set()
        assert console.cancel_requested is True
        cancel.clear()
        assert console.cancel_requested is False

    def test_blank_print_resets_column_without_preparing_new_line(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import threading as _threading

        spinner = loop_state.SpinnerState()
        console = StreamingConsole(
            spinner,
            _threading.Event(),
            file=io.StringIO(),
            force_terminal=False,
            width=80,
        )

        calls: list[str] = []
        monkeypatch.setattr(
            "surfaces.shared.terminal.components.choice_menu.ensure_tty_column_zero",
            lambda: calls.append("ensure"),
        )
        monkeypatch.setattr(
            "surfaces.shared.terminal.components.choice_menu.prepare_repl_output_line",
            lambda: calls.append("prepare"),
        )

        console.print()
        assert calls == ["ensure"]


# ── ReplState dataclass tests ────────────────────────────────────────────────


class TestReplState:
    """``_ReplState`` is the single owner of the cancellation primitives
    shared between the prompt loop, the queue processor, and the
    Esc/Ctrl+L key bindings. Its methods exist so callers don't poke
    raw fields and re-derive ``is_running`` everywhere.
    """

    def test_default_state_is_idle(self) -> None:
        state = loop_state.ReplState()
        assert state.is_dispatch_running() is False
        assert state.exit_requested is False
        # No active dispatch → no cancel event parked.
        assert state.current_cancel_event is None
        assert state.queue.empty()
        assert state.phase is loop_state.TurnPhase.IDLE

    def test_phase_transitions_dispatch_to_idle(self) -> None:
        async def _scenario() -> None:
            state = loop_state.ReplState()

            async def _noop() -> None:
                return None

            task = asyncio.create_task(_noop())
            cancel = threading.Event()
            state.start_dispatch(task=task, cancel_event=cancel)
            assert state.is_dispatch_running() is True
            assert await task is None
            state.finish_dispatch(cancel)
            assert state.phase is loop_state.TurnPhase.IDLE
            state.clear_current_task()
            assert state.phase is loop_state.TurnPhase.IDLE

        asyncio.run(_scenario())

    def test_confirmation_phase_round_trips_to_dispatching(self) -> None:
        async def _scenario() -> None:
            state = loop_state.ReplState()

            async def _waits() -> None:
                await asyncio.sleep(1.0)

            task = asyncio.create_task(_waits())
            state.start_dispatch(task=task, cancel_event=threading.Event())
            state.begin_confirmation(threading.Event(), "Proceed? ")
            assert state.is_awaiting_confirmation() is True
            assert state.phase is loop_state.TurnPhase.AWAITING_CONFIRMATION
            # A normal confirmation completion returns to dispatching (task alive).
            state.clear_confirmation()
            assert state.is_awaiting_confirmation() is False
            assert state.phase is loop_state.TurnPhase.DISPATCHING
            task.cancel()

        asyncio.run(_scenario())

    def test_clear_confirmation_returns_to_idle_without_active_task(self) -> None:
        state = loop_state.ReplState()
        state.begin_confirmation(threading.Event(), "Proceed? ")
        assert state.phase is loop_state.TurnPhase.AWAITING_CONFIRMATION
        state.clear_confirmation()
        assert state.phase is loop_state.TurnPhase.IDLE

    def test_cancel_sets_cancelling_phase(self) -> None:
        state = loop_state.ReplState()
        state.current_cancel_event = threading.Event()
        state.cancel_current_dispatch()
        assert state.phase is loop_state.TurnPhase.CANCELLING

    def test_cancel_during_confirmation_keeps_cancelling_phase(self) -> None:
        state = loop_state.ReplState()
        state.current_cancel_event = threading.Event()
        state.begin_confirmation(threading.Event(), "Proceed? ")
        state.cancel_current_dispatch()
        assert state.phase is loop_state.TurnPhase.CANCELLING
        # The confirmation cleanup must NOT downgrade an in-progress cancel.
        state.clear_confirmation()
        assert state.phase is loop_state.TurnPhase.CANCELLING

    def test_idle_cancel_does_not_set_cancelling_phase(self) -> None:
        state = loop_state.ReplState()
        state.cancel_current_dispatch()
        assert state.phase is loop_state.TurnPhase.IDLE

    def test_is_dispatch_running_tracks_task_lifecycle(self) -> None:
        async def _scenario() -> None:
            state = loop_state.ReplState()

            async def _slow() -> None:
                await asyncio.sleep(0.05)

            state.current_task = asyncio.create_task(_slow())
            assert state.is_dispatch_running() is True
            await state.current_task
            assert state.is_dispatch_running() is False

        asyncio.run(_scenario())

    def test_cancel_current_dispatch_signals_event_and_task(self) -> None:
        async def _scenario() -> None:
            import threading as _threading

            state = loop_state.ReplState()
            dispatch_cancel = _threading.Event()
            state.current_cancel_event = dispatch_cancel

            async def _waits_forever() -> None:
                # Long sleep — only the cancel can interrupt this.
                await asyncio.sleep(1.0)

            state.current_task = asyncio.create_task(_waits_forever())
            # Snapshot to a local — re-reading ``state.current_task``
            # across the cancel + await would let CodeQL / code-quality
            # bots flag the bare ``await`` as ineffectual and would also
            # leave the assertion racey if anything reassigned the field.
            task = state.current_task
            state.cancel_current_dispatch()

            # Both signals must fire — per-dispatch event flipped AND
            # the asyncio task cancelled.
            assert dispatch_cancel.is_set() is True
            try:  # noqa: SIM105
                await task
            except asyncio.CancelledError:
                # Expected — that's the whole point of the cancel.
                pass
            assert task.cancelled() is True

        asyncio.run(_scenario())

    def test_cancel_from_worker_thread_uses_call_soon_threadsafe(self) -> None:
        """``Task.cancel`` is not thread-safe; ``cancel_current_dispatch``
        must send the cancel via ``loop.call_soon_threadsafe`` when
        invoked from a worker thread (the ``/exit`` slash handler runs
        in ``asyncio.to_thread`` and reaches us through
        :func:`_request_exit`).
        """

        async def _scenario() -> None:
            import threading as _threading

            state = loop_state.ReplState()
            state.loop = asyncio.get_running_loop()

            async def _waits_forever() -> None:
                await asyncio.sleep(10.0)

            state.current_task = asyncio.create_task(_waits_forever())
            task = state.current_task

            worker_done = _threading.Event()

            def _cancel_in_worker() -> None:
                state.cancel_current_dispatch()
                worker_done.set()

            worker = _threading.Thread(target=_cancel_in_worker)
            worker.start()
            worker.join(timeout=1.0)
            assert worker_done.is_set(), "worker thread did not return"

            try:  # noqa: SIM105
                await task
            except asyncio.CancelledError:
                # Expected: the worker-triggered cancellation surfaces
                # here once the scheduled ``task.cancel`` callback runs
                # on the loop. Swallow so the assertion below can verify
                # cancellation state without the exception unwinding the
                # test.
                pass
            assert task.cancelled() is True

        asyncio.run(_scenario())

    def test_cancel_when_no_task_is_a_no_op(self) -> None:
        """``cancel_current_dispatch`` is idempotent — safe to call when
        nothing is running. With no active dispatch parked,
        ``current_cancel_event`` is ``None`` and there's nothing to flip."""
        state = loop_state.ReplState()
        state.cancel_current_dispatch()
        assert state.is_dispatch_running() is False
        assert state.current_cancel_event is None

    def test_per_dispatch_cancel_events_are_isolated(self) -> None:
        """Regression guard for the shared-event race that used to let
        a previous turn's worker-thread observation get clobbered by
        the next turn's ``Event.clear()``.

        The fix: each dispatch processor turn allocates a fresh
        ``threading.Event`` and parks it at ``state.current_cancel_event``.
        The previous turn's worker keeps a strong reference to its OWN
        event; a new turn replacing the parked one never resets the
        old worker's signal.
        """
        import threading as _threading

        state = loop_state.ReplState()

        # Turn 1: park its event, fire cancel — that event is now set.
        old_event = _threading.Event()
        state.current_cancel_event = old_event
        state.cancel_current_dispatch()
        assert old_event.is_set() is True

        # Turn 2 starts: a fresh event is parked. The OLD event must
        # still be set (its worker is still polling it from the prior
        # turn); the new event must not be — turn 2 hasn't been
        # cancelled yet.
        new_event = _threading.Event()
        state.current_cancel_event = new_event
        assert old_event.is_set() is True, (
            "old turn's event must not be cleared by a new turn parking"
        )
        assert new_event.is_set() is False

        # Cancelling the new turn flips ONLY the new event.
        state.cancel_current_dispatch()
        assert new_event.is_set() is True
        # Old event still set independently.
        assert old_event.is_set() is True


# ── Cancel key bindings ──────────────────────────────────────────────────────


class TestBuildCancelKeyBindings:
    """``_build_cancel_key_bindings`` returns prompt-level control
    handlers. The handlers are extracted out of the
    prompt loop so they can be exercised without the full async
    machinery; this test instantiates the bindings and verifies they
    were registered for the right keys."""

    def test_returns_bindings_for_ctrl_c_escape_and_ctrl_l(self) -> None:
        state = loop_state.ReplState()
        kb = build_cancel_key_bindings(state)
        # Flatten each binding's keys tuple. ``Keys`` enum members have
        # ``.value`` strings like ``"escape"``/``"c-l"`` matching the
        # decorator argument; plain string keys are themselves.
        registered = {getattr(k, "value", k) for b in kb.bindings for k in b.keys}
        assert "c-c" in registered, f"Ctrl+C binding missing — registered: {registered}"
        assert "escape" in registered, f"escape binding missing — registered: {registered}"
        assert "c-l" in registered, f"Ctrl+L binding missing — registered: {registered}"


# ── Prompt confirmation bridge (worker-thread handoff) ──────────────────────────────


class TestRequestConfirmationViaPrompt:
    """``request_confirmation_via_prompt`` runs on the worker thread that
    runs a queued turn. It parks on a ``threading.Event`` while the main
    asyncio loop collects the next ``prompt_async`` return and hands the
    text back via ``state.deliver_confirmation``. ``Esc`` (or any other
    cancel path) flips ``state.current_cancel_event`` and the polling
    loop raises ``DispatchCancelled`` within one prompt refresh tick.

    These tests pin both paths so a stuck event can never leave a worker
    parked forever. Each runs the function in a real background thread
    and asserts it returns within a short timeout.
    """

    # Generous join timeout — one poll tick is
    # ``loop_state.PROMPT_REFRESH_INTERVAL_S`` (~100ms); every return path
    # must complete well inside this, even on slow CI hardware. A
    # regression that leaves the worker parked surfaces as a test
    # failure (``t.is_alive()``) rather than a hang.
    _JOIN_TIMEOUT_S = 2.0
    # How long ``_wait_until_parked`` polls for the worker thread to
    # reach the parked state. Worker parks in microseconds (a few
    # Python statements after thread start), so 1s is a wide safety
    # margin while still failing fast if the parking never happens.
    # Must be < ``_JOIN_TIMEOUT_S`` so the parking check fails before
    # the join would.
    _PARK_TIMEOUT_S = 1.0
    # Spin granularity for the parking poll. 5ms is fine-grained enough
    # that the test sees the parked state within one or two ticks of it
    # happening (~10ms upper bound on added test latency), while
    # avoiding a tight loop that hogs the GIL from the worker thread
    # we're waiting on.
    _PARK_POLL_INTERVAL_S = 0.005

    def _wait_until_parked(self, state: loop_state.ReplState) -> None:
        """Spin until the worker has assigned ``state.confirm_event``.

        The function does this before its first ``response_event.wait``,
        so once we see it, the worker is definitively in the poll loop
        and ready to receive a delivery or cancel signal.
        """
        deadline = time.monotonic() + self._PARK_TIMEOUT_S
        while time.monotonic() < deadline:
            if state.is_awaiting_confirmation():
                return
            time.sleep(self._PARK_POLL_INTERVAL_S)
        raise AssertionError("worker never parked on confirm_event")

    def _run_in_thread(
        self, state: loop_state.ReplState, prompt_text: str
    ) -> tuple[threading.Thread, list[str], list[Exception]]:
        """Run the worker in a background thread and capture both its
        return value (``result``) and any raised exception (``exc``).

        Cancellation now raises :class:`controller_runtime.DispatchCancelled` instead
        of returning ``""``, so tests need access to both channels —
        the happy path checks ``result``, the cancel paths check
        ``exc``.
        """
        result: list[str] = []
        exc: list[Exception] = []

        def target() -> None:
            try:
                result.append(
                    controller_runtime.request_confirmation_via_prompt(state, prompt_text)
                )
            except Exception as e:
                exc.append(e)

        t = threading.Thread(target=target, daemon=True)
        t.start()
        return t, result, exc

    def test_returns_delivered_response_and_clears_state(self) -> None:
        """Happy path: main loop calls ``state.deliver_confirmation('y')``
        → worker wakes from poll → returns ``"y"``; ``confirm_event`` and
        ``confirm_response`` are cleared so the next confirmation starts
        from a fresh slate.
        """
        state = loop_state.ReplState()
        # Active dispatch must have a cancel event parked; in production
        # ``interactive_shell.runtime.turn_host.run_agent_turn`` allocates this
        # before invoking the confirm_fn. Never set in this test.
        state.current_cancel_event = threading.Event()

        t, result, exc = self._run_in_thread(state, "Proceed? [y/N] ")
        self._wait_until_parked(state)

        # Prompt text is stored in state so prompt-toolkit owns the render.
        assert state.confirm_prompt_text == "Proceed? [y/N] "

        state.deliver_confirmation("y")
        t.join(timeout=self._JOIN_TIMEOUT_S)

        assert not t.is_alive(), "worker did not return within timeout"
        assert exc == [], f"happy path raised unexpectedly: {exc}"
        assert result == ["y"]
        # Finally-block invariant: state cleared for the next prompt.
        assert state.confirm_event is None
        assert state.confirm_response == []
        assert state.confirm_prompt_text == ""

    def test_raises_dispatch_cancelled_when_cancel_event_fires(self) -> None:
        """Esc / ``/cancel`` **user-facing** path: goes through
        ``state.cancel_current_dispatch()``, which sets BOTH
        ``current_cancel_event`` AND ``confirm_event``. The worker
        wakes from ``response_event.wait`` because ``confirm_event``
        is the same object as ``response_event``; the polling loop
        exits via the natural ``while not response_event.is_set()``
        condition.

        With ``confirm_response`` never populated, the function MUST
        raise :class:`controller_runtime.DispatchCancelled` rather than returning
        the empty string. Returning ``""`` would be silently confirmed
        by ``execution_policy`` because ``[Y/n]`` treats empty as YES,
        so the in-flight action would run despite the user cancelling.
        Raising propagates out of ``execution_allowed`` and the
        surrounding action loop instead, matching what the user
        expects from ``Esc``.
        """
        state = loop_state.ReplState()
        state.current_cancel_event = threading.Event()

        t, result, exc = self._run_in_thread(state, "Proceed? [y/N] ")
        self._wait_until_parked(state)

        state.cancel_current_dispatch()
        t.join(timeout=self._JOIN_TIMEOUT_S)

        assert not t.is_alive(), "worker did not return within timeout"
        assert result == [], f"cancel path returned a value: {result}"
        assert len(exc) == 1, f"expected exactly one exception, got {exc}"
        assert isinstance(exc[0], controller_runtime.DispatchCancelled), (
            f"expected DispatchCancelled, got {type(exc[0]).__name__}: {exc[0]}"
        )
        assert state.confirm_event is None
        assert state.confirm_response == []

    def test_raises_dispatch_cancelled_when_cancel_already_set_before_park(self) -> None:
        """Race-safety: if the user pressed Esc *before* the worker
        reaches the poll loop, the first iteration must observe
        ``current_cancel_event.is_set()`` and raise immediately rather
        than waiting forever for a confirmation that won't come.

        Isolates the **in-loop cancel-check** on the FIRST iteration:
        ``current_cancel_event`` is pre-set; ``confirm_event`` is NOT
        set; the worker enters the loop, reaches the cancel-check
        BEFORE the first ``response_event.wait``, and raises
        :class:`controller_runtime.DispatchCancelled`. Deleting the cancel-check
        would make this test hang to ``_JOIN_TIMEOUT_S`` and fail.
        """
        state = loop_state.ReplState()
        cancel = threading.Event()
        cancel.set()  # already cancelled
        state.current_cancel_event = cancel

        t, result, exc = self._run_in_thread(state, "Proceed? ")
        t.join(timeout=self._JOIN_TIMEOUT_S)

        assert not t.is_alive(), "pre-set cancel did not unblock worker"
        assert result == []
        assert len(exc) == 1
        assert isinstance(exc[0], controller_runtime.DispatchCancelled)
        assert state.confirm_event is None

    def test_in_loop_cancel_check_raises_after_wait_timeout(self) -> None:
        """In-loop cancel-check on a SUBSEQUENT iteration: the worker
        is already parked in ``response_event.wait(timeout=0.1s)`` when
        cancel fires. The wait times out (because we don't touch
        ``confirm_event``), the next iteration's cancel-check sees
        ``current_cancel_event.is_set()`` and raises
        :class:`controller_runtime.DispatchCancelled`.

        Why this is needed: the user-facing test
        (``test_raises_dispatch_cancelled_when_cancel_event_fires``)
        calls ``state.cancel_current_dispatch()``, which sets BOTH the
        cancel and confirm events — the worker exits via the
        confirm_event-set path (the post-wait empty-``confirm_response``
        check), NOT via the cancel-check. Deleting the cancel-check
        would still pass that test. This test sets ONLY the cancel
        event so the only way out is through the check.
        """
        state = loop_state.ReplState()
        cancel = threading.Event()
        state.current_cancel_event = cancel

        t, result, exc = self._run_in_thread(state, "Proceed? ")
        self._wait_until_parked(state)

        # Set cancel directly — do NOT set confirm_event. The worker
        # is inside ``response_event.wait(timeout=0.1s)``; that wait
        # will time out, the next iteration's cancel-check then fires.
        cancel.set()
        t.join(timeout=self._JOIN_TIMEOUT_S)

        assert not t.is_alive(), (
            "in-loop cancel-check did not return within timeout — "
            "the function ignored a cancel signal that arrived mid-wait"
        )
        assert result == []
        assert len(exc) == 1
        assert isinstance(exc[0], controller_runtime.DispatchCancelled)
        assert state.confirm_event is None

    def test_confirm_response_reset_before_confirm_event_published(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Race-safety: the response list MUST be reset before
        ``confirm_event`` is published. Otherwise a concurrent
        ``deliver_confirmation`` (running between publish and reset)
        appends to the current list, the next statement rebinds
        ``confirm_response`` to ``[]``, and the user's answer is
        silently dropped.

        ``deliver_confirmation`` early-exits when ``confirm_event is
        None``, so resetting the list first is invisible to the main
        thread; only the event publish makes the parking observable.
        This test instruments ``__setattr__`` to verify the ordering
        deterministically — timing-based tests can't reliably hit the
        sub-microsecond race window even when the bug is present.
        """
        state = loop_state.ReplState()
        state.current_cancel_event = threading.Event()

        # Track every ``confirm_event`` / ``confirm_response`` write
        # made by ``request_confirmation_via_prompt``. Monkeypatching
        # AFTER state construction so the dataclass ``__init__`` field
        # writes don't pollute the recorded order.
        assignments: list[str] = []
        real_setattr = loop_state.ReplState.__setattr__

        def tracking_setattr(obj: object, name: str, value: object) -> None:
            if name in ("confirm_event", "confirm_response"):
                assignments.append(name)
            real_setattr(obj, name, value)  # type: ignore[arg-type]

        monkeypatch.setattr(loop_state.ReplState, "__setattr__", tracking_setattr)

        t, result, exc = self._run_in_thread(state, "Proceed? ")
        self._wait_until_parked(state)

        state.deliver_confirmation("answer")
        t.join(timeout=self._JOIN_TIMEOUT_S)

        assert exc == [], f"happy path raised unexpectedly: {exc}"
        assert result == ["answer"]

        # During setup ``request_confirmation_via_prompt`` writes both
        # attributes. The first write of each is the setup phase
        # (later writes are the ``finally`` cleanup which clears
        # both — order there doesn't matter). Pull out just the
        # setup-phase assignment order.
        setup_order: list[str] = []
        for name in assignments:
            setup_order.append(name)
            if name == "confirm_event":
                break  # confirm_event publish is the last setup write
        assert "confirm_response" in setup_order, (
            f"confirm_response never reset during setup — saw {setup_order}"
        )
        response_idx = setup_order.index("confirm_response")
        event_idx = setup_order.index("confirm_event")
        assert response_idx < event_idx, (
            f"race window: confirm_event published before "
            f"confirm_response was reset — order was {setup_order}"
        )

    def test_empty_string_delivery_returns_empty_string(self) -> None:
        """User presses Enter on the confirmation prompt without typing
        anything → ``state.deliver_confirmation("")`` is called →
        function returns ``""``. Real production case: ``[Y/n]`` prompts
        treat plain Enter as "yes" (capital Y default).

        This is the ONLY path that legitimately yields an empty-string
        return: ``deliver_confirmation`` populated ``confirm_response``
        with ``""`` BEFORE setting the event, so the post-wait check
        sees a non-empty list and returns the user's actual answer.
        Cancellation, by contrast, sets the event WITHOUT delivering
        an answer and is now distinguishable — that path raises
        :class:`controller_runtime.DispatchCancelled` (see the cancel-path tests
        above).
        """
        state = loop_state.ReplState()
        state.current_cancel_event = threading.Event()

        t, result, exc = self._run_in_thread(state, "Proceed? [y/N] ")
        self._wait_until_parked(state)

        state.deliver_confirmation("")
        t.join(timeout=self._JOIN_TIMEOUT_S)

        assert not t.is_alive(), "empty delivery did not unblock worker"
        assert exc == [], f"explicit empty delivery should not raise: {exc}"
        assert result == [""]
        assert state.confirm_event is None
        assert state.confirm_response == []


def test_reset_prompt_buffer_schedules_on_the_ui_loop() -> None:
    """Confirmation parks on a worker thread; buffer.reset must not run there."""
    from surfaces.interactive_shell.runtime.turn_host import _reset_prompt_buffer

    scheduled: list[object] = []

    class _Loop:
        def call_soon_threadsafe(self, fn: object, *args: object) -> None:
            scheduled.append((fn, args))

    class _Buffer:
        def __init__(self) -> None:
            self.resets = 0

        def reset(self) -> None:
            self.resets += 1

    class _App:
        def __init__(self) -> None:
            self.current_buffer = _Buffer()

    session = Session()
    app = _App()
    session.terminal.prompt_app = app
    session.terminal.main_loop = _Loop()

    _reset_prompt_buffer(session)

    assert app.current_buffer.resets == 0
    assert len(scheduled) == 1
    fn, args = scheduled[0]
    assert args == ()
    assert callable(fn)
    fn()
    assert app.current_buffer.resets == 1


class TestExecutionAllowedRespectsDispatchCancelled:
    """End-to-end contract: cancelling during ``Proceed? [Y/n]`` must
    actually STOP the in-flight action — not just stop the spinner.

    Pre-fix, the cancel handler returned ``""``; ``execution_allowed``
    treated empty as YES (since ``[Y/n]`` defaults to Y) and the worker
    happily ran the action it was supposed to interrupt — only the
    spinner stopped, the agent kept going. This test class pins the
    new contract: the confirm callable raises ``DispatchCancelled`` and
    the exception propagates out of ``execution_allowed`` *without*
    silently confirming the action. The action loop in
    ``run_action_tool_turn`` therefore exits via the exception, the
    in-flight action never runs, and any further actions in the same
    plan are skipped.
    """

    def test_dispatch_cancelled_propagates_through_execution_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rich.console import Console

        from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
        from tools.interactive_shell.shared import (
            ExecutionPolicyResult,
        )

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        session = Session()
        console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

        def _cancel_confirm(_prompt: str) -> str:
            raise controller_runtime.DispatchCancelled("cancelled while awaiting confirmation")

        policy = ExecutionPolicyResult(
            verdict="ask",
            tool_type="opensre_cli",
            reason="this opensre subcommand may change local config or infrastructure",
        )

        with pytest.raises(controller_runtime.DispatchCancelled):
            execution_allowed(
                policy,
                session=session,
                console=console,
                action_summary="opensre remote health --help",
                confirm_fn=_cancel_confirm,
                is_tty=True,
            )

    def test_empty_confirm_response_would_silently_allow_without_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard documenting WHY the raise is required.

        If the confirm callable returned ``""`` instead of raising,
        ``execution_allowed`` would treat the empty answer as YES
        (``[Y/n]`` defaults to Y) and the action would run. This test
        pins that footgun by demonstrating the bad outcome with an
        empty-string return — the cancel handler MUST raise, not
        return, to actually stop the in-flight action.
        """
        from rich.console import Console

        from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
        from tools.interactive_shell.shared import (
            ExecutionPolicyResult,
        )

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        session = Session()
        console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

        policy = ExecutionPolicyResult(
            verdict="ask",
            tool_type="opensre_cli",
            reason="this opensre subcommand may change local config or infrastructure",
        )

        # Empty string answer (the pre-fix cancel return value) is
        # silently confirmed — proving the bug existed and that we
        # cannot rely on a sentinel string for cancellation.
        assert (
            execution_allowed(
                policy,
                session=session,
                console=console,
                action_summary="opensre remote health --help",
                confirm_fn=lambda _prompt: "",
                is_tty=True,
            )
            is True
        )


class TestThemeCommand:
    @staticmethod
    def _capture() -> tuple:
        from rich.console import Console

        buf = io.StringIO()
        return Console(file=buf, force_terminal=False, highlight=False), buf

    def test_theme_command_is_registered(self) -> None:
        assert "/theme" in SLASH_COMMANDS

    def test_theme_command_updates_active_theme_and_persists(self, monkeypatch) -> None:
        from surfaces.interactive_shell.command_registry import theme as theme_cmd

        saved_payloads: list[dict[str, object]] = []
        monkeypatch.setattr(theme_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(theme_cmd, "repl_choose_one", lambda **_kwargs: "blue")
        monkeypatch.setattr(theme_cmd, "_refresh_prompt_style", lambda _session: None)
        monkeypatch.setattr("config.local_settings.load_local_settings", lambda: {})
        monkeypatch.setattr(
            "config.local_settings.save_local_settings",
            lambda data: saved_payloads.append(dict(data)),
        )

        set_active_theme("green")
        session = Session()
        console, _buf = self._capture()

        assert dispatch_slash("/theme", session, console) is True
        assert get_active_theme_name() == "blue"
        assert saved_payloads
        interactive = saved_payloads[-1].get("interactive")
        assert isinstance(interactive, dict)
        assert interactive.get("theme") == "blue"

    def test_theme_picker_uses_session_theme_as_current(self, monkeypatch) -> None:
        from surfaces.interactive_shell.command_registry import theme as theme_cmd

        monkeypatch.setattr(theme_cmd, "repl_tty_interactive", lambda: True)
        captured: dict[str, object] = {}

        def _fake_choose_one(**kwargs: object) -> None:
            captured.update(kwargs)
            return None

        monkeypatch.setattr(theme_cmd, "repl_choose_one", _fake_choose_one)

        session = Session()
        session.terminal.active_theme_name = "pink"
        set_active_theme("pink")
        console, _buf = self._capture()

        dispatch_slash("/theme", session, console)

        assert captured.get("initial_value") == "pink"
        choices = captured.get("choices")
        assert isinstance(choices, list)
        assert any("pink (current)" in label for _value, label in choices)

    def test_theme_command_direct_arg_sets_theme(self, monkeypatch) -> None:
        from surfaces.interactive_shell.command_registry import theme as theme_cmd

        monkeypatch.setattr(theme_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(theme_cmd, "_refresh_prompt_style", lambda _session: None)
        monkeypatch.setattr("config.local_settings.load_local_settings", lambda: {})
        monkeypatch.setattr("config.local_settings.save_local_settings", lambda _data: None)

        set_active_theme("green")
        session = Session()
        console, _buf = self._capture()

        assert dispatch_slash("/theme amber", session, console) is True
        assert get_active_theme_name() == "amber"

    def test_theme_change_refreshes_welcome_poster(self, monkeypatch) -> None:
        from surfaces.interactive_shell.command_registry import theme as theme_cmd

        monkeypatch.setattr(theme_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(theme_cmd, "repl_choose_one", lambda **_kwargs: "blue")
        monkeypatch.setattr(theme_cmd, "_refresh_prompt_style", lambda _session: None)
        monkeypatch.setattr("config.local_settings.load_local_settings", lambda: {})
        monkeypatch.setattr("config.local_settings.save_local_settings", lambda _data: None)

        refreshed: list[dict[str, object | None]] = []

        def _refresh(
            console: object,
            *,
            session: object = None,
            theme_notice: str | None = None,
        ) -> None:
            refreshed.append({"console": console, "session": session, "theme_notice": theme_notice})

        monkeypatch.setattr(
            "surfaces.interactive_shell.ui.poster.refresh_welcome_poster",
            _refresh,
        )

        session = Session()
        console, _buf = self._capture()

        assert dispatch_slash("/theme", session, console) is True
        assert len(refreshed) == 1
        assert refreshed[0]["session"] is session
        assert refreshed[0]["theme_notice"] == "blue"

    def test_theme_picker_lists_all_registered_themes(self, monkeypatch) -> None:
        from infrastructure.terminal.theme import list_theme_names
        from surfaces.interactive_shell.command_registry import theme as theme_cmd

        monkeypatch.setattr(theme_cmd, "repl_tty_interactive", lambda: True)
        captured: dict[str, object] = {}

        def _fake_choose_one(**kwargs: object) -> None:
            captured.update(kwargs)
            return None

        monkeypatch.setattr(theme_cmd, "repl_choose_one", _fake_choose_one)

        session = Session()
        console, _buf = self._capture()
        dispatch_slash("/theme", session, console)

        choices = captured.get("choices")
        assert isinstance(choices, list)
        assert [value for value, _label in choices] == list(list_theme_names())

    def test_theme_persist_drains_cpr_around_poster_and_defers_prompt_refresh(
        self, monkeypatch
    ) -> None:
        from surfaces.interactive_shell.command_registry import theme as theme_cmd

        drains: list[str] = []
        monkeypatch.setattr(
            theme_cmd,
            "_settle_and_drain_cpr",
            lambda: drains.append("drain"),
        )
        monkeypatch.setattr(
            "surfaces.interactive_shell.ui.poster.refresh_welcome_poster",
            lambda *_args, **_kwargs: drains.append("poster"),
        )
        monkeypatch.setattr("config.local_settings.load_local_settings", lambda: {})
        monkeypatch.setattr("config.local_settings.save_local_settings", lambda _data: None)

        session = Session()
        console, _buf = self._capture()
        theme_cmd._persist_and_report_theme(session, console, "pink")

        assert drains == ["drain", "poster", "drain"]
        assert session.terminal.pending_theme_refresh is True


def test_refresh_prompt_theme_skips_invalidate_when_app_not_running() -> None:
    from surfaces.interactive_shell.ui.input_prompt.style import refresh_prompt_theme

    invalidated: list[bool] = []

    class _App:
        is_running = False
        style = None
        placeholder = None
        renderer = None

        def invalidate(self) -> None:
            invalidated.append(True)

    session = Session()
    session.terminal.prompt_app = _App()
    refresh_prompt_theme(session)
    assert invalidated == []
    assert session.terminal.prompt_app.style is not None
