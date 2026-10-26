"""Ban umbrella words in new first-party module and top-level class names.

Names like ``manager``, ``handler``, ``context``, or bare ``utils`` say
nothing about what a file or class actually does, so responsibilities pile
up under them with nothing pushing back. See docs/NAMING.md for the
vocabulary to use instead (State, Snapshot, Slice, Host, ...).

This guard is shrink-only: today's offenders are recorded in
tests/quality/umbrella_names_allowlist.txt so this test passes as of the
PR that introduced it. Do not add a new entry to silence a new offender —
rename it instead.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.shared.product_sources import product_python_files

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRODUCT_ROOTS = (
    "bootstrap",
    "config",
    "core",
    "gateway",
    "integrations",
    "infrastructure",
    "surfaces",
    "tools",
)
_UMBRELLA_WORDS = frozenset(
    {
        "port",
        "wiring",
        "manager",
        "handler",
        "processor",
        "engine",
        "wrapper",
        "coordinator",
        "context",
    }
)
_NETWORK_PORT_PREFIXES = frozenset(
    {
        "http",
        "https",
        "tcp",
        "udp",
        "grpc",
        "ws",
        "wss",
        "ssh",
        "ftp",
        "smtp",
        "dns",
        "ip",
        "ipv",
    }
)
_NETWORK_PORT_QUALIFIERS = frozenset({"listener", "server", "socket"})
_HEXAGONAL_PORT_BASE_NAMES = frozenset({"Protocol", "ABC"})
_BARE_UMBRELLA_WORDS = frozenset({"utils", "helpers", "common", "misc"})
_ALLOWLIST_PATH = Path(__file__).resolve().parent / "umbrella_names_allowlist.txt"
_CLASS_NAME_PART_REGEX = re.compile(r"[A-Z]+[0-9]*(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_VERSION_WORD_REGEX = re.compile(r"v?[0-9]+")


def _load_allowlist() -> set[tuple[str, str, str]]:
    allowlist: set[tuple[str, str, str]] = set()

    for line in _ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        path, kind, name = line.split("|", 2)
        allowlist.add((path, kind, name))
    return allowlist


def _is_test_path(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_")


def _without_trailing_digits(word: str) -> str:
    return word.rstrip("0123456789")


def _umbrella_word(word: str) -> str | None:
    normalized = _without_trailing_digits(word)
    if normalized in _UMBRELLA_WORDS:
        return normalized
    if normalized.endswith("s") and normalized[:-1] in _UMBRELLA_WORDS:
        return normalized[:-1]
    return None


def _bare_umbrella_word(words: list[str]) -> str | None:
    if len(words) == 2 and _VERSION_WORD_REGEX.fullmatch(words[1]) is None:
        return None
    if len(words) not in (1, 2):
        return None
    word = _without_trailing_digits(words[0])
    return word if word in _BARE_UMBRELLA_WORDS else None


def _is_network_prefix(word: str) -> bool:
    return _without_trailing_digits(word) in _NETWORK_PORT_PREFIXES


def _network_prefix_ends_at(words: list[str]) -> bool:
    if not words:
        return False
    if _is_network_prefix(words[-1]):
        return True
    return len(words) >= 2 and _is_network_prefix("".join(words[-2:]))


def _is_network_port(
    word: str,
    preceding_words: list[str],
    is_hexagonal_port: bool,
) -> bool:
    if word != "port" or is_hexagonal_port:
        return False
    if _network_prefix_ends_at(preceding_words):
        return True
    return bool(
        preceding_words
        and preceding_words[-1] in _NETWORK_PORT_QUALIFIERS
        and _network_prefix_ends_at(preceding_words[:-1])
    )


def _hexagonal_port_base_names(tree: ast.AST) -> frozenset[str]:
    names = set(_HEXAGONAL_PORT_BASE_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in (
            "typing",
            "typing_extensions",
            "abc",
        ):
            for alias in node.names:
                if alias.name in _HEXAGONAL_PORT_BASE_NAMES and alias.asname:
                    names.add(alias.asname)
    return frozenset(names)


def _is_hexagonal_port_class(node: ast.ClassDef, base_names: frozenset[str]) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Subscript):
            base = base.value
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if name in base_names:
            return True
    return False


def _split_module_name(name: str) -> list[str]:
    return [
        match.group(0).lower()
        for component in name.strip("_").split("_")
        for match in _CLASS_NAME_PART_REGEX.finditer(component)
    ]


def _split_class_name(name: str) -> list[str]:
    return [match.group(0).lower() for match in _CLASS_NAME_PART_REGEX.finditer(name.strip("_"))]


def _offending_word(words: list[str], is_hexagonal_port: bool = False) -> str | None:
    bare_word = _bare_umbrella_word(words)
    if bare_word is not None:
        return bare_word

    for index, word in enumerate(words):
        offending_word = _umbrella_word(word)
        if offending_word is not None and not _is_network_port(
            offending_word,
            words[:index],
            is_hexagonal_port,
        ):
            return offending_word
    return None


def _class_name_offenses(tree: ast.AST) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []

    hexagonal_port_base_names: frozenset[str] = _hexagonal_port_base_names(tree)

    class _Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            is_hexagonal_port = _is_hexagonal_port_class(node, hexagonal_port_base_names)
            offending_word = _offending_word(_split_class_name(node.name), is_hexagonal_port)

            if offending_word is not None:
                hits.append(("class", node.name))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            pass

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            pass

    _Visitor().visit(tree)
    return hits


def _package_directories(root: Path, files: list[Path]) -> set[Path]:
    dirs: set[Path] = set()
    for file in files:
        for rel_parent in file.relative_to(root).parents:
            if rel_parent == Path("."):
                continue
            dirs.add(rel_parent)
    return dirs


def _module_name_offenses(root: Path, files: list[Path]) -> set[tuple[str, str, str]]:
    hits: set[tuple[str, str, str]] = set()

    for package_dir in _package_directories(root, files):
        offending_word = _offending_word(_split_module_name(package_dir.name))
        if offending_word is not None:
            # Namespace packages have no __init__.py on disk, so append __init__.py
            # for reporting. Add it to the allowlist path too when allowlisting.
            report_path = (package_dir / "__init__.py").as_posix()
            hits.add((report_path, "module", package_dir.name))

    for file in files:
        offending_word = _offending_word(_split_module_name(file.stem))
        if offending_word is not None:
            relpath = file.relative_to(root).as_posix()
            hits.add((relpath, "module", file.stem))

    return hits


def _scan_offenders(root: Path) -> set[tuple[str, str, str]]:
    offenders: set[tuple[str, str, str]] = set()
    files: list[Path] = []

    for path in product_python_files(root):
        if _is_test_path(path):
            continue
        files.append(path)
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        relpath = path.relative_to(_REPO_ROOT).as_posix()
        for kind, name in _class_name_offenses(tree):
            offenders.add((relpath, kind, name))

    package = root.relative_to(_REPO_ROOT).as_posix()
    for rel_from_root, kind, name in _module_name_offenses(root, files):
        offenders.add((f"{package}/{rel_from_root}", kind, name))

    return offenders


@pytest.mark.parametrize("package", _PRODUCT_ROOTS)
def test_product_packages_have_no_umbrella_names(package: str) -> None:
    root = _REPO_ROOT / package
    if not root.is_dir():
        pytest.skip(f"{package}/ missing")

    offenders: list[str] = []
    allowlist: set[tuple[str, str, str]] = _load_allowlist()
    files: list[Path] = []
    for path in product_python_files(root):
        if _is_test_path(path):
            continue
        files.append(path)
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            offenders.append(f"{path.relative_to(_REPO_ROOT)}: syntax error: {exc}")
            continue

        relpath = path.relative_to(_REPO_ROOT).as_posix()
        for kind, name in _class_name_offenses(tree):
            if (relpath, kind, name) in allowlist:
                continue

            offenders.append(f"{relpath}:{kind}: {name}")

    for rel_from_root, kind, name in _module_name_offenses(root, files):
        relpath = f"{package}/{rel_from_root}"
        if (relpath, kind, name) in allowlist:
            continue

        offenders.append(f"{relpath}:{kind}: {name}")

    assert offenders == [], (
        "New umbrella name introduced (see docs/NAMING.md for the vocabulary to use "
        "instead). Rename it — do not add it to "
        "tests/quality/umbrella_names_allowlist.txt to silence this:\n" + "\n".join(offenders)
    )


def test_umbrella_allowlist_has_no_stale_entries() -> None:
    allowlist: set[tuple[str, str, str]] = _load_allowlist()

    offenders: set[tuple[str, str, str]] = set()
    for package in _PRODUCT_ROOTS:
        root = _REPO_ROOT / package
        if not root.is_dir():
            continue
        offenders |= _scan_offenders(root)

    stale_entries: set[tuple[str, str, str]] = allowlist - offenders
    assert stale_entries == set(), (
        "umbrella_names_allowlist.txt has entries that no longer match a real, "
        "current offender (already renamed, or never existed). Remove them:\n"
        + "\n".join(sorted(f"{path}|{kind}|{name}" for path, kind, name in stale_entries))
    )


def test_offending_package_dunder_init_uses_parent_dir_name(tmp_path: Path) -> None:
    offending_package_dir = tmp_path / "prompt_manager"
    offending_package_dir.mkdir()

    init_file = offending_package_dir / "__init__.py"
    init_file.write_text("")

    hits = _module_name_offenses(tmp_path, [init_file])

    assert ("prompt_manager/__init__.py", "module", "prompt_manager") in hits


def test_network_port_module_name_is_exempt() -> None:
    path = Path("infrastructure/network/http_port.py")
    hits = _module_name_offenses(path.parent, [path])

    assert hits == set()


def test_network_port_package_dunder_init_is_exempt(tmp_path: Path) -> None:
    package_dir = tmp_path / "http_port"
    package_dir.mkdir()
    init_file = package_dir / "__init__.py"
    init_file.write_text("")

    hits = _module_name_offenses(tmp_path, [init_file])

    assert hits == set()


def test_namespace_package_without_init_is_detected(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "prompt_manager"
    pkg_dir.mkdir()
    (pkg_dir / "service.py").write_text("")

    hits = _module_name_offenses(tmp_path, [pkg_dir / "service.py"])

    assert ("prompt_manager/__init__.py", "module", "prompt_manager") in hits


def test_nested_namespace_package_all_ancestor_levels_are_detected(tmp_path: Path) -> None:
    deep = tmp_path / "prompt_manager" / "sub"
    deep.mkdir(parents=True)
    (deep / "service.py").write_text("")

    hits = _module_name_offenses(tmp_path, [deep / "service.py"])

    assert any(name == "prompt_manager" for _, _, name in hits)


def test_namespace_package_non_offending_name_is_not_detected(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "grounding"
    pkg_dir.mkdir()
    (pkg_dir / "provider.py").write_text("")

    hits = _module_name_offenses(tmp_path, [pkg_dir / "provider.py"])

    assert hits == set()


def test_syntax_error_file_name_is_still_detected(tmp_path: Path) -> None:
    bad_file = tmp_path / "session_manager.py"
    bad_file.write_text("def broken(:\n")

    hits = _module_name_offenses(tmp_path, [bad_file])

    assert ("session_manager.py", "module", "session_manager") in hits


def test_hexagonal_port_still_flagged() -> None:
    path = Path("infrastructure/storage/storage_port.py")
    tree = ast.parse("class StoragePort:\n    pass\n")

    class_hits = _class_name_offenses(tree)
    module_hits = _module_name_offenses(path.parent, [path])

    assert class_hits == [("class", "StoragePort")]
    assert ("storage_port.py", "module", "storage_port") in module_hits


def test_network_port_class_name_is_exempt() -> None:
    tree = ast.parse("class HttpPort:\n    pass\n")
    hits = _class_name_offenses(tree)

    assert hits == []


def test_banned_word_suffixes_do_not_bypass_guard() -> None:
    for class_name in (
        "RequestManager2",
        "RequestManagers",
        "StoragePorts",
        "Utils2",
        "UtilsV2",
    ):
        tree = ast.parse(f"class {class_name}:\n    pass\n")
        assert _class_name_offenses(tree) == [("class", class_name)]

    for module_name in (
        "request_manager2.py",
        "request_managers.py",
        "storage_ports.py",
        "utils_2.py",
        "utils_v2.py",
    ):
        path = Path("core") / module_name
        assert (module_name, "module", path.stem) in _module_name_offenses(path.parent, [path])

    assert _class_name_offenses(ast.parse("class CommonConfig2:\n    pass\n")) == []


def test_camel_case_module_name_does_not_bypass_guard() -> None:
    path = Path("core/PromptManager.py")

    assert ("PromptManager.py", "module", "PromptManager") in _module_name_offenses(
        path.parent, [path]
    )


def test_versioned_network_port_names_are_exempt() -> None:
    for class_name, module_name in (
        ("HTTP2Port", "http2_port.py"),
        ("HTTP2Ports", "http2_ports.py"),
        ("IPv6Port", "ipv6_port.py"),
    ):
        tree = ast.parse(f"class {class_name}:\n    pass\n")
        path = Path("infrastructure/network") / module_name

        assert _class_name_offenses(tree) == []
        assert _module_name_offenses(path.parent, [path]) == set()


def test_compound_network_port_names_are_exempt() -> None:
    tree = ast.parse("class HTTPServerPort:\n    pass\n")
    path = Path("infrastructure/network/http_server_port.py")

    assert _class_name_offenses(tree) == []
    assert _module_name_offenses(path.parent, [path]) == set()


def test_domain_word_between_network_prefix_and_port_is_not_exempt() -> None:
    tree = ast.parse("class HttpServicePort:\n    pass\n")
    path = Path("infrastructure/service/http_service_port.py")

    assert _class_name_offenses(tree) == [("class", "HttpServicePort")]
    assert ("http_service_port.py", "module", "http_service_port") in _module_name_offenses(
        path.parent, [path]
    )


def test_versioned_network_port_protocol_is_detected() -> None:
    tree = ast.parse("class HTTP2Ports(Protocol):\n    pass\n")

    assert _class_name_offenses(tree) == [("class", "HTTP2Ports")]


def test_class_inside_if_block_is_detected() -> None:
    tree = ast.parse("if True:\n    class RequestManager:\n        pass\n")
    hits = _class_name_offenses(tree)

    assert ("class", "RequestManager") in hits


def test_class_inside_module_level_except_importerror_is_detected() -> None:
    tree = ast.parse(
        "try:\n"
        "    from botocore.exceptions import ClientError\n"
        "except ImportError:\n"
        "    class FallbackHandler(Exception):\n"
        "        pass\n"
    )
    hits = _class_name_offenses(tree)

    assert ("class", "FallbackHandler") in hits


def test_class_inside_for_loop_is_detected() -> None:
    tree = ast.parse("for _ in range(1):\n    class BazEngine:\n        pass\n")
    hits = _class_name_offenses(tree)

    assert ("class", "BazEngine") in hits


def test_class_inside_with_block_is_detected() -> None:
    tree = ast.parse("with suppress(Exception):\n    class QueueCoordinator:\n        pass\n")
    hits = _class_name_offenses(tree)

    assert ("class", "QueueCoordinator") in hits


def test_class_inside_function_is_not_detected() -> None:
    tree = ast.parse("def build():\n    class TempManager:\n        pass\n    return TempManager\n")
    hits = _class_name_offenses(tree)

    assert hits == []


def test_nested_class_inside_classdef_is_not_detected() -> None:
    tree = ast.parse("class Outer:\n    class InnerManager:\n        pass\n")
    hits = _class_name_offenses(tree)

    assert hits == []


def test_protocol_http_port_acronym_class_is_detected() -> None:
    tree = ast.parse("class HTTPPort(Protocol):\n    pass\n")
    hits = _class_name_offenses(tree)

    assert ("class", "HTTPPort") in hits


def test_http_port_acronym_class_name_is_exempt() -> None:
    tree = ast.parse("class HTTPPort:\n    pass\n")
    hits = _class_name_offenses(tree)

    assert hits == []


def test_tcp_port_acronym_class_name_is_exempt() -> None:
    tree = ast.parse("class TCPPort:\n    pass\n")
    hits = _class_name_offenses(tree)

    assert hits == []


def test_split_class_name_keeps_acronym_with_digit_together() -> None:
    assert _split_class_name("S3Client") == ["s3", "client"]


def test_abc_http_port_class_is_detected() -> None:
    tree = ast.parse("class HttpPort(ABC):\n    pass\n")
    hits = _class_name_offenses(tree)

    assert ("class", "HttpPort") in hits


def test_protocol_as_second_base_class_is_still_detected() -> None:
    tree = ast.parse("class HttpPort(ExecutionGate, Protocol):\n    pass\n")
    hits = _class_name_offenses(tree)

    assert ("class", "HttpPort") in hits


def test_aliased_protocol_import_is_still_detected() -> None:
    tree = ast.parse(
        "from typing import Protocol as _Protocol\n"
        "from typing_extensions import Protocol as _ExtensionsProtocol\n"
        "\n"
        "\n"
        "class HttpPort(_Protocol):\n"
        "    pass\n"
        "\n"
        "\n"
        "class HTTP2Port(_ExtensionsProtocol):\n"
        "    pass\n"
    )
    hits = _class_name_offenses(tree)

    assert ("class", "HttpPort") in hits
    assert ("class", "HTTP2Port") in hits


def test_generic_protocol_port_class_is_detected() -> None:
    tree = ast.parse(
        "from typing import Protocol, TypeVar\n"
        "T = TypeVar('T')\n"
        "\n"
        "\n"
        "class HttpPort(Protocol[T]):\n"
        "    pass\n"
    )
    hits = _class_name_offenses(tree)
    assert ("class", "HttpPort") in hits
