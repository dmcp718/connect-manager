"""
SQLite Database for State Persistence
Stores non-sensitive configuration data
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any
import json


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


def init_db() -> None:
    """Initialize the database schema."""
    conn = get_connection()
    cursor = conn.cursor()

    # Settings table for key-value storage
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Connection profiles for quick switching
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

    conn.commit()
    conn.close()


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


# ============== Import Jobs ==============

def create_job(
    bucket: str,
    prefix: str,
    filespace_id: str,
    datastore_id: str,
) -> int:
    """Create a new import job."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO import_jobs (bucket, prefix, filespace_id, datastore_id, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (bucket, prefix, filespace_id, datastore_id))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    return job_id


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    """Get a job by ID."""
    conn = get_connection()
    cursor = conn.cursor()
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


def list_jobs(limit: int = 50) -> list:
    """List recent jobs with duration and throughput calculation."""
    conn = get_connection()
    cursor = conn.cursor()
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


def cancel_job(job_id: int) -> bool:
    """Cancel a pending or running job."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE import_jobs
        SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status IN ('pending', 'running')
    """, (job_id,))
    cancelled = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return cancelled


def delete_job(job_id: int) -> bool:
    """Delete a job from history."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM import_jobs WHERE id = ?", (job_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def clear_completed_jobs() -> int:
    """Clear all completed/failed/cancelled jobs."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM import_jobs
        WHERE status IN ('completed', 'failed', 'cancelled')
    """)
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted
