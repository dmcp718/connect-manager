"""
Application State Management
Centralized state for the web application with persistence
DataStore-centric model: credentials stored per DataStore for S3 browsing
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
import uuid

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from services.lucidlink import LucidLinkClient
from services.s3_service import S3Service
from services import database as db
from services import secrets
from db.models import DatastoreCredentials, SqsCredentials, SqsQueue
from db.repositories.datastore import (
    DatastoreCredentialsRepository,
    SqsCredentialsRepository,
    SqsQueueRepository,
)


class AppState:
    """Global application state with SQLite + Keyring persistence."""

    def __init__(self):
        # Initialize database
        db.init_db()

        # Connection state
        self.is_connected: bool = False
        self.token: str = ""
        self.api_host: str = ""

        # LucidLink state
        self.filespaces: Dict[str, str] = {}  # name -> id
        self.datastores: Dict[
            str, dict
        ] = {}  # id -> {name, bucket_name, region, endpoint, ...}
        self.selected_filespace: str = ""
        self.selected_datastore: str = ""
        self.ll_client: Optional[LucidLinkClient] = None

        # DataStore credentials for S3 browsing (keyed by datastore_id)
        self.datastore_credentials: Dict[str, dict] = {}
        self.s3_services: Dict[str, S3Service] = {}  # datastore_id -> S3Service

        # Current browsing state
        self.current_prefix: str = ""

        # Progress tracking
        self.progress: float = 0.0
        self.is_importing: bool = False

        # Activity logs
        self.logs: List[str] = []
        self._max_logs: int = 500

        # Load saved state
        self._load_saved_state()

        # Load DataStore credentials
        self._load_datastore_credentials()

    def _load_saved_state(self) -> None:
        """Load saved state from database and keyring."""
        # Load token from keyring
        saved_token = secrets.get_lucidlink_token()
        if saved_token:
            self.token = saved_token
            self.log("Loaded saved API token")

        # Load API host from database
        saved_api_host = db.get_setting("api_host")
        if saved_api_host:
            self.api_host = saved_api_host
        else:
            self.api_host = ""

        # Load last used profile for filespace/datastore selection
        profile = db.get_default_profile()
        if profile:
            self.selected_filespace = profile.get("filespace_name", "")
            self.selected_datastore = profile.get("datastore_name", "")

            # Restore filespace mapping
            if profile.get("filespace_name") and profile.get("filespace_id"):
                self.filespaces[profile["filespace_name"]] = profile["filespace_id"]

    def _load_datastore_credentials(self) -> None:
        """Load all saved DataStore credentials."""
        self.datastore_credentials.clear()
        self.s3_services.clear()

        all_creds = db.get_all_datastore_credentials()
        for cred in all_creds:
            datastore_id = cred["datastore_id"]
            self.datastore_credentials[datastore_id] = cred
            self._init_s3_service_for_datastore(cred)

        if all_creds:
            self.log(f"Loaded {len(all_creds)} DataStore(s) for browsing")

    def _init_s3_service_for_datastore(self, cred: Dict) -> None:
        """Initialize an S3 service for a DataStore."""
        datastore_id = cred.get("datastore_id")
        credentials_key = cred.get("credentials_key")

        if not datastore_id or not credentials_key:
            return

        # Get credentials
        access_key, secret_key = secrets.get_named_credentials(credentials_key)
        if not access_key or not secret_key:
            return

        # Create S3 service
        self.s3_services[datastore_id] = S3Service(
            access_key=access_key,
            secret_key=secret_key,
            region=cred.get("region") or "us-east-1",
            endpoint_url=cred.get("endpoint"),
        )

    def get_s3_service_for_datastore(self, datastore_id: str) -> Optional[S3Service]:
        """Get or create S3 service for a DataStore."""
        if datastore_id in self.s3_services:
            return self.s3_services[datastore_id]

        # Try to load from database
        cred = db.get_datastore_credentials(datastore_id)
        if cred:
            self._init_s3_service_for_datastore(cred)
            self.datastore_credentials[datastore_id] = cred
            return self.s3_services.get(datastore_id)

        return None

    def get_datastore_by_id(self, datastore_id: str) -> Optional[Dict]:
        """Get DataStore credentials by ID."""
        return self.datastore_credentials.get(datastore_id)

    def save_datastore_for_browsing(
        self,
        datastore_id: str,
        datastore_name: str,
        filespace_id: str,
        filespace_name: str,
        bucket_name: str,
        region: Optional[str],
        endpoint: Optional[str],
        aws_access_key: str,
        aws_secret_key: str,
    ) -> None:
        """Save DataStore credentials for S3 browsing."""
        # Generate and store credentials
        credentials_key = secrets.generate_credentials_key()
        secrets.set_named_credentials(credentials_key, aws_access_key, aws_secret_key)

        # Save to database
        db.save_datastore_credentials(
            datastore_id=datastore_id,
            datastore_name=datastore_name,
            filespace_id=filespace_id,
            filespace_name=filespace_name,
            bucket_name=bucket_name,
            region=region,
            endpoint=endpoint,
            credentials_key=credentials_key,
        )

        # Reload to update in-memory state
        self._load_datastore_credentials()
        self.log(f"Saved DataStore '{datastore_name}' for browsing")

    def remove_datastore_credentials(self, datastore_id: str) -> bool:
        """Remove DataStore credentials."""
        cred = db.get_datastore_credentials(datastore_id)
        if cred:
            # Delete credentials from keyring
            credentials_key = cred.get("credentials_key")
            if credentials_key:
                secrets.delete_named_credentials(credentials_key)

            # Delete from database
            if db.delete_datastore_credentials(datastore_id):
                # Remove from in-memory state
                if datastore_id in self.s3_services:
                    del self.s3_services[datastore_id]
                if datastore_id in self.datastore_credentials:
                    del self.datastore_credentials[datastore_id]

                self.log(
                    f"Removed DataStore credentials: {cred.get('datastore_name', 'Unknown')}"
                )
                return True
        return False

    def get_browsable_datastores(self) -> List[Dict]:
        """Get list of DataStores that have credentials for browsing."""
        return list(self.datastore_credentials.values())

    def save_connection(self, save_secrets: bool = True) -> None:
        """Save current connection state to database and keyring."""
        # Save secrets to keyring
        if save_secrets and self.token:
            secrets.set_lucidlink_token(self.token)

        # Save API host to database
        if self.api_host:
            db.set_setting("api_host", self.api_host)

        # Save profile to database for quick restore
        if self.selected_filespace:
            filespace_id = self.filespaces.get(self.selected_filespace, "")
            profile_name = f"{self.selected_filespace}"
            db.save_profile(
                name=profile_name,
                bucket="",
                filespace_name=self.selected_filespace,
                filespace_id=filespace_id,
                datastore_name=self.selected_datastore,
                datastore_id="",
            )
            db.set_setting("default_profile", profile_name)
            self.log("Connection saved")

    def clear_saved_data(self) -> None:
        """Clear all saved data (for logout/reset)."""
        secrets.clear_all_secrets()
        self.log("Cleared saved credentials")

    def get_saved_profiles(self) -> list:
        """Get list of saved connection profiles."""
        return db.list_profiles()

    def log(self, message: str) -> None:
        """Add a timestamped log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")

        # Trim old logs
        if len(self.logs) > self._max_logs:
            self.logs = self.logs[-self._max_logs :]


# ── Async Postgres-backed helpers (aws-kubernetes branch) ─────────────────────
# These replace the SQLite db.* calls that the AppState class methods used.
# Each accepts an AsyncSession as its first argument; callers commit.
# Encryption contract (CLAUDE.md rule #5): credentials are Fernet-encrypted to
# bytes before writing and decrypted to str after reading.  The Fernet key is
# derived from JWT_SECRET_KEY via secrets._get_fernet().


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
