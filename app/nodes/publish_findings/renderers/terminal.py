"""Terminal rendering for RCA reports — Claude-style output."""

import re

from rich.console import Console
from rich.text import Text

from app.output import get_output_format

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+")
# Matches Slack-style links: <url|label> or <url>
_SLACK_LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")


def _rich_line_with_links(text: str) -> Text:
    """Convert a plain/Slack-mrkdwn string into a Rich Text with blue hyperlinks."""
    result = Text()
    cursor = 0

    for m in _SLACK_LINK_RE.finditer(text):
        # Text before the match
        if m.start() > cursor:
            result.append(text[cursor:m.start()])
        url = m.group(1)
        label = m.group(2) or url
        result.append(label, style=f"link {url} bold blue underline")
        cursor = m.end()

    remaining = text[cursor:]
    # Linkify any bare https?:// URLs left in remaining text
    sub_cursor = 0
    for m in _URL_RE.finditer(remaining):
        if m.start() > sub_cursor:
            result.append(remaining[sub_cursor:m.start()])
        url = m.group(0).rstrip(".,;)")
        result.append(url, style=f"link {url} bold blue underline")
        sub_cursor = m.end()
    if sub_cursor < len(remaining):
        result.append(remaining[sub_cursor:])

    return result


def _strip_slack_links(text: str) -> str:
    """Convert Slack <url|label> to plain 'label (url)' for plain text mode."""
    def _repl(m: re.Match) -> str:
        url, label = m.group(1), m.group(2)
        return f"{label} ({url})" if label else url
    return _SLACK_LINK_RE.sub(_repl, text)


def _strip_mrkdwn(text: str) -> str:
    """Remove Slack mrkdwn bold markers (*text*) for plain output."""
    return re.sub(r"\*([^*]+)\*", r"\1", text)


# ─────────────────────────────────────────────────────────────────────────────
# Section renderers
# ─────────────────────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*?([^*]+)\*\*?")


def _render_rich_section_heading(console: Console, title: str) -> None:
    console.print()
    t = Text()
    t.append(f"  {title}", style="bold white")
    console.print(t)


def _render_rich_bullet(console: Console, line: str, *, indent: int = 4) -> None:
    """Render a bullet line with links resolved."""
    body = line.lstrip("•● -").strip()
    t = Text(" " * indent + "· ")
    t.append_text(_rich_line_with_links(body))
    console.print(t)


def _render_rich_numbered(console: Console, line: str) -> None:
    """Render a numbered trace step."""
    m = re.match(r"^(\d+)\.\s+(.+)$", line)
    if not m:
        _render_rich_bullet(console, line)
        return
    num, body = m.group(1), m.group(2)
    t = Text(f"    {num}. ")
    t.style = "dim"
    t.append_text(_rich_line_with_links(body))
    console.print(t)


def _render_rich_evidence_item(console: Console, line: str) -> None:
    """Render a cited evidence item (lines starting with '- ')."""
    body = line.lstrip("- ").strip()
    t = Text("    — ")
    t.append_text(_rich_line_with_links(body))
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
# Main render entry points
# ─────────────────────────────────────────────────────────────────────────────

def render_report(slack_message: str, root_cause_category: str | None = None) -> None:
    """Render the final RCA report to terminal."""
    fmt = get_output_format()

    if not slack_message:
        if fmt == "rich":
            Console().print(Text.assemble(("  ● ", "bold yellow"), ("No report generated.", "dim")))
        else:
            print("No report generated.")
        return

    if fmt == "rich":
        _render_rich_report(slack_message, root_cause_category=root_cause_category)
    else:
        _render_plain_report(slack_message, root_cause_category=root_cause_category)


def _render_rich_report(slack_message: str, root_cause_category: str | None = None) -> None:
    console = Console()
    console.print()

    # Header varies by outcome
    done = Text()
    if root_cause_category == "healthy":
        done.append("  ✓ ", style="bold green")
        done.append("Systems healthy", style="bold green")
    else:
        done.append("  ● ", style="bold green")
        done.append("Investigation complete", style="bold white")
    console.print(done)
    console.print()

    lines = slack_message.splitlines()
    in_evidence = False

    for line in lines:
        stripped = line.strip()

        # Section headings  (## Findings / ## Investigation Trace)
        m = _HEADING_RE.match(stripped)
        if m:
            _render_rich_section_heading(console, m.group(1))
            in_evidence = False
            continue

        # *Cited Evidence:* label
        if stripped in ("*Cited Evidence:*", "Cited Evidence:"):
            _render_rich_section_heading(console, "Cited Evidence")
            in_evidence = True
            continue

        # Evidence items  (lines starting with "- ")
        if stripped.startswith("- ") and in_evidence:
            _render_rich_evidence_item(console, stripped)
            continue

        # Bullet points  (• or - at start)
        if stripped.startswith(("• ", "● ", "- ")) and not in_evidence:
            _render_rich_bullet(console, stripped)
            continue

        # Numbered trace steps  "1. …"
        if re.match(r"^\d+\.", stripped):
            _render_rich_numbered(console, stripped)
            continue

        # Code spans  "`…`"
        if stripped.startswith("`") and stripped.endswith("`"):
            t = Text(f"    {stripped}", style="dim cyan")
            console.print(t)
            continue

        # Skip Timing line — already visible in spinner timings per step
        if stripped.startswith("Timing:"):
            continue

        # Alert ID meta
        if stripped.startswith(("*Alert ID:*", "Alert ID:")):
            clean = _BOLD_RE.sub(r"\1", stripped)
            console.print(Text(f"    {clean}", style="dim"))
            continue

        # Blank lines — pass through (skip double blanks)
        if not stripped:
            continue

        # Default: render with link highlighting
        t = Text("  ")
        t.append_text(_rich_line_with_links(stripped))
        console.print(t)

    console.print()


def _render_plain_report(slack_message: str, root_cause_category: str | None = None) -> None:
    print()
    if root_cause_category == "healthy":
        print("✓ Systems healthy")
    else:
        print("Investigation complete")
    print()
    clean = _strip_slack_links(_strip_mrkdwn(slack_message))
    print(clean)
