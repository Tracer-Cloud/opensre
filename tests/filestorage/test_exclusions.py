"""User-chosen exclusions: they shrink what syncs, and can never widen it.

The security property under test is that no pattern, however written, can put a
credential file into the store or stop the engine refusing a root that points
at one. The planted canary from ``test_remote_sync`` is reused so both suites
fail on the same regression.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.constants.filestorage import (
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_EXCLUDE_ENV,
    REMOTE_SYNC_EXCLUDE_OFF_ENV,
)
from infrastructure.filestorage.config import load_remote_sync_config
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.engine import pull, push, run_sync
from infrastructure.filestorage.enums import SyncDirection, SyncRootName
from infrastructure.filestorage.errors import RemoteSyncConfigError, UnsyncablePathError
from infrastructure.filestorage.exclusions import NO_EXCLUSIONS, ExclusionRules, parse_exclusions
from infrastructure.filestorage.syncable import SyncRoot
from tests.filestorage.test_remote_sync import LEAKED_SECRET, FakeObjectStore


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A laptop ~/.opensre with sessions, memory, and credential files."""
    (tmp_path / "sessions").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "sessions" / "abc.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    (tmp_path / "sessions" / "scratch-1.jsonl").write_text('{"turn": 2}\n', encoding="utf-8")
    (tmp_path / "memory" / "a-fact.md").write_text("remembered\n", encoding="utf-8")
    (tmp_path / "memory" / "notes.tmp").write_text("scratch\n", encoding="utf-8")
    (tmp_path / "integrations.json").write_text(
        f'{{"datadog": {{"api_key": "{LEAKED_SECRET}"}}}}', encoding="utf-8"
    )
    (tmp_path / "llm-auth.json").write_text(f'{{"openai": "{LEAKED_SECRET}"}}', encoding="utf-8")
    return tmp_path


@pytest.fixture
def roots(home: Path) -> tuple[SyncRoot, ...]:
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=home / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=home / "memory"),
    )


# ── The deny-list stays out of reach ────────────────────────────────────────


def test_negated_patterns_are_rejected_not_ignored() -> None:
    """There is no way to spell "include", so nothing can re-admit a secret."""
    with pytest.raises(RemoteSyncConfigError, match="negation is not supported"):
        parse_exclusions(["!integrations.json"])
    with pytest.raises(RemoteSyncConfigError, match="negation is not supported"):
        parse_exclusions("*.tmp,!llm-auth.json")


@pytest.mark.parametrize(
    "patterns",
    [
        ("integrations.json",),
        ("*",),
        ("*.json",),
        (),
    ],
)
def test_no_pattern_can_put_a_credential_in_the_store(
    home: Path, roots: tuple[SyncRoot, ...], patterns: tuple[str, ...]
) -> None:
    """Whatever the settings say, the canary reaches no stored object."""
    store = FakeObjectStore()

    push(store, roots=roots, exclusions=ExclusionRules(patterns=patterns))

    for key, body in store.objects.items():
        assert LEAKED_SECRET.encode() not in body, f"secret leaked into {key}"
    assert not any(key.endswith("integrations.json") for key in store.objects)


def test_an_exclusion_cannot_suppress_the_refusal_to_leave_a_root(home: Path) -> None:
    """A root pointing at credentials still raises, even if a pattern covers it.

    The deny-list check runs on every candidate before the patterns are
    consulted, so an exclusion can drop a file from the upload but never turn
    the security refusal into a silent skip.
    """
    bad_roots = (SyncRoot(name="home", path=home),)
    store = FakeObjectStore()

    with pytest.raises(UnsyncablePathError):
        push(
            store,
            roots=bad_roots,
            # Broad enough to cover integrations.json several times over.
            exclusions=ExclusionRules(patterns=("*", "*.json", "home/integrations.json")),
        )

    assert store.objects == {}, "nothing may be uploaded once a root is refused"


def test_excluding_everything_still_uploads_nothing_sensitive(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    store = FakeObjectStore()

    report = push(store, roots=roots, exclusions=ExclusionRules(patterns=("*",)))

    assert store.objects == {}
    assert report.uploaded == []
    assert len(report.excluded) == 4


# ── Matching semantics ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pattern", "key", "expected"),
    [
        # Bare filename, anywhere under any root.
        ("notes.tmp", "memory/notes.tmp", True),
        ("*.tmp", "sessions/nested/deep/notes.tmp", True),
        ("*.tmp", "sessions/abc.jsonl", False),
        # Full key.
        ("sessions/scratch-1.jsonl", "sessions/scratch-1.jsonl", True),
        ("sessions/scratch-*", "sessions/scratch-1.jsonl", True),
        ("sessions/scratch-*", "memory/scratch-1.jsonl", False),
        # An ancestor directory takes everything beneath it.
        ("sessions/archive", "sessions/archive/2026/old.jsonl", True),
        ("sessions/archive", "sessions/archive-notes.jsonl", False),
        # A whole root.
        ("memory", "memory/a-fact.md", True),
        ("memory", "sessions/a-fact.md", False),
        # fnmatch's "*" crosses separators, which is what makes "memory/*" work.
        ("memory/*", "memory/deep/nested.md", True),
    ],
)
def test_pattern_matching(pattern: str, key: str, expected: bool) -> None:
    assert ExclusionRules(patterns=(pattern,)).excludes(key) is expected


def test_matching_is_case_sensitive_on_every_platform() -> None:
    """fnmatchcase, not fnmatch: one settings file must mean one thing."""
    rules = ExclusionRules(patterns=("memory/Notes.md",))
    assert rules.excludes("memory/Notes.md") is True
    assert rules.excludes("memory/notes.md") is False


def test_no_patterns_excludes_nothing() -> None:
    assert NO_EXCLUSIONS.excludes("sessions/abc.jsonl") is False
    assert bool(NO_EXCLUSIONS) is False


# ── Parsing ─────────────────────────────────────────────────────────────────


def test_parsing_accepts_a_list_a_string_and_a_scalar() -> None:
    assert parse_exclusions(["*.tmp", "sessions/scratch-*"]).patterns == (
        "*.tmp",
        "sessions/scratch-*",
    )
    assert parse_exclusions("*.tmp, sessions/scratch-*").patterns == (
        "*.tmp",
        "sessions/scratch-*",
    )
    assert parse_exclusions("*.tmp").patterns == ("*.tmp",)
    assert parse_exclusions(None) is NO_EXCLUSIONS
    assert parse_exclusions("") is NO_EXCLUSIONS


def test_parsing_normalizes_without_eating_a_leading_dot() -> None:
    """lstrip("./") would turn ".DS_Store" into "DS_Store" and match nothing."""
    assert parse_exclusions([".DS_Store"]).patterns == (".DS_Store",)
    assert parse_exclusions(["./memory/notes.md"]).patterns == ("memory/notes.md",)
    assert parse_exclusions(["/sessions/a.jsonl"]).patterns == ("sessions/a.jsonl",)
    assert parse_exclusions(["memory\\notes.md"]).patterns == ("memory/notes.md",)
    assert parse_exclusions(["sessions/archive/"]).patterns == ("sessions/archive",)


def test_parsing_drops_blanks_and_duplicates_but_keeps_written_order() -> None:
    assert parse_exclusions("*.tmp, ,*.tmp,sessions/x, ").patterns == ("*.tmp", "sessions/x")


def test_parsing_rejects_shapes_that_are_not_patterns() -> None:
    with pytest.raises(RemoteSyncConfigError, match="must be a list"):
        parse_exclusions({"pattern": "*.tmp"})
    with pytest.raises(RemoteSyncConfigError, match="must be text"):
        parse_exclusions(["*.tmp", 7])


# ── Config ──────────────────────────────────────────────────────────────────


def test_exclusions_load_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_ENV, "*.tmp,sessions/scratch-*")

    config = load_remote_sync_config()

    assert config is not None
    assert config.exclude.patterns == ("*.tmp", "sessions/scratch-*")


def test_exclusions_load_from_the_settings_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text(
        "remote_sync:\n"
        "  enabled: true\n"
        "  bucket: stored-bucket\n"
        "  exclude:\n"
        "    - '*.tmp'\n"
        "    - sessions/scratch-*\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)
    monkeypatch.delenv(REMOTE_SYNC_EXCLUDE_ENV, raising=False)

    config = load_remote_sync_config()

    assert config is not None
    assert config.exclude.patterns == ("*.tmp", "sessions/scratch-*")


def test_the_environment_overrides_the_stored_patterns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text(
        "remote_sync:\n  enabled: true\n  bucket: b\n  exclude:\n    - stored-only\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_ENV, "env-only")

    config = load_remote_sync_config()

    assert config is not None
    assert config.exclude.patterns == ("env-only",)


def test_an_empty_environment_variable_does_not_drop_the_stored_patterns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty means "not set" here, exactly as it does for bucket and prefix.

    Reading it as "exclude nothing" would make a shell expanding an unset name
    (``export OPENSRE_REMOTE_SYNC_EXCLUDE=$TYPO``) silently upload the paths the
    user asked to keep local. An accidental blank has to be inert.
    """
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text(
        "remote_sync:\n  enabled: true\n  bucket: b\n  exclude:\n    - '*.tmp'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_ENV, "")

    config = load_remote_sync_config()

    assert config is not None
    assert config.exclude.patterns == ("*.tmp",)
    # The same rule the neighbouring settings follow, checked side by side so a
    # future change cannot make exclusions the odd one out.
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "")
    with_blank_bucket = load_remote_sync_config()
    assert with_blank_bucket is not None
    assert with_blank_bucket.bucket == "b"


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_the_off_switch_clears_the_configured_patterns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    """The deliberate way to sync a normally-excluded path for one run."""
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text(
        "remote_sync:\n  enabled: true\n  bucket: b\n  exclude:\n    - '*.tmp'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_OFF_ENV, value)

    config = load_remote_sync_config()

    assert config is not None
    assert config.exclude.patterns == ()
    assert bool(config.exclude) is False


@pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe"])
def test_the_off_switch_needs_a_deliberate_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    """Anything short of an affirmative leaves the patterns in force."""
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text(
        "remote_sync:\n  enabled: true\n  bucket: b\n  exclude:\n    - '*.tmp'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_OFF_ENV, value)

    config = load_remote_sync_config()

    assert config is not None
    assert config.exclude.patterns == ("*.tmp",)


def test_no_word_is_reserved_so_any_filename_can_be_excluded() -> None:
    """Turning exclusions off is a separate switch, so no pattern is stolen.

    A file named ``none`` is excludable by writing ``none``, which would not be
    true if the off switch were spelled as a reserved pattern value.
    """
    for word in ("none", "off", "false", "null"):
        rules = parse_exclusions([word])
        assert rules.patterns == (word,)
        assert rules.excludes(f"memory/{word}") is True


def test_a_corrupt_settings_file_defaults_to_no_exclusions_when_env_covers_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A damaged file must not block the documented env-only path.

    The docs teach enabling sync with just the switch and the bucket in env;
    everything else falls back to defaults. An unreadable stored section counts
    as "no stored settings", so the exclusions default to "exclude nothing".
    The built-in deny-list in ``syncable`` is the boundary that still keeps
    credential files local.
    """
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text("not a mapping", encoding="utf-8")
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "env-bucket")
    monkeypatch.delenv(REMOTE_SYNC_EXCLUDE_ENV, raising=False)
    monkeypatch.delenv(REMOTE_SYNC_EXCLUDE_OFF_ENV, raising=False)

    config = load_remote_sync_config()

    assert config is not None
    assert config.exclude is NO_EXCLUSIONS
    assert config.exclude.patterns == ()


# ── The engine honours them, both directions ────────────────────────────────


def test_push_holds_back_excluded_files(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    store = FakeObjectStore()

    report = push(
        store,
        roots=roots,
        exclusions=ExclusionRules(patterns=("*.tmp", "sessions/scratch-*")),
    )

    assert set(store.objects) == {"sessions/abc.jsonl", "memory/a-fact.md"}
    assert report.excluded == {"sessions/scratch-1.jsonl", "memory/notes.tmp"}


def test_pull_does_not_fetch_excluded_objects(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """An exclusion means "not on this machine", so it holds on the way down too."""
    store = FakeObjectStore()
    now = datetime.now(tz=UTC)
    remote = [
        RemoteObject(key="sessions/from-elsewhere.jsonl", size=3, last_modified=now),
        RemoteObject(key="sessions/scratch-9.jsonl", size=3, last_modified=now),
    ]
    store.objects["sessions/from-elsewhere.jsonl"] = b"a\n"
    store.objects["sessions/scratch-9.jsonl"] = b"b\n"

    report = pull(
        store,
        roots=roots,
        remote=remote,
        exclusions=ExclusionRules(patterns=("sessions/scratch-*",)),
    )

    assert report.downloaded == ["sessions/from-elsewhere.jsonl"]
    assert report.excluded == {"sessions/scratch-9.jsonl"}
    assert not (home / "sessions" / "scratch-9.jsonl").exists()


def test_a_two_way_sync_counts_an_excluded_file_once(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """Held back on the way up and the way down is still one file held back."""
    store = FakeObjectStore()
    store.objects["sessions/scratch-1.jsonl"] = b'{"turn": 9}\n'

    report = run_sync(
        store,
        direction=SyncDirection.BOTH,
        roots=roots,
        exclusions=ExclusionRules(patterns=("sessions/scratch-*",)),
    )

    assert report.excluded == {"sessions/scratch-1.jsonl"}
    assert store.objects["sessions/scratch-1.jsonl"] == b'{"turn": 9}\n', "not overwritten"


def test_an_excluded_file_already_in_the_store_is_left_alone(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """Excluding is not deleting: the engine never removes anything."""
    store = FakeObjectStore()
    store.objects["memory/notes.tmp"] = b"uploaded before the pattern existed\n"

    run_sync(store, roots=roots, exclusions=ExclusionRules(patterns=("*.tmp",)))

    assert store.objects["memory/notes.tmp"] == b"uploaded before the pattern existed\n"


def test_no_exclusions_moves_everything(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """The default has to behave exactly as it did before the feature existed."""
    store = FakeObjectStore()

    report = push(store, roots=roots)

    assert len(store.objects) == 4
    assert report.excluded == set()


# ── Visible in status ───────────────────────────────────────────────────────


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """Sync switched on against an in-memory store, with the given roots."""
    from config.constants import paths
    from config.constants.filestorage import REMOTE_SYNC_PROVIDER_ENV
    from infrastructure.filestorage import operations as sync_service
    from infrastructure.filestorage.providers.registry import register_object_store

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", home)
    register_object_store("excl-memory", lambda _cfg: FakeObjectStore())
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "excl-memory")
    monkeypatch.setattr(sync_service, "syncable_roots", lambda: roots)


@pytest.mark.usefixtures("configured")
def test_status_reports_the_patterns_and_what_they_hold_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A count per root is the only way to tell a live pattern from a typo."""
    from infrastructure.filestorage.messages import format_status_lines
    from infrastructure.filestorage.operations import get_sync_status

    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_ENV, "*.tmp,sessions/scratch-*")

    status = get_sync_status()

    assert status.exclusions.patterns == ("*.tmp", "sessions/scratch-*")
    counts = {str(root.name): root.excluded for root in status.roots}
    assert counts == {"sessions": 1, "memory": 1}

    lines = format_status_lines(status)
    assert "Excluded by your settings:" in lines
    assert "  *.tmp" in lines
    assert "  sessions/scratch-*" in lines
    assert any("1 excluded" in line for line in lines)


@pytest.mark.usefixtures("configured")
def test_status_says_so_when_nothing_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    from infrastructure.filestorage.messages import NO_EXCLUSIONS_HELP, format_status_lines
    from infrastructure.filestorage.operations import get_sync_status

    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_ENV, "")

    status = get_sync_status()

    assert bool(status.exclusions) is False
    assert all(root.excluded == 0 for root in status.roots)
    lines = format_status_lines(status)
    assert NO_EXCLUSIONS_HELP in lines
    assert not any("excluded" in line for line in lines if line.startswith("  "))


@pytest.mark.usefixtures("configured")
def test_the_slash_surface_shows_the_same_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The REPL and gateway reply must not drift from the CLI wording."""
    import io

    from rich.console import Console

    from surfaces.interactive_shell.command_registry.remote_sync_cmds import _print_status

    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_ENV, "*.tmp")
    buffer = io.StringIO()

    _print_status(Console(file=buffer, force_terminal=False, highlight=False, width=200))

    output = buffer.getvalue()
    assert "Excluded by your settings:" in output
    assert "*.tmp" in output
    assert "1 excluded" in output


@pytest.mark.usefixtures("configured")
def test_the_slash_surface_survives_bracket_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Character classes are ordinary fnmatch syntax and must not reach Rich markup raw.

    Unescaped, "[a-z]" is silently swallowed by Rich's markup parser and a
    literal "[/]" inside a pattern raises MarkupError, crashing this command on
    every surface it serves — gateway chat included.
    """
    import io

    from rich.console import Console

    from surfaces.interactive_shell.command_registry.remote_sync_cmds import _print_status

    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_ENV, "memory/[a-z]*.md,logs/[/]archive")
    buffer = io.StringIO()

    _print_status(Console(file=buffer, force_terminal=False, highlight=False, width=200))

    output = buffer.getvalue()
    assert "memory/[a-z]*.md" in output
    assert "logs/[/]archive" in output


def test_the_report_line_is_unchanged_when_nothing_is_excluded() -> None:
    """Existing surfaces assert this line exactly; the feature must not touch it."""
    from infrastructure.filestorage.engine import SyncReport
    from infrastructure.filestorage.messages import format_report_lines

    report = SyncReport(uploaded=["a"], downloaded=["b"], skipped=2)

    lines = format_report_lines(report)

    assert lines[0] == "Sync complete — 1 downloaded, 1 uploaded, 2 already current."
    assert len(lines) == 1


def test_the_report_names_how_many_were_held_back() -> None:
    from infrastructure.filestorage.engine import SyncReport
    from infrastructure.filestorage.messages import format_report_lines

    report = SyncReport(skipped=0, excluded={"memory/notes.tmp", "sessions/scratch-1.jsonl"})

    lines = format_report_lines(report)

    assert lines[0] == "Sync complete — 0 downloaded, 0 uploaded, 0 already current."
    assert lines[1] == "2 held back by your exclude settings."
