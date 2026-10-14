"""Missing-argument interaction metadata shared by slash hosts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandInput:
    """Describe one subcommand whose incomplete input can be collected."""

    command: str
    subcommands: tuple[str, ...]
    value_options: frozenset[str] = frozenset()
    flag_options: frozenset[str] = frozenset()
    greedy_value_options: frozenset[str] = frozenset()
    empty_value_options: frozenset[str] = frozenset()
    required_options: frozenset[str] = frozenset()
    alternative_options: tuple[frozenset[str], ...] = ()
    max_positionals: int = 0
    missing_target: bool = False

    def matches(self, command: str, args: list[str]) -> bool:
        """Return whether this input applies to a valid but incomplete invocation."""
        if command.lower() != self.command or not args or args[0].lower() not in self.subcommands:
            return False
        nonempty: set[str] = set()
        positionals = 0
        supplied_target = False
        index = 1
        while index < len(args):
            option = args[index]
            if not option.startswith("--") and positionals < self.max_positionals:
                positionals += 1
                supplied_target = supplied_target or bool(option.strip())
                index += 1
                continue
            if option in self.flag_options:
                has_value = True
                index += 1
            else:
                if option not in self.value_options or index + 1 >= len(args):
                    return False
                value = args[index + 1]
                if (
                    not value.strip() and option not in self.empty_value_options
                ) or value.startswith("--"):
                    return False
                has_value = bool(value.strip())
                index += 2
                if option in self.greedy_value_options:
                    while index < len(args) and not args[index].startswith("--"):
                        if args[index].strip():
                            has_value = True
                        index += 1
            alternatives = next(
                (options for options in self.alternative_options if option in options), ()
            )
            nonempty.difference_update(alternatives)
            if has_value:
                nonempty.add(option)
            else:
                nonempty.discard(option)
        if self.missing_target:
            return not supplied_target
        if self.required_options or self.alternative_options:
            missing_required = not self.required_options.issubset(nonempty)
            missing_alternative = any(
                not options.intersection(nonempty) for options in self.alternative_options
            )
            return missing_required or missing_alternative
        return True


WORK_ADD_INPUT = CommandInput(
    "/work", ("add",), frozenset({"--project", "--owner", "--priority", "--due"})
)
GOAL_SET_INPUT = CommandInput("/goal", ("set",), frozenset({"--max-turns", "--max_turns"}))
LOOPS_ADD_INPUT = CommandInput(
    "/loops",
    ("add",),
    value_options=frozenset(
        {
            "--name",
            "--prompt",
            "--time",
            "--tz",
            "--cron",
            "--channel",
            "--channels",
            "--telegram-chat-id",
            "--slack-chat-id",
            "--window",
            "--mode",
        }
    ),
    flag_options=frozenset({"--weekdays", "--daily", "--run-now"}),
    greedy_value_options=frozenset({"--name", "--prompt", "--cron"}),
    empty_value_options=frozenset(
        {
            "--name",
            "--prompt",
            "--time",
            "--cron",
            "--tz",
            "--mode",
            "--channel",
            "--channels",
            "--telegram-chat-id",
            "--slack-chat-id",
        }
    ),
    required_options=frozenset({"--name", "--prompt"}),
    alternative_options=(frozenset({"--time", "--cron"}),),
)
LOOPS_TARGET_INPUT = CommandInput(
    "/loops",
    (
        "run",
        "start",
        "resume",
        "enable",
        "stop",
        "pause",
        "disable",
        "delete",
        "remove",
        "next",
        "debug",
    ),
    max_positionals=1,
    missing_target=True,
)
WORK_DONE_INPUT = CommandInput(
    "/work", ("done", "complete"), max_positionals=1, missing_target=True
)
CRON_TARGET_INPUT = CommandInput(
    "/cron", ("logs", "run", "remove"), max_positionals=1, missing_target=True
)
GOAL_EDIT_INPUT = CommandInput("/goal", ("edit",))
WORK_EDIT_INPUT = CommandInput("/work", ("edit",), max_positionals=1)
LOOPS_EDIT_INPUT = CommandInput("/loops", ("edit",), max_positionals=1)
WORK_EDIT_TARGET_INPUT = CommandInput(
    "/work",
    ("edit",),
    value_options=WORK_ADD_INPUT.value_options | {"--title"},
    empty_value_options=frozenset({"--title", "--project", "--owner", "--due"}),
    max_positionals=1,
    missing_target=True,
)
LOOPS_EDIT_TARGET_INPUT = CommandInput(
    "/loops",
    ("edit",),
    value_options=frozenset(
        {"--name", "--prompt", "--time", "--tz", "--cron", "--window", "--mode"}
    ),
    flag_options=frozenset({"--daily", "--weekdays"}),
    greedy_value_options=frozenset({"--name", "--prompt", "--cron"}),
    empty_value_options=frozenset(
        {"--name", "--prompt", "--time", "--tz", "--cron", "--window", "--mode"}
    ),
    max_positionals=1,
    missing_target=True,
)
COMMAND_INPUTS = (
    WORK_ADD_INPUT,
    GOAL_SET_INPUT,
    LOOPS_ADD_INPUT,
    LOOPS_TARGET_INPUT,
    WORK_DONE_INPUT,
    CRON_TARGET_INPUT,
    GOAL_EDIT_INPUT,
    WORK_EDIT_INPUT,
    LOOPS_EDIT_INPUT,
    WORK_EDIT_TARGET_INPUT,
    LOOPS_EDIT_TARGET_INPUT,
)


def needs_command_input(command: str, args: list[str]) -> bool:
    """Return whether a supported slash invocation needs interactive input."""
    return any(spec.matches(command, args) for spec in COMMAND_INPUTS)
