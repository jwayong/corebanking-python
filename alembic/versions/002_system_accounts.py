"""system_accounts

Revision ID: rev_002
Revises: rev_001
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'rev_002'
down_revision = 'rev_001'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('''
    CREATE TABLE system_accounts (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tb_account_id   BYTEA NOT NULL UNIQUE,
        currency        CHAR(3) NOT NULL,
        ledger          INT NOT NULL,
        account_code    SMALLINT NOT NULL,
        account_name    VARCHAR(128) NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (currency, account_code)
    );
    ''')

def downgrade():
    op.execute('DROP TABLE IF EXISTS system_accounts')
