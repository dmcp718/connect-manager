"""
SQLite Database for State Persistence
Stores non-sensitive configuration data
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
import json
import uuid


# Database location - use DATA_DIR env var or fall back to home directory
_data_dir = os.getenv("DATA_DIR")
if _data_dir:
    DB_PATH = Path(_data_dir) / "state.db"
else:
    DB_PATH = Path.home() / ".lucidlink-labs" / "state.db"


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating the DB if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def transaction():
    """Context manager for atomic database transactions."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database schema."""
    conn = get_connection()
    cursor = conn.cursor()

    # Users table for multi-user support
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    """)

    # User sessions for JWT tracking and revocation
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            revoked BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Settings table for key-value storage
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Connection profiles for quick switching (kept for backwards compatibility)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            bucket TEXT,
            filespace_name TEXT,
            filespace_id TEXT,
            datastore_name TEXT,
            datastore_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # DataStore credentials - stores S3 credentials for browsing DataStores
    # user_id allows per-user isolation of credentials
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS datastore_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datastore_id TEXT NOT NULL,
            datastore_name TEXT NOT NULL,
            filespace_id TEXT NOT NULL,
            filespace_name TEXT NOT NULL,
            bucket_name TEXT NOT NULL,
            region TEXT,
            endpoint TEXT,
            credentials_key TEXT NOT NULL,
            user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(datastore_id, user_id)
        )
    """)

    # Import jobs queue
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS import_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'pending',
            bucket TEXT NOT NULL,
            prefix TEXT NOT NULL,
            filespace_id TEXT NOT NULL,
            datastore_id TEXT NOT NULL,
            total_files INTEGER DEFAULT 0,
            completed_files INTEGER DEFAULT 0,
            failed_files INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    # SQS Credentials - stores IAM credentials for SQS access
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sqs_credentials (
            id INTEGER PRIMARY KEY,
            access_key TEXT NOT NULL,
            secret_key_encrypted TEXT NOT NULL,
            region TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # SQS Queues - configured queues for event-driven imports
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sqs_queues (
            id TEXT PRIMARY KEY,
            queue_url TEXT NOT NULL,
            queue_arn TEXT,
            name TEXT NOT NULL,
            region TEXT NOT NULL,
            datastore_id TEXT NOT NULL,
            filespace_id TEXT NOT NULL,
            import_prefix TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_poll_at TIMESTAMP,
            error_message TEXT
        )
    """)

    # SQS Events - tracked S3 events from SQS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sqs_events (
            id TEXT PRIMARY KEY,
            queue_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            bucket TEXT NOT NULL,
            object_key TEXT NOT NULL,
            object_size INTEGER,
            event_time TIMESTAMP,
            status TEXT DEFAULT 'pending',
            job_id TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (queue_id) REFERENCES sqs_queues(id)
        )
    """)

    # Performance indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_jobs_status ON import_jobs(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_jobs_user_id ON import_jobs(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_import_jobs_status_user ON import_jobs(status, user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sqs_events_status ON sqs_events(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sqs_events_queue_id ON sqs_events(queue_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sqs_events_queue_status ON sqs_events(queue_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sqs_events_message_id ON sqs_events(message_id)")

    # Migration: Add user_id columns to existing tables if they don't exist
    _migrate_add_user_id_columns(cursor)

    conn.commit()
    conn.close()


def _migrate_add_user_id_columns(cursor) -> None:
    """Add user_id columns to existing tables for multi-user support.

    Note: For fresh multi-user deployments, these columns are already in the schema.
    This migration handles upgrades from single-user to multi-user.
    """
    tables_to_migrate = [
        "profiles",
        "sqs_credentials",
        "sqs_queues",
        "import_jobs",
    ]

    for table in tables_to_migrate:
        # Check if user_id column exists
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        if "user_id" not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")


def _ensure_default_admin() -> str:
    """Ensure a default admin user exists, return their ID."""
    conn = get_connection()
    cursor = conn.cursor()

    # Check if any users exist
    cursor.execute("SELECT id FROM users LIMIT 1")
    existing = cursor.fetchone()

    if existing:
        conn.close()
        return existing[0]

    # Create default admin user
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    admin_id = str(uuid.uuid4())
    default_password = pwd_context.hash("admin")  # Default password, should be changed

    cursor.execute("""
        INSERT INTO users (id, email, password_hash, display_name, is_admin)
        VALUES (?, ?, ?, ?, ?)
    """, (admin_id, "admin@localhost", default_password, "Admin", True))

    conn.commit()
    conn.close()
    return admin_id


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get a setting value by key."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Set a setting value."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
    """, (key, value))
    conn.commit()
    conn.close()


def get_all_settings() -> Dict[str, str]:
    """Get all settings as a dictionary."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    rows = cursor.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def save_profile(
    name: str,
    bucket: str,
    filespace_name: str,
    filespace_id: str,
    datastore_name: str,
    datastore_id: str,
) -> int:
    """Save a connection profile."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO profiles (name, bucket, filespace_name, filespace_id, datastore_name, datastore_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET
            bucket = excluded.bucket,
            filespace_name = excluded.filespace_name,
            filespace_id = excluded.filespace_id,
            datastore_name = excluded.datastore_name,
            datastore_id = excluded.datastore_id,
            updated_at = CURRENT_TIMESTAMP
    """, (name, bucket, filespace_name, filespace_id, datastore_name, datastore_id))
    conn.commit()
    profile_id = cursor.lastrowid
    conn.close()
    return profile_id


def get_profile(name: str) -> Optional[Dict[str, Any]]:
    """Get a profile by name."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles WHERE name = ?", (name,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_default_profile() -> Optional[Dict[str, Any]]:
    """Get the default (most recently used) profile."""
    default_name = get_setting("default_profile")
    if default_name:
        return get_profile(default_name)

    # Fall back to most recent
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles ORDER BY updated_at DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_profiles() -> list:
    """List all saved profiles."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, bucket, filespace_name, datastore_name FROM profiles ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_profile(name: str) -> bool:
    """Delete a profile by name."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM profiles WHERE name = ?", (name,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ============== DataStore Credentials ==============

def save_datastore_credentials(
    datastore_id: str,
    datastore_name: str,
    filespace_id: str,
    filespace_name: str,
    bucket_name: str,
    region: Optional[str],
    endpoint: Optional[str],
    credentials_key: str,
    user_id: Optional[str] = None,
) -> int:
    """Save credentials for a DataStore (for S3 browsing)."""
    conn = get_connection()
    cursor = conn.cursor()
    # Use composite key of datastore_id + user_id for uniqueness
    cursor.execute("""
        INSERT INTO datastore_credentials (
            datastore_id, datastore_name, filespace_id, filespace_name,
            bucket_name, region, endpoint, credentials_key, user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(datastore_id, user_id) DO UPDATE SET
            datastore_name = excluded.datastore_name,
            filespace_id = excluded.filespace_id,
            filespace_name = excluded.filespace_name,
            bucket_name = excluded.bucket_name,
            region = excluded.region,
            endpoint = excluded.endpoint,
            credentials_key = excluded.credentials_key
    """, (
        datastore_id, datastore_name, filespace_id, filespace_name,
        bucket_name, region, endpoint, credentials_key, user_id
    ))
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_datastore_credentials(datastore_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get credentials for a specific DataStore (filtered by user)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute(
            "SELECT * FROM datastore_credentials WHERE datastore_id = ? AND user_id = ?",
            (datastore_id, user_id)
        )
    else:
        cursor.execute(
            "SELECT * FROM datastore_credentials WHERE datastore_id = ?",
            (datastore_id,)
        )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_datastore_credentials(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all stored DataStore credentials for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
            SELECT * FROM datastore_credentials
            WHERE user_id = ?
            ORDER BY datastore_name
        """, (user_id,))
    else:
        cursor.execute("""
            SELECT * FROM datastore_credentials
            ORDER BY datastore_name
        """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_datastore_credentials(datastore_id: str, user_id: Optional[str] = None) -> bool:
    """Delete credentials for a DataStore (filtered by user)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute(
            "DELETE FROM datastore_credentials WHERE datastore_id = ? AND user_id = ?",
            (datastore_id, user_id)
        )
    else:
        cursor.execute(
            "DELETE FROM datastore_credentials WHERE datastore_id = ?",
            (datastore_id,)
        )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ============== Import Jobs ==============

def create_job(
    bucket: str,
    prefix: str,
    filespace_id: str,
    datastore_id: str,
    user_id: Optional[str] = None,
) -> int:
    """Create a new import job."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO import_jobs (bucket, prefix, filespace_id, datastore_id, status, user_id)
        VALUES (?, ?, ?, ?, 'pending', ?)
    """, (bucket, prefix, filespace_id, datastore_id, user_id))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    return job_id


def get_job(job_id: int, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a job by ID (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute(
            "SELECT * FROM import_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id)
        )
    else:
        cursor.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_job(
    job_id: int,
    status: Optional[str] = None,
    total_files: Optional[int] = None,
    completed_files: Optional[int] = None,
    failed_files: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """Update a job's status and progress."""
    conn = get_connection()
    cursor = conn.cursor()

    updates = []
    params = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
        if status == "running":
            updates.append("started_at = CURRENT_TIMESTAMP")
        elif status in ("completed", "failed", "cancelled"):
            updates.append("completed_at = CURRENT_TIMESTAMP")

    if total_files is not None:
        updates.append("total_files = ?")
        params.append(total_files)

    if completed_files is not None:
        updates.append("completed_files = ?")
        params.append(completed_files)

    if failed_files is not None:
        updates.append("failed_files = ?")
        params.append(failed_files)

    if error_message is not None:
        updates.append("error_message = ?")
        params.append(error_message)

    if updates:
        params.append(job_id)
        cursor.execute(
            f"UPDATE import_jobs SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()

    conn.close()


def list_jobs(limit: int = 50, user_id: Optional[str] = None) -> list:
    """List recent jobs with duration and throughput calculation (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
            SELECT *,
                CASE
                    WHEN started_at IS NOT NULL AND completed_at IS NOT NULL THEN
                        CAST((julianday(completed_at) - julianday(started_at)) * 86400 AS INTEGER)
                    WHEN started_at IS NOT NULL AND status = 'running' THEN
                        CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER)
                    ELSE NULL
                END as duration_seconds,
                CASE
                    WHEN started_at IS NOT NULL AND completed_at IS NOT NULL
                         AND (julianday(completed_at) - julianday(started_at)) * 86400 > 0 THEN
                        ROUND(CAST(completed_files AS REAL) / ((julianday(completed_at) - julianday(started_at)) * 86400), 2)
                    WHEN started_at IS NOT NULL AND status = 'running'
                         AND (julianday('now') - julianday(started_at)) * 86400 > 0 THEN
                        ROUND(CAST(completed_files AS REAL) / ((julianday('now') - julianday(started_at)) * 86400), 2)
                    ELSE NULL
                END as entries_per_second
            FROM import_jobs
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
    else:
        cursor.execute("""
            SELECT *,
                CASE
                    WHEN started_at IS NOT NULL AND completed_at IS NOT NULL THEN
                        CAST((julianday(completed_at) - julianday(started_at)) * 86400 AS INTEGER)
                    WHEN started_at IS NOT NULL AND status = 'running' THEN
                        CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER)
                    ELSE NULL
                END as duration_seconds,
                CASE
                    WHEN started_at IS NOT NULL AND completed_at IS NOT NULL
                         AND (julianday(completed_at) - julianday(started_at)) * 86400 > 0 THEN
                        ROUND(CAST(completed_files AS REAL) / ((julianday(completed_at) - julianday(started_at)) * 86400), 2)
                    WHEN started_at IS NOT NULL AND status = 'running'
                         AND (julianday('now') - julianday(started_at)) * 86400 > 0 THEN
                        ROUND(CAST(completed_files AS REAL) / ((julianday('now') - julianday(started_at)) * 86400), 2)
                    ELSE NULL
                END as entries_per_second
            FROM import_jobs
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_pending_jobs() -> list:
    """Get all pending jobs in queue order."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM import_jobs
        WHERE status = 'pending'
        ORDER BY created_at ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_running_job() -> Optional[Dict[str, Any]]:
    """Get the currently running job, if any."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM import_jobs
        WHERE status = 'running'
        LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def cancel_job(job_id: int, user_id: Optional[str] = None) -> bool:
    """Cancel a pending or running job (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
            UPDATE import_jobs
            SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('pending', 'running') AND user_id = ?
        """, (job_id, user_id))
    else:
        cursor.execute("""
            UPDATE import_jobs
            SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('pending', 'running')
        """, (job_id,))
    cancelled = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return cancelled


def delete_job(job_id: int, user_id: Optional[str] = None) -> bool:
    """Delete a job from history (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute(
            "DELETE FROM import_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id)
        )
    else:
        cursor.execute("DELETE FROM import_jobs WHERE id = ?", (job_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def clear_completed_jobs(user_id: Optional[str] = None) -> int:
    """Clear all completed/failed/cancelled jobs (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
            DELETE FROM import_jobs
            WHERE status IN ('completed', 'failed', 'cancelled') AND user_id = ?
        """, (user_id,))
    else:
        cursor.execute("""
            DELETE FROM import_jobs
            WHERE status IN ('completed', 'failed', 'cancelled')
        """)
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


# ============== SQS Credentials ==============

def save_sqs_credentials(access_key: str, secret_key_encrypted: str, region: str, user_id: Optional[str] = None) -> None:
    """Save SQS IAM credentials (per-user in multi-user mode, single row otherwise)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        # Per-user credentials: delete existing for this user and insert new
        cursor.execute("DELETE FROM sqs_credentials WHERE user_id = ?", (user_id,))
        cursor.execute("""
            INSERT INTO sqs_credentials (access_key, secret_key_encrypted, region, user_id)
            VALUES (?, ?, ?, ?)
        """, (access_key, secret_key_encrypted, region, user_id))
    else:
        # Global credentials (single-user mode): delete all and insert with id=1
        cursor.execute("DELETE FROM sqs_credentials WHERE user_id IS NULL")
        cursor.execute("""
            INSERT INTO sqs_credentials (id, access_key, secret_key_encrypted, region)
            VALUES (1, ?, ?, ?)
        """, (access_key, secret_key_encrypted, region))
    conn.commit()
    conn.close()


def get_sqs_credentials(user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get stored SQS credentials (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("SELECT * FROM sqs_credentials WHERE user_id = ?", (user_id,))
    else:
        cursor.execute("SELECT * FROM sqs_credentials WHERE user_id IS NULL")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_sqs_credentials(user_id: Optional[str] = None) -> bool:
    """Delete SQS credentials (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("DELETE FROM sqs_credentials WHERE user_id = ?", (user_id,))
    else:
        cursor.execute("DELETE FROM sqs_credentials WHERE user_id IS NULL")
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ============== SQS Queues ==============

def create_sqs_queue(
    queue_id: str,
    queue_url: str,
    queue_arn: Optional[str],
    name: str,
    region: str,
    datastore_id: str,
    filespace_id: str,
    import_prefix: str = "",
    user_id: Optional[str] = None,
) -> str:
    """Create a new SQS queue configuration."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sqs_queues (id, queue_url, queue_arn, name, region, datastore_id, filespace_id, import_prefix, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (queue_id, queue_url, queue_arn, name, region, datastore_id, filespace_id, import_prefix, user_id))
    conn.commit()
    conn.close()
    return queue_id


def get_sqs_queue(queue_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get an SQS queue by ID (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute(
            "SELECT * FROM sqs_queues WHERE id = ? AND user_id = ?",
            (queue_id, user_id)
        )
    else:
        cursor.execute("SELECT * FROM sqs_queues WHERE id = ?", (queue_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_sqs_queues(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all SQS queues (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute(
            "SELECT * FROM sqs_queues WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
    else:
        cursor.execute("SELECT * FROM sqs_queues ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_active_sqs_queues() -> List[Dict[str, Any]]:
    """List only active SQS queues (for polling)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sqs_queues WHERE status = 'active' ORDER BY created_at")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_sqs_queue(
    queue_id: str,
    status: Optional[str] = None,
    last_poll_at: bool = False,
    error_message: Optional[str] = None,
    user_id: Optional[str] = None,
) -> bool:
    """Update an SQS queue (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()

    updates = []
    params = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)

    if last_poll_at:
        updates.append("last_poll_at = CURRENT_TIMESTAMP")

    if error_message is not None:
        updates.append("error_message = ?")
        params.append(error_message if error_message else None)

    if updates:
        params.append(queue_id)
        if user_id:
            params.append(user_id)
            cursor.execute(
                f"UPDATE sqs_queues SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                params
            )
        else:
            cursor.execute(
                f"UPDATE sqs_queues SET {', '.join(updates)} WHERE id = ?",
                params
            )
        conn.commit()

    updated = cursor.rowcount > 0
    conn.close()
    return updated


def delete_sqs_queue(queue_id: str, user_id: Optional[str] = None) -> bool:
    """Delete an SQS queue configuration (filtered by user in multi-user mode)."""
    with transaction() as conn:
        cursor = conn.cursor()
        # First delete associated events
        cursor.execute("DELETE FROM sqs_events WHERE queue_id = ?", (queue_id,))
        # Then delete the queue
        if user_id:
            cursor.execute(
                "DELETE FROM sqs_queues WHERE id = ? AND user_id = ?",
                (queue_id, user_id)
            )
        else:
            cursor.execute("DELETE FROM sqs_queues WHERE id = ?", (queue_id,))
        return cursor.rowcount > 0


# ============== SQS Events ==============

def create_sqs_event(
    event_id: str,
    queue_id: str,
    message_id: str,
    event_type: str,
    bucket: str,
    object_key: str,
    object_size: Optional[int],
    event_time: Optional[str],
) -> str:
    """Create a new SQS event record."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sqs_events (id, queue_id, message_id, event_type, bucket, object_key, object_size, event_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_id, queue_id, message_id, event_type, bucket, object_key, object_size, event_time))
    conn.commit()
    conn.close()
    return event_id


def update_sqs_event(
    event_id: str,
    status: Optional[str] = None,
    job_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    """Update an SQS event."""
    conn = get_connection()
    cursor = conn.cursor()

    updates = []
    params = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)

    if job_id is not None:
        updates.append("job_id = ?")
        params.append(job_id)

    if error_message is not None:
        updates.append("error_message = ?")
        params.append(error_message if error_message else None)

    if updates:
        params.append(event_id)
        cursor.execute(
            f"UPDATE sqs_events SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()

    updated = cursor.rowcount > 0
    conn.close()
    return updated


def list_sqs_events(limit: int = 50, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List recent SQS events (filtered by user via queue's user_id in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
            SELECT e.*, q.name as queue_name
            FROM sqs_events e
            LEFT JOIN sqs_queues q ON e.queue_id = q.id
            WHERE q.user_id = ?
            ORDER BY e.created_at DESC
            LIMIT ?
        """, (user_id, limit))
    else:
        cursor.execute("""
            SELECT e.*, q.name as queue_name
            FROM sqs_events e
            LEFT JOIN sqs_queues q ON e.queue_id = q.id
            ORDER BY e.created_at DESC
            LIMIT ?
        """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_sqs_event_count_today(queue_id: str, user_id: Optional[str] = None) -> int:
    """Get count of events for a queue today (filtered by user in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        # Verify queue belongs to user before counting
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM sqs_events e
            JOIN sqs_queues q ON e.queue_id = q.id
            WHERE e.queue_id = ? AND q.user_id = ? AND date(e.created_at) = date('now')
        """, (queue_id, user_id))
    else:
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM sqs_events
            WHERE queue_id = ? AND date(created_at) = date('now')
        """, (queue_id,))
    row = cursor.fetchone()
    conn.close()
    return row["count"] if row else 0


def clear_old_sqs_events(days: int = 7) -> int:
    """Clear SQS events older than specified days."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM sqs_events
        WHERE created_at < datetime('now', '-' || ? || ' days')
    """, (days,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def clear_sqs_events(user_id: Optional[str] = None) -> int:
    """Clear all SQS events (filtered by user via queue's user_id in multi-user mode)."""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
            DELETE FROM sqs_events
            WHERE queue_id IN (SELECT id FROM sqs_queues WHERE user_id = ?)
        """, (user_id,))
    else:
        cursor.execute("DELETE FROM sqs_events")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


# ============== User Management ==============

def create_user(
    email: str,
    password_hash: str,
    display_name: Optional[str] = None,
    is_admin: bool = False,
) -> str:
    """Create a new user and return their ID."""
    conn = get_connection()
    cursor = conn.cursor()

    user_id = str(uuid.uuid4())
    cursor.execute("""
        INSERT INTO users (id, email, password_hash, display_name, is_admin)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, email.lower(), password_hash, display_name or email.split("@")[0], is_admin))

    conn.commit()
    conn.close()
    return user_id


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Get a user by their ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Get a user by their email."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email.lower(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_last_login(user_id: str) -> None:
    """Update user's last login timestamp."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def update_user_password(user_id: str, password_hash: str) -> bool:
    """Update a user's password."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (password_hash, user_id)
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def list_users() -> List[Dict[str, Any]]:
    """List all users."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, email, display_name, is_admin, created_at, last_login
        FROM users
        ORDER BY created_at
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_user(user_id: str) -> bool:
    """Delete a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def count_users() -> int:
    """Count total number of users."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM users")
    row = cursor.fetchone()
    conn.close()
    return row["count"] if row else 0


def update_user_role(user_id: str, is_admin: bool) -> bool:
    """Update a user's admin role."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET is_admin = ? WHERE id = ?",
        (is_admin, user_id)
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ============== Session Management ==============

def create_session(user_id: str, session_id: str, expires_at: datetime) -> str:
    """Create a new session record."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_sessions (id, user_id, expires_at)
        VALUES (?, ?, ?)
    """, (session_id, user_id, expires_at.isoformat()))
    conn.commit()
    conn.close()
    return session_id


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get a session by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, u.email, u.display_name, u.is_admin
        FROM user_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.id = ? AND s.revoked = FALSE
    """, (session_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def revoke_session(session_id: str) -> bool:
    """Revoke a session."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE user_sessions SET revoked = TRUE WHERE id = ?",
        (session_id,)
    )
    revoked = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return revoked


def revoke_all_user_sessions(user_id: str) -> int:
    """Revoke all sessions for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE user_sessions SET revoked = TRUE WHERE user_id = ?",
        (user_id,)
    )
    revoked = cursor.rowcount
    conn.commit()
    conn.close()
    return revoked


def cleanup_expired_sessions() -> int:
    """Remove expired sessions."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM user_sessions
        WHERE expires_at < datetime('now') OR revoked = TRUE
    """)
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted
