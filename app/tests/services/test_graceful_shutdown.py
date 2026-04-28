"""Subprocess-based integration test for SIGTERM graceful drain.

Design notes:
- A fresh free port is picked per fixture run via socket.bind((host, 0)) so
  consecutive tests don't collide on a TIME_WAIT / not-yet-released bind from
  the previous subprocess. This is the awsk-ap6 fix — the prior hardcoded
  port (18765) was reliably reused by the OS but flaked when subprocess
  teardown raced the next subprocess startup.
- GRACEFUL_DRAIN_SECONDS=3 keeps wall-clock time to ~5-8s total.
- We spin up a minimal FastAPI app that only mounts the health router
  and uses ``graceful_lifespan`` — no database, no job queue, no auth.
  This avoids the broken-db.* import chain in main.py (awsk-opc territory).
- The test process runs in the ``app/`` directory so that relative imports
  (``routes.health``, ``services.shutdown``) resolve correctly via a
  ``-c`` heredoc passed to ``python -c``.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import textwrap
import time

import httpx
import pytest

DRAIN_SECONDS = 3
STARTUP_TIMEOUT = 10.0
DRAIN_FLIP_TIMEOUT = 2.0
PROCESS_EXIT_TIMEOUT = DRAIN_SECONDS + 5


def _find_free_port() -> int:
    """Return an OS-assigned free port. There's a TOCTOU window between
    close() and uvicorn's bind, but it's small enough that this is the
    standard pattern for ephemeral test servers.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _build_app_src(port: int) -> str:
    return textwrap.dedent(
        f"""\
        import asyncio
        import os
        import sys

        os.environ.setdefault("GRACEFUL_DRAIN_SECONDS", "{DRAIN_SECONDS}")

        from fastapi import FastAPI
        from routes.health import router as health_router
        from services.shutdown import graceful_lifespan

        async def _noop_startup() -> None:
            pass

        async def _noop_shutdown() -> None:
            pass

        async def lifespan(app: FastAPI):  # type: ignore[override]
            async with graceful_lifespan(app, startup=_noop_startup, shutdown=_noop_shutdown):
                yield

        app = FastAPI(lifespan=lifespan)
        app.include_router(health_router)

        if __name__ == "__main__":
            import uvicorn
            uvicorn.run(app, host="127.0.0.1", port={port}, log_level="warning")
        """
    )


def _wait_for_ready(port: int, timeout: float = STARTUP_TIMEOUT) -> None:
    """Poll /health until the server responds 200 or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise TimeoutError(f"Server did not start within {timeout}s")


def _poll_until_503(port: int, timeout: float = DRAIN_FLIP_TIMEOUT) -> None:
    """Poll /ready until it returns 503 or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/ready", timeout=1.0)
            if r.status_code == 503:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"/ready did not flip to 503 within {timeout}s")


@pytest.fixture()
def app_process(tmp_path):  # type: ignore[no-untyped-def]
    """Start the minimal health-only FastAPI app as a subprocess.

    Yields (proc, port) so tests can target the port the OS picked for this
    specific run.
    """
    port = _find_free_port()
    app_file = tmp_path / "drain_test_app.py"
    app_file.write_text(_build_app_src(port))

    app_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    app_dir = os.path.abspath(app_dir)

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = f"{app_dir}:{existing_pythonpath}" if existing_pythonpath else app_dir
    env = {
        **os.environ,
        "GRACEFUL_DRAIN_SECONDS": str(DRAIN_SECONDS),
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "LOG_FORMAT": "text",
        "PYTHONPATH": pythonpath,
    }

    proc = subprocess.Popen(
        [sys.executable, str(app_file)],
        cwd=app_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_ready(port)
    except TimeoutError:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(
            f"Server failed to start.\nstdout: {stdout.decode()}\nstderr: {stderr.decode()}"
        )

    yield proc, port

    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


def test_ready_returns_200_before_sigterm(app_process) -> None:  # type: ignore[no-untyped-def]
    _, port = app_process
    r = httpx.get(f"http://127.0.0.1:{port}/ready", timeout=2.0)
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "not_ready")


def test_health_returns_200_unconditionally(app_process) -> None:  # type: ignore[no-untyped-def]
    _, port = app_process
    r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_flips_to_503_on_sigterm(app_process) -> None:  # type: ignore[no-untyped-def]
    proc, port = app_process
    r_before = httpx.get(f"http://127.0.0.1:{port}/ready", timeout=2.0)
    assert r_before.status_code in (200, 503)

    proc.send_signal(signal.SIGTERM)

    _poll_until_503(port, timeout=DRAIN_FLIP_TIMEOUT)

    r_after = httpx.get(f"http://127.0.0.1:{port}/ready", timeout=2.0)
    assert r_after.status_code == 503
    assert r_after.json() == {"status": "draining"}


def test_health_stays_200_during_drain(app_process) -> None:  # type: ignore[no-untyped-def]
    proc, port = app_process
    proc.send_signal(signal.SIGTERM)

    _poll_until_503(port, timeout=DRAIN_FLIP_TIMEOUT)

    r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_process_exits_within_drain_window(app_process) -> None:  # type: ignore[no-untyped-def]
    """Process must exit ≤ DRAIN_SECONDS + 5s after SIGTERM."""
    proc, port = app_process
    proc.send_signal(signal.SIGTERM)
    _poll_until_503(port, timeout=DRAIN_FLIP_TIMEOUT)

    try:
        exit_code = proc.wait(timeout=PROCESS_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail(
            f"Process did not exit within {PROCESS_EXIT_TIMEOUT}s after SIGTERM"
        )

    assert exit_code is not None
