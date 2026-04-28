"""
Per-User State Management
Replaces the global AppState with user-scoped sessions
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from dataclasses import dataclass, field
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.lucidlink import LucidLinkClient
from services.s3_service import S3Service
from services import secrets
from services.logging import get_logger
from services.state import (
    delete_datastore_credentials,
    delete_sqs_credentials,
    get_datastore_credentials,
    get_sqs_credentials,
    list_all_datastore_credentials,
    list_sqs_credentials,
    list_sqs_queues,
    save_datastore_credentials,
    save_sqs_credentials,
)

# Legacy db.* shims: services/database.py was rewritten as async-only engine
# in awsk-rp6.8 — the SQLite-era db.get_setting / db.get_all_datastore_credentials
# / etc. are gone. These shims keep boot + first-request paths AttributeError-
# free. Functionality that depended on these is degraded; proper async
# conversion is tracked as a follow-up to awsk-opc.
_log_legacy = get_logger("user_state.legacy_db")


def _legacy_get_setting(_key: str) -> Optional[str]:
    return None


def _legacy_set_setting(key: str, _value: str) -> None:
    _log_legacy.warning("set_setting skipped (no settings repo)", extra={"setting_key": key})


def _legacy_get_all_datastore_credentials(*, user_id: Optional[str] = None) -> List[Dict]:
    return []


def _legacy_get_datastore_credentials(_datastore_id: str, *, user_id: Optional[str] = None) -> Optional[Dict]:
    return None


def _legacy_save_datastore_credentials(**kwargs: Any) -> None:
    _log_legacy.warning("save_datastore_credentials skipped (use state.save_datastore_credentials async)", extra=kwargs)


def _legacy_delete_datastore_credentials(_datastore_id: str, *, user_id: Optional[str] = None) -> bool:
    return False


@dataclass
class UserSession:
    """Per-user session state."""

    user_id: str

    # Connection state
    is_connected: bool = False
    token: str = ""
    api_host: str = ""

    # LucidLink state
    filespaces: Dict[str, str] = field(default_factory=dict)  # name -> id
    datastores: Dict[str, dict] = field(
        default_factory=dict
    )  # name -> {id, name, bucket, ...}
    selected_filespace: str = ""
    selected_datastore: str = ""
    ll_client: Optional[LucidLinkClient] = None

    # DataStore credentials for S3 browsing (keyed by datastore_id)
    datastore_credentials: Dict[str, dict] = field(default_factory=dict)
    s3_services: Dict[str, S3Service] = field(default_factory=dict)

    # Current browsing state
    current_prefix: str = ""

    # Progress tracking
    progress: float = 0.0
    is_importing: bool = False

    # Activity logs (per-user)
    logs: List[str] = field(default_factory=list)
    _max_logs: int = 500

    def load_from_db(self) -> None:
        """Load saved state from database for this user."""
        # Load user-specific token
        saved_token = secrets.get_user_token(self.user_id)
        if saved_token:
            self.token = saved_token

        # Load API host (can be user-specific or global)
        saved_api_host = _legacy_get_setting(f"api_host_{self.user_id}")
        if not saved_api_host:
            saved_api_host = _legacy_get_setting("api_host")
        if saved_api_host:
            self.api_host = saved_api_host

        # Load user's DataStore credentials
        self._load_datastore_credentials()

    def _load_datastore_credentials(self) -> None:
        """Load all saved DataStore credentials for this user."""
        self.datastore_credentials.clear()
        self.s3_services.clear()

        # Get only credentials for this user
        all_creds = _legacy_get_all_datastore_credentials(user_id=self.user_id)
        for cred in all_creds:
            datastore_id = cred["datastore_id"]
            self.datastore_credentials[datastore_id] = cred
            self._init_s3_service_for_datastore(cred)

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

        # Try to load from database (user-specific)
        cred = _legacy_get_datastore_credentials(datastore_id, user_id=self.user_id)
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

        # Save to database with user_id for isolation
        _legacy_save_datastore_credentials(
            datastore_id=datastore_id,
            datastore_name=datastore_name,
            filespace_id=filespace_id,
            filespace_name=filespace_name,
            bucket_name=bucket_name,
            region=region,
            endpoint=endpoint,
            credentials_key=credentials_key,
            user_id=self.user_id,
        )

        # Reload to update in-memory state
        self._load_datastore_credentials()
        self.log(f"Saved DataStore '{datastore_name}' for browsing")

    def remove_datastore_credentials(self, datastore_id: str) -> bool:
        """Remove DataStore credentials for this user."""
        cred = _legacy_get_datastore_credentials(datastore_id, user_id=self.user_id)
        if cred:
            # Delete credentials from keyring
            credentials_key = cred.get("credentials_key")
            if credentials_key:
                secrets.delete_named_credentials(credentials_key)

            # Delete from database (user-specific)
            if _legacy_delete_datastore_credentials(datastore_id, user_id=self.user_id):
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
        # Save secrets to keyring (user-specific)
        if save_secrets and self.token:
            secrets.set_user_token(self.user_id, self.token)

        # Save API host to database (user-specific)
        if self.api_host:
            _legacy_set_setting(f"api_host_{self.user_id}", self.api_host)

        self.log("Connection saved")

    def log(self, message: str) -> None:
        """Add a timestamped log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")

        # Trim old logs
        if len(self.logs) > self._max_logs:
            self.logs = self.logs[-self._max_logs :]


class UserStateManager:
    """
    Manages per-user sessions.
    Thread-safe singleton that maintains user state across requests.
    """

    _instance: Optional["UserStateManager"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions = {}
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_sessions"):
            self._sessions: Dict[str, UserSession] = {}

    def get_session(self, user_id: str) -> UserSession:
        """Get or create a session for a user."""
        if user_id not in self._sessions:
            session = UserSession(user_id=user_id)
            session.load_from_db()
            self._sessions[user_id] = session
        return self._sessions[user_id]

    def clear_session(self, user_id: str) -> None:
        """Clear a user's session (on logout)."""
        if user_id in self._sessions:
            del self._sessions[user_id]

    def get_all_sessions(self) -> Dict[str, UserSession]:
        """Get all active sessions (admin use)."""
        return self._sessions.copy()

    def session_count(self) -> int:
        """Get count of active sessions."""
        return len(self._sessions)


# Global singleton instance
user_state_manager = UserStateManager()


def get_user_session(user_id: str) -> UserSession:
    """Convenience function to get a user's session."""
    return user_state_manager.get_session(user_id)


# ── Per-user async wrappers ───────────────────────────────────────────────────
# Thin wrappers over the functions in services.state that fix user_id.
# These are the call-sites for routes/main that operate in multi-user mode.


async def save_user_datastore_credentials(
    session: AsyncSession,
    user_id: uuid.UUID,
    datastore_id: str,
    datastore_name: str,
    filespace_id: str,
    filespace_name: str,
    bucket_name: str,
    access_key: str,
    secret_key: str,
    region: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> Any:
    return await save_datastore_credentials(
        session,
        datastore_id=datastore_id,
        datastore_name=datastore_name,
        filespace_id=filespace_id,
        filespace_name=filespace_name,
        bucket_name=bucket_name,
        access_key=access_key,
        secret_key=secret_key,
        region=region,
        endpoint=endpoint,
        user_id=user_id,
    )


async def get_user_datastore_credentials(
    session: AsyncSession,
    user_id: uuid.UUID,
    datastore_id: str,
) -> Optional[Dict[str, Any]]:
    return await get_datastore_credentials(session, datastore_id, user_id=user_id)


async def delete_user_datastore_credentials(
    session: AsyncSession,
    user_id: uuid.UUID,
    datastore_id: str,
) -> bool:
    return await delete_datastore_credentials(session, datastore_id, user_id=user_id)


async def list_user_datastore_credentials(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> List[Dict[str, Any]]:
    return await list_all_datastore_credentials(session, user_id=user_id)


async def save_user_sqs_credentials(
    session: AsyncSession,
    user_id: uuid.UUID,
    access_key: str,
    secret_key: str,
    region: str,
) -> Any:
    return await save_sqs_credentials(
        session,
        access_key=access_key,
        secret_key=secret_key,
        region=region,
        user_id=user_id,
    )


async def get_user_sqs_credentials(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> Optional[Dict[str, Any]]:
    return await get_sqs_credentials(session, user_id=user_id)


async def list_user_sqs_credentials(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> List[Dict[str, Any]]:
    return await list_sqs_credentials(session, user_id=user_id)


async def delete_user_sqs_credentials(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> bool:
    return await delete_sqs_credentials(session, user_id=user_id)


async def list_user_sqs_queues(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> Sequence[Any]:
    return await list_sqs_queues(session, user_id=user_id)
