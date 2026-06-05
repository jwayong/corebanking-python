"""products

Revision ID: rev_003
Revises: rev_002
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'rev_003'
down_revision = 'rev_002'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('''
    CREATE TABLE fee_schedules (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name            VARCHAR(128) NOT NULL,
        fees            JSONB NOT NULL,
        is_active       BOOLEAN NOT NULL DEFAULT true,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    op.execute('''
    CREATE TABLE products (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        code            VARCHAR(32) NOT NULL UNIQUE,
        name            VARCHAR(128) NOT NULL,
        category        VARCHAR(32) NOT NULL,
        tb_account_code SMALLINT NOT NULL,
        currency        CHAR(3) NOT NULL,
        tb_ledger       INT NOT NULL,
        interest_rate   NUMERIC(10,6),
        interest_basis  VARCHAR(16),
        fee_schedule_id BIGINT REFERENCES fee_schedules(id),
        min_balance     BIGINT,
        max_balance     BIGINT,
        is_active       BOOLEAN NOT NULL DEFAULT true,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    op.execute('''
    ALTER TABLE accounts
        ADD CONSTRAINT fk_accounts_product
        FOREIGN KEY (product_id) REFERENCES products(id);
    ''')

def downgrade():
    op.execute('ALTER TABLE accounts DROP CONSTRAINT IF EXISTS fk_accounts_product')
    op.execute('DROP TABLE IF EXISTS products')
    op.execute('DROP TABLE IF EXISTS fee_schedules')
