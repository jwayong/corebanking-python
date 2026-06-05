"""init_schema

Revision ID: rev_001
Revises: 
CreateDate: 2026-06-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'rev_001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # ── Customers ───────────────────────────────────────────────────────
    op.execute('''
    CREATE TABLE customers (
        customer_ref    VARCHAR(64) PRIMARY KEY,
        name            VARCHAR(256) NOT NULL,
        labels          JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    # ── Accounts (metadata overlay on TigerBeetle) ─────────────────────
    op.execute('''
    CREATE TABLE accounts (
        id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tb_account_id       BYTEA NOT NULL UNIQUE,
        product_id          BIGINT NOT NULL,
        account_number      VARCHAR(32) NOT NULL UNIQUE,
        status              VARCHAR(16) NOT NULL DEFAULT 'active',
        opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        closed_at           TIMESTAMPTZ,
        interest_accrued    BIGINT NOT NULL DEFAULT 0,
        last_interest_date  DATE,
        labels              JSONB DEFAULT '{}',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    # ── Customer-Account Relationship ───────────────────────────────────
    op.execute('''
    CREATE TABLE customer_accounts (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        customer_ref    VARCHAR(64) NOT NULL REFERENCES customers(customer_ref),
        account_id      BIGINT NOT NULL REFERENCES accounts(id),
        ownership_type  VARCHAR(20) NOT NULL,
        role            VARCHAR(20) NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (customer_ref, account_id, role)
    );
    ''')

    # ── Loan Details ────────────────────────────────────────────────────
    op.execute('''
    CREATE TABLE loan_details (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        account_id      BIGINT NOT NULL UNIQUE REFERENCES accounts(id),
        principal       BIGINT NOT NULL,
        outstanding     BIGINT NOT NULL,
        interest_rate   NUMERIC(10,6) NOT NULL,
        term_months     INT NOT NULL,
        disbursed_at    TIMESTAMPTZ NOT NULL,
        maturity_date   DATE NOT NULL,
        next_payment_due DATE NOT NULL,
        payment_amount  BIGINT NOT NULL,
        arrears_amount  BIGINT NOT NULL DEFAULT 0,
        status          VARCHAR(16) NOT NULL DEFAULT 'active',
        collateral      JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    # ── Transfer Metadata ───────────────────────────────────────────────
    op.execute('''
    CREATE TABLE transfer_metadata (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tb_transfer_id  BYTEA NOT NULL UNIQUE,
        tb_correlation  BYTEA,
        account_id      BIGINT NOT NULL REFERENCES accounts(id),
        counterparty    VARCHAR(128),
        description     VARCHAR(256),
        reference       VARCHAR(64),
        value_date      DATE NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    # ── Audit Log ───────────────────────────────────────────────────────
    op.execute('''
    CREATE TABLE audit_log (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        action          VARCHAR(64) NOT NULL,
        entity_type     VARCHAR(32) NOT NULL,
        entity_id       VARCHAR(64) NOT NULL,
        actor           VARCHAR(128) NOT NULL,
        details         JSONB DEFAULT '{}',
        ip_address      INET,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ''')

    # ── Interest Accrual Log ────────────────────────────────────────────
    op.execute('''
    CREATE TABLE interest_accrual_log (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        account_id      BIGINT NOT NULL REFERENCES accounts(id),
        accrual_date    DATE NOT NULL,
        amount          BIGINT NOT NULL,
        rate_applied    NUMERIC(10,6) NOT NULL,
        days_basis      INT NOT NULL,
        tb_transfer_id  BYTEA,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (account_id, accrual_date)
    );
    ''')

def downgrade():
    op.execute('DROP TABLE IF EXISTS interest_accrual_log')
    op.execute('DROP TABLE IF EXISTS audit_log')
    op.execute('DROP TABLE IF EXISTS transfer_metadata')
    op.execute('DROP TABLE IF EXISTS loan_details')
    op.execute('DROP TABLE IF EXISTS customer_accounts')
    op.execute('DROP TABLE IF EXISTS accounts')
    op.execute('DROP TABLE IF EXISTS customers')
