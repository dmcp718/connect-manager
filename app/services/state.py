"""
Application State Management
Centralized state for the web application with persistence
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
        self.datastores: Dict[str, str] = {}  # name -> id
        self.selected_filespace: str = ""
        self.selected_datastore: str = ""
        self.ll_client: Optional[LucidLinkClient] = None

        # S3 state
        self.s3_service: Optional[S3Service] = None
        self.current_bucket: str = "native-layout-demonstration"
        self.current_prefix: str = ""

        # Progress tracking
        self.progress: float = 0.0
        self.is_importing: bool = False

        # Activity logs
        self.logs: List[str] = []
        self._max_logs: int = 500

        # Load saved state
        self._load_saved_state()

    def _load_saved_state(self) -> None:
        """Load saved state from database and keyring."""
        # Load token from keyring
        saved_token = secrets.get_lucidlink_token()
        if saved_token:
            self.token = saved_token
            self.log("🔑 Loaded saved API token")

        # Load API host from database
        saved_api_host = db.get_setting("api_host")
        if saved_api_host:
            self.api_host = saved_api_host
        else:
            self.api_host = ""

        # Load last used profile
        profile = db.get_default_profile()
        if profile:
            self.current_bucket = profile.get("bucket", self.current_bucket)
            self.selected_filespace = profile.get("filespace_name", "")
            self.selected_datastore = profile.get("datastore_name", "")

            # Restore filespace/datastore mappings
            if profile.get("filespace_name") and profile.get("filespace_id"):
                self.filespaces[profile["filespace_name"]] = profile["filespace_id"]
            if profile.get("datastore_name") and profile.get("datastore_id"):
                self.datastores[profile["datastore_name"]] = profile["datastore_id"]

            self.log(f"📂 Loaded profile: {profile.get('name', 'default')}")

    def save_connection(self, save_secrets: bool = True) -> None:
        """Save current connection state to database and keyring."""
        # Save secrets to keyring
        if save_secrets and self.token:
            secrets.set_lucidlink_token(self.token)

        # Save API host to database
        if self.api_host:
            db.set_setting("api_host", self.api_host)

        # Save profile to database
        if self.selected_filespace and self.selected_datastore:
            profile_name = f"{self.current_bucket}/{self.selected_filespace}"
            db.save_profile(
                name=profile_name,
                bucket=self.current_bucket,
                filespace_name=self.selected_filespace,
                filespace_id=self.filespaces.get(self.selected_filespace, ""),
                datastore_name=self.selected_datastore,
                datastore_id=self.datastores.get(self.selected_datastore, ""),
            )
            db.set_setting("default_profile", profile_name)
            self.log("💾 Connection saved")

    def save_aws_credentials(self, access_key: str, secret_key: str) -> None:
        """Save AWS credentials to keyring."""
        if access_key and secret_key:
            secrets.set_aws_credentials(access_key, secret_key)
            self.log("💾 AWS credentials saved")

    def get_saved_aws_credentials(self) -> tuple[Optional[str], Optional[str]]:
        """Get saved AWS credentials from keyring."""
        return secrets.get_aws_credentials()

    def clear_saved_data(self) -> None:
        """Clear all saved data (for logout/reset)."""
        secrets.clear_all_secrets()
        self.log("🗑️ Cleared saved credentials")

    def get_saved_profiles(self) -> list:
        """Get list of saved connection profiles."""
        return db.list_profiles()

    def load_profile(self, name: str) -> bool:
        """Load a saved profile by name."""
        profile = db.get_profile(name)
        if not profile:
            return False

        self.current_bucket = profile.get("bucket", "")
        self.selected_filespace = profile.get("filespace_name", "")
        self.selected_datastore = profile.get("datastore_name", "")

        if profile.get("filespace_name") and profile.get("filespace_id"):
            self.filespaces[profile["filespace_name"]] = profile["filespace_id"]
        if profile.get("datastore_name") and profile.get("datastore_id"):
            self.datastores[profile["datastore_name"]] = profile["datastore_id"]

        db.set_setting("default_profile", name)
        self.log(f"📂 Loaded profile: {name}")
        return True

    def log(self, message: str) -> None:
        """Add a timestamped log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")

        # Trim old logs
        if len(self.logs) > self._max_logs:
            self.logs = self.logs[-self._max_logs:]

    async def import_folder_recursive(self, prefix: str) -> None:
        """Import all files from an S3 folder recursively with parallel processing."""
        if not self.is_connected or not self.ll_client or not self.s3_service:
            self.log("❌ Not connected")
            return

        if self.is_importing:
            self.log("⚠️ Import already in progress")
            return

        self.is_importing = True
        start_time = time.time()
        self.log(f"🚀 Bulk import: {prefix}")
        self.progress = 0.0

        try:
            # 1. Scan for all files
            self.log("📂 Scanning folder...")
            keys = await self.s3_service.list_all_objects(self.current_bucket, prefix)
            total = len(keys)

            if total == 0:
                self.log("⚠️ No files found")
                self.progress = 1.0
                self.is_importing = False
                return

            self.log(f"📄 Found {total} files")

            # 2. Pre-create folder structure
            unique_dirs = set()
            for k in keys:
                d = os.path.dirname(k)
                if d:
                    unique_dirs.add(d)

            sorted_dirs = sorted(list(unique_dirs), key=len)
            self.log(f"📂 Ensuring {len(sorted_dirs)} directories exist...")

            for d in sorted_dirs:
                success, error = await self.ll_client.ensure_structure(d + "/dummy_file")
                if not success:
                    self.log(f"⚠️ Failed to create folder {d}: {error}")

            # 3. Import files with progress tracking
            self.log("🚀 Starting parallel import...")
            completed = 0

            # Process in batches to avoid overwhelming the API
            batch_size = 10
            for i in range(0, len(keys), batch_size):
                batch = keys[i:i + batch_size]

                # Process batch concurrently
                tasks = [
                    self.ll_client.import_file(key, f"/{key}")
                    for key in batch
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for key, result in zip(batch, results):
                    completed += 1
                    self.progress = completed / total
                    fname = key.split("/")[-1]

                    if isinstance(result, Exception):
                        self.log(f"❌ {fname}: {result}")
                    else:
                        code, error_msg = result
                        if code in [200, 201]:
                            self.log(f"✅ {fname}")
                        elif code in [400, 409]:
                            self.log(f"⏭️ {fname} (exists)")
                        else:
                            self.log(f"❌ {fname} (HTTP {code}): {error_msg}")

            elapsed = time.time() - start_time
            self.log(f"✅ Import complete. Time: {elapsed:.2f}s")

        except Exception as e:
            self.log(f"❌ Import failed: {e}")

        finally:
            self.progress = 1.0
            self.is_importing = False
