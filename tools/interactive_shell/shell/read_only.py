"""Deterministic read-only classification for shell commands.

A command is read-only only when every executable across its pipeline/sequence
segments is on a curated allowlist and nothing writes to a file. The classifier
is deliberately conservative: anything it cannot prove safe (command
substitution, heredocs, output redirects, unknown executables, command-runners
like ``xargs``/``sudo``, or a read-only tool invoked with a write flag such as
``find -delete``, ``sort -o``, ``rg --pre``, or a git diff-family command that
can run a configured helper) returns ``False`` so the execution gate still
asks. A read-only command may then run without approval, matching how coding
agents let inspection commands through while still gating mutations.
"""

from __future__ import annotations

import os
import re
import shlex

# Executables that only read state. Command-runners (sudo, env, xargs, timeout,
# nohup, watch), anything that writes (tee, dd, cp, mv, rm), and network tools
# are intentionally absent so they always fall through to the gate.
_READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "dir",
        "vdir",
        "locate",
        "tree",
        "cat",
        "tac",
        "nl",
        "bat",
        "head",
        "tail",
        "less",
        "more",
        "most",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "ack",
        "pwd",
        "cd",
        "pushd",
        "popd",
        "echo",
        "printf",
        "wc",
        "stat",
        "file",
        "du",
        "df",
        "which",
        "whereis",
        "whatis",
        "type",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "printenv",
        "date",
        "cal",
        "uname",
        "hostname",
        "arch",
        "id",
        "whoami",
        "groups",
        "who",
        "uptime",
        "uniq",
        "cut",
        "column",
        "comm",
        "join",
        "paste",
        "tr",
        "fold",
        "fmt",
        "expand",
        "unexpand",
        "rev",
        "seq",
        "jq",
        "cksum",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "sha512sum",
        "hexdump",
        "xxd",
        "od",
        "strings",
        "diff",
        "cmp",
        "ps",
        "pstree",
        "pgrep",
        "nproc",
        "lscpu",
        "lsblk",
        "tty",
        "true",
        "false",
    }
)
# git subcommands that only read. Mutation-capable ones (remote/branch/tag/config/
# symbolic-ref/reflog) are handled separately: read-only only without a write
# verb, positional, or flag. Diff-family porcelain (diff/show/whatchanged/blame)
# is not in this set: helpers can run with no extra flag.
_GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "ls-files",
        "rev-parse",
        "rev-list",
        "describe",
        "shortlog",
        "ls-tree",
        "ls-remote",
        "cat-file",
        "for-each-ref",
        "show-ref",
        "name-rev",
        "merge-base",
        "count-objects",
        "verify-commit",
        "verify-tag",
        "grep",
        "version",
        "help",
    }
)
# `git remote <verb>` verbs that change remotes; bare/list/-v/show/get-url read.
_GIT_REMOTE_WRITE_VERBS: frozenset[str] = frozenset(
    {"add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune", "update"}
)
# Write flags per subcommand. `-a` reads for branch (all) but writes for tag
# (annotate), so the two sets are kept separate.
_GIT_BRANCH_WRITE_FLAGS: frozenset[str] = frozenset(
    {
        "-d",
        "-D",
        "-m",
        "-M",
        "-c",
        "-C",
        "-u",
        "-f",
        "--delete",
        "--move",
        "--copy",
        "--force",
        "--edit-description",
        "--set-upstream-to",
        "--unset-upstream",
        "--create-reflog",
    }
)
_GIT_TAG_WRITE_FLAGS: frozenset[str] = frozenset(
    {"-a", "-s", "-d", "-f", "-m", "--delete", "--force", "--annotate", "--sign"}
)
# Write flags for `git config`; a bare `key value` pair also writes.
_GIT_CONFIG_WRITE_FLAGS: frozenset[str] = frozenset(
    {
        "--unset",
        "--unset-all",
        "--replace-all",
        "--add",
        "--edit",
        "-e",
        "--rename-section",
        "--remove-section",
    }
)
# `git symbolic-ref <name>` reads; `--delete` / `-m` always write (including
# the attached ``-mreason`` form, which is not a separate positional).
_GIT_SYMBOLIC_REF_WRITE_FLAGS: frozenset[str] = frozenset({"-d", "--delete", "-m"})
# `git reflog` / `show` / `list` / `exists` read. Any other verb (`expire`,
# `delete`, `drop`, `write`, unknown future verbs) mutates refs — fail closed.
# A first positional that looks like a ref (`HEAD`, `refs/…`, `name@{1}`) is
# implicit `show`, not a verb.
_GIT_REFLOG_READ_VERBS: frozenset[str] = frozenset({"show", "list", "exists"})


# Diff-family ``--output`` / ``-o`` write a file. Shared by ``diff``, ``log``,
# ``show``, ``whatchanged``, and other porcelain that reuses the diff options —
# not just ``git diff``.
_GIT_OUTPUT_WRITE_FLAGS: frozenset[str] = frozenset({"--output", "-o"})
# Porcelain that runs the diff machinery. Gitattributes diff drivers and
# textconv filters are on by default here, so these are read-only only when
# both ``--no-ext-diff`` and ``--no-textconv`` are present.
_GIT_DIFF_FAMILY_SUBCOMMANDS: frozenset[str] = frozenset({"diff", "show", "whatchanged", "blame"})
_GIT_DIFF_HELPER_DISABLE_FLAGS: frozenset[str] = frozenset({"--no-ext-diff", "--no-textconv"})
# ``git log`` only invokes that machinery when producing a patch.
_GIT_LOG_PATCH_FLAGS: frozenset[str] = frozenset(
    {
        "-p",
        "-u",
        "-U",
        "--patch",
        "--unified",
        "--raw",
        "--patch-with-stat",
        "--patch-with-raw",
    }
)


def _token_is_write_flag(token: str, write_flags: frozenset[str]) -> bool:
    """True when ``token`` is a write flag, including ``--flag=value`` / ``-uupstream``."""
    if token in write_flags:
        return True
    if token.startswith("--") and "=" in token:
        return token.split("=", 1)[0] in write_flags
    # Short option with an attached value (``-uupstream`` for ``-u``).
    return any(
        len(flag) == 2
        and flag.startswith("-")
        and not flag.startswith("--")
        and token.startswith(flag)
        and len(token) > 2
        for flag in write_flags
    )


def _git_diff_helpers_disabled(flags: list[str]) -> bool:
    """True when argv opted out of both external diff drivers and textconv."""
    return all(name in flags for name in _GIT_DIFF_HELPER_DISABLE_FLAGS)


def _git_is_read_only(rest: list[str]) -> bool:
    positional = [tok for tok in rest if not tok.startswith("-")]
    subcommand = positional[0] if positional else None
    if subcommand is None:
        return True  # bare `git`, `git --version`, `git -h`
    after = rest[rest.index(subcommand) + 1 :] if subcommand in rest else []
    flags_after = [tok for tok in after if tok.startswith("-")]
    positional_after = [tok for tok in after if not tok.startswith("-")]
    # ``git {diff,log,show,reflog,…} --output=<file>`` overwrites a file.
    if any(_token_is_write_flag(flag, _GIT_OUTPUT_WRITE_FLAGS) for flag in flags_after):
        return False
    # Diff-family porcelain runs configured textconv / diff.*.command helpers
    # with no extra flag. Require an explicit opt-out before auto-allowing.
    if subcommand in _GIT_DIFF_FAMILY_SUBCOMMANDS:
        return _git_diff_helpers_disabled(flags_after)
    if subcommand == "log" and any(
        _token_is_write_flag(flag, _GIT_LOG_PATCH_FLAGS) for flag in flags_after
    ):
        return _git_diff_helpers_disabled(flags_after)
    if subcommand in _GIT_READ_ONLY_SUBCOMMANDS:
        return True
    if subcommand == "symbolic-ref":
        # ``HEAD`` reads; ``HEAD refs/heads/foo`` / ``--delete`` / ``-m`` write.
        if any(_token_is_write_flag(flag, _GIT_SYMBOLIC_REF_WRITE_FLAGS) for flag in flags_after):
            return False
        return len(positional_after) <= 1
    if subcommand == "reflog":
        if not positional_after:
            return True  # implicit `show`
        verb = positional_after[0]
        if verb in _GIT_REFLOG_READ_VERBS:
            return True
        # `git reflog HEAD` / `git reflog refs/heads/foo` — implicit show.
        return verb.upper() == "HEAD" or any(char in verb for char in "/@:")
    if subcommand == "remote":
        return not any(tok in _GIT_REMOTE_WRITE_VERBS for tok in positional_after)
    if subcommand in ("branch", "tag"):
        write_flags = _GIT_BRANCH_WRITE_FLAGS if subcommand == "branch" else _GIT_TAG_WRITE_FLAGS
        # List forms carry only read flags and no positional (create/delete) name.
        # ``--set-upstream-to=<upstream>`` must match even with ``=value`` attached.
        return not positional_after and not any(
            _token_is_write_flag(flag, write_flags) for flag in flags_after
        )
    if subcommand == "config":
        if any(_token_is_write_flag(flag, _GIT_CONFIG_WRITE_FLAGS) for flag in flags_after):
            return False
        return len(positional_after) <= 1  # `--get key` reads; `key value` writes
    return False


# find primaries that delete, run, or write instead of only listing.
_FIND_MUTATING_PRIMARIES: frozenset[str] = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)

_HEREDOC_RE = re.compile(r"<<-?\s*(?:'[^'\n]+'|\"[^\"\n]+\"|[^\s\\|;&<>]+)")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# ``cmd.exe`` expands paired environment variables plus batch-style positional
# parameters. An unmatched percent sign is literal and must not be over-gated.
_CMD_PERCENT_EXPANSION_RE = re.compile(
    r"%(?:[^%\r\n]+%|[0-9*]|~[fdpnxsatz$]*[0-9])",
    re.IGNORECASE,
)
# Redirects that do not write a file: stderr/stdout to /dev/null, or fd dups.
_SAFE_REDIRECTS = (
    "2>/dev/null",
    "1>/dev/null",
    "&>/dev/null",
    ">/dev/null",
    "2>&1",
    "1>&2",
)


def _has_file_write_redirect(text: str) -> bool:
    scrubbed = text
    for pattern in _SAFE_REDIRECTS:
        scrubbed = scrubbed.replace(pattern, " ")
    return ">" in scrubbed


def _shell_lexing_rules() -> tuple[bool, tuple[str, ...], str]:
    """Return the host shell's quote characters and escape character."""
    is_windows = os.name == "nt"
    if is_windows:
        return True, ('"',), "^"
    return False, ("'", '"'), "\\"


def _split_on_operators(text: str) -> list[str] | None:
    """Split into pipeline/sequence segments at unquoted ``| ; & && ||``.

    Returns ``None`` when quoting is unbalanced (cannot classify safely).
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    is_windows, quote_characters, escape_character = _shell_lexing_rules()
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote is not None:
            current.append(char)
            if char == escape_character and index + 1 < length and not is_windows and quote == '"':
                current.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in quote_characters:
            quote = char
            current.append(char)
            index += 1
            continue
        if char == escape_character and index + 1 < length:
            current.append(char)
            current.append(text[index + 1])
            index += 2
            continue
        if text[index : index + 2] in ("&&", "||"):
            segments.append("".join(current))
            current = []
            index += 2
            continue
        # A newline separates commands like ``;`` — without splitting on it, a
        # mutation on the next line would ride in the first segment and be judged
        # only by the leading read-only executable.
        if char in ("|", ";", "&", "\n"):
            segments.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if quote is not None:
        return None
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def _is_redirect_token(token: str) -> bool:
    return (">" in token) or ("<" in token) or token == "/dev/null"


# git transports that run a helper program (``git ls-remote ext::<cmd>``); these
# execute code regardless of the read-only subcommand, so always gate them.
_DANGEROUS_TRANSPORTS = ("ext::", "fd::")
# Process-local config (``git -c`` / ``--config``) can re-enable those transports
# for a single invocation — never auto-allow when present anywhere on the argv.
_GIT_CONFIG_INJECT_FLAGS: frozenset[str] = frozenset({"-c", "--config", "--config-env"})
# Helper-path overrides run an attacker-chosen executable (default Git config).
# ``--exec-path`` relocates the git-core helper directory for this process.
_GIT_HELPER_EXEC_FLAGS: frozenset[str] = frozenset(
    {"--upload-pack", "--receive-pack", "--exec", "--exec-path"}
)
# Diff-family helpers. ``--ext-diff`` runs ``diff.external``; ``--textconv`` runs
# attribute/config converters. ``-c`` already covers process-local config of either.
_GIT_DIFF_HELPER_FLAGS: frozenset[str] = frozenset({"--ext-diff", "--textconv"})
_GIT_UNSAFE_ARGV_FLAGS: frozenset[str] = (
    _GIT_CONFIG_INJECT_FLAGS | _GIT_HELPER_EXEC_FLAGS | _GIT_DIFF_HELPER_FLAGS
)
# ``rg --pre`` / ``--pre=`` runs a preprocessor per matched file. ``--hostname-bin``
# runs an arbitrary hostname helper when hyperlinks are on. Matched with
# ``_token_is_write_flag`` so ``--pretty`` / ``--pre-glob`` stay ordinary reads.
_RG_HELPER_FLAGS: frozenset[str] = frozenset({"--pre", "--hostname-bin"})
# ``sort --compress-program`` runs an external compressor on temp files.
_SORT_HELPER_FLAGS: frozenset[str] = frozenset({"--compress-program"})
# ``date -s`` / ``date --set=`` set the system clock.
_DATE_WRITE_FLAGS: frozenset[str] = frozenset({"-s", "--set"})
# Linux ``hostname -F <file>`` / ``-b`` write the kernel hostname from a file.
_HOSTNAME_WRITE_FLAGS: frozenset[str] = frozenset({"-F", "--file", "-b", "--boot"})


def _git_argv_is_unsafe(rest: list[str]) -> bool:
    """True when argv enables helper execution or process-local config injection."""
    return any(
        tok.lower().startswith(_DANGEROUS_TRANSPORTS)
        or _token_is_write_flag(tok, _GIT_UNSAFE_ARGV_FLAGS)
        for tok in rest
    )


def _date_is_read_only(rest: list[str]) -> bool:
    """``date`` is read-only only for display forms.

    ``date -s`` / ``--set`` and the legacy positional ``MMDDhhmm[[CC]YY][.ss]``
    form set the clock on POSIX. Under ``cmd.exe``, bare ``date`` prompts for a
    new date and only ``date /t`` is display-only.
    """
    if os.name == "nt":
        return len(rest) == 1 and rest[0].lower() == "/t"
    if any(_token_is_write_flag(tok, _DATE_WRITE_FLAGS) for tok in rest):
        return False
    return not any(not tok.startswith(("-", "+")) for tok in rest)


def _hostname_is_read_only(rest: list[str]) -> bool:
    """``hostname`` is read-only with display flags only — never a new name or ``-F``."""
    if any(_token_is_write_flag(tok, _HOSTNAME_WRITE_FLAGS) for tok in rest):
        return False
    return not any(not tok.startswith("-") for tok in rest)


def _executable_is_read_only(exe: str, rest: list[str]) -> bool:
    if "/" in exe:
        # A path-qualified executable (./ls, /tmp/git, /usr/bin/…) runs that exact
        # file, not the allowlisted command resolved on PATH; never trust it.
        return False
    name = exe
    if name == "git":
        if _git_argv_is_unsafe(rest):
            return False
        return _git_is_read_only(rest)
    if name == "find":
        return not any(tok in _FIND_MUTATING_PRIMARIES for tok in rest)
    if name == "sort":
        if any(tok.startswith("-o") or tok.startswith("--output") for tok in rest):
            return False
        return not any(_token_is_write_flag(tok, _SORT_HELPER_FLAGS) for tok in rest)
    if name == "date":
        return _date_is_read_only(rest)
    if name == "hostname":
        return _hostname_is_read_only(rest)
    if name in ("rg", "ag", "ack", "grep", "egrep", "fgrep"):
        return not any(_token_is_write_flag(tok, _RG_HELPER_FLAGS) for tok in rest)
    if name in ("yq", "sed", "perl", "awk"):
        # These can edit files in place / run programs; only ever gate them.
        return False
    if name not in _READ_ONLY_COMMANDS:
        return False
    # Membership is not enough: unknown helper-exec flags on an allowlisted
    # name (``--pre``, ``--hostname-bin``, ``--compress-program``) still run
    # a program. Fail closed the same way ``rg`` / ``sort`` already do.
    return not any(_token_is_write_flag(tok, _RG_HELPER_FLAGS | _SORT_HELPER_FLAGS) for tok in rest)


def _segment_is_read_only(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        if _is_redirect_token(token):
            continue
        if _ENV_ASSIGN_RE.match(token):
            # An env-var prefix (LD_PRELOAD, GIT_SSH_COMMAND, GIT_CONFIG_*, …) can
            # change a command's behavior or enable code execution; never auto-run.
            return False
        return _executable_is_read_only(token, tokens[index + 1 :])
    return False


def _has_unquoted_brace_expansion(text: str) -> bool:
    """Return whether POSIX shell syntax can expand an unquoted brace expression."""
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and quote != "'":
            index += 2
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "{":
            closing = text.find("}", index + 1)
            if closing != -1 and (
                "," in text[index + 1 : closing] or ".." in text[index + 1 : closing]
            ):
                return True
        index += 1
    return False


def _has_dangerous_shell_construct(text: str) -> bool:
    """True when shell evaluation can replace or execute command arguments.

    Fail closed on substitutions, subshells, and unquoted glob or brace
    expansion. A glob or brace expansion can produce an executable option
    after the policy has classified the literal input. Quoted or escaped
    patterns remain literal arguments.
    """
    is_windows, quote_characters, escape_character = _shell_lexing_rules()
    # cmd.exe expands %NAME% even inside quotes, and the expanded value may
    # introduce operators or flag-shaped arguments after policy classification.
    if is_windows and _CMD_PERCENT_EXPANSION_RE.search(text):
        return True
    if not is_windows and _has_unquoted_brace_expansion(text):
        return True

    quote: str | None = None
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if (
            char == escape_character
            and index + 1 < length
            and (quote is None or (not is_windows and quote == '"'))
        ):
            index += 2
            continue
        if quote is not None:
            if char == quote:
                quote = None
            elif not is_windows and quote == '"' and char in "$`":
                return True  # $ and backtick still expand inside double quotes
            index += 1
            continue
        if char in quote_characters:
            quote = char
        elif char in "$`()" or char in "*?[":
            return True
        index += 1
    return False


def is_read_only_shell_command(command: str) -> bool:
    """True when every segment of ``command`` only reads state (see module docs)."""
    text = command.strip()
    if not text:
        return False
    # Fail closed on anything that can expand to or run a nested command
    # (substitution, subshell, heredoc) — the whole class, not one syntax at a time.
    if _has_dangerous_shell_construct(text) or _HEREDOC_RE.search(text):
        return False
    if _has_file_write_redirect(text):
        return False
    segments = _split_on_operators(text)
    if not segments:
        return False
    return all(_segment_is_read_only(segment) for segment in segments)


__all__ = ["is_read_only_shell_command"]
