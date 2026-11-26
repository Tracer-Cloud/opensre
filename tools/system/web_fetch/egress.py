"""Where a fetch may go: plain web URLs on the public internet, nothing else.

The agent often runs inside production infrastructure, where an address that
is not public is a database, a control plane, or a cloud metadata endpoint
that hands out credentials. A URL is therefore judged twice: its shape before
any lookup, and every address its host resolves to before any connection.
The caller connects to an address returned here and never looks the name up
again, so a second DNS answer cannot differ from the one that was judged.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from urllib.parse import quote, urljoin, urlsplit

IPAddress = IPv4Address | IPv6Address
#: Looks a host name up and returns its addresses as text, in the order to try.
Resolver = Callable[[str, int], Sequence[str]]

_SCHEME_PORTS = {"http": 80, "https": 443}
_MAX_URL_CHARS = 4096
_MAX_HOST_CHARS = 253
_HOST_LABEL = re.compile(r"(?!-)[a-z0-9_-]{1,63}(?<!-)")
# RFC 3986 characters that keep their meaning; everything else is percent-encoded.
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + "?"

_NOT_HTTP = "only http:// and https:// URLs can be fetched."
_HAS_CREDENTIALS = "it carries a username or password."
_MALFORMED = "it is not a valid web address."

_LOOPBACK = "a loopback address"
_PRIVATE = "a private network address"
_LINK_LOCAL = "a link-local address (the range cloud metadata endpoints use)"
_SHARED = "a carrier-grade NAT address"
_MULTICAST = "a multicast address"
_UNSPECIFIED = "an unspecified address"
_RESERVED = "a reserved address"
_IPV4_MAPPED = "an IPv4-mapped IPv6 address"

# The IANA special-purpose registries, spelled out so the answer does not
# change with the interpreter's own table.
_IPV4_REFUSED: tuple[tuple[IPv4Network, str], ...] = (
    (IPv4Network("0.0.0.0/8"), _UNSPECIFIED),
    (IPv4Network("10.0.0.0/8"), _PRIVATE),
    (IPv4Network("100.64.0.0/10"), _SHARED),
    (IPv4Network("127.0.0.0/8"), _LOOPBACK),
    (IPv4Network("169.254.0.0/16"), _LINK_LOCAL),
    (IPv4Network("172.16.0.0/12"), _PRIVATE),
    (IPv4Network("192.0.0.0/24"), _RESERVED),
    (IPv4Network("192.0.2.0/24"), _RESERVED),
    (IPv4Network("192.88.99.0/24"), _RESERVED),
    (IPv4Network("192.168.0.0/16"), _PRIVATE),
    (IPv4Network("198.18.0.0/15"), _RESERVED),
    (IPv4Network("198.51.100.0/24"), _RESERVED),
    (IPv4Network("203.0.113.0/24"), _RESERVED),
    (IPv4Network("224.0.0.0/4"), _MULTICAST),
    (IPv4Network("240.0.0.0/4"), _RESERVED),
)
_IPV6_REFUSED: tuple[tuple[IPv6Network, str], ...] = (
    (IPv6Network("::/128"), _UNSPECIFIED),
    (IPv6Network("::1/128"), _LOOPBACK),
    (IPv6Network("fc00::/7"), _PRIVATE),
    (IPv6Network("fe80::/10"), _LINK_LOCAL),
    (IPv6Network("fec0::/10"), _PRIVATE),
    (IPv6Network("ff00::/8"), _MULTICAST),
    # Inside the global unicast block but not routable as a public host:
    # protocol assignments (Teredo among them), 6to4 and documentation.
    (IPv6Network("2001::/23"), _RESERVED),
    (IPv6Network("2001:db8::/32"), _RESERVED),
    (IPv6Network("2002::/16"), _RESERVED),
    (IPv6Network("3fff::/20"), _RESERVED),
)
# Every public IPv6 host is in this block; the rest of the space is reserved.
_IPV6_GLOBAL_UNICAST = IPv6Network("2000::/3")
# An IPv6-only network reaches IPv4 hosts through this prefix, with the IPv4
# address in the last 32 bits. The IPv4 address is the real destination.
_NAT64 = IPv6Network("64:ff9b::/96")
_IPV4_BITS = 0xFFFFFFFF


class EgressRefused(Exception):
    """A URL that will not be fetched.

    ``str()`` is the whole reason. It names a rule and a kind of address, never
    the URL, the host or the address, so it is safe to show and to record.
    """

    def __init__(self, reason: str, *, redirected: bool = False) -> None:
        self.reason = reason
        self.redirected = redirected
        opening = (
            "The page redirected to a URL that web_fetch refused: "
            if redirected
            else "web_fetch refused this URL: "
        )
        super().__init__(opening + reason)

    def after_redirect(self) -> EgressRefused:
        """The same refusal, told as the redirect it stopped."""
        return EgressRefused(self.reason, redirected=True)


@dataclass(frozen=True, slots=True)
class WebTarget:
    """One request target, parsed once.

    Every later step uses these parts and never re-reads the URL text, so the
    host that was judged is the host that is asked.
    """

    scheme: str
    #: Lower-case ASCII host name, or an IP literal without brackets.
    host: str
    port: int
    #: Path and query as sent on the request line, percent-encoded ASCII.
    resource: str

    @property
    def authority(self) -> str:
        """``host[:port]`` as the ``Host`` header carries it."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == _SCHEME_PORTS[self.scheme] else f"{host}:{self.port}"

    @property
    def url(self) -> str:
        """The target as a URL, without credentials or fragment."""
        return f"{self.scheme}://{self.authority}{self.resource}"


def parse_target(url: str) -> WebTarget:
    """Read ``url`` as an http(s) request target, or raise :class:`EgressRefused`."""
    text = url.strip()
    if not text or len(text) > _MAX_URL_CHARS or not _plain(text):
        raise EgressRefused(_MALFORMED)
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise EgressRefused(_MALFORMED) from None
    scheme = parts.scheme.lower()
    if scheme not in _SCHEME_PORTS:
        raise EgressRefused(_NOT_HTTP)
    if "@" in parts.netloc:
        raise EgressRefused(_HAS_CREDENTIALS)
    if port == 0:
        raise EgressRefused(_MALFORMED)
    resource = quote(parts.path or "/", safe=_PATH_SAFE)
    if parts.query:
        resource += "?" + quote(parts.query, safe=_QUERY_SAFE)
    return WebTarget(
        scheme=scheme,
        host=_host(parts.hostname or ""),
        port=port or _SCHEME_PORTS[scheme],
        resource=resource,
    )


def redirect_target(current: WebTarget, location: str) -> WebTarget:
    """The target a ``Location`` header names, judged like a URL typed by hand."""
    # Servers do send unencoded spaces. Anything else that is not plain is
    # refused here, because joining would silently drop some of it.
    reference = location.strip().replace(" ", "%20")
    if not _plain(reference):
        raise EgressRefused(_MALFORMED, redirected=True)
    try:
        joined = urljoin(current.url, reference)
    except ValueError:
        raise EgressRefused(_MALFORMED, redirected=True) from None
    try:
        return parse_target(joined)
    except EgressRefused as refused:
        raise refused.after_redirect() from None


def public_addresses(target: WebTarget, resolve: Resolver) -> tuple[IPAddress, ...]:
    """Every address of ``target``'s host, or raise when any of them is not public.

    A name that answers with one public and one internal address is refused
    whole: the internal one is what a retry would reach. Raises ``OSError``
    when the name does not resolve.
    """
    literal = _ip_literal(target.host)
    if literal is not None:
        answers: tuple[IPAddress, ...] = (literal,)
    else:
        answers = tuple(dict.fromkeys(_answer(text) for text in resolve(target.host, target.port)))
    if not answers:
        raise OSError("the host name has no addresses")
    for address in answers:
        kind = address_refusal(address)
        if kind is not None:
            raise EgressRefused(
                f"its host resolves to {kind}, and only public internet addresses are fetched."
            )
    return answers


def address_refusal(address: IPAddress) -> str | None:
    """What kind of non-public address ``address`` is, or ``None`` when it is public."""
    if isinstance(address, IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return address_refusal(mapped) or _IPV4_MAPPED
        if address in _NAT64:
            return address_refusal(IPv4Address(int(address) & _IPV4_BITS))
        for v6_network, kind in _IPV6_REFUSED:
            if address in v6_network:
                return kind
        if address not in _IPV6_GLOBAL_UNICAST or not address.is_global:
            return _RESERVED
        return None
    for v4_network, kind in _IPV4_REFUSED:
        if address in v4_network:
            return kind
    return None if address.is_global else _RESERVED


def system_resolver(host: str, port: int) -> list[str]:
    """Addresses the operating system's resolver gives for ``host``, in its order."""
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return [str(answer[4][0]) for answer in answers]


def _plain(text: str) -> bool:
    """Whether ``text`` is free of what URL parsers read differently or drop.

    Whitespace and control characters are removed by some parsers and kept by
    others, and a backslash is a path separator to a browser and not to this one.
    """
    return "\\" not in text and all(char.isprintable() and not char.isspace() for char in text)


def _host(raw: str) -> str:
    """``raw`` as a lower-case ASCII host name or an IP literal, or raise."""
    host = raw.rstrip(".").lower()
    if _ip_literal(host) is not None:
        return host
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise EgressRefused(_MALFORMED) from None
    labels = host.split(".")
    if not host or len(host) > _MAX_HOST_CHARS or not all(map(_HOST_LABEL.fullmatch, labels)):
        raise EgressRefused(_MALFORMED)
    # A numeric last label is an IPv4 address to every resolver that accepts
    # short, octal or hexadecimal forms (127.1, 0177.0.0.1, 0x7f000001). Only
    # the dotted form judged above is taken as an address.
    if labels[-1].isdigit() or labels[-1].startswith("0x"):
        raise EgressRefused(_MALFORMED)
    return host


def _ip_literal(host: str) -> IPAddress | None:
    try:
        return ip_address(host)
    except ValueError:
        return None


def _answer(text: str) -> IPAddress:
    """One resolver answer as an address; an answer that cannot be read is refused."""
    try:
        return ip_address(text)
    except ValueError:
        raise EgressRefused(
            "its host resolves to an address that could not be read, and only public "
            "internet addresses are fetched."
        ) from None


__all__ = [
    "EgressRefused",
    "IPAddress",
    "Resolver",
    "WebTarget",
    "address_refusal",
    "parse_target",
    "public_addresses",
    "redirect_target",
    "system_resolver",
]
