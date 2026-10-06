# Interactive shell UI: terminal responsiveness

These instructions apply to `interactive_shell/ui/` and every change that
prints to, paints on, or resizes the interactive terminal, wherever the code
lives. Parent `AGENTS.md` files still apply.

## The model: the terminal never owns live output

The shell runs as a full-screen prompt-toolkit app (alternate screen). The
conversation lives in `transcript_view.TranscriptStore` as entries that render
themselves at any width. `TranscriptControl` draws the newest rows above the
status line and composer, so a resize is just another paint. When the app
leaves the screen (pickers, exit), entries not yet in scrollback are written
there in order.

Resize bugs came back every time the terminal was allowed to own live rows and
we tried to predict how it reflowed them (`CR CUU(n) ED` erases, `ESC[2J`
banner repaints, settle timers). Terminals reflow differently and repaint
timing races them. Do not reintroduce that class of code.

## Rules

- **Write through `sys.stdout`.** The shell's stdout proxy routes text into the
  transcript while the app is on screen. Writing to `sys.__stdout__`, a saved
  original stream, or fd 1 bypasses it and corrupts the full-screen frame.
- **Print structured output as a renderable.** Use `print_repl_table`,
  `print_repl_renderable`, `print_repl_text`, `print_repl_json`, or a
  `StreamingConsole` print (all reach `record_in_transcript`). The store then
  keeps the Rich object and lays it out again on shrink *and* grow. Output
  from an ad-hoc `Console().print` is captured as ANSI text: it re-wraps on
  shrink but never widens back.
- **No cursor-addressed repaint of history.** No relative erases above the
  cursor, no `ESC[2J`/`ESC[3J` to "clean up", no reflow prediction or resize
  debounce. To start over, clear the store (`reset_full_screen_transcript`,
  `TranscriptStore.clear`).
- **Mark inline menus transient.** A menu that repaints itself in place must
  open with `begin_inline_menu_output()` (or `enter_inline_menu`) and close in
  `leave_inline_menu`, so its frames are painted but never recorded.
- **Lay out per render, never per construction.** Read the width from Rich's
  `ConsoleOptions` at render time; do not capture `console.width` when building
  a renderable. Do not pad rows to the full width (`Align.center`, padded
  grids): use `UnpaddedRows` / `_GutterRow`, and bound centred content.
- **Let new chrome collapse.** Any row added to the prompt frame must allow
  `Dimension(min=0)` so a short window keeps the 3-row composer instead of
  showing prompt-toolkit's "Window too small".
- **Leave full screen through the prompt builder.** Code that hands the
  terminal to another UI (pickers, raw input, subprocesses) must go through
  the paths that call `PromptBuilder._restore_terminal_autowrap`, which flushes
  the transcript to scrollback first.

## Terminal facts that bite

- VTE (GNOME Terminal, Tilix) moves the screen into scrollback on `ESC[2J`, and
  does not support DEC 2026 synchronized output.
- tmux coalesces resize signals to one per 250ms; iTerm2 gates `ESC[3J`.
- Without mouse support, VTE's alternate-scroll mode turns the wheel into
  arrow keys, which scroll prompt history instead of the transcript.
- Rich ignores a `width` override on `TERM=dumb`/`unknown` unless `height` is
  passed too.
- prompt-toolkit's own size poll repaints every 0.5s, so any repaint you add
  will run again; it must be idempotent.

## Verify locally before pushing (mandatory for UI changes)

Unit tests are necessary but not sufficient: they cannot see how a real
terminal reflows, scrolls, or leaves the alternate screen.

1. Run the focused tests:

   ```bash
   uv run python -m pytest -q tests/interactive_shell/ui/test_transcript_view.py
   ```

   Add a test for each new failure mode and confirm it fails on the code
   before your fix.

2. Drive the real shell in headless tmux. tmux models the alternate screen and
   scrollback, so it shows what a user sees; a pure emulator such as `pyte`
   does not, and misreports exit behaviour.

   ```bash
   T="tmux -L osre"; S=osre
   $T new-session -d -s $S -x 100 -y 30 \
     "OPENSRE_NO_UPDATE_CHECK=1 uv run opensre; sleep 600"
   sleep 12; $T send-keys -t $S C; sleep 4                  # demo menu: open the shell
   $T send-keys -t $S -l "/version"; $T send-keys -t $S Enter; sleep 3
   $T resize-window -t $S -x 60 -y 30; sleep 2
   $T capture-pane -t $S -p                                  # current screen
   $T capture-pane -t $S -p -S -                             # scrollback + screen
   $T kill-server
   ```

   Send `Enter` as its own `send-keys` call; an `Escape` immediately followed
   by `Enter` arrives as Alt+Enter and inserts a newline.

3. Walk this checklist and record the results in the PR:

   | Scenario | Pass when |
   |---|---|
   | Drag width 100 → 140 → 78 → 60 → 140 | Exactly one status line and one composer at every width; banner re-centres; replies and tables re-flow both ways |
   | Short window: 80×9 → 80×4, then 33×3 with a draft, back to 100×30 | Composer and draft stay visible; the status line gives way first; draft survives the regrow |
   | Stream a model reply while scrolled up (wheel or PageUp) | The passage being read does not move; submitting returns to the bottom |
   | Open and close an inline menu or picker | No menu frames left in the transcript; the shell redraws cleanly |
   | `/clear` | Transcript resets with a banner that fits the width |
   | `/exit` (and Ctrl-D on an empty prompt) | Scrollback holds the banner and the whole conversation once, in order, before `goodbye.` |

   If you touched terminal-specific behaviour, repeat the drag and exit rows in
   a real terminal (VTE-based, and macOS Terminal or iTerm2 when available).
