"""SQLAlchemy 2.x async declarative models for the CONNECT Manager Postgres schema.

Greenfield Postgres translation of the SQLite schema on `main`. Column names match
the existing schema exactly so the API layer (Pydantic schemas, route handlers) can
swap repositories without churn. Repositories and the AsyncSession factory are
introduced in Epic 4 (awsk-rp8.*); this module is models-only.

Scope: User + auth tables (Bead awsk-rp6.4) and Datastore + SQS-config tables
(Bead awsk-rp6.5). Job/processed_jobs land in awsk-rp6.6.

Customer-credential confidentiality (CLAUDE.md rule #5, SPEC §1.3 / §5.1)
-----------------------------------------------------------------------
On `main` the SQLite schema stores opaque TEXT identifiers (`credentials_key`,
`secret_key_encrypted`) that point at a Fernet-encrypted JSON blob in
`DATA_DIR/secrets.enc`. K8s pods are stateless and have no shared `DATA_DIR`,
so this branch folds the ciphertext into Postgres directly: the same columns
become `BYTEA` holding Fernet-encrypted bytes. Encryption-at-rest semantics
are unchanged — the Fernet key is still derived from `JWT_SECRET_KEY` via the
same PBKDF2 path used in `app/services/secrets.py` on `main`. Repository code
in Epic 4 (`app/db/repos/**`) owns encrypt-on-write and decrypt-on-read; this
module never holds plaintext credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all CONNECT Manager Postgres models."""


class User(Base):
    """A CONNECT Manager user account.

    On `main` the `id` column is TEXT containing a stringified UUID (see
    `services/auth.py: register_user` which calls `str(uuid4())`). We promote
    this to a real PostgreSQL `UUID` column with a `uuid.uuid4` default so the
    application no longer has to stringify on insert.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserSession(Base):
    """JWT-backed session record for revocation support.

    The `id` is the JWT `jti` claim — `services/auth.py: create_access_token`
    generates it with `str(uuid4())` and embeds it in the token. Revocation is
    by row deletion or by setting `revoked=true`; `decode_token` checks
    `get_session(session_id)` to reject revoked tokens.
    """

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    user: Mapped["User"] = relationship(back_populates="sessions")


class DatastoreCredentials(Base):
    """Customer S3 credentials for browsing a LucidLink DataStore's backing bucket.

    Mirrors the `datastore_credentials` table on `main`. The cleartext-vs-ciphertext
    semantics are preserved across the SQLite -> Postgres jump:

    - `credentials_key` on `main` is a TEXT identifier (8-hex-char UUID slice) that
      points at an entry in `DATA_DIR/secrets.enc` keyed `aws_creds_<credentials_key>`,
      whose value is a Fernet-encrypted JSON blob `{access_key, secret_key}`.
    - On this branch there is no shared `DATA_DIR`, so the ciphertext lives inline
      as a `BYTEA` here. The column name stays `credentials_key` for API/Pydantic
      compatibility, but its on-disk type is `LargeBinary` holding Fernet ciphertext.

    The repository layer (Epic 4) is the only code that calls
    `Fernet.encrypt(...) -> bytes` on the way in and `Fernet.decrypt(...)` on the
    way out. Models never touch plaintext.

    `user_id` is nullable because `main` permits anonymous (single-user) rows and
    only adds the column via a runtime ALTER (`_migrate_add_user_id_columns`).
    The composite uniqueness `(datastore_id, user_id)` matches the SQLite
    `UNIQUE(datastore_id, user_id)` clause.
    """

    __tablename__ = "datastore_credentials"
    __table_args__ = (
        UniqueConstraint(
            "datastore_id",
            "user_id",
            name="uq_datastore_credentials_datastore_user",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    datastore_id: Mapped[str] = mapped_column(String, nullable=False)
    datastore_name: Mapped[str] = mapped_column(String, nullable=False)
    filespace_id: Mapped[str] = mapped_column(String, nullable=False)
    filespace_name: Mapped[str] = mapped_column(String, nullable=False)
    bucket_name: Mapped[str] = mapped_column(String, nullable=False)
    region: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    endpoint: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Fernet ciphertext of `{access_key, secret_key}` JSON; decrypted only by repo layer.
    credentials_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[Optional["User"]] = relationship()


# Alias name to satisfy `bd show awsk-rp6.5`'s wording ("Datastore" entity).
# The on-disk table is `datastore_credentials` to match `main`.
Datastore = DatastoreCredentials


class SqsCredentials(Base):
    """IAM user credentials for polling customer SQS queues.

    On `main` `secret_key_encrypted` is a TEXT identifier — `app/services/sqs_poller.py`
    fetches the actual ciphertext via `secrets.get_secret(f"sqs_secret_{secret_key_encrypted}")`.
    Same K8s rationale as `DatastoreCredentials`: we keep the column name for API
    compatibility but promote the type to `BYTEA` holding Fernet ciphertext directly.

    `access_key` is the customer's IAM access-key ID and is non-sensitive on its own
    (matches `main`'s plaintext storage). The pair only unlocks SQS when combined
    with the decrypted secret.
    """

    __tablename__ = "sqs_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    access_key: Mapped[str] = mapped_column(String, nullable=False)
    # Fernet ciphertext of the IAM secret-access-key; decrypted only by repo layer.
    secret_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    region: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[Optional["User"]] = relationship()


class SqsQueue(Base):
    """A configured SQS queue feeding event-driven imports.

    On `main` the primary key is TEXT (a stringified UUID generated by the API layer
    on insert). We promote it to a real `UUID` column with a `uuid.uuid4` default,
    matching the User/UserSession treatment.
    """

    __tablename__ = "sqs_queues"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    queue_url: Mapped[str] = mapped_column(String, nullable=False)
    queue_arn: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    region: Mapped[str] = mapped_column(String, nullable=False)
    datastore_id: Mapped[str] = mapped_column(String, nullable=False)
    filespace_id: Mapped[str] = mapped_column(String, nullable=False)
    import_prefix: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
        server_default="",
    )
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="active",
        server_default="active",
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_poll_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    user: Mapped[Optional["User"]] = relationship()
    events: Mapped[list["SqsEvent"]] = relationship(
        back_populates="queue",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SqsEvent(Base):
    """Tracked S3 event delivered via SQS.

    Mirrors `sqs_events` on `main`. `id` and `queue_id` are stringified UUIDs on
    `main`; we promote both to native `UUID`. `job_id` stays a free-form string
    because the Job table is owned by `awsk-rp6.6` and may use a different PK
    type — repository layer can tighten the FK once that lands.
    """

    __tablename__ = "sqs_events"
    __table_args__ = (
        Index("idx_sqs_events_status", "status"),
        Index("idx_sqs_events_queue_id", "queue_id"),
        Index("idx_sqs_events_queue_status", "queue_id", "status"),
        Index("idx_sqs_events_message_id", "message_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    queue_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sqs_queues.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)
    object_key: Mapped[str] = mapped_column(String, nullable=False)
    object_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    event_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    job_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    queue: Mapped["SqsQueue"] = relationship(back_populates="events")


class Job(Base):
    """An import job (S3 prefix → LucidLink filespace ingestion).

    Mirrors `import_jobs` on `main`. Column names are kept identical so route
    handlers and Pydantic schemas can swap the SQLite layer for the Postgres
    repository without touching keys. The on-disk table name stays
    `import_jobs` for the same reason; the Python class is `Job` because the
    surrounding code (services/job_queue.py, services/worker.py) refers to
    "jobs" and the `import_` prefix is a SQLite-era artifact.

    `id` is an autoincrement integer matching `main`'s `INTEGER PRIMARY KEY
    AUTOINCREMENT` — the API layer relies on small numeric IDs in URLs and
    log lines (e.g. `Job #42`), so promoting to UUID would be a breaking
    contract change forbidden by CLAUDE.md rule #2.

    `user_id` is nullable because `main` adds it via runtime ALTER
    (`_migrate_add_user_id_columns`); existing rows pre-migration have NULL.
    """

    __tablename__ = "import_jobs"
    __table_args__ = (
        Index("idx_import_jobs_status", "status"),
        Index("idx_import_jobs_status_user", "status", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    bucket: Mapped[str] = mapped_column(String, nullable=False)
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    filespace_id: Mapped[str] = mapped_column(String, nullable=False)
    datastore_id: Mapped[str] = mapped_column(String, nullable=False)
    total_files: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    completed_files: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    failed_files: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    user: Mapped[Optional["User"]] = relationship()


class ProcessedJob(Base):
    """Worker-idempotency dedup primitive (CLAUDE.md architecture rule #7, SPEC §5.5).

    KEDA scales workers from zero on queue depth and reaps idle pods. A worker
    that dies mid-execution leaves its ARQ job un-acked; ARQ then redelivers
    the same job to a fresh worker, which would otherwise re-import the same
    S3 prefix into LucidLink. This table is the dedup primitive: every worker
    entry-point INSERTs a row keyed by `job_id` with `ON CONFLICT (job_id) DO
    NOTHING` semantics and skips the body if the insert affected zero rows.

    `job_id` is an ARQ-level string (the `_job_id` ARQ assigns at enqueue
    time, not the import_jobs PK) so the dedup happens at the redelivery
    boundary that ARQ controls. It's a free-form String rather than a FK to
    `import_jobs.id` because cron/maintenance jobs (e.g. `timeout_stale_jobs`
    in services/worker.py) also pass through this table and have no
    import_jobs row.

    Greenfield table — `main` has no equivalent. Schema is intentionally
    minimal: dedup is the entire feature. `worker_id` and `result_status` are
    captured for forensics (which pod claimed which job, what came of it)
    but are not load-bearing for correctness — the PK uniqueness is.
    """

    __tablename__ = "processed_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    worker_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    result_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class ActivityLog(Base):
    """Audit-style activity log row.

    ActivityLogger.log() emits one of these per significant user action
    (login, DataStore CRUD, job lifecycle, SQS event processing, admin
    role changes). Logs also ship to stdout → CloudWatch as structured
    JSON; this table backs the in-UI filter/list views (logs_app.html
    et al.) where the admin tabs paginate by category and user_id.

    user_id NULLable: some categories of event (failed login attempt,
    pre-auth bootstrap actions) have no associated user.
    """

    __tablename__ = "activity_logs"
    __table_args__ = (
        Index("idx_activity_logs_category_created", "category", "created_at"),
        Index("idx_activity_logs_user_created", "user_id", "created_at"),
        Index("idx_activity_logs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str] = mapped_column(
        String, nullable=False, default="info", server_default="info"
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    related_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    related_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class UserSetting(Base):
    """Per-user key/value setting.

    Used for: ``api_host`` (LucidLink API endpoint per user, read by the
    SQS poller and the worker). Anything that needs per-user persistence
    and doesn't warrant its own table goes here.

    user_id is part of the composite PK and therefore NOT NULL — multi-
    user mode is mandatory on aws-fargate, so the legacy SQLite "global
    setting with user_id NULL" tier is intentionally not modelled.
    """

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "Base",
    "User",
    "UserSession",
    "UserSetting",
    "ActivityLog",
    "DatastoreCredentials",
    "Datastore",
    "SqsCredentials",
    "SqsQueue",
    "SqsEvent",
    "Job",
    "ProcessedJob",
]
