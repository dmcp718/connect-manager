"""
ARQ Worker for Import Jobs
Parallel job processing with Valkey/Redis backend
DataStore-centric credential lookup
"""

import asyncio
import json
import os
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from services import database as db
from services.lucidlink import LucidLinkClient
from services.s3_service import S3Service
from services import secrets

# Initialize database on module load
db.init_db()

# Valkey/Redis connection settings
def get_redis_settings() -> RedisSettings:
    return RedisSettings(
        host=os.getenv("VALKEY_HOST", "localhost"),
        port=int(os.getenv("VALKEY_PORT", 6379)),
    )

# Log channel for pub/sub
LOG_CHANNEL = "worker:logs"


async def publish_log(ctx: dict, message: str) -> None:
    """Publish log message to Valkey for web UI consumption."""
    redis = ctx.get("redis")
    if redis:
        await redis.publish(LOG_CHANNEL, json.dumps({"message": message}))


async def import_job(ctx: dict[str, Any], job_id: int) -> dict[str, Any]:
    """Process an import job."""
    async def log(msg: str) -> None:
        await publish_log(ctx, msg)

    job = db.get_job(job_id)
    if not job:
        await log(f"Job #{job_id} not found")
        return {"status": "error", "message": f"Job {job_id} not found"}

    # Mark as running
    db.update_job(job_id, status="running")
    await log(f"Job #{job_id} started: {job['prefix']}")

    ll_client = None
    try:
        # Get credentials
        token = secrets.get_lucidlink_token()
        if not token:
            raise ValueError("No API token available - please reconnect in web UI")

        # Get AWS credentials from DataStore credentials
        aws_key, aws_secret = None, None
        s3_endpoint = None
        s3_region = "us-east-1"

        datastore_id = job.get("datastore_id")
        if datastore_id:
            cred = db.get_datastore_credentials(datastore_id)
            if cred:
                credentials_key = cred.get("credentials_key")
                aws_key, aws_secret = secrets.get_named_credentials(credentials_key)
                s3_endpoint = cred.get("endpoint")
                s3_region = cred.get("region") or "us-east-1"
                await log(f"Using DataStore: {cred.get('datastore_name', 'Unknown')}")

        if not aws_key or not aws_secret:
            raise ValueError("No AWS credentials found for this DataStore")

        # Validate job has required IDs
        if not job.get("filespace_id") or not job.get("datastore_id"):
            raise ValueError("Job missing filespace_id or datastore_id")

        # Get saved API host
        api_host = db.get_setting("api_host") or ""

        await log(f"Using filespace: {job['filespace_id'][:8]}...")

        # Initialize clients
        s3_service = S3Service(
            access_key=aws_key,
            secret_key=aws_secret,
            region=s3_region,
            endpoint_url=s3_endpoint,
        )

        ll_client = LucidLinkClient(api_host=api_host)
        ll_client.configure(
            token=token,
            filespace_id=job["filespace_id"],
            datastore_id=job["datastore_id"],
            api_host=api_host,
        )

        # Scan for files
        await log("Scanning folder...")
        keys = await s3_service.list_all_objects(job["bucket"], job["prefix"])
        total = len(keys)

        if total == 0:
            await log("No files found")
            db.update_job(job_id, status="completed", total_files=0)
            return {"status": "completed", "total": 0}

        db.update_job(job_id, total_files=total)
        await log(f"Found {total} files")

        # Pre-create folder structure (nested under bucket name)
        bucket_name = job["bucket"]
        unique_dirs = set()
        unique_dirs.add(bucket_name)
        for k in keys:
            d = os.path.dirname(k)
            if d:
                unique_dirs.add(f"{bucket_name}/{d}")

        sorted_dirs = sorted(list(unique_dirs), key=len)
        await log(f"Creating {len(sorted_dirs)} directories...")

        # Create folders sequentially (parallel causes race conditions)
        for d in sorted_dirs:
            success, error = await ll_client.ensure_structure(d + "/dummy_file")
            if not success:
                await log(f"Failed to create folder {d}: {error}")

        # Check for cancellation
        job = db.get_job(job_id)
        if job and job.get("status") == "cancelled":
            await log(f"Job #{job_id} cancelled")
            return {"status": "cancelled"}

        # Import files in parallel batches
        await log("Importing files...")
        completed = 0
        failed = 0
        total_new = 0
        total_skipped = 0
        batch_size = 25  # Tuned for throughput

        for i in range(0, len(keys), batch_size):
            # Check for cancellation
            job = db.get_job(job_id)
            if job and job.get("status") == "cancelled":
                break

            batch = keys[i:i + batch_size]
            tasks = [
                ll_client.import_file(key, f"/{bucket_name}/{key}")
                for key in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_new = 0
            batch_skipped = 0
            batch_failed = 0
            first_error = None
            for key, result in zip(batch, results):
                if isinstance(result, Exception):
                    batch_failed += 1
                    if not first_error:
                        first_error = f"Exception: {result}"
                else:
                    code, error_msg = result
                    if code in [200, 201]:  # Newly imported
                        batch_new += 1
                    elif code == 409:  # Conflict = Already exists
                        batch_skipped += 1
                    elif code == 400 and "already exists" in error_msg.lower():
                        # LucidLink API returns 400 for existing entries
                        batch_skipped += 1
                    else:
                        batch_failed += 1
                        if not first_error:
                            first_error = f"HTTP {code}: {error_msg[:200]}"

            total_new += batch_new
            total_skipped += batch_skipped
            completed += batch_new + batch_skipped
            failed += batch_failed

            # Log progress with cumulative totals
            progress_pct = int((completed + failed) / total * 100)
            if first_error and batch_failed > 0:
                await log(f"Progress: {completed + failed}/{total} ({progress_pct}%) - {total_new} new, {total_skipped} skipped, {failed} errors: {first_error}")
            else:
                await log(f"Progress: {completed + failed}/{total} ({progress_pct}%) - {total_new} new, {total_skipped} skipped, {failed} errors")

            # Update progress
            db.update_job(
                job_id,
                completed_files=completed,
                failed_files=failed,
            )

            # Small delay between batches to avoid rate limiting
            await asyncio.sleep(0.05)

        # Final status
        job = db.get_job(job_id)
        if job and job.get("status") == "cancelled":
            await log(f"Job #{job_id} cancelled ({completed}/{total} files)")
            return {"status": "cancelled", "completed": completed, "failed": failed}

        db.update_job(job_id, status="completed")
        await log(f"Job #{job_id} complete: {total_new} new, {total_skipped} skipped, {failed} failed")
        return {"status": "completed", "completed": completed, "failed": failed, "new": total_new, "skipped": total_skipped}

    except Exception as e:
        await log(f"Job #{job_id} failed: {e}")
        db.update_job(job_id, status="failed", error_message=str(e))
        return {"status": "failed", "message": str(e)}
    finally:
        # Always close the HTTP client to release connections
        if ll_client:
            await ll_client.close()


async def on_startup(ctx: dict) -> None:
    """Called when worker starts - store redis connection in context."""
    ctx["redis"] = ctx["redis"]  # Already set by arq


async def on_shutdown(ctx: dict) -> None:
    """Called when worker shuts down."""
    pass


class WorkerSettings:
    """ARQ worker settings."""
    functions = [import_job]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = get_redis_settings()
    max_jobs = int(os.getenv("ARQ_MAX_JOBS", 4))  # Parallel job limit
    job_timeout = 3600  # 1 hour max per job
    keep_result = 3600  # Keep results for 1 hour
