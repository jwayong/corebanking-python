"""batch_runs

Revision ID: rev_007
Revises: rev_006
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'rev_007'
down_revision = 'rev_006'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('''
    CREATE TABLE batch_runs (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        job_name        VARCHAR(64)  NOT NULL,
        business_date   DATE         NOT NULL,
        status          VARCHAR(16)  NOT NULL DEFAULT 'running',
        processed_count INT          NOT NULL DEFAULT 0,
        success_count   INT          NOT NULL DEFAULT 0,
        error_count     INT          NOT NULL DEFAULT 0,
        errors          JSONB        DEFAULT '[]',
        dry_run         BOOLEAN      NOT NULL DEFAULT false,
        started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        completed_at    TIMESTAMPTZ
    );
    ''')

    op.execute('CREATE INDEX idx_batch_runs_date ON batch_runs (business_date DESC, started_at DESC)')

def downgrade():
    op.execute('DROP TABLE IF EXISTS batch_runs')
