"""Contracts for duration-balanced pull-request test sharding."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from tests.ci_sharding import (
    FileTiming,
    assign_file_groups,
    discover_test_files,
    merge_timing_files,
)


def test_slow_files_are_balanced_across_groups() -> None:
    counts = {f"tests/test_{index}.py": 10 for index in range(6)}
    timings = {
        path: FileTiming(seconds=seconds, tests=10)
        for path, seconds in zip(counts, (30.0, 29.0, 20.0, 19.0, 10.0, 9.0))
    }

    assignments = assign_file_groups(counts, timings, splits=3)

    totals: dict[int, float] = defaultdict(float)
    for path, group in assignments.items():
        totals[group] += timings[path].seconds
    assert set(assignments) == set(counts)
    assert set(assignments.values()) == {1, 2, 3}
    assert max(totals.values()) - min(totals.values()) <= 1.0


def test_new_files_use_the_recorded_per_file_average() -> None:
    files = {"tests/slow.py", "tests/fast.py", "tests/new.py"}
    timings = {
        "tests/slow.py": FileTiming(seconds=8.0, tests=2),
        "tests/fast.py": FileTiming(seconds=2.0, tests=20),
    }

    assignments = assign_file_groups(files, timings, splits=2)

    assert assignments == {
        "tests/slow.py": 1,
        "tests/new.py": 2,
        "tests/fast.py": 2,
    }


def test_precollection_discovery_assigns_every_eligible_file_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests = tmp_path / "tests"
    ignored = tests / "ignored"
    ignored.mkdir(parents=True)
    (tests / "test_one.py").touch()
    (tests / "helper.py").touch()
    (ignored / "two_test.py").touch()
    (ignored / "test_three.py").touch()
    monkeypatch.chdir(tmp_path)

    files = discover_test_files([Path("tests")], [Path("tests/ignored/two_test.py")])
    assignments = assign_file_groups(files, {}, splits=2)

    assert files == ["tests/ignored/test_three.py", "tests/test_one.py"]
    assert set(assignments) == set(files)
    assert sorted(assignments.values()) == [1, 2]


def test_precollection_discovery_rejects_missing_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError, match="pytest path does not exist"):
        discover_test_files([Path("tests/missing")])


def test_merge_sums_split_file_fragments_and_retains_unobserved_base(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    first.write_text(
        '{"tests/test_split.py": {"seconds": 1.25, "tests": 2}}',
        encoding="utf-8",
    )
    second = tmp_path / "second.json"
    second.write_text(
        '{"tests/test_split.py": {"seconds": 2.75, "tests": 3}}',
        encoding="utf-8",
    )
    base = {"tests/test_skipped.py": FileTiming(seconds=4.0, tests=1)}

    merged = merge_timing_files(base, [second, first])

    assert merged == {
        "tests/test_skipped.py": FileTiming(seconds=4.0, tests=1),
        "tests/test_split.py": FileTiming(seconds=4.0, tests=5),
    }
