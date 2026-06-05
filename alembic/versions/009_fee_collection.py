"""fee_collection

Revision ID: rev_009
Revises: rev_008
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'rev_009'
down_revision = 'rev_008'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('ALTER TABLE accounts ADD COLUMN last_fee_date DATE')
    op.execute('''
    CREATE INDEX idx_accounts_fee_pending ON accounts (id) 
    WHERE last_fee_date IS NULL OR DATE_TRUNC('month', last_fee_date) < DATE_TRUNC('month', CURRENT_DATE);
    ''')

def downgrade():
    op.execute('DROP INDEX IF EXISTS idx_accounts_fee_pending')
    op.execute('ALTER TABLE accounts DROP COLUMN IF EXISTS last_fee_date')
