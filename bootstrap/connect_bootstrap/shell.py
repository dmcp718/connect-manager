"""Subprocess helpers — capture, live-stream, with timeout + cancellation."""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator, Optional, Sequence


async def run_capture(
    argv: Sequence[str],
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    """Run a command, wait for completion, return (exit_code, stdout, stderr).

    Raises asyncio.TimeoutError on timeout.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env={**os.environ, **(env or {})},
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


async def run_stream(
    argv: Sequence[str],
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> AsyncIterator[tuple[str, str]]:
    """Run a command, yielding ('stdout'|'stderr', line) tuples until exit.

    The terminating tuple is ('exit', str(exit_code)).
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env={**os.environ, **(env or {})},
    )

    async def _drain(stream: asyncio.StreamReader, label: str, q: asyncio.Queue) -> None:
        async for line in stream:
            await q.put((label, line.decode(errors="replace").rstrip("\n")))
        await q.put((label, None))  # sentinel for end-of-stream

    q: asyncio.Queue = asyncio.Queue()
    assert proc.stdout is not None and proc.stderr is not None
    t_out = asyncio.create_task(_drain(proc.stdout, "stdout", q))
    t_err = asyncio.create_task(_drain(proc.stderr, "stderr", q))

    open_streams = 2
    while open_streams:
        label, line = await q.get()
        if line is None:
            open_streams -= 1
            continue
        yield label, line

    await asyncio.gather(t_out, t_err)
    rc = await proc.wait()
    yield "exit", str(rc)
