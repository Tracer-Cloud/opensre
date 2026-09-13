"""Deterministic code and skill-data inputs for release artifacts."""

from __future__ import annotations

from pathlib import Path

_RUNTIME_PACKAGE_NAMES = (
    "integrations",
    # Lazy-loaded via ``COMMAND_SPECS`` / ``importlib`` — Analysis cannot see
    # these edges, so the frozen binary must list every command module here
    # (including hidden ``_package-smoke`` for release artifact checks).
    "surfaces.cli.commands",
    "surfaces.interactive_shell",
    "tools",
)
_ACTION_SKILLS_DIR = Path("core/agent_harness/prompts/skills")
_SKILL_DATA_ROOTS = (Path("integrations"), Path("tools"))
#: Data files read at runtime that are not skill documents. Without an entry
#: here a file can be absent from both the wheel and the frozen binary.
_RUNTIME_DATA_FILES = (Path("integrations/yandex_cloud/api_index.json"),)
_RUNTIME_DISCOVERY_EXCLUSIONS = frozenset({"registry.py"})
#: Non-Python trees under ``infrastructure/`` that never run from the frozen
#: binary (e.g. a Cloudflare Worker deployed separately via ``wrangler``).
#: Bundling them only adds dead weight to the release artifact.
_INFRASTRUCTURE_DATA_EXCLUSIONS = (Path("infrastructure/deployment/cloudflare_install_proxy"),)


def _module_name(repo_root: Path, source_path: Path) -> str:
    relative = source_path.relative_to(repo_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def runtime_hidden_imports(repo_root: Path) -> tuple[str, ...]:
    """Return every module under packages discovered dynamically at runtime.

    PyInstaller's ``collect-submodules`` imports packages while enumerating
    them. That can silently omit OpenSRE packages when the stdlib ``platform``
    module wins over the first-party package during analysis. Walking source
    paths avoids executing application imports while producing the same module
    inventory for PyInstaller's ``hiddenimports`` input.
    """
    modules: set[str] = set()
    for package_name in _RUNTIME_PACKAGE_NAMES:
        package_root = repo_root / Path(*package_name.split("."))
        for source_path in package_root.rglob("*.py"):
            if (
                "__pycache__" in source_path.parts
                or _RUNTIME_DISCOVERY_EXCLUSIONS.intersection(source_path.parts)
                or source_path.stem.endswith("_test")
            ):
                continue
            module_name = _module_name(repo_root, source_path)
            if module_name:
                modules.add(module_name)
    return tuple(sorted(modules))


def required_skill_files(repo_root: Path) -> tuple[Path, ...]:
    """Return built-in action skills, workflow guidance, and tool data files."""
    files = set((repo_root / _ACTION_SKILLS_DIR).rglob("*.md"))
    files.update((repo_root / _ACTION_SKILLS_DIR).glob("**/scripts/*.py"))
    for relative_root in _SKILL_DATA_ROOTS:
        files.update((repo_root / relative_root).rglob("SKILL.md"))
    files.update(repo_root / relative_path for relative_path in _RUNTIME_DATA_FILES)
    return tuple(sorted(path for path in files if path.is_file()))


def skill_data_entries(repo_root: Path) -> tuple[tuple[str, str], ...]:
    """Return PyInstaller ``datas`` entries preserving repo-relative paths."""
    return tuple(
        (str(path), str(path.parent.relative_to(repo_root)))
        for path in required_skill_files(repo_root)
    )


def infrastructure_data_entries(repo_root: Path) -> tuple[tuple[str, str], ...]:
    """Return PyInstaller ``datas`` entries for ``infrastructure/``, per-file.

    Walking file-by-file (rather than handing PyInstaller the whole directory)
    lets us drop ``_INFRASTRUCTURE_DATA_EXCLUSIONS`` trees that hold no code the
    frozen binary ever imports or executes.
    """
    excluded_roots = tuple(repo_root / excluded for excluded in _INFRASTRUCTURE_DATA_EXCLUSIONS)
    infrastructure_root = repo_root / "infrastructure"
    entries: list[tuple[str, str]] = []
    for path in sorted(infrastructure_root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if any(excluded == path or excluded in path.parents for excluded in excluded_roots):
            continue
        entries.append((str(path), str(path.parent.relative_to(repo_root))))
    return tuple(entries)


__all__ = [
    "infrastructure_data_entries",
    "required_skill_files",
    "runtime_hidden_imports",
    "skill_data_entries",
]
