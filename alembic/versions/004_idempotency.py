"""idempotency

Revision ID: rev_004
Revises: rev_003
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'rev_004'
down_revision = 'rev_003'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('''
    CREATE TABLE idempotency_keys (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        idempotency_key VARCHAR(64) NOT NULL UNIQUE,
        status          VARCHAR(16) NOT NULL DEFAULT 'pending',
        tb_transfer_id  BYTEA,
        response_code   INT,
        response_body   JSONB,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at    TIMESTAMPTZ
    );
    ''')

    op.execute('CREATE INDEX idx_idempotency_keys_key ON idempotency_keys (idempotency_key)')

def downgrade():
    op.execute('DROP TABLE IF EXISTS idempotency_keys')
