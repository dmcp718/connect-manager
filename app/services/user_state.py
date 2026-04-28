"""Per-User State Management.

Pure in-memory session state — singleton-per-process keyed by user_id.
Persistence (DataStore credentials, LucidLink token) lives in Postgres +
services.secrets; UserSession holds a cache that is hydrated on the
first post-login request via :meth:`UserSession.hydrate` and updated
write-through by the routes in main.py that call services.state's
async repo helpers.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services import secrets
from services.lucidlink import LucidLinkClient
from services.s3_service import S3Service
from services.state import list_all_datastore_credentials


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

    # Set to True after the first successful hydrate() so subsequent route
    # calls can skip the Postgres round-trip.
    hydrated: bool = False

    def load_from_db(self) -> None:
        """Load saved state that doesn't require an async DB session.

        Currently just the user's LucidLink API token, which lives in
        services.secrets (Fernet-encrypted in container mode, OS keyring
        in local-dev mode) — neither path needs a Postgres call. The
        DataStore credential rehydrate happens lazily via async
        :meth:`hydrate` because route handlers own the AsyncSession.
        """
        saved_token = secrets.get_user_token(self.user_id)
        if saved_token:
            self.token = saved_token
        # api_host persistence isn't yet wired (no Settings model on this
        # branch). Falls back to the empty default; LucidLinkClient uses
        # LL_HOST when api_host is empty.

    async def hydrate(
        self, session: AsyncSession, user_uuid: Optional[uuid.UUID]
    ) -> None:
        """Repopulate the in-memory DataStore-credential cache from Postgres.

        Idempotent — only runs once per UserSession via the `hydrated` flag.
        Routes that read state.datastore_credentials right after login
        (index, tab_browser, tab_settings) await this so a fresh post-login
        view sees the user's previously-saved DataStores.
        """
        if self.hydrated:
            return
        rows = await list_all_datastore_credentials(session, user_id=user_uuid)
        for row in rows:
            datastore_id = row["datastore_id"]
            # row contains decrypted access_key + secret_key from
            # state.list_all_datastore_credentials. Mirror the legacy dict
            # shape into the in-memory cache (the `credentials_key` field
            # is no longer used — Fernet ciphertext lives only in the
            # ORM column, never in the cache).
            self.datastore_credentials[datastore_id] = {
                "datastore_id": datastore_id,
                "datastore_name": row.get("datastore_name"),
                "filespace_id": row.get("filespace_id"),
                "filespace_name": row.get("filespace_name"),
                "bucket_name": row.get("bucket_name"),
                "region": row.get("region"),
                "endpoint": row.get("endpoint"),
            }
            self._init_s3_service_from_keys(
                datastore_id,
                access_key=row["access_key"],
                secret_key=row["secret_key"],
                region=row.get("region"),
                endpoint=row.get("endpoint"),
            )
        self.hydrated = True

    def _init_s3_service_from_keys(
        self,
        datastore_id: str,
        *,
        access_key: str,
        secret_key: str,
        region: Optional[str],
        endpoint: Optional[str],
    ) -> None:
        """Construct an S3Service from already-decrypted creds."""
        if not access_key or not secret_key:
            return
        self.s3_services[datastore_id] = S3Service(
            access_key=access_key,
            secret_key=secret_key,
            region=region or "us-east-1",
            endpoint_url=endpoint,
        )

    def get_s3_service_for_datastore(self, datastore_id: str) -> Optional[S3Service]:
        """Return the S3 service for a DataStore from the in-memory cache.

        Cache is populated by :meth:`hydrate` (post-login) and by the
        save/delete routes in main.py that write through to Postgres.
        Returns None if the cache miss — callers can re-trigger hydrate
        if they suspect the cache is stale.
        """
        return self.s3_services.get(datastore_id)

    def get_datastore_by_id(self, datastore_id: str) -> Optional[Dict]:
        """Get DataStore credentials by ID from the in-memory cache."""
        return self.datastore_credentials.get(datastore_id)

    def get_browsable_datastores(self) -> List[Dict]:
        """Get list of DataStores that have credentials for browsing."""
        return list(self.datastore_credentials.values())

    def save_connection(self, save_secrets: bool = True) -> None:
        """Persist the user's LucidLink token via services.secrets.

        api_host persistence is intentionally not yet implemented — there
        is no Settings model on this branch (legacy SQLite settings table
        was dropped in the Postgres rewrite). The api_host stays in
        in-memory UserSession for the duration of the process; on
        restart the user re-enters it via the Settings tab. Tracked as
        a separate follow-up.
        """
        if save_secrets and self.token:
            secrets.set_user_token(self.user_id, self.token)
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
