"""indexes

Revision ID: rev_005
Revises: rev_004
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op

revision = 'rev_005'
down_revision = 'rev_004'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('CREATE INDEX idx_interest_accrual_account ON interest_accrual_log (account_id, accrual_date)')
    op.execute('CREATE INDEX idx_customer_accounts_customer ON customer_accounts (customer_ref)')
    op.execute('CREATE INDEX idx_customer_accounts_account ON customer_accounts (account_id)')
    op.execute('CREATE INDEX idx_transfer_metadata_account ON transfer_metadata (account_id)')
    op.execute('CREATE INDEX idx_transfer_metadata_correlation ON transfer_metadata (tb_correlation)')
    op.execute('CREATE INDEX idx_accounts_product ON accounts (product_id)')
    op.execute('CREATE INDEX idx_accounts_status ON accounts (status)')

def downgrade():
    op.execute('DROP INDEX IF EXISTS idx_accounts_status')
    op.execute('DROP INDEX IF EXISTS idx_accounts_product')
    op.execute('DROP INDEX IF EXISTS idx_transfer_metadata_correlation')
    op.execute('DROP INDEX IF EXISTS idx_transfer_metadata_account')
    op.execute('DROP INDEX IF EXISTS idx_customer_accounts_account')
    op.execute('DROP INDEX IF EXISTS idx_customer_accounts_customer')
    op.execute('DROP INDEX IF EXISTS idx_interest_accrual_account')
