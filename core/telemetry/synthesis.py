"""One killable synthesis process shared by telemetry report candidates."""

from __future__ import annotations

import logging
import multiprocessing
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SynthesisResult:
    status: Literal["ok", "model_unavailable", "deadline", "invalid_report"]
    content: str = ""


def synthesize(
    payload: str,
    provider: str,
    worker: Callable[[Connection, str, str], None],
    *,
    timeout: float,
) -> SynthesisResult:
    """Stop and reap the worker before returning, including on interruption."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(send, payload, provider), daemon=True)
    result = SynthesisResult("model_unavailable")
    try:
        process.start()
        send.close()
        if receive.poll(timeout):
            try:
                content = receive.recv()
                if isinstance(content, SynthesisResult):
                    result = content
                elif isinstance(content, str) and content.strip():
                    result = SynthesisResult("ok", content)
            except EOFError:
                logger.warning("Telemetry model process exited without a report")
        else:
            result = SynthesisResult("deadline")
    finally:
        send.close()
        receive.close()
        if process.pid is not None:
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
    return result
