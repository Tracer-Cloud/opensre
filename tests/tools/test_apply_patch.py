"""Editing the working directory with one patch.

``apply_patch`` writes files on the model's word, so these pin what must not
give: a patch lands whole or not at all, never outside the working directory
or inside a credential file, and never before the user's ``/auto`` level and a
plan-only request have had their say.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from config.constants.repl_autonomy import FILE_EDIT_TOOL_TYPE, AutoLevel
from core.agent_harness.tools import ActionToolScope
from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY
from core.llm.types import ToolCall
from core.tool import ToolExecutionResult
from core.tool.execution import execute_tool_calls
from surfaces.cli.ask.approval import approval_required
from tools.interactive_shell.shared import (
    ExecutionPolicyResult,
    allow_tool,
    apply_auto_level,
    apply_plan_only_gate,
)
from tools.registry import clear_tool_registry_cache, get_registered_tool
from tools.system.workspace_files import patching
from tools.system.workspace_files.patch_tool import apply_patch


@pytest.fixture(autouse=True)
def _work_where_the_files_are(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool only writes inside the working directory; point it at the fixture tree."""
    monkeypatch.chdir(tmp_path)


def _write(root: Path, name: str, text: str) -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


def _patch(*sections: str) -> str:
    return "\n".join(("*** Begin Patch", *sections, "*** End Patch"))


def _applied(result: Any) -> str:
    assert isinstance(result, ToolExecutionResult), result
    assert isinstance(result.content, str)
    return result.content


def _refusal(result: Any) -> str:
    assert isinstance(result, dict), result
    assert result["ok"] is False
    return str(result["error"])


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --- what a patch does ------------------------------------------------------


def test_one_patch_adds_edits_renames_and_deletes(tmp_path: Path) -> None:
    # Arrange
    _write(tmp_path, "src/app.py", 'def greet():\n    print("Hi")\n')
    _write(tmp_path, "obsolete.txt", "old\n")

    # Act
    result = apply_patch(
        input=_patch(
            "*** Add File: docs/notes.md",
            "+# Notes",
            "+",
            "+body",
            "*** Update File: src/app.py",
            "*** Move to: src/main.py",
            "@@ def greet():",
            '-    print("Hi")',
            '+    print("Hello")',
            "*** Delete File: obsolete.txt",
        )
    )

    # Assert: the model reads the summary it is trained on; the disk matches it.
    assert _applied(result) == (
        "Success. Updated the following files:\n"
        "A docs/notes.md\nD src/app.py\nA src/main.py\nD obsolete.txt"
    )
    assert _tree(tmp_path) == {
        "docs/notes.md": b"# Notes\n\nbody\n",
        "src/main.py": b'def greet():\n    print("Hello")\n',
    }
    assert result.details["summary"] == (
        "Changed 4 files (+4 \N{MINUS SIGN}2): docs/notes.md, src/app.py, src/main.py, obsolete.txt"
    )


def test_a_header_places_a_hunk_whose_lines_repeat(tmp_path: Path) -> None:
    """Without the ``@@`` line the change would land on the first of two equal lines."""
    _write(tmp_path, "app.py", "def a():\n    return 1\n\n\ndef b():\n    return 1\n")

    apply_patch(
        input=_patch("*** Update File: app.py", "@@ def b():", "-    return 1", "+    return 2")
    )

    assert (
        tmp_path / "app.py"
    ).read_text() == "def a():\n    return 1\n\n\ndef b():\n    return 2\n"


def test_a_hunk_is_never_applied_before_the_place_its_header_names(tmp_path: Path) -> None:
    """``b`` no longer returns 1; the only matching line is in ``a``, which the hunk did not name."""
    before = "def a():\n    return 1\n\n\ndef b():\n    return 5\n"
    _write(tmp_path, "app.py", before)

    result = apply_patch(
        input=_patch("*** Update File: app.py", "@@ def b():", "-    return 1", "+    return 2")
    )

    assert "app.py: could not find these lines" in _refusal(result)
    assert (tmp_path / "app.py").read_text() == before


def test_hunks_with_only_added_lines_go_after_their_header_or_to_the_end(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "def a():\n    return 1\n")

    apply_patch(
        input=_patch(
            "*** Update File: app.py",
            "@@ def a():",
            "+    # first",
            "*** Update File: app.py",
            "+# last",
        )
    )

    assert (tmp_path / "app.py").read_text() == "def a():\n    # first\n    return 1\n# last\n"


@pytest.mark.parametrize(
    ("before", "hunk", "after"),
    [
        # Windows line endings stay Windows line endings.
        ("one\r\ntwo\r\n", (" one", "-two", "+TWO"), "one\r\nTWO\r\n"),
        # A file with no final newline keeps none.
        ("alpha\nbeta", ("-beta", "+gamma"), "alpha\ngamma"),
        # Trailing spaces and re-indented context still find their lines.
        (
            "def f():   \n\treturn 1\n",
            (" def f():", "-    return 1", "+    return 2"),
            "def f():   \n    return 2\n",
        ),
        # The file holds curly quotes and a dash; the model typed ASCII.
        ("It’s “fine” — really\n", ('-It\'s "fine" - really', "+done"), "done\n"),
    ],
)
def test_a_hunk_finds_its_lines_as_the_file_writes_them(
    tmp_path: Path, before: str, hunk: tuple[str, ...], after: str
) -> None:
    _write(tmp_path, "file.txt", before)

    apply_patch(input=_patch("*** Update File: file.txt", *hunk))

    assert (tmp_path / "file.txt").read_bytes() == after.encode("utf-8")


@pytest.mark.parametrize(
    "wrapped",
    [
        "apply_patch <<'EOF'\n{patch}\nEOF",
        "```\n{patch}\n```",
        "\n\n{patch}\n\n",
    ],
)
def test_a_patch_wrapped_the_way_the_shell_form_was_still_applies(
    tmp_path: Path, wrapped: str
) -> None:
    _write(tmp_path, "a.txt", "x\n")

    apply_patch(input=wrapped.format(patch=_patch("*** Update File: a.txt", "-x", "+y")))

    assert (tmp_path / "a.txt").read_text() == "y\n"


def test_an_edited_or_renamed_script_stays_executable(tmp_path: Path) -> None:
    """A rename is a delete and a create; the new file must not fall back to the defaults."""
    _write(tmp_path, "run.sh", "#!/bin/sh\necho hi\n").chmod(0o755)
    _write(tmp_path, "old.sh", "#!/bin/sh\necho old\n").chmod(0o755)

    apply_patch(
        input=_patch(
            "*** Update File: run.sh",
            "-echo hi",
            "+echo hello",
            "*** Update File: old.sh",
            "*** Move to: bin/new.sh",
            "-echo old",
            "+echo new",
        )
    )

    assert stat.S_IMODE((tmp_path / "run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((tmp_path / "bin/new.sh").stat().st_mode) == 0o755
    # Nothing the write staged or set aside is left behind.
    assert sorted(_tree(tmp_path)) == ["bin/new.sh", "run.sh"]


# --- whole or not at all ----------------------------------------------------


def test_a_patch_that_does_not_fit_one_file_changes_none(tmp_path: Path) -> None:
    # Arrange
    _write(tmp_path, "b.py", "value = 1\n")
    before = _tree(tmp_path)

    # Act: the first section is fine, the second expects a line that is not there.
    result = apply_patch(
        input=_patch(
            "*** Add File: a.py",
            "+created = True",
            "*** Update File: b.py",
            "-value = 2",
            "+value = 3",
        )
    )

    # Assert
    message = _refusal(result)
    assert "b.py: could not find these lines" in message and "value = 2" in message
    assert message.endswith("Nothing was changed.")
    assert _tree(tmp_path) == before


_THREE_FILE_PATCH = (
    "*** Add File: new.py",
    "+n = 1",
    "*** Update File: a.py",
    "-a = 1",
    "+a = 2",
    "*** Delete File: gone.py",
    "*** Update File: b.py",
    "-b = 1",
    "+b = 2",
)


def _failing_swap(
    monkeypatch: pytest.MonkeyPatch, *, unwritable: str, unrestorable: str | None = None
) -> None:
    """Make swapping ``unwritable`` in fail, and optionally putting ``unrestorable`` back."""
    real_replace = os.replace

    def replace(source: Any, destination: Any) -> None:
        name = Path(destination).name
        if name == unwritable and str(source).endswith(".new"):
            raise PermissionError("read-only")
        if name == unrestorable and str(source).endswith(".orig"):
            raise OSError("no space left on device")
        real_replace(source, destination)

    monkeypatch.setattr(patching.os, "replace", replace)


def test_a_write_that_fails_part_way_puts_every_file_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: the last file of the patch cannot be swapped in.
    _write(tmp_path, "a.py", "a = 1\n").chmod(0o640)
    _write(tmp_path, "b.py", "b = 1\n")
    _write(tmp_path, "gone.py", "g = 1\n")
    before = _tree(tmp_path)
    _failing_swap(monkeypatch, unwritable="b.py")

    # Act
    result = apply_patch(input=_patch(*_THREE_FILE_PATCH))

    # Assert: contents, the deleted file and permissions are all as they were.
    assert _refusal(result) == (
        "could not write b.py (PermissionError); every file was put back as it was."
    )
    assert _tree(tmp_path) == before
    assert stat.S_IMODE((tmp_path / "a.py").stat().st_mode) == 0o640


def test_a_file_that_cannot_be_put_back_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saying every file was restored when one was not hides a half-applied patch."""
    _write(tmp_path, "a.py", "a = 1\n")
    _write(tmp_path, "b.py", "b = 1\n")
    _write(tmp_path, "gone.py", "g = 1\n")
    _failing_swap(monkeypatch, unwritable="b.py", unrestorable="a.py")

    message = _refusal(apply_patch(input=_patch(*_THREE_FILE_PATCH)))

    assert "these files could not be put back and need checking: a.py" in message
    assert "every file was put back" not in message


def test_new_content_that_cannot_be_written_touches_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contents are written beside their targets first, so a full disk fails before any swap."""
    # Arrange
    _write(tmp_path, "a.py", "a = 1\n")
    _write(tmp_path, "b.py", "b = 1\n")
    _write(tmp_path, "gone.py", "g = 1\n")
    before = _tree(tmp_path)
    real_open = os.open

    def open_until_b(path: Any, flags: int, mode: int = 0o777) -> int:
        if Path(path).name.startswith(".b.py."):
            raise OSError("no space left on device")
        return real_open(path, flags, mode)

    monkeypatch.setattr(patching.os, "open", open_until_b)

    # Act
    result = apply_patch(input=_patch(*_THREE_FILE_PATCH))

    # Assert
    assert _refusal(result) == "could not write b.py (OSError). Nothing was changed."
    assert _tree(tmp_path) == before


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ("*** Update File: a.txt\n-x\n+y", "must start with '*** Begin Patch'"),
        (_patch("*** Update File: missing.txt", "-x", "+y"), "missing.txt does not exist"),
        (_patch("*** Add File: a.txt", "+again"), "a.txt already exists"),
        (_patch("*** Update File: a.txt", "-x", "+x"), "leaves every file as it already is"),
        (
            _patch("*** Update File: a.txt", "@@ def nowhere():", "+y"),
            "could not find the context line",
        ),
        (_patch("*** Update File: blob.bin", "-x", "+y"), "blob.bin is a binary file"),
        (_patch("*** Update File: a.txt", "\tx"), "a hunk line starts with"),
    ],
)
def test_a_patch_that_cannot_apply_says_why_and_writes_nothing(
    tmp_path: Path, patch: str, reason: str
) -> None:
    _write(tmp_path, "a.txt", "x\n")
    (tmp_path / "blob.bin").write_bytes(b"x\0\x01")
    before = _tree(tmp_path)

    assert reason in _refusal(apply_patch(input=patch))
    assert _tree(tmp_path) == before


# --- where a patch may write ------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/tmp/outside.txt",
        "link-out/inside.txt",
        ".env",
        ".ENV",
        "deploy/server.pem",
        # A hook written here would run on the user's next commit.
        ".git/hooks/pre-commit",
    ],
)
def test_a_patch_cannot_write_outside_the_working_directory_or_into_secrets(
    tmp_path: Path, path: str
) -> None:
    # Arrange
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link-out").symlink_to(outside, target_is_directory=True)
    _write(tmp_path, "safe.txt", "x\n")
    before = _tree(tmp_path)

    # Act: a patch whose other section is harmless must not land either.
    result = apply_patch(
        input=_patch("*** Update File: safe.txt", "-x", "+y", f"*** Add File: {path}", "+planted")
    )

    # Assert
    message = _refusal(result)
    assert "outside the working directory" in message or "holds credentials" in message
    assert _tree(tmp_path) == before
    assert list(outside.iterdir()) == []


def test_a_rename_cannot_move_a_file_out(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "x\n")

    result = apply_patch(
        input=_patch("*** Update File: a.txt", "*** Move to: ../a.txt", "-x", "+y")
    )

    assert "outside the working directory" in _refusal(result)
    assert (tmp_path / "a.txt").read_text() == "x\n"


# --- who decides whether it runs --------------------------------------------


class _Presenter:
    """The shell's execution gate, recording what it was asked."""

    def __init__(self, *, allow: bool) -> None:
        self.allow = allow
        self.asked: list[tuple[ExecutionPolicyResult, str]] = []

    def execution_allowed(self, policy: ExecutionPolicyResult, *, action_summary: str) -> bool:
        self.asked.append((policy, action_summary))
        return self.allow


def _run_through_the_executor(patch: str, presenter: _Presenter | None) -> ToolExecutionResult:
    clear_tool_registry_cache()
    resources = (
        {}
        if presenter is None
        else {
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolScope(
                session=None, console=None, subprocess_presenter=presenter
            )
        }
    )
    [result] = execute_tool_calls(
        [ToolCall(id="call_1", name="apply_patch", input={"input": patch})],
        [get_registered_tool("apply_patch")],  # type: ignore[list-item]
        {},
        tool_resources=resources,
    )
    return result


def test_a_declined_patch_writes_nothing_and_tells_the_model_not_to_resend(tmp_path: Path) -> None:
    # Arrange
    _write(tmp_path, "a.txt", "x\n")
    presenter = _Presenter(allow=False)

    # Act
    result = _run_through_the_executor(_patch("*** Update File: a.txt", "-x", "+y"), presenter)

    # Assert: asked once, as a file edit, showing the files and the patch itself.
    [(policy, summary)] = presenter.asked
    assert policy.tool_type == FILE_EDIT_TOOL_TYPE
    assert "edit a.txt" in summary and "+y" in summary
    assert result.is_error is True
    assert "declined" in str(result.provider_content())
    assert (tmp_path / "a.txt").read_text() == "x\n"


def test_a_file_saved_while_the_approval_was_open_is_not_overwritten(tmp_path: Path) -> None:
    """The patch was worked out from the earlier contents; writing it would drop the user's edit."""
    # Arrange: the user edits the file, then approves.
    target = _write(tmp_path, "a.txt", "x\nkeep\n")

    class _EditsThenAllows(_Presenter):
        def execution_allowed(self, policy: ExecutionPolicyResult, *, action_summary: str) -> bool:
            target.write_text("x\nkeep\nthe user's own line\n")
            return super().execution_allowed(policy, action_summary=action_summary)

    # Act
    result = _run_through_the_executor(
        _patch("*** Update File: a.txt", "-x", "+y"), _EditsThenAllows(allow=True)
    )

    # Assert
    assert result.is_error is True
    assert "a.txt changed after the patch was read" in str(result.provider_content())
    assert target.read_text() == "x\nkeep\nthe user's own line\n"


def test_an_allowed_patch_is_written_through_the_executor(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "x\n")

    result = _run_through_the_executor(
        _patch("*** Update File: a.txt", "-x", "+y"), _Presenter(allow=True)
    )

    assert result.provider_content() == "Success. Updated the following files:\nM a.txt"
    assert (tmp_path / "a.txt").read_text() == "y\n"


def test_a_patch_asks_wherever_a_shell_command_would() -> None:
    """Asked below High and under a plan-only request; refused headless unless allowed."""
    clear_tool_registry_cache()
    edit = allow_tool(FILE_EDIT_TOOL_TYPE)

    assert apply_auto_level(edit, AutoLevel.HIGH).verdict == "allow"
    assert [
        apply_auto_level(edit, level).verdict
        for level in (AutoLevel.MED, AutoLevel.LOW, AutoLevel.OFF)
    ] == ["ask"] * 3
    assert apply_plan_only_gate(edit, plan_only_active=True).verdict == "ask"
    registered = get_registered_tool("apply_patch")
    assert approval_required(registered) is True  # type: ignore[arg-type]
    assert registered.concurrency_safe is False
