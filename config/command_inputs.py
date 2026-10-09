"""Missing-argument interaction metadata shared by slash hosts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandInput:
    """Describe one subcommand whose missing positional argument can be collected."""

    command: str
    subcommands: tuple[str, ...]
    value_options: frozenset[str] = frozenset()

    def matches(self, command: str, args: list[str]) -> bool:
        """Return whether this input applies to a missing positional argument."""
        if command.lower() != self.command or not args or args[0].lower() not in self.subcommands:
            return False
        index = 1
        while index < len(args):
            if args[index] not in self.value_options or index + 1 >= len(args):
                return False
            if not args[index + 1].strip() or args[index + 1].startswith("--"):
                return False
            index += 2
        return True


WORK_ADD_INPUT = CommandInput(
    "/work", ("add",), frozenset({"--project", "--owner", "--priority", "--due"})
)
COMMAND_INPUTS = (WORK_ADD_INPUT,)


def needs_command_input(command: str, args: list[str]) -> bool:
    """Return whether a supported slash invocation needs interactive input."""
    return any(spec.matches(command, args) for spec in COMMAND_INPUTS)
