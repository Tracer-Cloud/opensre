# Terminal UI design guidance

Applies to this UI tree. The repo-root and parent `AGENTS.md` rules still apply.
Use these conventions for related CLI and shared-terminal presentation changes
as well; keep implementation in its owning package, not necessarily this folder.

## Reuse the existing presentation layer

- Use `RecordTable`, `RecordColumn`, and `RecordRow` from
  `surfaces/shared/terminal/tables/records.py` for record-style lists. Do not
  duplicate column sizing, breakpoints, or stacked-row rendering per command.
- Use `description_details` from `surfaces/shared/terminal/tables/descriptions.py`
  for spaced summary text. Keep domain-specific formatting in its owning renderer;
  keep command handlers focused on parsing, fetching, and dispatching.
- Share presentation between CLI and REPL where their contracts match. Do not
  introduce another generic framework or force non-record views into a table.

## Hierarchy and spacing

- Names/titles are bold and primary. Descriptions and ordinary results use
  `SECONDARY`; IDs, supporting metadata, timestamps, and hints use `DIM`.
- **Input-form convention:** field descriptions, examples, and required/optional/default
  markers must remain readable. Use `SECONDARY` (the shared command-input
  `description` style) or `TEXT`; never `DIM`/`hint` for information needed to
  fill a field. Reserve `DIM`/`hint` for keyboard shortcuts and tertiary metadata.
  Keep field labels primary and errors in their semantic error style. Apply
  this consistently to CLI, REPL, and shared-terminal forms.
- Do not make every field equally dim: descriptions must remain readable.
  Preserve semantic emphasis for errors, warnings, running progress, and states
  such as **Invalid**, **Failed**, or **Blocked**. Never globally mute diagnostics.
- Use tokens from `infrastructure/terminal/theme.py`, not inline colors. Resolve
  lazy tokens with `str(token)` when constructing a concrete Rich text style.
  Express hierarchy through weight and color, not per-row terminal font sizes.
- Use two terminal cells of outer horizontal padding and four cells between
  summary columns. Size columns to displayed content; never stretch short fields
  across the screen or squeeze the gutters to retain a table.
- Put full IDs in `RecordRow.metadata`, aligned below the primary column after
  one blank line. Keep Channel, TZ, and populated Project values in summary
  columns rather than crowding them onto the ID line. Omit all-empty optional
  columns. IDs must not participate in summary column sizing.
- Indent supporting details and descriptions two cells beyond the outer padding.
  On narrow screens, keep name and IDs first, then separated labeled fields.
  Choose stacked output from actual column widths plus gutters, not a fixed
  terminal breakpoint. Preserve readable name wrapping for exceptionally long names.
- Put one blank line above a non-empty summary and one between records. Let
  `description_details` and `RecordTable` supply those gaps; do not add them twice.
  Empty descriptions must not create an empty summary block.

## Responsive output and bounded previews

- Keep wide-screen summary columns compact and grouped on the left, rather than
  stretching metadata to the terminal's right edge. Let the shared renderer
  recompute geometry on render/replay so resizing remains correct.
- On narrow terminals, use labeled stacked records. Long IDs and descriptions
  belong in reflowing detail lines, not tiny columns or a no-wrap cell.
- Bound list previews by terminal-cell width, not Python string length. The shared
  description helper defaults to 80 cells with an ellipsis; use explicit limits
  where the view's existing contract requires them. Normalize summary whitespace.
- Truncation requires a usable route to the complete content: for example
  `/memory show <name>`, `/loops show <id>`, `opensre cron logs <task_id> --run <Run>`,
  or `opensre --json cron list`. Keep full details unchanged at that destination.
- Preserve complete actionable identifiers and timezone context. Do not truncate
  work-ranking reasons when there is no full-detail destination; allow them to
  wrap. Render stored/user text with Rich `Text` or escape it before markup.

## Reduce noise without losing information

- Keep list rows for scanning. Move long recovery instructions to detail views,
  but retain the failing state and a clear navigation hint in the list.
- Omit absent metadata such as `Project: —`, duplicated command/summary text,
  and routine storage-path footers when an explicit `path` command exists.
  Keep legitimate zero values and meaningful statuses; do not blanket-hide fields.
- Keep privacy warnings such as memory being stored unencrypted. Shorten their
  wording rather than removing them; keep management/help routes discoverable.
- Preserve JSON output, stored data, execution semantics, and full-report content
  when polishing presentation. Do not infer failures from keywords in prose;
  use structured state to choose warning/error styling.

## Menus and verification

- When a root command opens a submenu, expose its default list operation as an
  explicit first `list` choice. Preserve existing bare-command dispatch where
  supported, and keep handler, completions, usage, and catalog metadata in sync.
- Follow the parent's stdin rules. Register the exact table-producing subcommand
  in `runtime/input_policy.py`; a bare-command reservation does not cover aliases.
  Use the established REPL render wrapper so transcript recording/replay works.
- For visible changes, inspect actual output at a narrow width (40 columns), a
  typical width (80–100), and a wide width (160 or more). Check long/Unicode names,
  literal markup, empty data, missing descriptions, and failure states.
- Add focused regressions for information loss, preview limits, spacing, and
  full-detail access. For menu/stdin changes, exercise the real PTY journey and
  type into the following prompt to catch leaked CPR bytes or broken editing.
- Put current before/after evidence in the PR's Demo section, using isolated
  synthetic data. Distinguish actual captures from design previews; verify images
  load. Do not commit demo screenshots into source or `docs/` just to host them.
  Follow `CI.md` for validation; this guide does not add a second CI pipeline.
