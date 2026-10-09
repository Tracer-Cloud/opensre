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


@pytest.mark.parametrize("command", ["/memory", "/memory list"])
def test_memory_lists_stored_memories(command: str) -> None:
    _seed()
    console, buf = _capture()
    assert dispatch_slash(command, Session(), console) is True
    output = buf.getvalue()
    assert "prod-cluster" in output
    assert "Prod cluster is eks-prod-1" in output
    assert "/memory forget" in output
    assert "/memory path" in output
    assert "stored unencrypted in" not in output


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


@pytest.mark.parametrize("width", [40, 160])
def test_memory_list_bounds_description_but_show_preserves_it(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("COLUMNS", str(width))
    description = "[literal] " + "日本 evidence " * 10 + "FULL DESCRIPTION END"
    save_memory(
        slug="long-description", memory_type="preference", description=description, body="Body"
    )
    output = io.StringIO()
    console = Console(file=output, width=width)
    dispatch_slash("/memory list", Session(), console)
    listing = output.getvalue()
    assert "[literal]" in listing and "…" in listing
    assert "FULL DESCRIPTION END" not in listing
    preview = listing[listing.index("[literal]") : listing.index("…") + 1]
    from rich.cells import cell_len

    assert cell_len(preview.replace("\n", "")) <= 80
    output.seek(0)
    output.truncate()
    dispatch_slash("/memory show long-description", Session(), console)
    assert description in " ".join(output.getvalue().split())


@pytest.mark.parametrize("width", [40, 160])
def test_memory_description_spacing_and_muted_style(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    from dataclasses import replace

    from infrastructure.terminal.theme import SECONDARY
    from surfaces.interactive_shell.ui import memory
    from surfaces.shared.terminal.tables.descriptions import description_details

    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("COLUMNS", str(width))
    _seed()
    record = list_memories()[0]
    output = io.StringIO()
    console = Console(file=output, width=width)
    memory.render_memories(console, [record])
    lines = output.getvalue().splitlines()
    description_index = next(i for i, line in enumerate(lines) if "Prod cluster" in line)
    assert not lines[description_index - 1].strip()
    assert str(description_details(record.description)[1].style) == str(SECONDARY)

    output.seek(0)
    output.truncate()
    memory.render_memories(console, [replace(record, description="  "), record])
    lines = output.getvalue().splitlines()
    rows = [i for i, line in enumerate(lines) if line.startswith(record.slug)]
    previous_metadata_end = rows[0] + (2 if width == 40 else 0)
    assert rows[1] - previous_metadata_end == 2
