"""SQS Polling Service - integrated with ARQ workers.

Polls every active SQS queue for S3 event notifications and creates import
records. Uses a Valkey-backed distributed lock so only one worker polls at
a time, preventing duplicate message processing across replicas.

Database access goes through the async repositories:
- SqsQueueRepository for the active-queue list and per-queue mark/error
  updates.
- SqsCredentialsRepository for per-user IAM creds.
- SqsEventRepository for idempotency probe + status transitions.
- DatastoreCredentialsRepository for the per-event DataStore lookup
  (metadata only — `bucket_name`, etc. — no Fernet decryption needed
  here).

The cron entrypoint owns its own session lifecycle: ``ctx["sessionmaker"]``
is set by ``services.worker.on_startup``. Every DB-touching helper accepts
an ``AsyncSession`` so the cron can scope a single session per queue and
commit explicitly.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from arq import cron
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.repositories.datastore import (
    DatastoreCredentialsRepository,
    SqsEventRepository,
    SqsQueueRepository,
)
from services import secrets, state as state_helpers
from services.activity_logger import ActivityLogger
from services.logging import get_logger
from services.lucidlink import LucidLinkClient
from services.sqs_service import SQSError, SQSService

_log = get_logger("sqs_poller")

# Lock key for distributed polling
POLL_LOCK_KEY = "sqs:poll:lock"
POLL_LOCK_TTL = 60  # seconds


async def acquire_poll_lock(redis, worker_id: str) -> bool:
    """Atomic SET NX EX — returns True iff this worker now holds the lock."""
    result = await redis.set(POLL_LOCK_KEY, worker_id, nx=True, ex=POLL_LOCK_TTL)
    return result is not None


async def release_poll_lock(redis, worker_id: str) -> bool:
    """Release the poll lock if we own it (atomic check-and-delete via Lua)."""
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


def _resolve_user_id(value: Any) -> Optional[uuid.UUID]:
    """Coerce queue.user_id (Optional[UUID|str]) to Optional[UUID]."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def poll_sqs_queues(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """ARQ cron entrypoint — poll every active queue once.

    Acquires the distributed lock, snapshots the active-queue list with one
    session, then per-queue spins a fresh session for the queue's
    credentials + per-event work. The fresh-session-per-queue pattern keeps
    one slow queue from holding a connection for the whole sweep.
    """
    redis = ctx.get("redis")
    sm: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    worker_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    if not await acquire_poll_lock(redis, worker_id):
        return {"status": "skipped", "reason": "another worker is polling"}

    try:
        async with sm() as session:
            queue_repo = SqsQueueRepository(session)
            queues = list(await queue_repo.list_active())
        if not queues:
            return {"status": "ok", "queues_polled": 0, "events_processed": 0}

        total_events = 0
        errors: list[str] = []
        sqs_clients: dict[tuple[Optional[uuid.UUID], str], SQSService] = {}

        for queue in queues:
            try:
                user_id = _resolve_user_id(queue.user_id)
                async with sm() as session:
                    sqs_creds = await state_helpers.get_sqs_credentials(
                        session, user_id
                    )
                if sqs_creds is None:
                    async with sm() as session:
                        await SqsQueueRepository(session).set_error(
                            queue.id, "No SQS credentials configured for user"
                        )
                        await session.commit()
                    continue

                access_key = sqs_creds["access_key"]
                # state.get_sqs_credentials Fernet-decrypts the secret on
                # read, so secret_key here is already plaintext.
                secret_key = sqs_creds["secret_key"]

                if not access_key or not secret_key:
                    async with sm() as session:
                        await SqsQueueRepository(session).set_error(
                            queue.id, "Invalid SQS credentials"
                        )
                        await session.commit()
                    continue

                queue_region = queue.region or sqs_creds.get("region") or "us-east-1"
                client_key = (user_id, queue_region)
                if client_key not in sqs_clients:
                    sqs_clients[client_key] = SQSService(
                        access_key, secret_key, queue_region
                    )

                sqs = sqs_clients[client_key]
                events_count = await poll_single_queue(ctx, sm, sqs, queue)
                total_events += events_count

                async with sm() as session:
                    await SqsQueueRepository(session).mark_polled(
                        queue.id, error_message=None
                    )
                    await session.commit()

            except Exception as e:
                error_msg = str(e)[:200]
                errors.append(f"{queue.name}: {error_msg}")
                async with sm() as session:
                    await SqsQueueRepository(session).set_error(queue.id, error_msg)
                    await session.commit()

        return {
            "status": "ok",
            "queues_polled": len(queues),
            "events_processed": total_events,
            "errors": errors if errors else None,
        }

    finally:
        await release_poll_lock(redis, worker_id)


async def poll_single_queue(
    ctx: Dict[str, Any],
    sm: async_sessionmaker[AsyncSession],
    sqs: SQSService,
    queue,  # SqsQueue ORM row
) -> int:
    """Receive up to 10 messages from one queue and process each."""
    queue_url = queue.queue_url
    queue_id = queue.id

    messages = sqs.receive_messages(queue_url, max_messages=10, wait_time_seconds=0)
    events_processed = 0

    for message in messages:
        message_id = message.get("MessageId", "")
        receipt_handle = message.get("ReceiptHandle", "")
        body = message.get("Body", "")

        s3_events = SQSService.parse_s3_events(body)

        for event in s3_events:
            if not SQSService.is_create_event(event.get("event_type", "")):
                continue

            try:
                await process_s3_event(ctx, sm, event, queue, message_id)
                events_processed += 1
            except Exception as e:
                # Best-effort: record the failed event so it shows up in the
                # admin SQS log. We synthesize a fresh UUID per failure
                # because we never inserted the row above.
                async with sm() as session:
                    repo = SqsEventRepository(session)
                    row = await repo.create_event(
                        queue_id=queue_id,
                        message_id=message_id,
                        event_type=event.get("event_type", "unknown"),
                        bucket=event.get("bucket", ""),
                        object_key=event.get("key", ""),
                        object_size=event.get("size"),
                        event_time=_parse_event_time(event.get("event_time")),
                    )
                    await repo.update_status(
                        row.id, status="failed", error_message=str(e)[:200]
                    )
                    await session.commit()

        # Delete the message from SQS even if some events inside it failed —
        # they're recorded; another redelivery would only duplicate.
        try:
            sqs.delete_message(queue_url, receipt_handle)
        except SQSError:
            pass

    return events_processed


async def process_s3_event(
    ctx: Dict[str, Any],
    sm: async_sessionmaker[AsyncSession],
    event: Dict[str, Any],
    queue,  # SqsQueue ORM row
    message_id: str,
) -> None:
    """Idempotently turn one S3 event into a LucidLink import."""
    queue_id = queue.id
    datastore_id = queue.datastore_id
    filespace_id = queue.filespace_id
    import_prefix = queue.import_prefix or ""
    user_id = _resolve_user_id(queue.user_id)

    bucket = event.get("bucket", "")
    object_key = event.get("key", "")
    object_size = event.get("size")
    event_type = event.get("event_type", "")
    event_time = _parse_event_time(event.get("event_time"))

    # Idempotency: skip if the same (message_id, queue_id, object_key) was
    # already recorded.
    async with sm() as session:
        events_repo = SqsEventRepository(session)
        if await events_repo.exists_for_message(message_id, queue_id, object_key):
            return

        row = await events_repo.create_event(
            queue_id=queue_id,
            message_id=message_id,
            event_type=event_type,
            bucket=bucket,
            object_key=object_key,
            object_size=object_size,
            event_time=event_time,
        )
        event_id = row.id
        await session.commit()

    # DataStore lookup — metadata only, so no Fernet decrypt needed.
    async with sm() as session:
        ds_cred = await DatastoreCredentialsRepository(session).get_for_datastore_user(
            datastore_id, user_id
        )
    if ds_cred is None:
        async with sm() as session:
            await SqsEventRepository(session).update_status(
                event_id,
                status="failed",
                error_message="DataStore credentials not found",
            )
            await session.commit()
        return

    # Token is Fernet-encrypted in user_settings under 'll_token_enc' since
    # v0.1.5. Fall back to the legacy services.secrets path for tokens
    # saved before that.
    token: Optional[str] = None
    if user_id:
        async with sm() as session:
            token = await state_helpers.load_ll_token(session, user_id)
    if not token:
        token = (
            secrets.get_user_token(str(user_id))
            if user_id
            else secrets.get_lucidlink_token()
        )
    if not token:
        async with sm() as session:
            await SqsEventRepository(session).update_status(
                event_id,
                status="failed",
                error_message=(
                    "LucidLink API token not configured - please reconnect in Settings"
                ),
            )
            await session.commit()
        return

    # Pull the per-user api_host from the user_settings table; falls back to
    # the global (user_id NULL) row inside get_user_setting. Empty string
    # makes LucidLinkClient use its LL_HOST default.
    async with sm() as session:
        api_host = (
            await state_helpers.get_user_setting(session, user_id, "api_host") or ""
        )

    async with sm() as session:
        await SqsEventRepository(session).update_status(event_id, status="processing")
        await session.commit()

    try:
        ll_client = LucidLinkClient(api_host=api_host)
        ll_client.configure(
            token=token,
            filespace_id=filespace_id,
            datastore_id=datastore_id,
            api_host=api_host,
        )

        if import_prefix:
            prefix_clean = import_prefix.strip("/")
            ll_path = f"/{bucket}/{prefix_clean}/{object_key}"
        else:
            ll_path = f"/{bucket}/{object_key}"

        structure_ok, structure_error = await ll_client.ensure_structure(ll_path)

        queue_name = queue.name or str(queue_id)[:8]

        if structure_ok:
            code, error_msg = await ll_client.import_file(object_key, ll_path)

            if code in (200, 201):
                async with sm() as session:
                    await SqsEventRepository(session).update_status(
                        event_id, status="completed"
                    )
                    await session.commit()
                ActivityLogger.sqs_event_processed(
                    str(user_id) if user_id else None,
                    str(queue_id),
                    queue_name,
                    object_key,
                    "success",
                )
            elif code in (400, 409) and "already exists" in error_msg.lower():
                async with sm() as session:
                    await SqsEventRepository(session).update_status(
                        event_id,
                        status="skipped",
                        error_message="Already exists",
                    )
                    await session.commit()
            else:
                async with sm() as session:
                    await SqsEventRepository(session).update_status(
                        event_id,
                        status="failed",
                        error_message=f"HTTP {code}: {error_msg[:150]}",
                    )
                    await session.commit()
                ActivityLogger.sqs_event_processed(
                    str(user_id) if user_id else None,
                    str(queue_id),
                    queue_name,
                    object_key,
                    "failed",
                    error=f"HTTP {code}: {error_msg[:100]}",
                )
        else:
            async with sm() as session:
                await SqsEventRepository(session).update_status(
                    event_id,
                    status="failed",
                    error_message=f"Failed to create folder: {structure_error[:150]}",
                )
                await session.commit()
            ActivityLogger.sqs_event_processed(
                str(user_id) if user_id else None,
                str(queue_id),
                queue_name,
                object_key,
                "failed",
                error=f"Folder creation failed: {structure_error[:100]}",
            )

        await ll_client.close()

    except Exception as e:
        async with sm() as session:
            await SqsEventRepository(session).update_status(
                event_id, status="failed", error_message=str(e)[:200]
            )
            await session.commit()
        queue_name = queue.name or str(queue_id)[:8]
        ActivityLogger.sqs_event_processed(
            str(user_id) if user_id else None,
            str(queue_id),
            queue_name,
            object_key,
            "failed",
            error=str(e)[:100],
        )


def _parse_event_time(value: Any) -> Optional[datetime]:
    """Best-effort parse of S3 event time strings into datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        # S3 event times are ISO-8601 with optional 'Z' suffix.
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# Cron job configuration for ARQ — runs every 10 seconds.
sqs_poll_cron = cron(poll_sqs_queues, second={0, 10, 20, 30, 40, 50})
