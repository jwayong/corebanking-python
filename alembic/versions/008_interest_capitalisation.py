"""interest_capitalisation

Revision ID: rev_008
Revises: rev_007
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'rev_008'
down_revision = 'rev_007'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('ALTER TABLE interest_accrual_log ADD COLUMN capitalised BOOLEAN NOT NULL DEFAULT false')
    op.execute('ALTER TABLE interest_accrual_log ADD COLUMN capitalised_at TIMESTAMPTZ')
    op.execute('ALTER TABLE interest_accrual_log ADD COLUMN capitalisation_tb_transfer_id BYTEA')
    op.execute('CREATE INDEX idx_interest_accrual_uncapitalised ON interest_accrual_log (account_id) WHERE capitalised = false')

def downgrade():
    op.execute('DROP INDEX IF EXISTS idx_interest_accrual_uncapitalised')
    op.execute('ALTER TABLE interest_accrual_log DROP COLUMN IF EXISTS capitalisation_tb_transfer_id')
    op.execute('ALTER TABLE interest_accrual_log DROP COLUMN IF EXISTS capitalised_at')
    op.execute('ALTER TABLE interest_accrual_log DROP COLUMN IF EXISTS capitalised')
