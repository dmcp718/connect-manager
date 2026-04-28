"""baseline: full Postgres schema for the K8s data plane

Greenfield translation of the SQLite schema on `main`. Creates every table
declared in app/db/models.py: users, user_sessions, datastore_credentials,
sqs_credentials, sqs_queues, sqs_events, import_jobs, processed_jobs.

Revision ID: 138ac64ad825
Revises:
Create Date: 2026-04-27 17:16:51.690491
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '138ac64ad825'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('processed_jobs',
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('worker_id', sa.String(), nullable=True),
    sa.Column('result_status', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('job_id')
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('password_hash', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=True),
    sa.Column('is_admin', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('datastore_credentials',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('datastore_id', sa.String(), nullable=False),
    sa.Column('datastore_name', sa.String(), nullable=False),
    sa.Column('filespace_id', sa.String(), nullable=False),
    sa.Column('filespace_name', sa.String(), nullable=False),
    sa.Column('bucket_name', sa.String(), nullable=False),
    sa.Column('region', sa.String(), nullable=True),
    sa.Column('endpoint', sa.String(), nullable=True),
    sa.Column('credentials_key', sa.LargeBinary(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('datastore_id', 'user_id', name='uq_datastore_credentials_datastore_user')
    )
    op.create_index(op.f('ix_datastore_credentials_user_id'), 'datastore_credentials', ['user_id'], unique=False)
    op.create_table('import_jobs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('status', sa.String(), server_default='pending', nullable=False),
    sa.Column('bucket', sa.String(), nullable=False),
    sa.Column('prefix', sa.String(), nullable=False),
    sa.Column('filespace_id', sa.String(), nullable=False),
    sa.Column('datastore_id', sa.String(), nullable=False),
    sa.Column('total_files', sa.Integer(), server_default='0', nullable=False),
    sa.Column('completed_files', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed_files', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error_message', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_import_jobs_status', 'import_jobs', ['status'], unique=False)
    op.create_index('idx_import_jobs_status_user', 'import_jobs', ['status', 'user_id'], unique=False)
    op.create_index(op.f('ix_import_jobs_user_id'), 'import_jobs', ['user_id'], unique=False)
    op.create_table('sqs_credentials',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('access_key', sa.String(), nullable=False),
    sa.Column('secret_key_encrypted', sa.LargeBinary(), nullable=False),
    sa.Column('region', sa.String(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sqs_credentials_user_id'), 'sqs_credentials', ['user_id'], unique=False)
    op.create_table('sqs_queues',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('queue_url', sa.String(), nullable=False),
    sa.Column('queue_arn', sa.String(), nullable=True),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('region', sa.String(), nullable=False),
    sa.Column('datastore_id', sa.String(), nullable=False),
    sa.Column('filespace_id', sa.String(), nullable=False),
    sa.Column('import_prefix', sa.String(), server_default='', nullable=False),
    sa.Column('status', sa.String(), server_default='active', nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_poll_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sqs_queues_user_id'), 'sqs_queues', ['user_id'], unique=False)
    op.create_table('user_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked', sa.Boolean(), server_default='false', nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)
    op.create_table('sqs_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('queue_id', sa.UUID(), nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('bucket', sa.String(), nullable=False),
    sa.Column('object_key', sa.String(), nullable=False),
    sa.Column('object_size', sa.BigInteger(), nullable=True),
    sa.Column('event_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(), server_default='pending', nullable=False),
    sa.Column('job_id', sa.String(), nullable=True),
    sa.Column('error_message', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['queue_id'], ['sqs_queues.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_sqs_events_message_id', 'sqs_events', ['message_id'], unique=False)
    op.create_index('idx_sqs_events_queue_id', 'sqs_events', ['queue_id'], unique=False)
    op.create_index('idx_sqs_events_queue_status', 'sqs_events', ['queue_id', 'status'], unique=False)
    op.create_index('idx_sqs_events_status', 'sqs_events', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_sqs_events_status', table_name='sqs_events')
    op.drop_index('idx_sqs_events_queue_status', table_name='sqs_events')
    op.drop_index('idx_sqs_events_queue_id', table_name='sqs_events')
    op.drop_index('idx_sqs_events_message_id', table_name='sqs_events')
    op.drop_table('sqs_events')
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_index(op.f('ix_sqs_queues_user_id'), table_name='sqs_queues')
    op.drop_table('sqs_queues')
    op.drop_index(op.f('ix_sqs_credentials_user_id'), table_name='sqs_credentials')
    op.drop_table('sqs_credentials')
    op.drop_index(op.f('ix_import_jobs_user_id'), table_name='import_jobs')
    op.drop_index('idx_import_jobs_status_user', table_name='import_jobs')
    op.drop_index('idx_import_jobs_status', table_name='import_jobs')
    op.drop_table('import_jobs')
    op.drop_index(op.f('ix_datastore_credentials_user_id'), table_name='datastore_credentials')
    op.drop_table('datastore_credentials')
    op.drop_table('users')
    op.drop_table('processed_jobs')
