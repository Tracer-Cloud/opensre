from __future__ import annotations

import os
from contextlib import contextmanager


@contextmanager
def temp_env(values: dict[str, str]):
    original = os.environ.copy()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)
