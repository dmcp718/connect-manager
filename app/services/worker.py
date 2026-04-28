"""ARQ Worker for Import Jobs.

Parallel job processing with Valkey/Redis backend.
DataStore-centric credential lookup.
SQS polling for event-driven imports.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional

from arq import cron
from arq.connections import RedisSettings

from db.repositories.job import JobRepository
from services.database import get_sessionmaker, shutdown_engine
from services.idempotency import idempotent
from services.lucidlink import LucidLinkClient
from services.metrics import instrumented
from services.s3_service import S3Service
from services import secrets
from services.sqs_poller import sqs_poll_cron
from services.activity_logger import ActivityLogger


def get_redis_settings() -> RedisSettings:
    return RedisSettings(
        host=os.getenv("VALKEY_HOST", "localhost"),
        port=int(os.getenv("VALKEY_PORT", 6379)),
    )


LOG_CHANNEL = "worker:logs"


async def publish_log(ctx: dict[str, Any], message: str) -> None:
    """Publish log message to Valkey for web UI consumption."""
    redis = ctx.get("redis")
    if redis:
        await redis.publish(LOG_CHANNEL, json.dumps({"message": message}))


async def _get_job_dict(ctx: dict[str, Any], job_id: int) -> Optional[dict[str, Any]]:
    """Fetch the job row and return it as a plain dict, or None if missing."""
    sm = ctx["sessionmaker"]
    async with sm() as session:
        repo = JobRepository(session)
        row = await repo.get(job_id)
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "bucket": row.bucket,
        "prefix": row.prefix,
        "filespace_id": row.filespace_id,
        "datastore_id": row.datastore_id,
        "user_id": str(row.user_id) if row.user_id else None,
    }


@instrumented
@idempotent
async def import_job(ctx: dict[str, Any], job_id: int) -> dict[str, Any]:
    """Process an import job."""

    async def log(msg: str) -> None:
        await publish_log(ctx, msg)

    sm = ctx["sessionmaker"]

    job = await _get_job_dict(ctx, job_id)
    if job is None:
        await log(f"Job #{job_id} not found")
        return {"status": "error", "message": f"Job {job_id} not found"}

    async with sm() as session:
        repo = JobRepository(session)
        await repo.update_status(job_id, status="running")
        await session.commit()

    await log(f"Job #{job_id} started: {job['prefix']}")

    user_id: Optional[str] = job.get("user_id")
    ActivityLogger.job_started(user_id, job_id, job["prefix"], total_files=0)

    ll_client: Optional[LucidLinkClient] = None
    try:
        if user_id:
            token = secrets.get_user_token(user_id)
        else:
            token = secrets.get_lucidlink_token()
        if not token:
            raise ValueError("No API token available - please reconnect in web UI")

        # awsk-opc: get_datastore_credentials pending repo conversion
        raise RuntimeError(
            "awsk-opc: get_datastore_credentials / get_setting pending repo conversion"
        )

    except Exception as e:
        await log(f"Job #{job_id} failed: {e}")
        async with sm() as session:
            repo = JobRepository(session)
            await repo.update_status(job_id, status="failed", error_message=str(e))
            await session.commit()

        ActivityLogger.job_failed(user_id, job_id, str(e))
        return {"status": "failed", "message": str(e)}
    finally:
        if ll_client:
            await ll_client.close()


async def _import_job_body(
    ctx: dict[str, Any],
    job: dict[str, Any],
    job_id: int,
    aws_key: str,
    aws_secret: str,
    s3_endpoint: Optional[str],
    s3_region: str,
    api_host: str,
    user_id: Optional[str],
    ll_client: LucidLinkClient,
) -> dict[str, Any]:
    """Inner body of import_job; called once credentials are resolved.

    Kept separate so the credential-fetch stubs above do not create unreachable
    code.  Wired up when awsk-opc lands.
    """

    async def log(msg: str) -> None:
        await publish_log(ctx, msg)

    sm = ctx["sessionmaker"]

    s3_service = S3Service(
        access_key=aws_key,
        secret_key=aws_secret,
        region=s3_region,
        endpoint_url=s3_endpoint,
    )

    await log(f"Using filespace: {job['filespace_id'][:8]}...")

    ll_client.configure(
        token="",  # resolved by caller
        filespace_id=job["filespace_id"],
        datastore_id=job["datastore_id"],
        api_host=api_host,
    )

    await log("Scanning folder...")
    keys = await s3_service.list_all_objects(job["bucket"], job["prefix"])
    total = len(keys)

    if total == 0:
        await log("No files found")
        async with sm() as session:
            repo = JobRepository(session)
            await repo.update_status(job_id, status="completed", total_files=0)
            await session.commit()
        return {"status": "completed", "total": 0}

    async with sm() as session:
        repo = JobRepository(session)
        await repo.update_status(job_id, status="running", total_files=total)
        await session.commit()
    await log(f"Found {total} files")

    bucket_name = job["bucket"]
    unique_dirs: set[str] = set()
    unique_dirs.add(bucket_name)
    for k in keys:
        d = os.path.dirname(k)
        if d:
            unique_dirs.add(f"{bucket_name}/{d}")

    sorted_dirs = sorted(list(unique_dirs), key=len)
    await log(f"Creating {len(sorted_dirs)} directories...")

    for d in sorted_dirs:
        success, error = await ll_client.ensure_structure(d + "/dummy_file")
        if not success:
            await log(f"Failed to create folder {d}: {error}")

    async with sm() as session:
        repo = JobRepository(session)
        current = await repo.get(job_id)
    if current and current.status == "cancelled":
        await log(f"Job #{job_id} cancelled")
        return {"status": "cancelled"}

    await log("Importing files...")
    completed = 0
    failed = 0
    total_new = 0
    total_skipped = 0
    batch_size = 25

    for i in range(0, len(keys), batch_size):
        async with sm() as session:
            repo = JobRepository(session)
            current = await repo.get(job_id)
        if current and current.status == "cancelled":
            break

        batch = keys[i : i + batch_size]
        tasks = [ll_client.import_file(key, f"/{bucket_name}/{key}") for key in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        batch_new = 0
        batch_skipped = 0
        batch_failed = 0
        first_error: Optional[str] = None
        for key, result in zip(batch, results):
            if isinstance(result, Exception):
                batch_failed += 1
                if not first_error:
                    first_error = f"Exception: {result}"
            else:
                code, error_msg = result  # type: ignore[misc]
                if code in [200, 201]:
                    batch_new += 1
                elif code == 409:
                    batch_skipped += 1
                elif code == 400 and "already exists" in error_msg.lower():
                    batch_skipped += 1
                else:
                    batch_failed += 1
                    if not first_error:
                        first_error = f"HTTP {code}: {error_msg[:200]}"

        total_new += batch_new
        total_skipped += batch_skipped
        completed += batch_new + batch_skipped
        failed += batch_failed

        progress_pct = int((completed + failed) / total * 100)
        if first_error and batch_failed > 0:
            await log(
                f"Progress: {completed + failed}/{total} ({progress_pct}%) "
                f"- {total_new} new, {total_skipped} skipped, {failed} errors: {first_error}"
            )
        else:
            await log(
                f"Progress: {completed + failed}/{total} ({progress_pct}%) "
                f"- {total_new} new, {total_skipped} skipped, {failed} errors"
            )

        async with sm() as session:
            repo = JobRepository(session)
            await repo.update_status(
                job_id,
                status="running",
                completed_files=completed,
                failed_files=failed,
            )
            await session.commit()

        await asyncio.sleep(0.05)

    async with sm() as session:
        repo = JobRepository(session)
        current = await repo.get(job_id)

    if current and current.status == "cancelled":
        await log(f"Job #{job_id} cancelled ({completed}/{total} files)")
        ActivityLogger.job_cancelled(user_id, job_id)
        return {"status": "cancelled", "completed": completed, "failed": failed}

    async with sm() as session:
        repo = JobRepository(session)
        await repo.update_status(job_id, status="completed")
        await session.commit()

    await log(
        f"Job #{job_id} complete: {total_new} new, {total_skipped} skipped, {failed} failed"
    )
    ActivityLogger.job_completed(user_id, job_id, completed, failed)
    return {
        "status": "completed",
        "completed": completed,
        "failed": failed,
        "new": total_new,
        "skipped": total_skipped,
    }


async def on_startup(ctx: dict[str, Any]) -> None:
    """Called when worker starts — store sessionmaker in context."""
    ctx["sessionmaker"] = get_sessionmaker()


async def on_shutdown(ctx: dict[str, Any]) -> None:
    """Called when worker shuts down."""
    await shutdown_engine()


@instrumented
@idempotent
async def cleanup_old_events(ctx: dict[str, Any]) -> int:
    """Periodic cleanup of old SQS events (runs every 6 hours)."""
    # awsk-opc: clear_old_sqs_events pending repo conversion
    raise RuntimeError("awsk-opc: clear_old_sqs_events pending repo conversion")


@instrumented
@idempotent
async def timeout_stale_jobs(ctx: dict[str, Any]) -> int:
    """Mark stale running jobs as failed (runs every 15 minutes).

    Jobs running for more than 1 hour are considered stale — likely the worker
    crashed or was terminated without updating the job status.
    """
    sm = ctx["sessionmaker"]
    async with sm() as session:
        repo = JobRepository(session)
        timed_out = await repo.timeout_stale(hours=1)
        await session.commit()
    if timed_out > 0:
        await publish_log(ctx, f"Timeout: marked {timed_out} stale job(s) as failed")
    return timed_out


@instrumented
@idempotent
async def cleanup_old_activity_logs(ctx: dict[str, Any]) -> dict[str, Any]:
    """Periodic cleanup of old activity logs (runs daily at 3 AM)."""
    # awsk-opc: clear_old_activity_logs pending repo conversion
    raise RuntimeError("awsk-opc: clear_old_activity_logs pending repo conversion")


class WorkerSettings:
    """ARQ worker settings."""

    functions = [import_job]
    cron_jobs = [
        sqs_poll_cron,
        cron(cleanup_old_events, hour={0, 6, 12, 18}, minute=0),
        cron(timeout_stale_jobs, minute={0, 15, 30, 45}),
        cron(cleanup_old_activity_logs, hour=3, minute=0),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = get_redis_settings()
    max_jobs = int(os.getenv("ARQ_MAX_JOBS", 4))
    job_timeout = 3600
    keep_result = 3600
    # K8s sends SIGTERM at the start of terminationGracePeriodSeconds (300s in
    # the chart Deployment). ARQ's default handle_sig cancels in-flight tasks
    # immediately on SIGTERM; setting job_completion_wait switches it to
    # handle_sig_wait_for_completion, which stops picking jobs and waits up to
    # this many seconds for the in-flight task to finish before exiting. Keep
    # this strictly below terminationGracePeriodSeconds so the worker has time
    # to write its result back before kubelet escalates to SIGKILL.
    job_completion_wait = int(os.getenv("ARQ_JOB_COMPLETION_WAIT", 290))
