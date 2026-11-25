"""Fetch one page inside fixed bounds: where it connects, how long, how much.

Each hop of a redirect chain is its own exchange. The hop's URL is judged, its
host is looked up once, and the request goes to an address that was judged:
the URL on the request names that address, while the ``Host`` header and the
TLS server name carry the host, so the certificate is still checked against
the name. A hop is one request on a transport of its own, with no HTTP client
around it: nothing keeps a cookie, follows a redirect, reads a ``Location``
header or adds credentials except the code in this module, and no proxy
setting of the environment applies.

One deadline covers the whole fetch. The client's own timeouts apply to a
single socket read, which a server that sends one byte at a time never
exceeds, so the fetch runs as a task that is cancelled when the deadline
passes.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import ssl
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from typing import Any

import httpx

from config.constants import SSL_CERT_DIR_ENV, SSL_CERT_FILE_ENV
from tools.system.web_fetch.egress import (
    EgressRefused,
    IPAddress,
    Resolver,
    WebTarget,
    parse_target,
    public_addresses,
    redirect_target,
    system_resolver,
)
from tools.system.web_fetch.page_text import READABLE_KINDS, is_text_media_type

MAX_REDIRECTS = 5
TOTAL_SECONDS = 20.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
#: One address that does not answer leaves time to try the host's next one.
_CONNECT_SECONDS = 5.0
_REQUEST_HEADERS = {
    "Accept": (
        "text/markdown, text/html;q=0.9, text/plain;q=0.8, application/json;q=0.8, "
        "application/xml;q=0.7, */*;q=0.1"
    ),
    # A compressed body would have to be inflated before its size is known.
    "Accept-Encoding": "identity",
    # Every hop has a connection of its own.
    "Connection": "close",
    "User-Agent": "OpenSRE-web-fetch/1.0",
}
# Limits on one socket operation. The deadline over the whole fetch is the
# bound that holds; these only let a dead address fail fast.
_SOCKET_TIMEOUTS = httpx.Timeout(TOTAL_SECONDS, connect=_CONNECT_SECONDS).as_dict()
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}")
_CHARSET = re.compile(r"charset\s*=\s*\"?([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_CAUSE_CHAIN_LIMIT = 8


class FetchFailed(Exception):
    """The page could not be fetched.

    ``str()`` is the whole reason, written here: it never repeats the URL or
    text the server sent, so it is safe to show and to record. ``kind`` is a
    stable category.
    """

    def __init__(self, reason: str, *, kind: str) -> None:
        self.kind = kind
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """A successful response, as far as it was read."""

    #: The address the body came from, after redirects.
    url: str
    media_type: str
    #: Character set the server declared, if any.
    charset: str | None
    body: bytes
    #: True when the body was longer than the size limit and was cut there.
    cut: bool
    redirects: int


def fetch_page(
    url: str,
    *,
    resolve: Resolver = system_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
    total_seconds: float = TOTAL_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> FetchedPage:
    """Fetch ``url``, following redirects by hand, or raise.

    Raises :class:`~tools.system.web_fetch.egress.EgressRefused` for a target
    that may not be fetched and :class:`FetchFailed` for everything else that
    stops the fetch. ``transport`` replaces the network.
    """
    target = parse_target(url)

    def fetch() -> Coroutine[Any, Any, FetchedPage]:
        return _within_deadline(
            target,
            resolve=resolve,
            transport=transport,
            total_seconds=total_seconds,
            max_bytes=max_bytes,
        )

    try:
        return _blocking(fetch)
    except (httpx.ConnectError, httpx.ConnectTimeout) as failed:
        if _certificate_rejected(failed):
            raise FetchFailed(
                "The site's TLS certificate could not be verified.", kind="tls"
            ) from failed
        raise FetchFailed("Could not connect to the host.", kind="connect") from failed
    except httpx.TimeoutException as failed:
        raise _timed_out(total_seconds) from failed
    except httpx.HTTPError as failed:
        raise FetchFailed(
            f"The connection failed before the page arrived ({type(failed).__name__}).",
            kind="transport",
        ) from failed


def _timed_out(total_seconds: float) -> FetchFailed:
    return FetchFailed(f"The page did not arrive within {total_seconds:g} seconds.", kind="timeout")


async def _within_deadline(
    target: WebTarget,
    *,
    resolve: Resolver,
    transport: httpx.AsyncBaseTransport | None,
    total_seconds: float,
    max_bytes: int,
) -> FetchedPage:
    """Fetch ``target`` under one deadline that covers every hop, lookup and read."""
    try:
        async with asyncio.timeout(total_seconds):
            return await _follow_redirects(
                target, resolve=resolve, transport=transport, max_bytes=max_bytes
            )
    except TimeoutError:
        raise _timed_out(total_seconds) from None


def _blocking(fetch: Callable[[], Coroutine[Any, Any, FetchedPage]]) -> FetchedPage:
    """Run ``fetch`` to its end from synchronous code, with or without a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fetch())
    # This thread's loop is busy running the caller; the fetch gets its own.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="web-fetch") as pool:
        return pool.submit(lambda: asyncio.run(fetch())).result()


async def _follow_redirects(
    target: WebTarget,
    *,
    resolve: Resolver,
    transport: httpx.AsyncBaseTransport | None,
    max_bytes: int,
) -> FetchedPage:
    for redirects in range(MAX_REDIRECTS + 1):
        addresses = await _judged_addresses(target, resolve, redirected=redirects > 0)
        answer = await _exchange(target, addresses, transport=transport, max_bytes=max_bytes)
        if isinstance(answer, _Page):
            return FetchedPage(
                url=target.url,
                media_type=answer.media_type,
                charset=answer.charset,
                body=answer.body,
                cut=answer.cut,
                redirects=redirects,
            )
        target = redirect_target(target, answer)
    raise FetchFailed(
        f"The page redirected more than {MAX_REDIRECTS} times.", kind="too_many_redirects"
    )


async def _judged_addresses(
    target: WebTarget, resolve: Resolver, *, redirected: bool
) -> tuple[IPAddress, ...]:
    """The public addresses of ``target``'s host, from one lookup."""
    try:
        return await _on_its_own_thread(lambda: public_addresses(target, resolve))
    except EgressRefused as refused:
        raise (refused.after_redirect() if redirected else refused) from None
    except OSError as failed:
        raise FetchFailed("The host name could not be resolved.", kind="dns") from failed


async def _on_its_own_thread[T](work: Callable[[], T]) -> T:
    """Await blocking ``work`` on a thread nothing waits for once the deadline passes.

    The system resolver has no timeout of its own. On the event loop's shared
    pool a lookup that hangs would hold the loop's shutdown, and with it the
    caller, long after the deadline.
    """
    loop = asyncio.get_running_loop()
    outcome: asyncio.Future[T] = loop.create_future()

    def run() -> None:
        settle: Callable[[], None]
        try:
            settle = partial(outcome.set_result, work())
        except Exception as raised:  # noqa: BLE001 - handed to the task that awaits the answer
            settle = partial(outcome.set_exception, raised)
        # The loop is closed when the fetch gave up first; nobody is waiting.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_unless_abandoned, outcome, settle)

    threading.Thread(target=run, name="web-fetch-lookup", daemon=True).start()
    return await outcome


def _unless_abandoned(outcome: asyncio.Future[Any], settle: Callable[[], None]) -> None:
    """Settle ``outcome`` unless the deadline already cancelled the wait for it."""
    if not outcome.done():
        settle()


@dataclass(frozen=True, slots=True)
class _Page:
    media_type: str
    charset: str | None
    body: bytes
    cut: bool


async def _exchange(
    target: WebTarget,
    addresses: tuple[IPAddress, ...],
    *,
    transport: httpx.AsyncBaseTransport | None,
    max_bytes: int,
) -> _Page | str:
    """One request and its response: the page, or the ``Location`` it redirects to."""
    async with transport or _direct_transport() as network:
        response = await _send(network, target, addresses)
        try:
            if response.has_redirect_location:
                return str(response.headers["location"])
            if not response.is_success:
                raise FetchFailed(
                    f"The server answered {_status(response.status_code)}.", kind="http_status"
                )
            media_type, charset = _declared_type(response)
            body, cut = await _capped_body(response, max_bytes)
        finally:
            await response.aclose()
    return _Page(media_type=media_type, charset=charset, body=body, cut=cut)


def _direct_transport() -> httpx.AsyncHTTPTransport:
    """A connection straight to the address on the request, never through a proxy.

    The TLS trust is ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` when set, else
    certifi. A network that inspects TLS needs its own CA, and a CA bundle is
    not a credential, so those two variables are the one thing a fetch reads
    from the environment.
    """
    try:
        trusted = httpx.create_ssl_context(trust_env=True)
    except OSError as failed:
        raise FetchFailed(
            f"The CA certificates named by {SSL_CERT_FILE_ENV} or {SSL_CERT_DIR_ENV} "
            "could not be loaded.",
            kind="tls",
        ) from failed
    return httpx.AsyncHTTPTransport(verify=trusted, trust_env=False, retries=0)


async def _send(
    network: httpx.AsyncBaseTransport, target: WebTarget, addresses: tuple[IPAddress, ...]
) -> httpx.Response:
    """Ask the first judged address that accepts a connection."""
    *earlier, last = addresses
    for address in earlier:
        try:
            return await network.handle_async_request(_request(target, address))
        except (httpx.ConnectError, httpx.ConnectTimeout) as failed:
            if _certificate_rejected(failed):
                raise
    return await network.handle_async_request(_request(target, last))


def _request(target: WebTarget, address: IPAddress) -> httpx.Request:
    """A GET of ``target`` at ``address``: the address is dialled, the host is named."""
    pinned = httpx.URL(
        scheme=target.scheme,
        host=str(address),
        port=target.port,
        raw_path=target.resource.encode("ascii"),
    )
    return httpx.Request(
        "GET",
        pinned,
        headers={**_REQUEST_HEADERS, "Host": target.authority},
        extensions={"sni_hostname": target.host, "timeout": _SOCKET_TIMEOUTS},
    )


def _declared_type(response: httpx.Response) -> tuple[str, str | None]:
    """The response's media type and charset, or raise when it is not text to read."""
    media_type, _, parameters = response.headers.get("content-type", "").partition(";")
    media_type = media_type.strip().lower()
    if not is_text_media_type(media_type):
        # The server chose this text: repeat it only when it has the shape of a media type.
        named = media_type if _MEDIA_TYPE.fullmatch(media_type) else "an unnamed kind of content"
        raise FetchFailed(
            f"The URL returned {named}, not text. web_fetch reads {READABLE_KINDS}.",
            kind="unsupported_content",
        )
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity"):
        raise FetchFailed(
            "The server compressed the page although an uncompressed one was requested.",
            kind="unsupported_content",
        )
    declared = _CHARSET.search(parameters)
    return media_type, declared.group(1) if declared else None


async def _capped_body(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """The body up to ``max_bytes``, and whether more was coming."""
    received = bytearray()
    async for chunk in response.aiter_raw():
        received += chunk
        if len(received) > max_bytes:
            return bytes(received[:max_bytes]), True
    return bytes(received), False


def _status(code: int) -> str:
    """``HTTP 404 Not Found``, with the standard phrase and never the server's own."""
    try:
        return f"HTTP {code} {HTTPStatus(code).phrase}"
    except ValueError:
        return f"HTTP {code}"


def _certificate_rejected(error: BaseException) -> bool:
    """Whether ``error`` came from a certificate that did not verify."""
    link: BaseException | None = error
    for _ in range(_CAUSE_CHAIN_LIMIT):
        if link is None:
            return False
        if isinstance(link, ssl.SSLCertVerificationError):
            return True
        link = link.__cause__ or link.__context__
    return False


__all__ = [
    "MAX_REDIRECTS",
    "MAX_RESPONSE_BYTES",
    "TOTAL_SECONDS",
    "FetchFailed",
    "FetchedPage",
    "fetch_page",
]
