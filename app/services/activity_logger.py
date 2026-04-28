"""
Activity Logger Service
Centralized logging service for categorized activity logs.

Persistence note: this branch ships activity logs to stdout as structured
JSON only — the CloudWatch Logs sink for the web/worker tasks captures them
and operators query via Logs Insights. The legacy SQLite-backed
db.create_activity_log / list_activity_logs / count_activity_logs were
removed when services/database.py was rewritten for Postgres async
(awsk-rp6.8). A future Postgres-backed ActivityLog model + repo would
restore in-app filtering — tracked as a follow-up bead.
"""

from typing import Optional, Dict, Any, List

from services.logging import get_logger

_log = get_logger("activity")


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
        """Emit a structured activity log entry to stdout (CloudWatch sink).

        Returns:
            0 — sentinel. The legacy contract returned a row ID; with
            stdout-only persistence there's no ID. Callers don't appear to
            use the return value.
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
        return 0

    @staticmethod
    def list_logs(
        category: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        include_all_users: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return activity logs for an admin viewer.

        Stub: returns []. Stdout-only persistence means no in-app query
        path. Operators use CloudWatch Logs Insights against the
        /aws/ecs/connect-<env>/web log group filtered by activity_category.
        """
        return []

    @staticmethod
    def count_logs(
        category: Optional[str] = None,
        user_id: Optional[str] = None,
        include_all_users: bool = False,
    ) -> int:
        """Count activity logs. Stub: returns 0 (matches list_logs)."""
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
