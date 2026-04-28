"""Verify ARQ worker handles K8s SIGTERM gracefully.

K8s sends SIGTERM at the start of `terminationGracePeriodSeconds` (300s in the
chart Deployment). ARQ's default ``handle_sig`` cancels in-flight tasks
immediately on SIGTERM; the K8s-friendly behavior requires
``job_completion_wait`` to be set, which switches the worker to
``handle_sig_wait_for_completion`` — that handler stops picking new jobs and
waits up to ``job_completion_wait`` seconds for the in-flight task to finish
before exiting.

This test spawns a real ARQ worker subprocess pointing at a real Valkey,
enqueues a job that sleeps a few seconds and writes a sentinel on completion,
sends SIGTERM mid-flight, and asserts the worker exited 0 with the sentinel
written. It is the regression guard for ``WorkerSettings.job_completion_wait``.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from arq import create_pool
from arq.connections import RedisSettings


VALKEY_HOST = os.environ.get("VALKEY_HOST", "localhost")
VALKEY_PORT = int(os.environ.get("VALKEY_PORT", "6379"))


def _valkey_reachable() -> bool:
    import socket

    try:
        with socket.create_connection((VALKEY_HOST, VALKEY_PORT), timeout=1.0):
            return True
    except OSError:
        return False


WORKER_SCRIPT = textwrap.dedent(
    """
    from __future__ import annotations

    import asyncio
    import os
    import sys
    from typing import Any

    from arq import run_worker
    from arq.connections import RedisSettings


    SENTINEL_PATH = os.environ["SIGTERM_TEST_SENTINEL"]
    SLEEP_SECS = float(os.environ.get("SIGTERM_TEST_SLEEP", "8"))
    REDIS_HOST = os.environ.get("SIGTERM_TEST_REDIS_HOST", "localhost")
    REDIS_PORT = int(os.environ.get("SIGTERM_TEST_REDIS_PORT", "6379"))
    QUEUE_NAME = os.environ.get("SIGTERM_TEST_QUEUE", "arq:queue")


    async def sleep_job(ctx: dict[str, Any]) -> str:
        await asyncio.sleep(SLEEP_SECS)
        with open(SENTINEL_PATH, "w") as fh:
            fh.write("done")
        return "ok"


    class WorkerSettings:
        functions = [sleep_job]
        redis_settings = RedisSettings(host=REDIS_HOST, port=REDIS_PORT)
        queue_name = QUEUE_NAME
        job_completion_wait = 60
        max_jobs = 1
        job_timeout = 120


    if __name__ == "__main__":
        run_worker(WorkerSettings)
    """
).strip()


async def _enqueue_sleep_job(host: str, port: int, queue_name: str) -> None:
    pool = await create_pool(RedisSettings(host=host, port=port))
    try:
        await pool.enqueue_job("sleep_job", _queue_name=queue_name)
    finally:
        await pool.close(close_connection_pool=True)


@pytest.mark.slow
@pytest.mark.skipif(
    not _valkey_reachable(),
    reason=f"Valkey not reachable at {VALKEY_HOST}:{VALKEY_PORT}",
)
def test_worker_sigterm_completes_in_flight_job(tmp_path: Path) -> None:
    """Worker must finish the in-flight sleep job after SIGTERM and exit 0."""
    host, port = VALKEY_HOST, VALKEY_PORT
    sentinel = tmp_path / "sigterm_sentinel"
    queue_name = f"arq:test:sigterm:{os.getpid()}"

    script_path = tmp_path / "_worker.py"
    script_path.write_text(WORKER_SCRIPT)

    asyncio.run(_enqueue_sleep_job(host, port, queue_name))

    env = {
        **os.environ,
        "SIGTERM_TEST_SENTINEL": str(sentinel),
        "SIGTERM_TEST_SLEEP": "8",
        "SIGTERM_TEST_REDIS_HOST": host,
        "SIGTERM_TEST_REDIS_PORT": str(port),
        "SIGTERM_TEST_QUEUE": queue_name,
        "PYTHONUNBUFFERED": "1",
    }

    proc = subprocess.Popen(
        [sys.executable, str(script_path)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Own process group so SIGTERM goes only to our worker.
        start_new_session=True,
    )

    try:
        # Wait for worker to pick up and start the job. 8s sleep > 3s buffer.
        time.sleep(3.0)
        assert proc.poll() is None, (
            "worker exited before SIGTERM was sent; output:\n"
            + (proc.stdout.read().decode() if proc.stdout else "")
        )
        proc.send_signal(signal.SIGTERM)

        # job_completion_wait=60 ; sleep is 8s ; allow generous total budget.
        try:
            stdout, _ = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            pytest.fail("worker did not exit within 60s of SIGTERM")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    output = stdout.decode(errors="replace")
    assert proc.returncode == 0, (
        f"worker exit code {proc.returncode} != 0\n--- output ---\n{output}"
    )
    assert sentinel.exists(), (
        f"sleep_job did not write sentinel — job was cancelled, not completed.\n"
        f"--- worker output ---\n{output}"
    )
    assert sentinel.read_text() == "done"


def test_production_worker_settings_set_job_completion_wait() -> None:
    """Regression guard: the production WorkerSettings must enable graceful
    SIGTERM handling. Without ``job_completion_wait > 0`` ARQ falls back to
    its default ``handle_sig`` which cancels in-flight jobs immediately and
    breaks the K8s graceful-shutdown contract.
    """
    from services.worker import WorkerSettings

    wait = getattr(WorkerSettings, "job_completion_wait", 0)
    assert wait and wait > 0, (
        "WorkerSettings.job_completion_wait must be > 0 for K8s graceful "
        "shutdown — see app/services/worker.py"
    )
    # Must leave headroom under terminationGracePeriodSeconds=300 so the worker
    # can finish writing results before kubelet escalates to SIGKILL.
    assert wait < 300, (
        "job_completion_wait must be < terminationGracePeriodSeconds (300s) "
        "to leave headroom for clean exit"
    )
