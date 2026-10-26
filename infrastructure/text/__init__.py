"""Small text/value helpers (truncate, coerce, URL checks, Markdown)."""

from infrastructure.text.coercion import safe_int
from infrastructure.text.data_blob import is_data_blob, looks_like_data_blob
from infrastructure.text.markdown import tighten_markdown_emphasis
from infrastructure.text.truncation import truncate
from infrastructure.text.url_validation import is_loopback_host, validate_https_or_loopback_http_url

__all__ = [
    "is_data_blob",
    "is_loopback_host",
    "looks_like_data_blob",
    "safe_int",
    "tighten_markdown_emphasis",
    "truncate",
    "validate_https_or_loopback_http_url",
]
