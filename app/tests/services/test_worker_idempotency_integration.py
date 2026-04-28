"""Smoke tests confirming idempotency decoration is applied to ARQ task functions.

These are import-time checks that do not require a running Postgres instance.
They verify that functools.wraps is applied (via __wrapped__ attribute) and that
the public task entry points are wrapped by the @idempotent decorator.
"""

from __future__ import annotations

import services.worker as worker_module


def test_import_job_is_wrapped() -> None:
    """import_job must carry __wrapped__ proving @idempotent was applied."""
    assert hasattr(worker_module.import_job, "__wrapped__"), (
        "import_job is not decorated with @idempotent (missing __wrapped__)"
    )


def test_cleanup_old_events_is_wrapped() -> None:
    assert hasattr(worker_module.cleanup_old_events, "__wrapped__"), (
        "cleanup_old_events is not decorated with @idempotent"
    )


def test_timeout_stale_jobs_is_wrapped() -> None:
    assert hasattr(worker_module.timeout_stale_jobs, "__wrapped__"), (
        "timeout_stale_jobs is not decorated with @idempotent"
    )


def test_cleanup_old_activity_logs_is_wrapped() -> None:
    assert hasattr(worker_module.cleanup_old_activity_logs, "__wrapped__"), (
        "cleanup_old_activity_logs is not decorated with @idempotent"
    )


def test_publish_log_is_not_wrapped() -> None:
    """publish_log is a utility, not a task — must NOT be decorated."""
    assert not hasattr(worker_module.publish_log, "__wrapped__"), (
        "publish_log should not be decorated with @idempotent"
    )


def test_on_startup_is_not_wrapped() -> None:
    """on_startup is a lifecycle hook — must NOT be decorated."""
    assert not hasattr(worker_module.on_startup, "__wrapped__"), (
        "on_startup should not be decorated with @idempotent"
    )


def test_on_shutdown_is_not_wrapped() -> None:
    """on_shutdown is a lifecycle hook — must NOT be decorated."""
    assert not hasattr(worker_module.on_shutdown, "__wrapped__"), (
        "on_shutdown should not be decorated with @idempotent"
    )
