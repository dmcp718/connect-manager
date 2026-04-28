"""Subprocess-based integration test for SIGTERM graceful drain.

Design notes:
- Port 18765 is hardcoded to avoid the port-0 / discovery problem.
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
import subprocess
import sys
import textwrap
import time

import httpx
import pytest

TEST_PORT = 18765
DRAIN_SECONDS = 3
STARTUP_TIMEOUT = 10.0
DRAIN_FLIP_TIMEOUT = 2.0
PROCESS_EXIT_TIMEOUT = DRAIN_SECONDS + 5


APP_SRC = textwrap.dedent(
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
        uvicorn.run(app, host="127.0.0.1", port={TEST_PORT}, log_level="warning")
    """
)


def _wait_for_ready(timeout: float = STARTUP_TIMEOUT) -> None:
    """Poll /health until the server responds 200 or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{TEST_PORT}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise TimeoutError(f"Server did not start within {timeout}s")


def _poll_until_503(timeout: float = DRAIN_FLIP_TIMEOUT) -> None:
    """Poll /ready until it returns 503 or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{TEST_PORT}/ready", timeout=1.0)
            if r.status_code == 503:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"/ready did not flip to 503 within {timeout}s")


@pytest.fixture()
def app_process(tmp_path):  # type: ignore[no-untyped-def]
    """Start the minimal health-only FastAPI app as a subprocess."""
    app_file = tmp_path / "drain_test_app.py"
    app_file.write_text(APP_SRC)

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
        _wait_for_ready()
    except TimeoutError:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(
            f"Server failed to start.\nstdout: {stdout.decode()}\nstderr: {stderr.decode()}"
        )

    yield proc

    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


def test_ready_returns_200_before_sigterm(app_process: subprocess.Popen) -> None:  # type: ignore[type-arg]
    r = httpx.get(f"http://127.0.0.1:{TEST_PORT}/ready", timeout=2.0)
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "not_ready")


def test_health_returns_200_unconditionally(app_process: subprocess.Popen) -> None:  # type: ignore[type-arg]
    r = httpx.get(f"http://127.0.0.1:{TEST_PORT}/health", timeout=2.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_flips_to_503_on_sigterm(app_process: subprocess.Popen) -> None:  # type: ignore[type-arg]
    r_before = httpx.get(f"http://127.0.0.1:{TEST_PORT}/ready", timeout=2.0)
    assert r_before.status_code in (200, 503)

    app_process.send_signal(signal.SIGTERM)

    _poll_until_503(timeout=DRAIN_FLIP_TIMEOUT)

    r_after = httpx.get(f"http://127.0.0.1:{TEST_PORT}/ready", timeout=2.0)
    assert r_after.status_code == 503
    assert r_after.json() == {"status": "draining"}


def test_health_stays_200_during_drain(app_process: subprocess.Popen) -> None:  # type: ignore[type-arg]
    app_process.send_signal(signal.SIGTERM)

    _poll_until_503(timeout=DRAIN_FLIP_TIMEOUT)

    r = httpx.get(f"http://127.0.0.1:{TEST_PORT}/health", timeout=2.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_process_exits_within_drain_window(app_process: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """Process must exit ≤ DRAIN_SECONDS + 5s after SIGTERM."""
    app_process.send_signal(signal.SIGTERM)
    _poll_until_503(timeout=DRAIN_FLIP_TIMEOUT)

    try:
        exit_code = app_process.wait(timeout=PROCESS_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        app_process.kill()
        pytest.fail(
            f"Process did not exit within {PROCESS_EXIT_TIMEOUT}s after SIGTERM"
        )

    assert exit_code is not None
