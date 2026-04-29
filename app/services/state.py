"""
Async Postgres-backed datastore + SQS credential helpers.

Each function takes an AsyncSession as its first argument; callers commit.
Encryption contract (CLAUDE.md rule #5): credentials are Fernet-encrypted to
bytes before writing and decrypted to str after reading. The Fernet key is
derived from JWT_SECRET_KEY via secrets._get_fernet().
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Sequence

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from services import secrets
from db.models import DatastoreCredentials, SqsCredentials, SqsQueue
from db.repositories.datastore import (
    DatastoreCredentialsRepository,
    SqsCredentialsRepository,
    SqsQueueRepository,
)
from db.repositories.settings import UserSettingsRepository


def _encrypt_creds(access_key: str, secret_key: str) -> bytes:
    """Return Fernet ciphertext of JSON {access_key, secret_key}."""
    fernet: Fernet = secrets._get_fernet()
    payload = json.dumps({"access_key": access_key, "secret_key": secret_key})
    return fernet.encrypt(payload.encode())


def _decrypt_creds(ciphertext: bytes) -> tuple[str, str]:
    """Return (access_key, secret_key) from Fernet ciphertext."""
    fernet: Fernet = secrets._get_fernet()
    data = json.loads(fernet.decrypt(ciphertext).decode())
    return str(data["access_key"]), str(data["secret_key"])


def _encrypt_secret(secret_key: str) -> bytes:
    """Return Fernet ciphertext of a single secret-key string."""
    fernet: Fernet = secrets._get_fernet()
    return fernet.encrypt(secret_key.encode())


def _decrypt_secret(ciphertext: bytes) -> str:
    """Return plaintext secret-key string from Fernet ciphertext."""
    fernet: Fernet = secrets._get_fernet()
    return fernet.decrypt(ciphertext).decode()


async def save_ll_token(
    session: AsyncSession, user_id: uuid.UUID, token: str
) -> None:
    """Persist the user's LucidLink token Fernet-encrypted in user_settings.

    Replaces the legacy services.secrets.set_user_token path which wrote
    to a per-task-instance Fernet file at $DATA_DIR/secrets.enc — that
    path was lost on task restart and not multi-replica safe, so users
    saw their token disappear after every web container churn. This
    keeps the same encryption-at-rest contract (Fernet derived from
    JWT_SECRET_KEY via secrets._get_fernet()) but in Postgres.
    """
    if not token:
        return
    repo = UserSettingsRepository(session)
    ciphertext = _encrypt_secret(token).decode("ascii")  # fernet output is URL-safe b64
    await repo.upsert(user_id, "ll_token_enc", ciphertext)


async def load_ll_token(
    session: AsyncSession, user_id: uuid.UUID
) -> Optional[str]:
    """Return the user's plaintext LucidLink token from user_settings, or None."""
    repo = UserSettingsRepository(session)
    ciphertext = await repo.get(user_id, "ll_token_enc")
    if not ciphertext:
        return None
    return _decrypt_secret(ciphertext.encode("ascii"))


async def save_datastore_credentials(
    session: AsyncSession,
    datastore_id: str,
    datastore_name: str,
    filespace_id: str,
    filespace_name: str,
    bucket_name: str,
    access_key: str,
    secret_key: str,
    region: Optional[str] = None,
    endpoint: Optional[str] = None,
    user_id: Optional[uuid.UUID] = None,
) -> DatastoreCredentials:
    ciphertext = _encrypt_creds(access_key, secret_key)
    repo = DatastoreCredentialsRepository(session)
    return await repo.upsert(
        datastore_id=datastore_id,
        datastore_name=datastore_name,
        filespace_id=filespace_id,
        filespace_name=filespace_name,
        bucket_name=bucket_name,
        credentials_key=ciphertext,
        region=region,
        endpoint=endpoint,
        user_id=user_id,
    )


async def get_datastore_credentials(
    session: AsyncSession,
    datastore_id: str,
    user_id: Optional[uuid.UUID] = None,
) -> Optional[Dict[str, Any]]:
    repo = DatastoreCredentialsRepository(session)
    row = await repo.get_for_datastore_user(datastore_id, user_id)
    if row is None:
        return None
    access_key, secret_key = _decrypt_creds(row.credentials_key)
    return {
        "id": row.id,
        "datastore_id": row.datastore_id,
        "datastore_name": row.datastore_name,
        "filespace_id": row.filespace_id,
        "filespace_name": row.filespace_name,
        "bucket_name": row.bucket_name,
        "region": row.region,
        "endpoint": row.endpoint,
        "access_key": access_key,
        "secret_key": secret_key,
        "user_id": row.user_id,
    }


async def delete_datastore_credentials(
    session: AsyncSession,
    datastore_id: str,
    user_id: Optional[uuid.UUID] = None,
) -> bool:
    repo = DatastoreCredentialsRepository(session)
    return await repo.delete_for_datastore_user(datastore_id, user_id)


async def list_all_datastore_credentials(
    session: AsyncSession,
    user_id: Optional[uuid.UUID] = None,
) -> List[Dict[str, Any]]:
    repo = DatastoreCredentialsRepository(session)
    rows: Sequence[DatastoreCredentials] = await repo.list_for_user(user_id)
    result: List[Dict[str, Any]] = []
    for row in rows:
        access_key, secret_key = _decrypt_creds(row.credentials_key)
        result.append(
            {
                "id": row.id,
                "datastore_id": row.datastore_id,
                "datastore_name": row.datastore_name,
                "filespace_id": row.filespace_id,
                "filespace_name": row.filespace_name,
                "bucket_name": row.bucket_name,
                "region": row.region,
                "endpoint": row.endpoint,
                "access_key": access_key,
                "secret_key": secret_key,
                "user_id": row.user_id,
            }
        )
    return result


async def save_sqs_credentials(
    session: AsyncSession,
    access_key: str,
    secret_key: str,
    region: str,
    user_id: Optional[uuid.UUID] = None,
) -> SqsCredentials:
    repo = SqsCredentialsRepository(session)
    await repo.delete_for_user(user_id)
    ciphertext = _encrypt_secret(secret_key)
    row = await repo.create(
        access_key=access_key,
        secret_key_encrypted=ciphertext,
        region=region,
        user_id=user_id,
    )
    return row


async def get_sqs_credentials(
    session: AsyncSession,
    user_id: Optional[uuid.UUID] = None,
) -> Optional[Dict[str, Any]]:
    repo = SqsCredentialsRepository(session)
    rows = await repo.list_for_user(user_id)
    if not rows:
        return None
    row = rows[0]
    secret_key = _decrypt_secret(row.secret_key_encrypted)
    return {
        "id": row.id,
        "access_key": row.access_key,
        "secret_key": secret_key,
        "region": row.region,
        "user_id": row.user_id,
    }


async def list_sqs_credentials(
    session: AsyncSession,
    user_id: Optional[uuid.UUID] = None,
) -> List[Dict[str, Any]]:
    repo = SqsCredentialsRepository(session)
    rows = await repo.list_for_user(user_id)
    result: List[Dict[str, Any]] = []
    for row in rows:
        secret_key = _decrypt_secret(row.secret_key_encrypted)
        result.append(
            {
                "id": row.id,
                "access_key": row.access_key,
                "secret_key": secret_key,
                "region": row.region,
                "user_id": row.user_id,
            }
        )
    return result


async def delete_sqs_credentials(
    session: AsyncSession,
    user_id: Optional[uuid.UUID] = None,
) -> bool:
    repo = SqsCredentialsRepository(session)
    deleted = await repo.delete_for_user(user_id)
    return deleted > 0


async def save_sqs_queue(
    session: AsyncSession,
    queue_url: str,
    name: str,
    region: str,
    datastore_id: str,
    filespace_id: str,
    import_prefix: str = "",
    queue_arn: Optional[str] = None,
    user_id: Optional[uuid.UUID] = None,
) -> SqsQueue:
    repo = SqsQueueRepository(session)
    return await repo.create(
        queue_url=queue_url,
        queue_arn=queue_arn,
        name=name,
        region=region,
        datastore_id=datastore_id,
        filespace_id=filespace_id,
        import_prefix=import_prefix,
        status="active",
        user_id=user_id,
    )


async def get_sqs_queue(
    session: AsyncSession,
    queue_id: uuid.UUID,
) -> Optional[SqsQueue]:
    repo = SqsQueueRepository(session)
    return await repo.get(queue_id)


async def list_sqs_queues(
    session: AsyncSession,
    user_id: Optional[uuid.UUID] = None,
) -> Sequence[SqsQueue]:
    repo = SqsQueueRepository(session)
    return await repo.list_for_user(user_id)


async def delete_sqs_queue(
    session: AsyncSession,
    queue_id: uuid.UUID,
) -> bool:
    repo = SqsQueueRepository(session)
    return await repo.delete(queue_id)


# ── Per-user settings (key/value store backed by user_settings table) ─────────


async def get_user_setting(
    session: AsyncSession,
    user_id: Optional[uuid.UUID],
    key: str,
) -> Optional[str]:
    """Read a per-user setting. Returns None when no user is logged in
    (the bootstrap admin path before auth fires) or when the row is unset."""
    if user_id is None:
        return None
    return await UserSettingsRepository(session).get(user_id, key)


async def set_user_setting(
    session: AsyncSession,
    user_id: uuid.UUID,
    key: str,
    value: str,
) -> None:
    """Upsert a per-user setting. Caller commits."""
    await UserSettingsRepository(session).upsert(user_id, key, value)
