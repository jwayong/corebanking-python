"""fx_rates

Revision ID: rev_006
Revises: rev_005
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op

revision = 'rev_006'
down_revision = 'rev_005'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('''
    CREATE TABLE exchange_rates (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        sell_currency   VARCHAR(3)  NOT NULL,
        buy_currency    VARCHAR(3)  NOT NULL,
        rate            NUMERIC(15,6) NOT NULL CHECK (rate > 0),
        effective_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    op.execute('CREATE UNIQUE INDEX idx_exchange_rates_pair_active ON exchange_rates (sell_currency, buy_currency, effective_at)')
    op.execute('CREATE INDEX idx_exchange_rates_effective ON exchange_rates (effective_at)')

def downgrade():
    op.execute('DROP TABLE IF EXISTS exchange_rates')
