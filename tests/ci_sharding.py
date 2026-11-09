"""Pre-collection CI sharding and per-file timing snapshots."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

DEFAULT_TIMINGS_PATH = Path(".github/ci/pytest-file-durations.json")
DEFAULT_MAX_SNAPSHOT_AGE_DAYS = 14.0
DEFAULT_MAX_MISSING_PERCENT = 5.0
_TEST_FILE_PATTERNS = ("test_*.py", "*_test.py")
_duration_seconds: defaultdict[str, float] = defaultdict(float)
_duration_nodeids: defaultdict[str, set[str]] = defaultdict(set)


@dataclass(frozen=True, slots=True)
class FileTiming:
    """Recorded aggregate duration and test count for one test file."""

    seconds: float
    tests: int


def assign_file_groups(
    file_paths: Collection[str],
    timings: Mapping[str, FileTiming],
    splits: int,
) -> dict[str, int]:
    """Assign every test file to a one-based least-duration group."""
    if splits < 1:
        raise ValueError("splits must be at least one")

    recorded_seconds = [timings[path].seconds for path in file_paths if path in timings]
    default_file_seconds = (
        sum(recorded_seconds) / len(recorded_seconds) if recorded_seconds else 1.0
    )
    estimates = {
        path: timings.get(path, FileTiming(default_file_seconds, 0)).seconds for path in file_paths
    }

    totals = [0.0] * splits
    assignments: dict[str, int] = {}
    for path in sorted(estimates, key=lambda item: (-estimates[item], item)):
        group_index = min(range(splits), key=lambda index: (totals[index], index))
        assignments[path] = group_index + 1
        totals[group_index] += estimates[path]
    return assignments


def discover_test_files(roots: Sequence[Path], ignored: Sequence[Path] = ()) -> list[str]:
    """Return test files below existing roots without importing or collecting them."""
    repository_root = Path.cwd().resolve()
    ignored_paths = [_repository_path(path, repository_root) for path in ignored]
    files: set[str] = set()

    for root in roots:
        absolute_root = _repository_path(root, repository_root)
        if not absolute_root.exists():
            raise FileNotFoundError(f"pytest path does not exist: {root}")
        candidates = [absolute_root] if absolute_root.is_file() else absolute_root.rglob("*.py")
        for candidate in candidates:
            if any(candidate == item or item in candidate.parents for item in ignored_paths):
                continue
            if any(candidate.match(pattern) for pattern in _TEST_FILE_PATTERNS):
                files.add(candidate.relative_to(repository_root).as_posix())

    return sorted(files)


def merge_timing_files(
    base: Mapping[str, FileTiming], paths: Sequence[Path]
) -> dict[str, FileTiming]:
    """Merge shard timing files, summing files split by test expression."""
    fresh_seconds: defaultdict[str, float] = defaultdict(float)
    fresh_tests: defaultdict[str, int] = defaultdict(int)
    for path in sorted(paths):
        for file_path, timing in _load_timings(path).items():
            fresh_seconds[file_path] += timing.seconds
            fresh_tests[file_path] += timing.tests

    merged = dict(base)
    merged.update(
        {
            path: FileTiming(seconds=seconds, tests=fresh_tests[path])
            for path, seconds in fresh_seconds.items()
        }
    )
    return merged


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the CI timing output option."""
    group = parser.getgroup("OpenSRE CI sharding")
    group.addoption("--ci-durations-output", type=Path)


def pytest_configure(config: pytest.Config) -> None:
    """Reset timing state for this pytest process."""
    _duration_seconds.clear()
    _duration_nodeids.clear()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Aggregate every test phase by repository-relative file."""
    file_path = report.nodeid.split("::", maxsplit=1)[0]
    _duration_seconds[file_path] += report.duration
    _duration_nodeids[file_path].add(report.nodeid)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write one timing fragment from the xdist controller."""
    del exitstatus
    config = session.config
    output = config.getoption("--ci-durations-output")
    if not isinstance(output, Path) or hasattr(config, "workerinput"):
        return
    timings = {
        path: FileTiming(seconds=_duration_seconds[path], tests=len(nodeids))
        for path, nodeids in _duration_nodeids.items()
    }
    _write_timings(output, timings)


def _repository_path(path: Path, repository_root: Path) -> Path:
    absolute = path.resolve() if path.is_absolute() else repository_root.joinpath(path).resolve()
    try:
        absolute.relative_to(repository_root)
    except ValueError as exc:
        raise ValueError(f"path is outside the repository: {path}") from exc
    return absolute


def _load_timings(path: Path) -> dict[str, FileTiming]:
    """Load a timing snapshot or shard fragment."""
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"CI durations file must be an object: {path}")
    timings: dict[str, FileTiming] = {}
    for file_path, value in raw.items():
        if not isinstance(file_path, str) or not isinstance(value, dict):
            raise ValueError(f"Invalid CI duration entry in {path}")
        seconds = value.get("seconds")
        tests = value.get("tests")
        if (
            not isinstance(seconds, int | float)
            or isinstance(seconds, bool)
            or seconds < 0
            or not isinstance(tests, int)
            or isinstance(tests, bool)
            or tests < 0
        ):
            raise ValueError(f"Invalid CI duration entry for {file_path}")
        timings[file_path] = FileTiming(seconds=float(seconds), tests=tests)
    return timings


def _write_timings(path: Path, timings: Mapping[str, FileTiming]) -> None:
    payload = {
        file_path: {"seconds": round(timing.seconds, 3), "tests": timing.tests}
        for file_path, timing in sorted(timings.items())
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _snapshot_age_days(path: Path) -> float | None:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", path.as_posix()],
        check=False,
        capture_output=True,
        text=True,
    )
    timestamp = result.stdout.strip()
    if result.returncode != 0 or not timestamp.isdigit():
        return None
    updated = datetime.fromtimestamp(int(timestamp), tz=UTC)
    return (datetime.now(tz=UTC) - updated).total_seconds() / 86_400


def _select(args: argparse.Namespace) -> int:
    files = discover_test_files(args.paths, args.ignore)
    assignments = assign_file_groups(files, _load_timings(args.timings), args.splits)
    if not 1 <= args.group <= args.splits:
        raise ValueError("group must be between one and splits")
    selected = [path for path in files if assignments[path] == args.group]
    if not selected:
        raise ValueError(f"CI shard {args.group}/{args.splits} selected no test files")
    args.output.write_text("\n".join(selected) + "\n", encoding="utf-8")
    print(f"selected {len(selected)}/{len(files)} files for shard {args.group}/{args.splits}")
    return 0


def _report(args: argparse.Namespace) -> int:
    files = discover_test_files(args.paths, args.ignore)
    timings = _load_timings(args.timings)
    missing = sorted(set(files) - timings.keys())
    missing_percent = (100.0 * len(missing) / len(files)) if files else 0.0
    age_days = _snapshot_age_days(args.timings)
    age = "unknown" if age_days is None else f"{age_days:.1f} days"
    summary = (
        f"{args.label}: age {age} (limit {args.max_age_days:g}); "
        f"missing {len(missing)}/{len(files)} files "
        f"({missing_percent:.1f}%, limit {args.max_missing_percent:g}%)"
    )
    print(summary)
    if age_days is None or age_days > args.max_age_days:
        print(f"::warning title=Stale pytest timing snapshot::{summary}")
    if missing_percent > args.max_missing_percent:
        print(f"::warning title=Incomplete pytest timing snapshot::{summary}")
    return 0


def _merge(args: argparse.Namespace) -> int:
    _write_timings(args.output, merge_timing_files(_load_timings(args.base), args.fragments))
    print(f"wrote merged timing snapshot to {args.output}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    select = commands.add_parser("select", help="write the file list for one shard")
    select.add_argument("paths", nargs="+", type=Path)
    select.add_argument("--ignore", action="append", default=[], type=Path)
    select.add_argument("--splits", required=True, type=int)
    select.add_argument("--group", required=True, type=int)
    select.add_argument("--timings", default=DEFAULT_TIMINGS_PATH, type=Path)
    select.add_argument("--output", required=True, type=Path)
    select.set_defaults(handler=_select)

    report = commands.add_parser("report", help="report snapshot freshness and coverage")
    report.add_argument("paths", nargs="+", type=Path)
    report.add_argument("--ignore", action="append", default=[], type=Path)
    report.add_argument("--timings", default=DEFAULT_TIMINGS_PATH, type=Path)
    report.add_argument("--label", required=True)
    report.add_argument("--max-age-days", default=DEFAULT_MAX_SNAPSHOT_AGE_DAYS, type=float)
    report.add_argument("--max-missing-percent", default=DEFAULT_MAX_MISSING_PERCENT, type=float)
    report.set_defaults(handler=_report)

    merge = commands.add_parser("merge", help="merge timing fragments into a snapshot")
    merge.add_argument("fragments", nargs="+", type=Path)
    merge.add_argument("--base", default=DEFAULT_TIMINGS_PATH, type=Path)
    merge.add_argument("--output", required=True, type=Path)
    merge.set_defaults(handler=_merge)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CI sharding utility."""
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
