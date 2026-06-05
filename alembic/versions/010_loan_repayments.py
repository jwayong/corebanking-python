"""loan_repayments

Revision ID: rev_010
Revises: rev_009
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op

revision = 'rev_010'
down_revision = 'rev_009'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('''
    CREATE TABLE loan_repayments (
        id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        account_id    BIGINT NOT NULL REFERENCES accounts(id),
        amount        BIGINT NOT NULL,
        payment_date  DATE NOT NULL,
        tb_transfer_id BYTEA,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    op.execute('CREATE INDEX idx_loan_repayments_account_date ON loan_repayments (account_id, payment_date)')

    op.execute('''
    CREATE INDEX idx_loan_details_overdue
        ON loan_details (account_id)
        WHERE next_payment_due < CURRENT_DATE
          AND outstanding > 0
          AND status != 'closed';
    ''')

def downgrade():
    op.execute('DROP INDEX IF EXISTS idx_loan_details_overdue')
    op.execute('DROP INDEX IF EXISTS idx_loan_repayments_account_date')
    op.execute('DROP TABLE IF EXISTS loan_repayments')
