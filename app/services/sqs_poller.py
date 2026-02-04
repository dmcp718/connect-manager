"""
SQS Polling Service - Integrated with ARQ workers.
Polls active SQS queues for S3 event notifications and creates import jobs.
Uses distributed locking to ensure only one worker polls at a time.
"""

import asyncio
import json
import os
import uuid
from typing import Any, Dict, Optional

from arq import cron

from services import database as db
from services import secrets
from services.sqs_service import SQSService, SQSError
from services.lucidlink import LucidLinkClient

# Lock key for distributed polling
POLL_LOCK_KEY = "sqs:poll:lock"
POLL_LOCK_TTL = 60  # seconds


async def acquire_poll_lock(redis, worker_id: str) -> bool:
    """
    Acquire distributed lock for SQS polling.
    Uses SET NX EX pattern for atomic lock acquisition.

    Args:
        redis: Redis/Valkey connection
        worker_id: Unique identifier for this worker

    Returns:
        True if lock acquired, False if another worker holds it
    """
    result = await redis.set(
        POLL_LOCK_KEY,
        worker_id,
        nx=True,  # Only set if not exists
        ex=POLL_LOCK_TTL,  # Expire after TTL
    )
    return result is not None


async def release_poll_lock(redis, worker_id: str) -> bool:
    """
    Release the poll lock if we own it.

    Args:
        redis: Redis/Valkey connection
        worker_id: Our worker ID

    Returns:
        True if released, False if we didn't own it
    """
    # Lua script for atomic check-and-delete
    script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    try:
        result = await redis.eval(script, 1, POLL_LOCK_KEY, worker_id)
        return result == 1
    except Exception:
        return False


async def poll_sqs_queues(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Poll all active SQS queues for new messages.
    This is called as an ARQ cron job every 30 seconds.

    Uses distributed locking to ensure only one worker polls at a time,
    preventing duplicate message processing.

    Args:
        ctx: ARQ context with redis connection

    Returns:
        Summary of polling results
    """
    redis = ctx.get("redis")
    worker_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    # Try to acquire lock
    if not await acquire_poll_lock(redis, worker_id):
        return {"status": "skipped", "reason": "another worker is polling"}

    try:
        # Get active queues (from all users - poller needs to process all)
        queues = db.list_active_sqs_queues()
        if not queues:
            return {"status": "ok", "queues_polled": 0, "events_processed": 0}

        total_events = 0
        errors = []

        # Group queues by (user_id, region) for efficient client reuse
        # Each user has their own SQS credentials
        sqs_clients = {}  # key: (user_id, region) -> SQSService

        for queue in queues:
            try:
                # Get user's SQS credentials for this queue
                user_id = queue.get("user_id")
                sqs_creds = db.get_sqs_credentials(user_id=user_id)
                if not sqs_creds:
                    db.update_sqs_queue(queue["id"], error_message="No SQS credentials configured for user")
                    continue

                # Decrypt secret key
                access_key = sqs_creds.get("access_key")
                secret_key_encrypted = sqs_creds.get("secret_key_encrypted")
                secret_key = secrets.get_secret(f"sqs_secret_{secret_key_encrypted}")

                if not access_key or not secret_key:
                    db.update_sqs_queue(queue["id"], error_message="Invalid SQS credentials")
                    continue

                # Use queue's region
                queue_region = queue.get("region") or sqs_creds.get("region", "us-east-1")

                # Get or create SQS client for this user+region combination
                client_key = (user_id, queue_region)
                if client_key not in sqs_clients:
                    sqs_clients[client_key] = SQSService(access_key, secret_key, queue_region)

                sqs = sqs_clients[client_key]
                events_count = await poll_single_queue(ctx, sqs, queue)
                total_events += events_count

                # Update last poll time
                db.update_sqs_queue(queue["id"], last_poll_at=True, error_message="")

            except Exception as e:
                error_msg = str(e)[:200]
                errors.append(f"{queue['name']}: {error_msg}")
                db.update_sqs_queue(queue["id"], error_message=error_msg)

        return {
            "status": "ok",
            "queues_polled": len(queues),
            "events_processed": total_events,
            "errors": errors if errors else None,
        }

    finally:
        # Always release lock
        await release_poll_lock(redis, worker_id)


async def poll_single_queue(
    ctx: Dict[str, Any],
    sqs: SQSService,
    queue: Dict[str, Any],
) -> int:
    """
    Poll a single SQS queue and process messages.

    Args:
        ctx: ARQ context
        sqs: SQS service instance
        queue: Queue configuration from database

    Returns:
        Number of events processed
    """
    queue_url = queue["queue_url"]
    queue_id = queue["id"]

    # Receive messages (short poll to avoid blocking)
    messages = sqs.receive_messages(queue_url, max_messages=10, wait_time_seconds=0)

    events_processed = 0

    for message in messages:
        message_id = message.get("MessageId", "")
        receipt_handle = message.get("ReceiptHandle", "")
        body = message.get("Body", "")

        # Parse S3 events from message
        s3_events = SQSService.parse_s3_events(body)

        for event in s3_events:
            # Only process object creation events
            if not SQSService.is_create_event(event.get("event_type", "")):
                continue

            # Process this event
            try:
                await process_s3_event(ctx, event, queue, message_id)
                events_processed += 1
            except Exception as e:
                # Log error but continue processing other events
                event_id = f"{queue_id}-{message_id}-{events_processed}"
                db.create_sqs_event(
                    event_id=event_id,
                    queue_id=queue_id,
                    message_id=message_id,
                    event_type=event.get("event_type", "unknown"),
                    bucket=event.get("bucket", ""),
                    object_key=event.get("key", ""),
                    object_size=event.get("size"),
                    event_time=event.get("event_time"),
                )
                db.update_sqs_event(event_id, status="failed", error_message=str(e)[:200])

        # Delete message after processing (even if some events failed)
        try:
            sqs.delete_message(queue_url, receipt_handle)
        except SQSError:
            pass  # Message will become visible again after visibility timeout

    return events_processed


async def process_s3_event(
    ctx: Dict[str, Any],
    event: Dict[str, Any],
    queue: Dict[str, Any],
    message_id: str,
) -> None:
    """
    Process a single S3 event - create import job.

    Args:
        ctx: ARQ context
        event: Parsed S3 event
        queue: Queue configuration
        message_id: SQS message ID
    """
    queue_id = queue["id"]
    datastore_id = queue["datastore_id"]
    filespace_id = queue["filespace_id"]
    import_prefix = queue.get("import_prefix", "")
    user_id = queue.get("user_id")

    bucket = event.get("bucket", "")
    object_key = event.get("key", "")
    object_size = event.get("size")
    event_type = event.get("event_type", "")
    event_time = event.get("event_time")

    # Generate unique event ID
    event_id = f"{uuid.uuid4().hex}"

    # Record the event
    db.create_sqs_event(
        event_id=event_id,
        queue_id=queue_id,
        message_id=message_id,
        event_type=event_type,
        bucket=bucket,
        object_key=object_key,
        object_size=object_size,
        event_time=event_time,
    )

    # Get DataStore credentials for S3 access (user-specific in multi-user mode)
    cred = db.get_datastore_credentials(datastore_id, user_id=user_id)
    if not cred:
        db.update_sqs_event(
            event_id,
            status="failed",
            error_message="DataStore credentials not found",
        )
        return

    # Get LucidLink API token for the user who created this queue
    token = secrets.get_user_token(user_id) if user_id else secrets.get_lucidlink_token()
    if not token:
        db.update_sqs_event(
            event_id,
            status="failed",
            error_message="LucidLink API token not configured - please reconnect in Settings",
        )
        return

    # Get API host (user-specific in multi-user mode)
    api_host_key = f"api_host_{user_id}" if user_id else "api_host"
    api_host = db.get_setting(api_host_key) or ""

    # Update event status to processing
    db.update_sqs_event(event_id, status="processing")

    try:
        # Initialize LucidLink client
        ll_client = LucidLinkClient(api_host=api_host)
        ll_client.configure(
            token=token,
            filespace_id=filespace_id,
            datastore_id=datastore_id,
            api_host=api_host,
        )

        # Build destination path
        # Pattern: /<bucket>/<prefix>/<key> or /<bucket>/<key>
        if import_prefix:
            # Clean prefix (remove leading/trailing slashes)
            prefix_clean = import_prefix.strip("/")
            ll_path = f"/{bucket}/{prefix_clean}/{object_key}"
        else:
            ll_path = f"/{bucket}/{object_key}"

        # Ensure folder structure exists
        structure_ok, structure_error = await ll_client.ensure_structure(ll_path)

        if structure_ok:
            # Import the file
            code, error_msg = await ll_client.import_file(object_key, ll_path)

            if code in [200, 201]:
                db.update_sqs_event(event_id, status="completed")
            elif code in [400, 409] and "already exists" in error_msg.lower():
                db.update_sqs_event(event_id, status="skipped", error_message="Already exists")
            else:
                db.update_sqs_event(
                    event_id,
                    status="failed",
                    error_message=f"HTTP {code}: {error_msg[:150]}",
                )
        else:
            db.update_sqs_event(
                event_id,
                status="failed",
                error_message=f"Failed to create folder: {structure_error[:150]}",
            )

        await ll_client.close()

    except Exception as e:
        db.update_sqs_event(event_id, status="failed", error_message=str(e)[:200])


# Cron job configuration for ARQ
# Runs every 10 seconds
sqs_poll_cron = cron(poll_sqs_queues, second={0, 10, 20, 30, 40, 50})
