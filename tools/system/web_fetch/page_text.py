"""Turn a fetched response body into text a model can read.

HTML becomes plain text that keeps what carries meaning: the title, headings,
paragraphs, list items, code blocks, table rows and link targets. Scripts and
styles are dropped. When a page marks its content with ``<main>`` or
``<article>``, only that part is kept, so a documentation page is not mostly
its navigation. Other text formats are returned as they are.
"""

from __future__ import annotations

import codecs
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

#: The kinds of response a fetch reads, as a refusal names them.
READABLE_KINDS = "HTML, plain text, Markdown, JSON and XML"

_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_TEXT_MEDIA_TYPES = frozenset({"application/json", "application/xml"})
_TEXT_SUFFIXES = ("+json", "+xml")

# Charset labels a page may name, by the codec that reads them. A label is the
# server's choice, so only real text encodings are accepted. Browsers read the
# Latin-1 and ASCII labels as Windows-1252.
_CODECS = {
    "ascii": "cp1252",
    "big5": "big5",
    "cp1250": "cp1250",
    "cp1251": "cp1251",
    "cp1252": "cp1252",
    "euc_jp": "euc_jp",
    "euc_kr": "euc_kr",
    "gb18030": "gb18030",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "iso2022_jp": "iso2022_jp",
    "iso8859-1": "cp1252",
    "iso8859-15": "iso8859-15",
    "iso8859-2": "iso8859-2",
    "koi8-r": "koi8-r",
    "shift_jis": "shift_jis",
    "utf-16": "utf-16",
    "utf-16-be": "utf-16-be",
    "utf-16-le": "utf-16-le",
    "utf-8": "utf-8",
    "utf-8-sig": "utf-8-sig",
}
_BYTE_ORDER_MARKS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_META_CHARSET = re.compile(rb"<meta[^>]{0,200}?charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.I)
_META_SCAN_BYTES = 4096

# Elements whose content is never page text.
_DROPPED = frozenset(
    {"script", "style", "noscript", "template", "svg", "iframe", "canvas", "object", "select"}
)
_CONTENT_REGIONS = frozenset({"main", "article"})
_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_LISTS = frozenset({"ul", "ol"})
_CELLS = frozenset({"td", "th"})
# Elements that begin and end a block of text.
_BLOCKS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "caption",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hr",
        "main",
        "nav",
        "p",
        "section",
        "summary",
        "table",
    }
)
# Stands for a ``<br>`` in a block's text until the block is closed. A newline
# cannot: the source's own line breaks are only spaces. No document reaches the
# parser with this character in it.
_LINE_BREAK = "\x00"
_FOLLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_LINK_CHARS = 500
_MAX_TITLE_CHARS = 200
#: Text a marked content region must hold before the rest of the page is left out.
_CONTENT_REGION_MIN_CHARS = 200
_FEED_CHARS = 64 * 1024
#: A page is parsed in pure Python; one that takes longer is returned as far as it got.
_CONVERT_SECONDS = 5.0
# Text can outgrow its markup: a relative link becomes an absolute one, a nested
# list item gains an indent. Both are capped so the text of a page stays near
# the size of the page, however it was written.
_MAX_TEXT_CHARS = 2 * 1024 * 1024
_MAX_INDENT_LEVELS = 6


class NotText(Exception):
    """A response declared as text that holds binary data."""

    kind = "unsupported_content"


@dataclass(frozen=True, slots=True)
class PageText:
    """What a page says, as text."""

    title: str
    text: str
    #: False when conversion stopped before the end of the document.
    complete: bool


def is_text_media_type(media_type: str) -> bool:
    """Whether ``media_type`` (lower case, no parameters) is one a fetch reads."""
    return (
        media_type.startswith("text/")
        or media_type in _HTML_MEDIA_TYPES
        or media_type in _TEXT_MEDIA_TYPES
        or media_type.endswith(_TEXT_SUFFIXES)
    )


def readable_text(body: bytes, *, media_type: str, charset: str | None, url: str) -> PageText:
    """Decode ``body`` and, for HTML, reduce it to its readable text.

    ``url`` is the page's own address, used to make its links absolute.
    Raises :class:`NotText` when the body is binary.
    """
    is_html = media_type in _HTML_MEDIA_TYPES
    document = _decoded(body, charset, sniff_meta=is_html)
    if "\x00" in document:
        raise NotText("The response is binary data, not text.")
    if not is_html:
        return PageText(title="", text=document.strip(), complete=True)
    reader = _ReadableHTML(url)
    stop_at = time.monotonic() + _CONVERT_SECONDS
    complete = True
    for start in range(0, len(document), _FEED_CHARS):
        if time.monotonic() > stop_at or reader.collected_chars() > _MAX_TEXT_CHARS:
            complete = False
            break
        reader.feed(document[start : start + _FEED_CHARS])
    else:
        reader.close()
    return PageText(title=reader.title(), text=reader.text(), complete=complete)


def _decoded(body: bytes, declared: str | None, *, sniff_meta: bool) -> str:
    """``body`` as text: by its byte-order mark, its declared charset, or UTF-8."""
    labels: list[str] = [codec for mark, codec in _BYTE_ORDER_MARKS if body.startswith(mark)]
    if declared:
        labels.append(declared)
    if sniff_meta:
        named = _META_CHARSET.search(body[:_META_SCAN_BYTES])
        if named:
            labels.append(named.group(1).decode("ascii"))
    for label in labels:
        try:
            codec = _CODECS.get(codecs.lookup(label).name)
        except LookupError:
            continue
        if codec:
            return body.decode(codec, errors="replace")
    return body.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class _Block:
    text: str
    #: A list item or table row: it follows the previous one without a blank line.
    tight: bool
    in_content_region: bool


class _ReadableHTML(HTMLParser):
    """Collects a document's text as blocks, in reading order."""

    def __init__(self, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._url = url
        self._blocks: list[_Block] = []
        self._block_chars = 0
        self._title: list[str] = []
        self._in_title = False
        # Inline text of the block being read, whether any of it is visible
        # yet, and how that block opens.
        self._run: list[str] = []
        self._run_has_text = False
        self._marker = ""
        self._tight = False
        # ``(tag, nesting)`` while inside a dropped element / a content region.
        self._dropping: tuple[str, int] | None = None
        self._region: tuple[str, int] | None = None
        self._preformatted: list[str] | None = None
        # One entry per open list: the next number of an ordered list, else None.
        self._lists: list[int | None] = []
        # The open link: its target, if it leads to another page, where its text
        # starts in ``_run``, and whether the block had visible text before it.
        self._link: tuple[str | None, int, bool] | None = None

    def title(self) -> str:
        return " ".join("".join(self._title).split())[:_MAX_TITLE_CHARS]

    def collected_chars(self) -> int:
        """How much text has been collected so far, closed blocks and open one."""
        return (
            self._block_chars + sum(map(len, self._run)) + sum(map(len, self._preformatted or ()))
        )

    def text(self) -> str:
        """The collected text: the marked content region when the page has one."""
        self._end_block()
        marked = [block for block in self._blocks if block.in_content_region]
        enough = sum(len(block.text) for block in marked) >= _CONTENT_REGION_MIN_CHARS
        chosen = marked if enough else self._blocks
        parts: list[str] = []
        previous: _Block | None = None
        for block in chosen:
            if previous is not None:
                parts.append("\n" if previous.tight and block.tight else "\n\n")
            parts.append(block.text)
            previous = block
        return "".join(parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._dropping is not None:
            self._dropping = _nested(self._dropping, tag, +1)
            return
        if tag in _DROPPED:
            self._dropping = (tag, 1)
            return
        if tag == "title":
            self._in_title = not self._title
            return
        # A title that is never closed must not take the page with it.
        self._in_title = False
        attributes = dict(attrs)
        if self._region is not None:
            self._region = _nested(self._region, tag, +1)
        elif tag in _CONTENT_REGIONS or attributes.get("role") == "main":
            self._end_block()
            self._region = (tag, 1)
        if self._preformatted is not None:
            if tag == "br":
                self._preformatted.append("\n")
            return
        if tag == "pre":
            self._end_block()
            self._preformatted = []
        elif tag == "br":
            self._run.append(_LINE_BREAK)
        elif tag in _HEADING_LEVELS:
            self._end_block()
            self._marker = "#" * _HEADING_LEVELS[tag] + " "
        elif tag in _LISTS:
            self._end_block()
            self._lists.append(1 if tag == "ol" else None)
        elif tag == "li":
            self._end_block()
            self._marker, self._tight = self._list_marker(), True
        elif tag == "tr":
            self._end_block()
            self._tight = True
        elif tag in _CELLS:
            if self._run_has_text:
                self._run.append(" | ")
        elif tag == "a":
            target = _link_target(self._url, attributes.get("href"))
            self._link = (target, len(self._run), self._run_has_text)
        elif tag == "code":
            self._run.append("`")
        elif tag in _BLOCKS:
            self._end_block()

    def handle_endtag(self, tag: str) -> None:
        if self._dropping is not None:
            self._dropping = _nested(self._dropping, tag, -1)
            return
        if tag == "title":
            self._in_title = False
            return
        if tag == "pre" and self._preformatted is not None:
            code = "".join(self._preformatted).strip("\n")
            self._preformatted = None
            if code.strip():
                self._add(f"```\n{code}\n```", tight=False)
        elif self._preformatted is not None:
            pass
        elif tag == "a":
            self._close_link()
        elif tag == "code":
            self._run.append("`")
        elif tag in _LISTS:
            self._end_block()
            if self._lists:
                self._lists.pop()
        elif tag in _HEADING_LEVELS or tag in ("li", "tr"):
            self._end_block()
            # An item that held no text leaves no marker for what follows it.
            self._marker, self._tight = "", False
        elif tag in _BLOCKS:
            self._end_block()
        if self._region is not None:
            closing = self._region[0] == tag and self._region[1] == 1
            if closing:
                self._end_block()
            self._region = _nested(self._region, tag, -1)

    def handle_data(self, data: str) -> None:
        if self._dropping is not None:
            return
        if self._in_title:
            self._title.append(data)
        elif self._preformatted is not None:
            self._preformatted.append(data)
        else:
            self._run.append(data)
            self._run_has_text = self._run_has_text or not data.isspace()

    def _list_marker(self) -> str:
        indent = "  " * min(max(len(self._lists) - 1, 0), _MAX_INDENT_LEVELS)
        number = self._lists[-1] if self._lists else None
        if number is None:
            return f"{indent}- "
        self._lists[-1] = number + 1
        return f"{indent}{number}. "

    def _close_link(self) -> None:
        if self._link is None:
            return
        target, start, had_text = self._link
        self._link = None
        label = " ".join("".join(self._run[start:]).replace(_LINE_BREAK, " ").split())
        if target is None:
            # A link within the page keeps its words; a bare permalink sign goes.
            if not any(char.isalnum() for char in label):
                del self._run[start:]
                self._run_has_text = had_text
            return
        del self._run[start:]
        if label:
            self._run.append(f"[{label}]({target})")

    def _end_block(self) -> None:
        """Close the block being read and keep it when it holds text.

        A list item's marker waits for the item's first text, which may sit in
        a paragraph of its own.
        """
        lines = (" ".join(line.split()) for line in "".join(self._run).split(_LINE_BREAK))
        text = "\n".join(line for line in lines if line)
        self._run.clear()
        self._run_has_text = False
        self._link = None
        if not text or text == "``":
            return
        self._add(self._marker + text, tight=self._tight)
        self._marker, self._tight = "", False

    def _add(self, text: str, *, tight: bool) -> None:
        self._blocks.append(_Block(text, tight, in_content_region=self._region is not None))
        self._block_chars += len(text)


def _nested(inside: tuple[str, int], tag: str, step: int) -> tuple[str, int] | None:
    """Follow nesting of the element being tracked; ``None`` once it has closed."""
    name, depth = inside
    if tag != name:
        return inside
    return (name, depth + step) if depth + step > 0 else None


def _link_target(page_url: str, href: str | None) -> str | None:
    """``href`` as an absolute web address, or ``None`` for a link that leads nowhere new."""
    if not href or href.startswith("#"):
        return None
    try:
        target = urljoin(page_url, href.strip())
        scheme = urlsplit(target).scheme
    except ValueError:
        return None
    if scheme not in _FOLLOWED_SCHEMES or len(target) > _MAX_LINK_CHARS:
        return None
    return target.replace(" ", "%20")


__all__ = ["READABLE_KINDS", "NotText", "PageText", "is_text_media_type", "readable_text"]
