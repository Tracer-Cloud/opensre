"""
File-based logging utilities (stateless).

Generic application logging to local files using Python's standard logging module.
Used for capturing pipeline execution logs in demos/test cases.
"""

import logging
import os
import sys


def configure_file_logging(log_file: str, level: int = logging.INFO) -> None:
    """
    Configure Python logging to write to a file and stdout (stateless).

    Args:
        log_file: Path to log file
        level: Logging level (default: INFO)
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def tail_log_file(log_file: str, max_lines: int = 40, max_chars: int = 2000) -> str:
    """
    Read the tail of a log file (stateless).

    Args:
        log_file: Path to log file
        max_lines: Maximum number of lines to read from the end
        max_chars: Maximum characters to return

    Returns:
        Tail of log file as string, empty if file doesn't exist
    """
    if not os.path.exists(log_file):
        return ""

    with open(log_file, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()

    tail = "".join(lines[-max_lines:])
    if len(tail) > max_chars:
        return tail[-max_chars:]
    return tail
