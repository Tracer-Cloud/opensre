"""Only commands that cannot change anything are reported as reads."""

from __future__ import annotations

import pytest

from tools.interactive_shell.shell.effects import shell_command_only_reads


@pytest.mark.parametrize(
    "command, reads",
    [
        ("git status --short", True),
        ('cd "/srv/repo" && git log --oneline -5 2>&1', True),
        ("git -C /srv/repo diff HEAD~1 > /dev/null", True),
        ("git --no-pager show HEAD --stat", True),
        ("rg TODO src", True),
        # Options and settings that write a file or run another program.
        ("git diff --output=/tmp/patch.diff", False),
        ("git grep -Oless TODO", False),
        ("git -c core.pager=less log", False),
        ("GIT_SSH_COMMAND=ssh git fetch", False),
        ("rg --pre ./run.sh TODO", False),
        ("git log > /dev/null.log", False),
        ("git push origin fix/x", False),
        ("git stash list", False),
        ("git status; rm -rf build", False),
        ("cat notes.txt | sh", False),
        ("ls > listing.txt", False),
        ("echo $(git push)", False),
        ("grep 'a|b' notes.txt", False),
    ],
)
def test_a_command_is_a_read_only_when_every_part_only_reads(command: str, reads: bool) -> None:
    assert shell_command_only_reads(command) is reads
