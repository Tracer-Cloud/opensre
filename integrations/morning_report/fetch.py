"""Integration-owned weather and headline fetches for scheduled morning reports."""

from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import quote
from xml.etree import ElementTree

_WEATHER_FORMAT = "%l:+%c+%t,+feels+%f,+%h+humidity,+wind+%w"
_HEADLINES_URL = "https://feeds.bbci.co.uk/news/rss.xml"
_TIMEOUT_SECONDS = 10
_USER_AGENT = "OpenSRE-scheduled-morning-report/1.0"
_MAX_HEADLINES = 8
_MAX_RESPONSE_BYTES = 256 * 1024


def format_fetched_briefing_inputs(inputs: dict[str, str] | None) -> str:
    """Return weather + headlines the unattended composer must use as-is."""
    city = str((inputs or {}).get("city") or "").strip()
    weather = fetch_weather(city)
    headlines = fetch_headlines()
    lines = "\n".join(f"- {title}" for title in headlines)
    return (
        "Pre-fetched live data for this unattended tick.\n"
        "Compose the briefing from this data only. Do not invent weather or headlines.\n"
        f"Weather: {weather}\n"
        f"Headlines:\n{lines}\n"
    )


def fetch_weather(city: str = "") -> str:
    """Return one wttr.in summary line for ``city``, or the default location."""
    path = quote(city, safe="") if city else ""
    url = f"https://wttr.in/{path}?format={_WEATHER_FORMAT}"
    text = _get(url).strip()
    if not text:
        raise RuntimeError("Morning-report weather fetch returned an empty body.")
    return text


def fetch_headlines() -> list[str]:
    """Return up to eight BBC RSS item titles (channel title excluded)."""
    xml = _get(_HEADLINES_URL)
    lowered = xml.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise RuntimeError("Morning-report headlines XML contains a forbidden DTD or entity.")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise RuntimeError("Morning-report headlines fetch returned invalid XML.") from exc
    titles: list[str] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        titles.append(title)
        if len(titles) >= _MAX_HEADLINES:
            break
    if not titles:
        raise RuntimeError("Morning-report headlines fetch returned no titles.")
    return titles


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise RuntimeError(f"Morning-report fetch exceeded {_MAX_RESPONSE_BYTES} bytes.")
            return bytes(payload).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Morning-report fetch failed for {url}: {type(exc).__name__}") from exc
