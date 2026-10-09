"""Tests for the /memory slash commands."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
from core.domain.memory import list_memories, save_memory
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.session import Session


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _seed(slug: str = "prod-cluster") -> None:
    save_memory(
        slug=slug,
        memory_type="infrastructure",
        description="Prod cluster is eks-prod-1",
        body="Full details about the prod cluster.",
    )


def test_memory_lists_stored_memories() -> None:
    _seed()
    console, buf = _capture()
    assert dispatch_slash("/memory", Session(), console) is True
    output = buf.getvalue()
    assert "prod-cluster" in output
    assert "Prod cluster is eks-prod-1" in output
    assert "/memory forget" in output


def test_memory_empty_state_message() -> None:
    console, buf = _capture()
    assert dispatch_slash("/memory", Session(), console) is True
    assert "no memories stored yet" in buf.getvalue()


def test_memory_show_prints_full_body() -> None:
    _seed()
    console, buf = _capture()
    assert dispatch_slash("/memory show prod-cluster", Session(), console) is True
    output = buf.getvalue()
    assert "Full details about the prod cluster." in output


def test_memory_show_unknown_name() -> None:
    console, buf = _capture()
    assert dispatch_slash("/memory show nope", Session(), console) is True
    assert "no memory named" in buf.getvalue()


def test_memory_forget_deletes() -> None:
    _seed()
    console, buf = _capture()
    assert dispatch_slash("/memory forget prod-cluster", Session(), console) is True
    assert "forgot" in buf.getvalue()
    assert list_memories() == []


def test_memory_forget_missing() -> None:
    console, buf = _capture()
    assert dispatch_slash("/memory forget nope", Session(), console) is True
    assert "no memory named" in buf.getvalue()


def test_memory_path_prints_directory(tmp_path: Path) -> None:
    console, buf = _capture()
    assert dispatch_slash("/memory path", Session(), console) is True
    assert str(tmp_path / "memory") in buf.getvalue().replace("\n", "")


def test_memory_unknown_subcommand_usage() -> None:
    console, buf = _capture()
    assert dispatch_slash("/memory bogus", Session(), console) is True
    assert "usage:" in buf.getvalue()


def test_memory_disabled_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_DISABLED_ENV, "1")
    console, buf = _capture()
    assert dispatch_slash("/memory", Session(), console) is True
    assert "memory is disabled" in buf.getvalue()


@pytest.mark.parametrize("width", [40, 80, 160])
def test_memory_list_preserves_literal_description_and_name_when_narrow(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    from rich.cells import cell_len

    monkeypatch.setenv("COLUMNS", str(width))
    monkeypatch.setenv("TERM", "xterm")
    slug = "production-checkout-regional-deployment-policy"
    save_memory(
        slug=slug,
        memory_type="investigation_learning",
        description="[literal] 日本 checkout requires verified rollback evidence.",
        body="PRIVATE BODY NOT IN LIST",
    )
    buf = io.StringIO()
    dispatch_slash("/memory", Session(), Console(file=buf, width=width))
    text = buf.getvalue()
    name_lines = text.splitlines()
    if width >= 72:
        header = next(line for line in name_lines if "Name" in line and "Type" in line)
        name_lines = [line[: header.index("Type")] for line in name_lines]
    assert slug in "".join("".join(name_lines).split())
    assert "[literal] 日本 checkout requires verified rollback evidence." in " ".join(text.split())
    assert "PRIVATE BODY NOT IN LIST" not in text
    assert "/memory show <name>" in text
    assert "stored unencrypted" in text
    assert max(cell_len(line) for line in text.splitlines()) <= width
    if width == 40:
        assert "Type:" in text and "Updated:" in text
