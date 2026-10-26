"""HTTP transport constants shared by every app OpenSRE serves."""

#: Cap on request body size accepted from any caller (authed or not) on every
#: mutating route. Realistic alert payloads top out around 50 KB, so 1 MiB is
#: ~20x headroom.
MAX_REQUEST_BODY_BYTES = 1 * 1024 * 1024

__all__ = ["MAX_REQUEST_BODY_BYTES"]
