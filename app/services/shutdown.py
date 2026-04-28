"""Graceful SIGTERM drain for K8s pod termination.

On SIGTERM the load balancer has already been notified by the kubelet (via
/ready returning 503), but the ALB deregistration delay means connections
are still arriving for up to ``GRACEFUL_DRAIN_SECONDS``.  This helper:

1. Sets the ``_shutting_down`` flag in ``routes.health`` so ``/ready``
   flips to 503 immediately.
2. Sleeps ``GRACEFUL_DRAIN_SECONDS`` (default 30) in the lifespan finally
   block so the LB finishes draining before resource teardown begins.

Design note — uvicorn signal chaining:
  Uvicorn installs ``Server.handle_exit`` via ``signal.signal()`` before the
  ASGI lifespan starts.  We capture that handler and install our own via
  ``loop.add_signal_handler``, which supersedes it.  Our handler sets the
  drain flag, then schedules a call to uvicorn's handler on the event loop so
  that ``Server.should_exit`` is set and ``main_loop`` can exit, allowing the
  lifespan finally block to run the drain sleep before uvicorn tears down the
  server.

``graceful_lifespan`` is a thin wrapper that other modules (and tests) can
compose without importing the full ``main.py`` import chain.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from types import FrameType
from typing import Union

from fastapi import FastAPI

from routes.health import set_draining
from services.logging import get_logger

log = get_logger(__name__)

_SignalHandler = Union[
    Callable[[int, FrameType | None], None],
    int,
    signal.Handlers,
    None,
]


@asynccontextmanager
async def graceful_lifespan(
    app: FastAPI,
    startup: Callable[[], Awaitable[None]] | None = None,
    shutdown: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[None]:
    """Wrap an existing lifespan with SIGTERM-based graceful drain.

    Reads ``GRACEFUL_DRAIN_SECONDS`` (default 30).  On SIGTERM/SIGINT, sets
    the draining flag so ``/ready`` returns 503, then waits the drain window
    before running the wrapped shutdown coroutine.

    Args:
        app: FastAPI application instance (required by the @asynccontextmanager
             lifespan protocol but unused internally).
        startup: Optional coroutine called once before ``yield``.
        shutdown: Optional coroutine called once after the drain wait.
    """
    if startup is not None:
        await startup()

    loop = asyncio.get_running_loop()
    drain_seconds = int(os.environ.get("GRACEFUL_DRAIN_SECONDS", "30"))
    shutdown_event = asyncio.Event()

    # Capture uvicorn's handler (installed via signal.signal before lifespan
    # starts) so we can chain to it and allow uvicorn's shutdown to proceed.
    _prev: dict[signal.Signals, _SignalHandler] = {
        sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)
    }

    def _on_sigterm() -> None:
        log.info("SIGTERM received; draining", extra={"drain_seconds": drain_seconds})
        set_draining()
        shutdown_event.set()
        # Forward to uvicorn's handler so Server.should_exit is set and the
        # main_loop can exit, which allows the lifespan finally block to run.
        for sig in (signal.SIGTERM, signal.SIGINT):
            prev = _prev[sig]
            if callable(prev):
                prev(sig.value, None)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_sigterm)

    try:
        yield
    finally:
        if shutdown_event.is_set():
            log.info("drain wait started", extra={"drain_seconds": drain_seconds})
            await asyncio.sleep(drain_seconds)
            log.info("drain wait complete")
        if shutdown is not None:
            await shutdown()
