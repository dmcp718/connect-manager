"""
ARQ Worker for Import Jobs
Parallel job processing with Valkey/Redis backend
"""

import asyncio
import os
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from services import database as db
from services.lucidlink import LucidLinkClient
from services.s3_service import S3Service
from services import secrets


# Valkey/Redis connection settings
def get_redis_settings() -> RedisSettings:
    return RedisSettings(
        host=os.getenv("VALKEY_HOST", "localhost"),
        port=int(os.getenv("VALKEY_PORT", 6379)),
    )


async def import_job(ctx: dict[str, Any], job_id: int) -> dict[str, Any]:
    """Process an import job."""
    job = db.get_job(job_id)
    if not job:
        return {"status": "error", "message": f"Job {job_id} not found"}

    # Mark as running
    db.update_job(job_id, status="running")

    try:
        # Get credentials
        token = secrets.get_lucidlink_token()
        if not token:
            raise ValueError("No API token available")

        aws_key, aws_secret = secrets.get_aws_credentials()

        # Validate job has required IDs
        if not job.get("filespace_id") or not job.get("datastore_id"):
            raise ValueError("Job missing filespace_id or datastore_id")

        # Initialize clients
        s3_service = S3Service(access_key=aws_key, secret_key=aws_secret)

        ll_client = LucidLinkClient()
        ll_client.configure(
            token=token,
            filespace_id=job["filespace_id"],
            datastore_id=job["datastore_id"],
        )

        # Scan for files
        keys = await s3_service.list_all_objects(job["bucket"], job["prefix"])
        total = len(keys)

        if total == 0:
            db.update_job(job_id, status="completed", total_files=0)
            return {"status": "completed", "total": 0}

        db.update_job(job_id, total_files=total)

        # Pre-create folder structure (nested under bucket name)
        bucket_name = job["bucket"]
        unique_dirs = set()
        unique_dirs.add(bucket_name)
        for k in keys:
            d = os.path.dirname(k)
            if d:
                unique_dirs.add(f"{bucket_name}/{d}")

        sorted_dirs = sorted(list(unique_dirs), key=len)

        for d in sorted_dirs:
            success, error = await ll_client.ensure_structure(d + "/dummy_file")
            if not success:
                print(f"Warning: Failed to create folder {d}: {error}")

        # Check for cancellation
        job = db.get_job(job_id)
        if job and job.get("status") == "cancelled":
            return {"status": "cancelled"}

        # Import files in parallel batches
        completed = 0
        failed = 0
        batch_size = 10

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

            for key, result in zip(batch, results):
                if isinstance(result, Exception):
                    failed += 1
                else:
                    code, error_msg = result
                    if code in [200, 201, 400, 409]:
                        completed += 1
                    else:
                        failed += 1

            # Update progress
            db.update_job(
                job_id,
                completed_files=completed,
                failed_files=failed,
            )

        # Final status
        job = db.get_job(job_id)
        if job and job.get("status") == "cancelled":
            return {"status": "cancelled", "completed": completed, "failed": failed}

        db.update_job(job_id, status="completed")
        return {"status": "completed", "completed": completed, "failed": failed}

    except Exception as e:
        db.update_job(job_id, status="failed", error_message=str(e))
        return {"status": "failed", "message": str(e)}


class WorkerSettings:
    """ARQ worker settings."""
    functions = [import_job]
    redis_settings = get_redis_settings()
    max_jobs = int(os.getenv("ARQ_MAX_JOBS", 4))  # Parallel job limit
    job_timeout = 3600  # 1 hour max per job
    keep_result = 3600  # Keep results for 1 hour
