"""
Application State Management
Centralized state for the web application with persistence
DataStore-centric model: credentials stored per DataStore for S3 browsing
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Optional, Dict, List

from services.lucidlink import LucidLinkClient
from services.s3_service import S3Service
from services import database as db
from services import secrets


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
        self.datastores: Dict[str, dict] = {}  # id -> {name, bucket_name, region, endpoint, ...}
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

                self.log(f"Removed DataStore credentials: {cred.get('datastore_name', 'Unknown')}")
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
            self.logs = self.logs[-self._max_logs:]
