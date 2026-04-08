from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_TOKENS = ("tests/test_case_", "test_case=test_case_", "test_orchestrator")


def _scan_paths() -> list[Path]:
    paths: list[Path] = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "Makefile",
        REPO_ROOT / "tests" / "README.md",
    ]
    paths.extend((REPO_ROOT / ".github" / "workflows").rglob("*.yml"))
    paths.extend((REPO_ROOT / "tests" / "e2e").rglob("*.py"))
    paths.extend((REPO_ROOT / "tests" / "synthetic").rglob("*.py"))
    return paths


def test_no_legacy_test_catalog_names() -> None:
    offenders: list[str] = []
    missing_paths: list[str] = []

    for path in _scan_paths():
        if not path.exists():
            missing_paths.append(str(path.relative_to(REPO_ROOT)))
            continue

        text = path.read_text(encoding="utf-8")
        for token in LEGACY_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} contains '{token}'")

    assert missing_paths == []
    assert offenders == []
