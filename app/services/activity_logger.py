"""Activity Logger Service.

Categorized activity logs that ship two places:
1. **stdout → CloudWatch Logs** as structured JSON, always (operator
   query path via Logs Insights).
2. **Postgres `activity_logs` table** when a sessionmaker has been
   wired via :func:`configure_persistence`. Persisted via fire-and-
   forget asyncio tasks so the sync ``log()`` API used at ~38 call
   sites doesn't have to await — failures fall back to stdout-only.

The in-UI tabs (logs_app / logs_jobs / logs_sqs / logs_admin) read
from Postgres via :func:`alist_logs` + :func:`acount_logs`; routes
own their own AsyncSession via Depends(get_db).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.repositories.activity_log import ActivityLogRepository
from services.logging import get_logger

_log = get_logger("activity")

# Set by services.activity_logger.configure_persistence() at app startup.
# When None, log() is stdout-only — keeps the module usable from tests
# and from the worker's startup-before-DB path.
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def configure_persistence(sm: async_sessionmaker[AsyncSession]) -> None:
    """Wire the async_sessionmaker for fire-and-forget DB inserts.

    Called from main._startup and worker.on_startup. Idempotent — last
    write wins, which matches our singleton sessionmaker pattern.
    """
    global _sessionmaker
    _sessionmaker = sm


def _parse_user_id(user_id: Optional[str]) -> Optional[uuid.UUID]:
    if user_id is None:
        return None
    try:
        return uuid.UUID(user_id)
    except (ValueError, AttributeError):
        return None


async def _persist(
    sm: async_sessionmaker[AsyncSession],
    *,
    category: str,
    action: str,
    message: str,
    level: str,
    user_uuid: Optional[uuid.UUID],
    details: Optional[Dict[str, Any]],
    related_id: Optional[str],
    related_type: Optional[str],
    ip_address: Optional[str],
) -> None:
    """Background task body — owns its own session and commits."""
    try:
        async with sm() as session:
            await ActivityLogRepository(session).create_entry(
                category=category,
                action=action,
                message=message,
                level=level,
                user_id=user_uuid,
                details=details,
                related_id=related_id,
                related_type=related_type,
                ip_address=ip_address,
            )
            await session.commit()
    except Exception as exc:  # pragma: no cover — degraded-mode log only
        _log.warning(
            "activity-log persist failed; row stays stdout-only",
            extra={"error": str(exc), "category": category, "action": action},
        )


async def alist_logs(
    session: AsyncSession,
    *,
    category: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    include_all_users: bool = False,
) -> List[Dict[str, Any]]:
    """Return logs in the dict shape the templates expect."""
    rows = await ActivityLogRepository(session).list_filtered(
        category=category,
        user_id=_parse_user_id(user_id),
        include_all_users=include_all_users,
        limit=limit,
        offset=offset,
    )
    return [
        {
            "id": r.id,
            "category": r.category,
            "action": r.action,
            "message": r.message,
            "level": r.level,
            "user_id": str(r.user_id) if r.user_id else None,
            "details": r.details,
            "related_id": r.related_id,
            "related_type": r.related_type,
            "ip_address": r.ip_address,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def acount_logs(
    session: AsyncSession,
    *,
    category: Optional[str] = None,
    user_id: Optional[str] = None,
    include_all_users: bool = False,
) -> int:
    return await ActivityLogRepository(session).count_filtered(
        category=category,
        user_id=_parse_user_id(user_id),
        include_all_users=include_all_users,
    )


async def aclear_logs(
    session: AsyncSession,
    *,
    category: Optional[str] = None,
    user_id: Optional[str] = None,
) -> int:
    return await ActivityLogRepository(session).clear_for_user(
        category=category,
        user_id=_parse_user_id(user_id),
    )


class ActivityLogger:
    """Centralized activity logging service with category-based organization."""

    # Log categories
    APP = "app"
    JOB = "job"
    SQS = "sqs"
    ADMIN = "admin"

    # Log levels
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"

    # Mapping from our level names to stdlib logging levels.
    _LEVEL_MAP = {
        "info": "info",
        "warning": "warning",
        "error": "error",
        "success": "info",  # Custom level; maps to info severity.
    }

    @staticmethod
    def log(
        category: str,
        action: str,
        message: str,
        user_id: Optional[str] = None,
        level: str = "info",
        details: Optional[Dict[str, Any]] = None,
        related_id: Optional[str] = None,
        related_type: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> int:
        """Emit a structured activity log entry.

        - Always ships to stdout → CloudWatch Logs (structured JSON).
        - Additionally fires an asyncio.create_task to persist to the
          ``activity_logs`` table, IF :func:`configure_persistence` has
          wired a sessionmaker AND a running event loop is available.
          Both conditions are true inside FastAPI route handlers and
          ARQ workers; the test path may not have a loop, in which
          case we degrade gracefully to stdout-only.

        Returns:
            0 — legacy sentinel; callers don't read the return value.
        """
        log_method = getattr(_log, ActivityLogger._LEVEL_MAP.get(level, "info"))
        log_method(
            message,
            extra={
                "activity_category": category,
                "activity_action": action,
                "activity_level": level,
                "user_id": user_id,
                "details": details,
                "related_id": related_id,
                "related_type": related_type,
                "ip_address": ip_address,
            },
        )

        sm = _sessionmaker
        if sm is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(
                    _persist(
                        sm,
                        category=category,
                        action=action,
                        message=message,
                        level=level,
                        user_uuid=_parse_user_id(user_id),
                        details=details,
                        related_id=related_id,
                        related_type=related_type,
                        ip_address=ip_address,
                    )
                )
        return 0

    @staticmethod
    def list_logs(
        category: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        include_all_users: bool = False,
    ) -> List[Dict[str, Any]]:
        """Synchronous shim — returns []. Use :func:`alist_logs` from
        async route handlers; this method survives only because removing
        it would break import paths in pre-async tests."""
        return []

    @staticmethod
    def count_logs(
        category: Optional[str] = None,
        user_id: Optional[str] = None,
        include_all_users: bool = False,
    ) -> int:
        """Synchronous shim — returns 0. Use :func:`acount_logs` instead."""
        return 0

    # ============== Application Logs ==============

    @classmethod
    def app_info(
        cls,
        message: str,
        user_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log an application info message."""
        return cls.log(
            category=cls.APP,
            action="info",
            message=message,
            user_id=user_id,
            level=cls.INFO,
            details=details,
            ip_address=ip_address,
        )

    @classmethod
    def app_error(
        cls,
        message: str,
        user_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log an application error."""
        return cls.log(
            category=cls.APP,
            action="error",
            message=message,
            user_id=user_id,
            level=cls.ERROR,
            details=details,
            ip_address=ip_address,
        )

    @classmethod
    def app_warning(
        cls,
        message: str,
        user_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log an application warning."""
        return cls.log(
            category=cls.APP,
            action="warning",
            message=message,
            user_id=user_id,
            level=cls.WARNING,
            details=details,
            ip_address=ip_address,
        )

    @classmethod
    def app_connection(
        cls,
        user_id: Optional[str],
        filespace_name: str,
        datastore_count: int,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log a successful API connection."""
        return cls.log(
            category=cls.APP,
            action="connection",
            message=f"Connected to filespace '{filespace_name}' with {datastore_count} DataStore(s)",
            user_id=user_id,
            level=cls.SUCCESS,
            details={
                "filespace_name": filespace_name,
                "datastore_count": datastore_count,
            },
            ip_address=ip_address,
        )

    @classmethod
    def app_datastore_created(
        cls,
        user_id: Optional[str],
        datastore_name: str,
        bucket: str,
        filespace_name: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log DataStore creation."""
        return cls.log(
            category=cls.APP,
            action="datastore_created",
            message=f"Created DataStore '{datastore_name}' for bucket '{bucket}' in filespace '{filespace_name}'",
            user_id=user_id,
            level=cls.SUCCESS,
            details={
                "datastore_name": datastore_name,
                "bucket": bucket,
                "filespace_name": filespace_name,
            },
            related_type="datastore",
            ip_address=ip_address,
        )

    @classmethod
    def app_datastore_deleted(
        cls,
        user_id: Optional[str],
        datastore_name: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log DataStore deletion."""
        return cls.log(
            category=cls.APP,
            action="datastore_deleted",
            message=f"Deleted DataStore '{datastore_name}'",
            user_id=user_id,
            level=cls.INFO,
            details={"datastore_name": datastore_name},
            related_type="datastore",
            ip_address=ip_address,
        )

    # ============== Job Logs ==============

    @classmethod
    def job_started(
        cls,
        user_id: Optional[str],
        job_id: int,
        prefix: str,
        total_files: int = 0,
    ) -> int:
        """Log job start."""
        return cls.log(
            category=cls.JOB,
            action="job_started",
            message=f"Started import job for '{prefix}' ({total_files} files)",
            user_id=user_id,
            level=cls.INFO,
            details={"prefix": prefix, "total_files": total_files},
            related_id=str(job_id),
            related_type="job",
        )

    @classmethod
    def job_progress(
        cls,
        user_id: Optional[str],
        job_id: int,
        completed: int,
        total: int,
    ) -> int:
        """Log job progress (use sparingly, e.g., at 25%, 50%, 75%)."""
        percent = round((completed / total) * 100) if total > 0 else 0
        return cls.log(
            category=cls.JOB,
            action="job_progress",
            message=f"Job #{job_id} progress: {completed}/{total} ({percent}%)",
            user_id=user_id,
            level=cls.INFO,
            details={"completed": completed, "total": total, "percent": percent},
            related_id=str(job_id),
            related_type="job",
        )

    @classmethod
    def job_completed(
        cls,
        user_id: Optional[str],
        job_id: int,
        completed: int,
        failed: int,
        duration_seconds: Optional[int] = None,
    ) -> int:
        """Log job completion."""
        total = completed + failed
        level = cls.SUCCESS if failed == 0 else cls.WARNING
        message = f"Completed job #{job_id}: {completed}/{total} files imported"
        if failed > 0:
            message += f" ({failed} failed)"
        if duration_seconds:
            message += f" in {duration_seconds}s"

        return cls.log(
            category=cls.JOB,
            action="job_completed",
            message=message,
            user_id=user_id,
            level=level,
            details={
                "completed": completed,
                "failed": failed,
                "duration_seconds": duration_seconds,
            },
            related_id=str(job_id),
            related_type="job",
        )

    @classmethod
    def job_failed(
        cls,
        user_id: Optional[str],
        job_id: int,
        error: str,
    ) -> int:
        """Log job failure."""
        return cls.log(
            category=cls.JOB,
            action="job_failed",
            message=f"Job #{job_id} failed: {error}",
            user_id=user_id,
            level=cls.ERROR,
            details={"error": error},
            related_id=str(job_id),
            related_type="job",
        )

    @classmethod
    def job_cancelled(
        cls,
        user_id: Optional[str],
        job_id: int,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log job cancellation."""
        return cls.log(
            category=cls.JOB,
            action="job_cancelled",
            message=f"Job #{job_id} cancelled by user",
            user_id=user_id,
            level=cls.WARNING,
            details={},
            related_id=str(job_id),
            related_type="job",
            ip_address=ip_address,
        )

    @classmethod
    def job_queued(
        cls,
        user_id: Optional[str],
        job_id: int,
        prefix: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log job queued for processing."""
        return cls.log(
            category=cls.JOB,
            action="job_queued",
            message=f"Queued import job #{job_id} for '{prefix}'",
            user_id=user_id,
            level=cls.INFO,
            details={"prefix": prefix},
            related_id=str(job_id),
            related_type="job",
            ip_address=ip_address,
        )

    @classmethod
    def single_file_import(
        cls,
        user_id: Optional[str],
        object_key: str,
        success: bool,
        error: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log single file import (not part of a batch job)."""
        if success:
            return cls.log(
                category=cls.JOB,
                action="file_imported",
                message=f"Imported file '{object_key}'",
                user_id=user_id,
                level=cls.SUCCESS,
                details={"object_key": object_key},
                ip_address=ip_address,
            )
        else:
            return cls.log(
                category=cls.JOB,
                action="file_import_failed",
                message=f"Failed to import file '{object_key}': {error}",
                user_id=user_id,
                level=cls.ERROR,
                details={"object_key": object_key, "error": error},
                ip_address=ip_address,
            )

    # ============== SQS Logs ==============

    @classmethod
    def sqs_credentials_saved(
        cls,
        user_id: Optional[str],
        region: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log SQS credentials saved."""
        return cls.log(
            category=cls.SQS,
            action="credentials_saved",
            message=f"SQS credentials saved for region '{region}'",
            user_id=user_id,
            level=cls.SUCCESS,
            details={"region": region},
            ip_address=ip_address,
        )

    @classmethod
    def sqs_credentials_deleted(
        cls,
        user_id: Optional[str],
        ip_address: Optional[str] = None,
    ) -> int:
        """Log SQS credentials deleted."""
        return cls.log(
            category=cls.SQS,
            action="credentials_deleted",
            message="SQS credentials removed",
            user_id=user_id,
            level=cls.INFO,
            ip_address=ip_address,
        )

    @classmethod
    def sqs_queue_created(
        cls,
        user_id: Optional[str],
        queue_name: str,
        queue_id: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log SQS queue configuration created."""
        return cls.log(
            category=cls.SQS,
            action="queue_created",
            message=f"Created SQS queue configuration '{queue_name}'",
            user_id=user_id,
            level=cls.SUCCESS,
            details={"queue_name": queue_name},
            related_id=queue_id,
            related_type="queue",
            ip_address=ip_address,
        )

    @classmethod
    def sqs_queue_deleted(
        cls,
        user_id: Optional[str],
        queue_name: str,
        queue_id: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log SQS queue configuration deleted."""
        return cls.log(
            category=cls.SQS,
            action="queue_deleted",
            message=f"Deleted SQS queue configuration '{queue_name}'",
            user_id=user_id,
            level=cls.INFO,
            details={"queue_name": queue_name},
            related_id=queue_id,
            related_type="queue",
            ip_address=ip_address,
        )

    @classmethod
    def sqs_event_processed(
        cls,
        user_id: Optional[str],
        queue_id: str,
        queue_name: str,
        object_key: str,
        status: str,
        error: Optional[str] = None,
    ) -> int:
        """Log SQS event processing result."""
        if status == "success":
            return cls.log(
                category=cls.SQS,
                action="event_processed",
                message=f"Processed S3 event for '{object_key}' from queue '{queue_name}'",
                user_id=user_id,
                level=cls.SUCCESS,
                details={"object_key": object_key, "queue_name": queue_name},
                related_id=queue_id,
                related_type="queue",
            )
        else:
            return cls.log(
                category=cls.SQS,
                action="event_failed",
                message=f"Failed to process S3 event for '{object_key}' from queue '{queue_name}': {error}",
                user_id=user_id,
                level=cls.ERROR,
                details={
                    "object_key": object_key,
                    "queue_name": queue_name,
                    "error": error,
                },
                related_id=queue_id,
                related_type="queue",
            )

    @classmethod
    def sqs_polling_error(
        cls,
        user_id: Optional[str],
        queue_id: str,
        queue_name: str,
        error: str,
    ) -> int:
        """Log SQS polling error."""
        return cls.log(
            category=cls.SQS,
            action="polling_error",
            message=f"Error polling queue '{queue_name}': {error}",
            user_id=user_id,
            level=cls.ERROR,
            details={"queue_name": queue_name, "error": error},
            related_id=queue_id,
            related_type="queue",
        )

    # ============== Admin Logs ==============

    @classmethod
    def admin_login(
        cls,
        user_id: str,
        email: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log user login."""
        return cls.log(
            category=cls.ADMIN,
            action="login",
            message=f"User '{email}' logged in",
            user_id=user_id,
            level=cls.INFO,
            details={"email": email},
            related_id=user_id,
            related_type="user",
            ip_address=ip_address,
        )

    @classmethod
    def admin_logout(
        cls,
        user_id: str,
        email: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log user logout."""
        return cls.log(
            category=cls.ADMIN,
            action="logout",
            message=f"User '{email}' logged out",
            user_id=user_id,
            level=cls.INFO,
            details={"email": email},
            related_id=user_id,
            related_type="user",
            ip_address=ip_address,
        )

    @classmethod
    def admin_login_failed(
        cls,
        email: str,
        reason: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log failed login attempt."""
        return cls.log(
            category=cls.ADMIN,
            action="login_failed",
            message=f"Failed login attempt for '{email}': {reason}",
            user_id=None,
            level=cls.WARNING,
            details={"email": email, "reason": reason},
            ip_address=ip_address,
        )

    @classmethod
    def admin_user_created(
        cls,
        admin_id: str,
        new_user_email: str,
        new_user_id: str,
        is_admin: bool = False,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log user creation by admin."""
        role = "admin" if is_admin else "user"
        return cls.log(
            category=cls.ADMIN,
            action="user_created",
            message=f"Created {role} account for '{new_user_email}'",
            user_id=admin_id,
            level=cls.SUCCESS,
            details={
                "new_user_email": new_user_email,
                "new_user_id": new_user_id,
                "is_admin": is_admin,
            },
            related_id=new_user_id,
            related_type="user",
            ip_address=ip_address,
        )

    @classmethod
    def admin_user_deleted(
        cls,
        admin_id: str,
        deleted_email: str,
        deleted_user_id: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log user deletion by admin."""
        return cls.log(
            category=cls.ADMIN,
            action="user_deleted",
            message=f"Deleted user account '{deleted_email}'",
            user_id=admin_id,
            level=cls.WARNING,
            details={
                "deleted_email": deleted_email,
                "deleted_user_id": deleted_user_id,
            },
            related_id=deleted_user_id,
            related_type="user",
            ip_address=ip_address,
        )

    @classmethod
    def admin_password_changed(
        cls,
        user_id: str,
        email: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log password change."""
        return cls.log(
            category=cls.ADMIN,
            action="password_changed",
            message=f"User '{email}' changed their password",
            user_id=user_id,
            level=cls.INFO,
            details={"email": email},
            related_id=user_id,
            related_type="user",
            ip_address=ip_address,
        )

    @classmethod
    def admin_role_changed(
        cls,
        admin_id: str,
        target_user_id: str,
        target_email: str,
        new_role: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """Log user role change."""
        return cls.log(
            category=cls.ADMIN,
            action="role_changed",
            message=f"Changed role for '{target_email}' to {new_role}",
            user_id=admin_id,
            level=cls.INFO,
            details={
                "target_email": target_email,
                "target_user_id": target_user_id,
                "new_role": new_role,
            },
            related_id=target_user_id,
            related_type="user",
            ip_address=ip_address,
        )
